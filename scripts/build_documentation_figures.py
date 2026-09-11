"""Render public figures from saved decisions; never rerun/tune blind inference.

Requires the original local recordings, which are not bundled with the repository.
Only motion CSVs and saved analysis decisions are read. No HR, notes or labels are
exported. The frozen blind-02 report and all input hashes must match before drawing.
"""

import argparse
import hashlib
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Patch

from polar_activity.counting import load_motion

BLIND_02_SHA = "bf80fd1a95935bfd6c85bd42044f015e3b3b68a441752ee91c5130c673a470b5"
PALETTE = {
    "jump": "#e87939",
    "squat": "#168c7b",
    "push-up": "#6753b8",
    "pull-up": "#bd4e88",
    "walking": "#4299cd",
    "stairs": "#a86b31",
    "household": "#d7b465",
    "stationary": "#d2ddd5",
    "standing_or_sitting": "#a7bbae",
    "other_movement": "#a2acb7",
    "unknown": "#e0e3e8",
}
INK = "#1c2a3a"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def style_axis(axis):
    axis.set_facecolor("white")
    axis.grid(axis="both", color="#dde3eb", linewidth=0.6, alpha=0.75)
    axis.set_axisbelow(True)
    axis.tick_params(colors="#526174", labelsize=10, length=3)
    for side in ("top", "right"):
        axis.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        axis.spines[side].set_color("#c4cdd8")


def heading(figure, title, subtitle, badge):
    figure.text(0.08, 0.956, title, fontsize=24, weight="bold", color=INK)
    figure.text(0.08, 0.918, subtitle, fontsize=12, color="#526174")
    figure.text(
        0.965,
        0.962,
        badge,
        ha="right",
        fontsize=9,
        weight="bold",
        color="#256054",
        bbox={"boxstyle": "round,pad=0.65", "fc": "#e1eee8", "ec": "none"},
    )


def overview(data, report, name, output):
    at, acc, gt, gyro = data
    figure, axes = plt.subplots(3, 1, figsize=(16, 8.7), sharex=True, height_ratios=[2.7, 2.7, 0.7])
    figure.subplots_adjust(left=0.08, right=0.965, top=0.81, bottom=0.19, hspace=0.18)
    fresh = name == "blind-02"
    heading(
        figure,
        "Blind 02 / A fresh mixed-session test"
        if fresh
        else "Blind 01 / Learning from the first test",
        "3 jumps  /  10 squats  /  11 push-ups  ·  All three totals confirmed afterward"
        if fresh
        else "5 push-ups  /  3 pull-ups  ·  Automatic recovery after development feedback",
        "FROZEN PREDICTIONS" if fresh else "DEVELOPMENT REPLAY",
    )
    axes[0].plot(at, np.linalg.norm(acc, axis=1), color="#24578b", lw=0.65)
    axes[1].plot(gt, np.linalg.norm(gyro, axis=1), color="#168c86", lw=0.65)
    axes[0].set_ylabel("Acceleration magnitude\n(mg)", color=INK, labelpad=12)
    axes[1].set_ylabel("Rotation speed\n(degrees/s)", color=INK, labelpad=12)
    for bout in report["sets"]:
        left, right = bout["start_time_s"], bout["end_time_s"]
        colour = PALETTE[bout["activity"]]
        for axis in axes[:2]:
            axis.axvspan(left, right, color=colour, alpha=0.12)
        axes[0].text(
            (left + right) / 2,
            1.04,
            f"{bout['rep_estimate']} {bout['activity']}s",
            transform=axes[0].get_xaxis_transform(),
            ha="center",
            color=colour,
            weight="bold",
            fontsize=11,
        )
        for cycle in bout["cycles"]:
            axes[1].axvline(cycle["start_time_s"], color=colour, lw=0.65, alpha=0.65)
    for interval in report["activities"]:
        axes[2].axvspan(
            interval["start_time_s"],
            interval["end_time_s"],
            facecolor=PALETTE[interval["activity"]],
            edgecolor="none",
        )
    for axis in axes:
        style_axis(axis)
    axes[2].set_yticks([])
    axes[2].set_ylabel("Activity\nestimate", color=INK, labelpad=14)
    axes[2].set_xlabel("Original session time (seconds)", color=INK, labelpad=8)
    axes[2].set_xlim(min(at[0], gt[0]), max(at[-1], gt[-1]))
    names = [name for name in PALETTE if name in {a["activity"] for a in report["activities"]}]
    figure.legend(
        handles=[Patch(facecolor=PALETTE[n], label=n.replace("_", " ")) for n in names],
        loc="lower center",
        bbox_to_anchor=(0.53, 0.062),
        ncol=6,
        frameon=False,
        fontsize=10,
    )
    figure.text(
        0.08,
        0.027,
        "Shaded signal regions = accepted sets. "
        "Lower band = class evidence, not confirmed activities."
        + (
            " One participant; boundaries unlabelled."
            if fresh
            else " Truth was known during development."
        ),
        fontsize=9,
        color="#526174",
    )
    figure.savefig(output, dpi=140, facecolor="#f6f8fb")
    plt.close(figure)


