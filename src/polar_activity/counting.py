"""Session input and reviewable outputs for the offline automatic counter."""

import csv
import hashlib
import json
from pathlib import Path

import numpy as np

from .counter import detect_sets
from .diagnostics import read_rows
from .storage import write_json


def load_motion(session: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    data = []
    for stream, unit in (("acc", "mg"), ("gyro", "dps")):
        rows = read_rows(session / f"{stream}.csv")
        if len(rows) < 2:
            raise ValueError(f"{stream.upper()} needs at least two samples for counting")
        try:
            # Only small, session-relative times become floats. Check integer device
            # timestamps as well; never sort/repair or float-cast the original epoch.
            stamps = [int(row["device_timestamp_ns"]) for row in rows]
            if any(b <= a for a, b in zip(stamps, stamps[1:], strict=False)):
                raise ValueError(f"{stream.upper()} has duplicate/backward device timestamps")
            times = np.array([float(row["time_s"]) for row in rows])
            expected = np.array([(stamp - stamps[0]) / 1e9 for stamp in stamps])
            if not np.allclose(times - times[0], expected, rtol=0, atol=2e-6):
                raise ValueError(f"{stream.upper()} session times disagree with device timestamps")
            values = np.array(
                [[float(row[f"{stream}_{axis}_{unit}"]) for axis in "xyz"] for row in rows]
            )
        except KeyError as exc:
            raise ValueError(f"Missing required {stream.upper()} CSV field: {exc}") from exc
        data.extend((times, values))
    return tuple(data)


def _write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _plot(data: tuple, result: dict, output: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    at, acc, gt, gyro = data
    rows = 2 + len(result["sets"])
    figure, axes = plt.subplots(rows, 1, figsize=(13, 3 * rows), squeeze=False)
    axes = axes[:, 0]
    axes[0].plot(at, np.linalg.norm(acc, axis=1), color="#235789", lw=0.7)
    axes[0].set_ylabel("ACC magnitude (mg)")
    axes[1].plot(gt, np.linalg.norm(gyro, axis=1), color="#2a9d8f", lw=0.7)
    axes[1].set_ylabel("Gyro magnitude (degrees/s)")
    for axis in axes[:2]:
        for bout in result["sets"]:
            axis.axvspan(bout["start_time_s"], bout["end_time_s"], color="#2a9d8f", alpha=0.2)
            for pause in bout["pauses"]:
                axis.axvspan(pause["start_time_s"], pause["end_time_s"], color="#dda448", alpha=0.7)
        for gap in result["gaps"]:
            axis.axvspan(gap["start_time_s"], gap["end_time_s"], color="#bf4536", alpha=0.2)
        axis.set_xlim(min(at[0], gt[0]), max(at[-1], gt[-1]))
    for axis, bout in zip(axes[2:], result["sets"], strict=True):
        mask = (gt >= bout["start_time_s"] - 1) & (gt <= bout["end_time_s"] + 1)
        # Each block retains its own automatically selected sensor direction.
        for block in bout["motion_blocks"]:
            selected = mask & (gt >= block["start_time_s"]) & (gt <= block["end_time_s"])
            projection = (gyro[selected] - np.array(block["gyro_bias_dps"])) @ np.array(
                block["gyro_axis"]
            )
            axis.plot(gt[selected], projection, color="#235789", lw=1)
        for pause in bout["pauses"]:
            axis.axvspan(pause["start_time_s"], pause["end_time_s"], color="#dda448", alpha=0.3)
            axis.text(
                (pause["start_time_s"] + pause["end_time_s"]) / 2,
                0.5,
                f"Rest\n{pause['duration_s']:.2f}s",
                transform=axis.get_xaxis_transform(),
                ha="center",
                va="center",
                fontsize=8,
            )
        for index, cycle in enumerate(bout["cycles"], 1):
            left, right = cycle["start_time_s"], cycle["end_time_s"]
            axis.axvspan(left, right, color="#2a9d8f", alpha=0.13 if index % 2 else 0.27)
            axis.text(
                (left + right) / 2,
                0.95,
                str(index),
                transform=axis.get_xaxis_transform(),
                ha="center",
                va="top",
                fontsize=9,
            )
        axis.axhline(0, color="#778899", lw=0.7)
        axis.set_ylabel("Gyro projection (degrees/s)")
        axis.set_title(
            f"Set {bout['set_id']}: {bout['complete_cycles']} complete motion cycles | "
            f"{bout['start_time_s']:.2f}–{bout['end_time_s']:.2f}s"
        )
    for axis in axes:
        axis.grid(alpha=0.2)
        axis.set_xlabel("Original session time (s)")
    figure.suptitle(
        "Automatic repeated-motion detection — no manual boundaries or counts\n"
        "Green = accepted cycles; amber = grouped rest; exercise type is unclassified"
    )
    figure.tight_layout()
    figure.savefig(output / "automatic_count.png", dpi=140)
    plt.close(figure)


def count_session(session: Path, output: Path | None = None, plot: bool = True) -> dict:
    output = output or session / "automatic-count"
    if output.resolve() == session.resolve():
        raise ValueError("Choose an output subdirectory or separate folder for derived counts")
    data = load_motion(session)
    # Intentionally do not open labels.csv, label_events.jsonl or HR data.
    result = detect_sets(*data)
    meta_path = session / "metadata.json"
    metadata = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    result.update(
        {
            "schema_version": 2,
            "source_session_id": metadata.get("session_id"),
            "source_status": metadata.get("status", "unknown"),
            "source_sha256": {
                name: hashlib.sha256((session / name).read_bytes()).hexdigest()
                for name in ("acc.csv", "gyro.csv")
            },
            "warnings": [],
            "limitations": [
                "Repeated motion is detected; exercise type and correct form remain unverified.",
                "Complete means an observed signal excursion and return, not a peak or half-cycle.",
                "At least three consistent cycles and an eight-second intact overlap are required.",
                "Very short, slow, low-rotation or changing-cadence sets can be missed.",
                "Boundaries use a derived 40ms grid; host alignment still has unknown BLE delay.",
                "Similarity scores measure waveform fit, not calibrated probabilities or accuracy.",
                "Compatible blocks may share a set across a quiet rest up to three seconds.",
            ],
            "output_directory": str(output),
        }
    )
    if metadata.get("status") not in (None, "complete"):
        result["warnings"].append(
            "Source session ended early; only saved intact portions were counted"
        )
    if result["gaps"]:
        result["warnings"].append("Data gaps split the analysis; no cycle spans a detected gap")
    if any(bout["near_recording_or_gap_edge"] for bout in result["sets"]):
        result["warnings"].append(
            "A set is near a recording edge or gap; only observed complete cycles are included"
        )
    if any(not segment["evaluated"] for segment in result["segments"]):
        result["warnings"].append("Some intact portions were too short for the detection window")
    if not result["segments"]:
        result["warnings"].append("ACC and gyro have no usable overlapping interval")
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "counts.json", result)
    _write_csv(
        output / "sets.csv",
        [
            "set_id",
            "activity",
            "start_time_s",
            "end_time_s",
            "complete_cycles",
            "shape_similarity",
            "pause_duration_s",
        ],
        result["sets"],
    )
    cycles = [
        {"set_id": bout["set_id"], "cycle": index, **cycle}
        for bout in result["sets"]
        for index, cycle in enumerate(bout["cycles"], 1)
    ]
    _write_csv(
        output / "cycles.csv",
        [
            "set_id",
            "cycle",
            "block_id",
            "start_time_s",
            "end_time_s",
            "duration_s",
            "acc_similarity",
            "gyro_similarity",
        ],
        cycles,
    )
    _write_csv(
        output / "pauses.csv",
        ["set_id", "pause", "start_time_s", "end_time_s", "duration_s"],
        [
            {"set_id": bout["set_id"], "pause": index, **pause}
            for bout in result["sets"]
            for index, pause in enumerate(bout["pauses"], 1)
        ],
    )
    if plot:
        _plot(data, result, output)
    return result


def format_counts(result: dict) -> str:
    lines = ["Automatic repeated-motion count (exercise type unclassified)"]
    for bout in result["sets"]:
        lines.append(
            f"Set {bout['set_id']}: {bout['start_time_s']:.2f}-{bout['end_time_s']:.2f}s | "
            f"{bout['complete_cycles']} complete cycles"
        )
        if bout["pauses"]:
            lines.append(
                f"  Includes {len(bout['pauses'])} short rest(s), "
                f"{bout['pause_duration_s']:.2f}s total"
            )
    if not result["sets"]:
        lines.append("No qualifying repeated-motion sets found.")
    lines.append(f"Total: {result['total_complete_cycles']} complete cycles")
    lines.extend(f"WARNING: {warning}" for warning in result["warnings"])
    lines.append(f"Saved counts and cycle boundaries: {result['output_directory']}")
    return "\n".join(lines)
