"""Personal activity analysis combining motion cycles, impacts and short excursions."""

import hashlib
import json
from pathlib import Path

import numpy as np

from .counter import detect_sets
from .counting import _write_csv, load_motion
from .motion_quality import arm_excursions, compare_excursions
from .recognition import (
    activity_intervals,
    classify,
    load_model,
    motion_blocks,
    predict_windows,
    source_hashes,
)
from .storage import write_json


def jump_impacts(times: np.ndarray, acc: np.ndarray) -> list[dict]:
    """One impact event per resolved acceleration pulse, not one rep per raw peak."""
    rate = 1 / float(np.median(np.diff(times)))
    width = max(1, round(0.05 * rate) // 2 * 2 + 1)
    norm = np.linalg.norm(acc, axis=1)
    x = np.convolve(
        np.pad(norm, (width // 2, width // 2), mode="edge"), np.ones(width) / width, mode="valid"
    )
    peaks = np.flatnonzero((x[1:-1] > x[:-2]) & (x[1:-1] >= x[2:])) + 1
    peaks = peaks[x[peaks] >= 2400]
    selected = []
    for index in sorted(peaks, key=lambda i: -x[i]):
        if all(abs(times[index] - times[old]) >= 0.4 for old in selected):
            selected.append(int(index))
    events = []
    for index in sorted(selected):
        before = np.flatnonzero((times >= times[index] - 0.4) & (times < times[index]))
        after = np.flatnonzero((times > times[index]) & (times <= times[index] + 0.5) & (x < 1400))
        if not len(before) or not len(after) or np.min(x[before]) >= 700:
            continue
        start = before[np.argmin(x[before])]
        if start == 0 or after[0] == len(times) - 1:
            continue
        events.append(
            {
                "start_time_s": float(times[start]),
                "end_time_s": float(times[after[0]]),
                "peak_time_s": float(times[index]),
                "peak_mg": float(x[index]),
            }
        )
    return events


def _jump_sets(
    data: tuple, blocks: list[tuple], intervals: list[dict], convention: str
) -> list[dict]:
    at, acc, _, _ = data
    result = []
    for block_id, (t, _, _) in enumerate(blocks):
        selected = (at >= t[0]) & (at <= t[-1])
        events = jump_impacts(at[selected], acc[selected])
        events = [
            e
            for e in events
            if any(
                i["activity"] == "jump"
                and i["block_id"] == block_id
                and i["start_time_s"] - 2 <= e["peak_time_s"] <= i["end_time_s"] + 2
                for i in intervals
            )
        ]
        groups = []
        for event in events:
            if not groups or event["peak_time_s"] - groups[-1][-1]["peak_time_s"] > 3:
                groups.append([])
            groups[-1].append(event)
        for group in groups:
            if len(group) < 3:
                continue
            pairs, orphans = [], []
            index = 0
            while index < len(group):
                if (
                    index + 1 < len(group)
                    and group[index + 1]["peak_time_s"] - group[index]["peak_time_s"] <= 1.1
                ):
                    pairs.append(
                        {
                            "start_time_s": group[index]["start_time_s"],
                            "end_time_s": group[index + 1]["end_time_s"],
                        }
                    )
                    index += 2
                else:
                    orphans.append(group[index])
                    index += 1
            estimate = (
                len(pairs)
                if convention == "paired_impacts"
                else len(group)
                if convention == "single_impact"
                else None
            )
            result.append(
                {
                    "activity": "jump",
                    "method": "impact-pairs-v1",
                    "rep_estimate": estimate,
                    "start_time_s": group[0]["start_time_s"],
                    "end_time_s": group[-1]["end_time_s"],
                    "count_convention": convention,
                    "impact_events": group,
                    "impact_count": len(group),
                    "paired_impact_count": len(pairs),
                    "unpaired_impact_count": len(orphans),
                    "cycles": group if convention == "single_impact" else pairs,
                    "count_status": "estimate"
                    if estimate is not None
                    else "convention_unconfirmed",
                }
            )
    return result


def short_excursions(t: np.ndarray, acc: np.ndarray, start: float, end: float) -> list[dict]:
    """Compatibility entry point for orientation-based motion excursions."""
    return arm_excursions(t, acc, start, end)


def _exercise_sets(periodic: dict, blocks: list[tuple], model: dict) -> list[dict]:
    sets = []
    for bout in periodic["sets"]:
        block = next(
            b for b in blocks if b[0][0] <= bout["start_time_s"] < bout["end_time_s"] <= b[0][-1]
        )
        t, acc, gyro = block
        mask = (t >= bout["start_time_s"]) & (t <= bout["end_time_s"])
        prediction = classify(acc[mask], gyro[mask], model)
        # A walking stride or repeated household action is not a workout repetition.
        if prediction["activity"] in {
            "walking",
            "stairs",
            "household",
            "standing_or_sitting",
            "other_movement",
            "stationary",
            "jump",
        }:
            continue
        sets.append(
            {
                **bout,
                "activity": prediction["activity"],
                "recognition": prediction,
                "method": "periodic-return-v2",
                "rep_estimate": bout["complete_cycles"],
                "count_status": "observed_motion_cycles",
            }
        )
    return sets


def analyse_session(
    session: Path, model_path: Path, output: Path | None = None, plot: bool = True
) -> dict:
    output = output or session / "analysis"
    if output.resolve() == session.resolve():
        raise ValueError("Choose a derived output subdirectory or separate folder")
    data = load_motion(session)
    model = load_model(model_path)
    metadata = json.loads((session / "metadata.json").read_text(encoding="utf-8"))
    for key in ("subject", "sensor_position"):
        if metadata.get(key) != model.get(key):
            raise ValueError(
                f"Session {key} differs from personal model; train matching references"
            )
    blocks = motion_blocks(data)
    windows = predict_windows(blocks, model)
    intervals = activity_intervals(windows)
    periodic = detect_sets(*data)
    sets = _exercise_sets(periodic, blocks, model)
    sets.extend(_jump_sets(data, blocks, intervals, model["jump_count_convention"]))
    for interval in intervals:
        if (
            interval["activity"] != "pull-up"
            or interval["end_time_s"] - interval["start_time_s"] < 3
        ):
            continue
        t, acc, gyro = blocks[interval["block_id"]]
        excursions = arm_excursions(t, acc, interval["start_time_s"], interval["end_time_s"], gyro)
        if len(excursions) < 2:
            continue
        left, right = excursions[0]["start_time_s"], excursions[-1]["end_time_s"]
        if any(min(right, s["end_time_s"]) > max(left, s["start_time_s"]) for s in sets):
            continue
        sets.append(
            {
                "activity": "pull-up",
                "method": "short-arm-excursions-v2",
                "motion_quality": compare_excursions(excursions),
                "rep_estimate": len(excursions),
                "start_time_s": left,
                "end_time_s": right,
                "cycles": excursions,
                "complete_cycles": sum(e["return_observed"] for e in excursions),
                "incomplete_returns": sum(not e["return_observed"] for e in excursions),
                "count_status": "attempt_estimate",
            }
        )
    sets.sort(key=lambda s: s["start_time_s"])
    for index, bout in enumerate(sets, 1):
        bout["set_id"] = f"{index:03d}"
    # Exercise names in the timeline also require a corresponding motion event.
    # Otherwise, a preparation gesture resembling a squat is just unknown motion.
    for window in windows:
        if window["activity"] in {"push-up", "pull-up", "squat", "jump"} and not any(
            s["activity"] == window["activity"]
            and min(s["end_time_s"] + 2, window["end_time_s"])
            > max(s["start_time_s"] - 2, window["start_time_s"])
            for s in sets
        ):
            window["activity"] = "unknown"
    intervals = activity_intervals(windows)
    hashes = source_hashes(session)
    reused = any(s["sha256"] == hashes for s in model["sources"])
    warnings = [f"Source recording: {w}" for w in metadata.get("quality", {}).get("warnings", [])]
    if not blocks:
        warnings.append("No intact ACC/gyro overlap of at least four seconds was available.")
    if reused:
        warnings.append(
            "This session contributed model references; "
            "this is a training replay, not a blind test."
        )
    if metadata.get("status") != "complete":
        warnings.append("Source ended early; analysing saved intact overlap only.")
    if periodic["gaps"]:
        warnings.append("Missing data split analysis; no event crosses a detected source gap.")
    if any(s["count_status"] == "convention_unconfirmed" for s in sets):
        warnings.append(
            "Jump repetition convention is unconfirmed; "
            "impact counts and possible pairs are reported separately."
        )
    if any(s.get("incomplete_returns") for s in sets):
        warnings.append(
            "A pull-up attempt has no fully observed return; its estimate includes that attempt."
        )
    result = {
        "schema_version": 1,
        "algorithm": "personal-analyser-v2",
        "source_session_id": metadata.get("session_id"),
        "source_sha256": hashes,
        "source_status": metadata.get("status"),
        "model_sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
        "model_path": str(model_path),
        "is_training_session": reused,
        "reads_target_labels": False,
        "model_uses_labelled_references": True,
        "jump_convention": {
            "value": model["jump_count_convention"],
            "source": model["jump_convention_source"],
        },
        "sets": sets,
        "activities": intervals,
        "windows": windows,
        "gaps": periodic["gaps"],
        "warnings": warnings,
        "limitations": model["limitations"]
        + [
            "Activity boundaries have about two seconds of window context; "
            "brief transitions are uncertain.",
            "Jump pulse pairing depends on the declared counting convention; "
            "no body-flight measurement is available.",
            "Short pull-up counts describe arm excursions, including flagged incomplete returns, "
            "not verified technique.",
        ],
        "output_directory": str(output),
    }
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "analysis.json", result)
    _write_csv(
        output / "activities.csv", ["activity", "start_time_s", "end_time_s", "block_id"], intervals
    )
    _write_csv(
        output / "sets.csv",
        [
            "set_id",
            "activity",
            "start_time_s",
            "end_time_s",
            "rep_estimate",
            "count_status",
            "method",
        ],
        sets,
    )
    _write_csv(
        output / "repetitions.csv",
        ["set_id", "repetition", "start_time_s", "end_time_s", "return_observed"],
        [
            {"set_id": s["set_id"], "repetition": i, **c}
            for s in sets
            if s["rep_estimate"] is not None
            for i, c in enumerate(s["cycles"], 1)
        ],
    )
    _write_csv(
        output / "motion_quality.csv",
        [
            "set_id",
            "repetition",
            "start_time_s",
            "end_time_s",
            "peak_time_s",
            "peak_departure_deg",
            "relative_excursion",
            "return_observed",
            "flags",
        ],
        [
            {"set_id": s["set_id"], **e, "flags": ";".join(e["flags"])}
            for s in sets
            for e in s.get("motion_quality", {}).get("repetitions", [])
        ],
    )
    _write_csv(
        output / "jump_impacts.csv",
        ["set_id", "impact", "start_time_s", "end_time_s", "peak_time_s", "peak_mg"],
        [
            {"set_id": s["set_id"], "impact": i, **e}
            for s in sets
            for i, e in enumerate(s.get("impact_events", []), 1)
        ],
    )
    if plot:
        plot_analysis(data, result, output / "analysis.png")
        if any(s.get("motion_quality") for s in sets):
            plot_motion_quality(sets, output / "motion_quality.png")
    return result


def plot_motion_quality(sets: list[dict], output: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    selected = [s for s in sets if s.get("motion_quality")]
    fig, axes = plt.subplots(len(selected), 1, figsize=(9, 4 * len(selected)), squeeze=False)
    for axis, bout in zip(axes[:, 0], selected, strict=True):
        quality = bout["motion_quality"]
        reps = quality["repetitions"]
        numbers = np.arange(1, len(reps) + 1)
        values = [r["peak_departure_deg"] for r in reps]
        axis.bar(numbers, values, color=["#cf6a32" if r["flags"] else "#46758e" for r in reps])
        if quality["reference_departure_deg"] is not None:
            axis.axhline(
                quality["reference_departure_deg"],
                color="#555",
                ls="--",
                lw=1,
                label="Median of returned excursions",
            )
            axis.legend(loc="upper right", fontsize=8)
        for number, value, rep in zip(numbers, values, reps, strict=True):
            text = f"{value:.0f}°"
            if not rep["return_observed"]:
                text += "\nReturn unobserved"
            axis.text(number, value + 3, text, ha="center", fontsize=9)
        axis.set_xticks(numbers, [f"Attempt {i}" for i in numbers])
        axis.set_ylim(0, max(values) * 1.4)
        axis.set_ylabel("Peak sensor-direction departure (degrees)")
        axis.set_title(f"Set {bout['set_id']}: arm-motion comparison")
        axis.grid(axis="y", alpha=0.2)
    fig.text(
        0.5,
        0.015,
        "Sensor proxy only: not joint range, pull-up height or a technique score.",
        ha="center",
        fontsize=9,
    )
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(output, dpi=140)
    plt.close(fig)


def plot_analysis(data: tuple, result: dict, output: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    at, acc, gt, gyro = data
    fig, axes = plt.subplots(3, 1, figsize=(15, 9), sharex=True, height_ratios=[2, 2, 1])
    axes[0].plot(at, np.linalg.norm(acc, axis=1), lw=0.6, color="#235789")
    axes[1].plot(gt, np.linalg.norm(gyro, axis=1), lw=0.6, color="#2a9d8f")
    axes[0].set_ylabel("Acceleration magnitude (mg)")
    axes[1].set_ylabel("Rotation speed (degrees/s)")
    palette = {
        "jump": "#ef8354",
        "push-up": "#5b5f97",
        "pull-up": "#8e44ad",
        "squat": "#2a9d8f",
        "walking": "#4ea5d9",
        "stairs": "#c2571a",
        "household": "#dda448",
        "stationary": "#cad2c5",
        "standing_or_sitting": "#a8b5a2",
        "other_movement": "#a8b5a2",
        "unknown": "#ddd",
    }
    for interval in result["activities"]:
        axes[2].axvspan(
            interval["start_time_s"], interval["end_time_s"], color=palette[interval["activity"]]
        )
    for bout in result["sets"]:
        left, right = bout["start_time_s"], bout["end_time_s"]
        count = bout["rep_estimate"] if bout["rep_estimate"] is not None else "?"
        label = f"{bout['activity']}: {count}"
        if bout.get("incomplete_returns"):
            label += " attempts*"
        if bout["rep_estimate"] is None:
            label += f" ({bout['impact_count']} impacts / {bout['paired_impact_count']} pairs)"
        axes[0].text(
            (left + right) / 2,
            1.01,
            label,
            transform=axes[0].get_xaxis_transform(),
            ha="center",
            fontsize=9,
        )
        for axis in axes[:2]:
            axis.axvspan(left, right, color=palette.get(bout["activity"], "#ddd"), alpha=0.2)
        for cycle in bout["cycles"]:
            axes[1].axvline(cycle["start_time_s"], color="#666", lw=0.5, alpha=0.7)
    names = sorted({i["activity"] for i in result["activities"]})
    handles = [plt.Rectangle((0, 0), 1, 1, color=palette[n]) for n in names]
    axes[2].legend(
        handles,
        [n.replace("_", " ") for n in names],
        loc="upper center",
        bbox_to_anchor=(0.5, -0.4),
        ncol=4,
    )
    axes[2].set_yticks([])
    axes[2].set_ylabel("Activity estimate")
    axes[2].set_xlabel("Original session time (s)")
    for axis in axes[:2]:
        axis.grid(alpha=0.2)
    fig.suptitle(
        f"{output.parent.name} — personal activity analysis\n"
        + (
            "Training replay; not a blind accuracy test"
            if result["is_training_session"]
            else "Not used in model references; development replay"
        )
    )
    fig.tight_layout()
    fig.savefig(output, dpi=130, bbox_inches="tight")
    plt.close(fig)


def format_analysis(result: dict) -> str:
    lines = ["Personal activity analysis"]
    for bout in result["sets"]:
        estimate = bout["rep_estimate"]
        detail = (
            f"{estimate} estimated repetitions"
            if estimate is not None
            else f"{bout['impact_count']} impact events / {bout['paired_impact_count']} pairs; "
            "rep convention unconfirmed"
        )
        lines.append(
            f"Set {bout['set_id']}: {bout['activity']} | {detail} | "
            f"{bout['start_time_s']:.2f}-{bout['end_time_s']:.2f}s"
        )
        if bout.get("incomplete_returns"):
            lines.append(
                f"  {bout['complete_cycles']} returned motion cycle(s); "
                f"{bout['incomplete_returns']} unclosed attempt(s)."
            )
        for rep in bout.get("motion_quality", {}).get("repetitions", []):
            if "reduced_arm_excursion" in rep["flags"]:
                lines.append(
                    f"  Attempt {rep['repetition']}: arm excursion "
                    f"{rep['relative_excursion']:.0%} of this set's returned-motion median; "
                    "reduced motion, not a technique score."
                )
    if not result["sets"]:
        lines.append("No qualifying exercise sets found.")
    walking = [
        i
        for i in result["activities"]
        if i["activity"] in {"walking", "stairs"} and i["end_time_s"] - i["start_time_s"] >= 5
    ]
    for interval in walking:
        lines.append(
            f"{interval['activity'].capitalize()} estimate: "
            f"{interval['start_time_s']:.1f}-{interval['end_time_s']:.1f}s "
            "(steps not counted)"
        )
    lines.extend(f"WARNING: {w}" for w in result["warnings"])
    lines.append(f"Saved report, timeline and plot: {result['output_directory']}")
    return "\n".join(lines)