def cycles_figure(data, report, output):
    at, acc, gt, gyro = data
    figure, axes = plt.subplots(3, 1, figsize=(16, 9.3))
    figure.subplots_adjust(left=0.08, right=0.965, top=0.815, bottom=0.09, hspace=0.65)
    heading(
        figure,
        "Blind 02 / Inside the 24 repetitions",
        "Numbered intervals are predicted cycles; exercise totals were confirmed after prediction.",
        "SAVED CYCLE BOUNDARIES",
    )
    for axis, bout in zip(axes, report["sets"], strict=True):
        colour = PALETTE[bout["activity"]]
        left, right = bout["start_time_s"], bout["end_time_s"]
        if bout["activity"] == "jump":
            mask = (at >= left - 1) & (at <= right + 1)
            axis.plot(at[mask], np.linalg.norm(acc[mask], axis=1), color=colour, lw=1)
            axis.set_ylabel("ACC magnitude\n(mg)", color=INK)
            detail = "3 paired events / 6 detected impacts"
        else:
            block = bout["motion_blocks"][0]
            mask = (gt >= left - 0.6) & (gt <= right + 0.6)
            projection = (gyro[mask] - np.array(block["gyro_bias_dps"])) @ np.array(
                block["gyro_axis"]
            )
            axis.plot(gt[mask], projection, color=colour, lw=1)
            axis.axhline(0, color="#526174", lw=0.7, ls="--", alpha=0.6)
            axis.set_ylabel("Projected gyro\n(degrees/s)", color=INK)
            detail = f"{bout['complete_cycles']} outward-and-return cycles"
        for index, cycle in enumerate(bout["cycles"], 1):
            a, b = cycle["start_time_s"], cycle["end_time_s"]
            axis.axvspan(a, b, color=colour, alpha=0.09 if index % 2 else 0.20)
            axis.text(
                (a + b) / 2,
                0.97,
                str(index),
                transform=axis.get_xaxis_transform(),
                ha="center",
                va="top",
                color=colour,
                weight="bold",
                fontsize=10,
                bbox={"fc": "white", "ec": "none", "alpha": 0.8, "pad": 1.5},
            )
        style_axis(axis)
        axis.set_title(
            f"{bout['activity'].capitalize()}  /  {detail}", loc="left", color=INK, pad=12
        )
        axis.set_xlim(left - 0.6, right + 0.6)
        axis.margins(y=0.24)
        axis.set_xlabel("Original session time (seconds)", color=INK)
    figure.text(
        0.08,
        0.02,
        "Gyro projection uses each saved set's dominant axis and bias. "
        "Cycle boundaries are estimates, not video-validated labels.",
        fontsize=9,
        color="#526174",
    )
    figure.savefig(output, dpi=140, facecolor="#f6f8fb")
    plt.close(figure)


def build(data_root, output):
    output.mkdir(parents=True, exist_ok=True)
    sources = {
        "blind-01": data_root / "raw/blind-01/analysis-adaptive/analysis.json",
        "blind-02": data_root / "processed/blind-02-evaluation/predictions/analysis.json",
    }
    if sha(sources["blind-02"]) != BLIND_02_SHA:
        raise ValueError(
            "Frozen blind-02 prediction hash differs; do not relabel another run as blind"
        )
    provenance = {"renderer": "scripts/build_documentation_figures.py", "sessions": {}}
    for name, path in sources.items():
        report = json.loads(path.read_text(encoding="utf-8"))
        session = data_root / "raw" / name
        for stream in ("acc.csv", "gyro.csv"):
            if sha(session / stream) != report["source_sha256"][stream]:
                raise ValueError(f"{name} {stream} no longer matches saved inference")
        data = load_motion(session)
        overview(data, report, name, output / f"{name}-overview.png")
        if name == "blind-02":
            cycles_figure(data, report, output / "blind-02-cycles.png")
        provenance["sessions"][name] = {
            "evaluation": "fresh_blind"
            if name == "blind-02"
            else "retrospective_development_replay",
            "analysis_sha256": sha(path),
            "source_sha256": report["source_sha256"],
            "model_sha256": report["model_sha256"],
            "sets": [
                {k: b[k] for k in ("activity", "rep_estimate", "start_time_s", "end_time_s")}
                for b in report["sets"]
            ],
        }
    provenance["image_sha256"] = {p.name: sha(p) for p in sorted(output.glob("blind-*.png"))}
    (output / "provenance.json").write_text(
        json.dumps(provenance, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Saved 3 figures and provenance: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--output", type=Path, default=Path("docs/images"))
    args = parser.parse_args()
    build(args.data_root, args.output)
