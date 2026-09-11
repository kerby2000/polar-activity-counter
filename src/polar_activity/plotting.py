"""Separate axis/magnitude plots with explicitly approximate label alignment."""

import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from .diagnostics import read_rows


def plot_session(
    path: Path,
    set_id: str | None = None,
    activity: str | None = None,
    start: float | None = None,
    end: float | None = None,
) -> list[Path]:
    if start is not None and end is not None and end <= start:
        raise ValueError("--end must be greater than --start")
    if not (path / "metadata.json").exists():
        raise ValueError(f"Not a session directory: {path}")
    labels = read_rows(path / "labels.csv")
    if activity:
        matches = [item for item in labels if item["activity"] == activity]
        if not matches:
            raise ValueError(f"No labelled sets for {activity}")
        return [
            plot
            for item in matches
            for plot in plot_session(path, item["set_id"], start=start, end=end)
        ]
    suffix = "session"
    if set_id:
        selected = next((item for item in labels if item["set_id"] == set_id.zfill(3)), None)
        if not selected or not selected["end_time_s"]:
            raise ValueError(f"No closed set {set_id}")
        start = max(
            float(selected["start_time_s"]) - 1, start if start is not None else -float("inf")
        )
        end = min(float(selected["end_time_s"]) + 1, end if end is not None else float("inf"))
        suffix = f"set-{selected['set_id']}"
    if start is not None or end is not None:
        suffix += f"_{start if start is not None else 'start'}_{end if end is not None else 'end'}"
    suffix = re.sub(r"[^a-zA-Z0-9_.-]", "_", suffix)
    directory = path / "plots"
    directory.mkdir(exist_ok=True)
    outputs = []
    for stream, unit in [("acc", "mg"), ("gyro", "deg/s"), ("mag", "µT"), ("hr", "bpm")]:
        rows = read_rows(path / f"{stream}.csv")
        rows = [
            row
            for row in rows
            if (start is None or float(row["time_s"]) >= start)
            and (end is None or float(row["time_s"]) <= end)
        ]
        if not rows:
            continue
        times = np.array([float(row["time_s"]) for row in rows])
        nplots = 1 if stream == "hr" else 4
        fig, axes = plt.subplots(
            nplots,
            1,
            figsize=(13, 3 if nplots == 1 else 9),
            sharex=True,
            squeeze=False,
            layout="constrained",
        )
        axes = axes[:, 0]
        if stream == "hr":
            from .heart_rate import plot_heart_rate

            plot_heart_rate(axes[0], rows)
        else:
            csv_unit = {"acc": "mg", "gyro": "dps", "mag": "ut"}[stream]
            values = np.array(
                [[float(row[f"{stream}_{axis}_{csv_unit}"]) for axis in "xyz"] for row in rows]
            )
            signals = [values[:, i] for i in range(3)] + [np.linalg.norm(values, axis=1)]
            for index, (axis, signal, name) in enumerate(
                zip(axes, signals, ["X", "Y", "Z", "Magnitude"], strict=True)
            ):
                axis.plot(
                    times,
                    signal,
                    color=["#2563a6", "#b05e13", "#247348", "#604093"][index],
                    linewidth=0.8,
                )
                axis.set_ylabel(f"{name} ({unit})")
        for axis in axes:
            axis.grid(alpha=0.2)
        low, high = (
            (start if start is not None else times.min()),
            (end if end is not None else times.max()),
        )
        if high <= low:
            high = low + 1
        for index, item in enumerate(labels):
            if not item["end_time_s"]:
                continue
            left, right = float(item["start_time_s"]), float(item["end_time_s"])
            if right < low or left > high:
                continue
            for axis in axes:
                axis.axvspan(left, right, color="#e8c870", alpha=0.23)
            label = (
                f"{item['activity'].upper()} | set {item['set_id']} | "
                f"{item['expected_rep_count'] or '?'} reps"
            )
            if item["completion"] != "complete":
                label += " (interrupted)"
            align_right = left > low + 0.7 * (high - low)
            axes[0].text(
                min(right, high) if align_right else max(left, low),
                0.98 - (index % 3) * 0.12,
                label,
                transform=axes[0].get_xaxis_transform(),
                va="top",
                ha="right" if align_right else "left",
                fontsize=8,
                bbox={"facecolor": "white", "alpha": 0.8, "edgecolor": "none"},
                clip_on=True,
            )
        axes[-1].set_xlim(low, high)
        axes[-1].set_xlabel("Session time (s); label/IMU alignment includes unknown BLE delay")
        fig.suptitle(f"{stream.upper()} - {path.name} - {suffix}", fontsize=12)
        output = directory / f"{stream}_{suffix}.png"
        fig.savefig(output, dpi=150)
        plt.close(fig)
        outputs.append(output)
    if not outputs:
        raise ValueError("No samples in the selected interval")
    return outputs
