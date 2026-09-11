"""Human-readable decision packet and offline plots from already-saved outputs."""

import json
from pathlib import Path

import numpy as np


def report(output):
    from .runner import load_sensor, save

    output = Path(output)
    manifest = json.loads((output / "manifest.json").read_text())
    scores = json.loads((output / "scores.json").read_text())
    records = {r["group"]: r for r in manifest["data"]["records"]}
    annotations = json.loads(Path(manifest["config"]["annotations"]).read_text())
    rows = [
        "# EXP-R1 decision packet",
        "",
        f"Run: `{output.name}`.",
        "",
        "Whole-recording retrospective development evaluation. No untouched holdout remains.",
        "F0 uses all original references; experimental folds exclude their target recording.",
        "DTW_cycles is diagnostic. E1 and E2 share the same sets; counts are not combined.",
        "Unknown counts remain abstentions. Count-range errors use distance to the reported range.",
        "Coarse waveform annotations do not establish precise set boundaries.",
        "",
        "| Recording | Method | Representation | Named / reference | Missed / wrong | "
        "Count errors | False sets / background s |",
        "|---|---|---|---:|---:|---|---:|",
    ]
    for r in scores:
        s = r["score"]
        errors = [e["end_to_end_absolute_error"] for e in s["events"]]
        name = Path(records[r["group"]]["path"]).name
        rows.append(
            f"| {name} | {r['run']} | {r['representation']} | "
            f"{s['correctly_named_sets']}/{s['reference_sets']} | "
            f"{s['missed_sets']}/{len(s['wrong_class_predictions'])} | {errors} | "
            f"{s['background_false_sets']}/{s['background_seconds']:.1f} |"
        )
    rows += [
        "",
        "## Assisted component comparisons",
        "",
        "Counts below received a supplied interval and class. D2 received interval only.",
        "",
        "| Recording | Reference | Representation | Existing count | MM-Fit count | "
        "Old-feature name | DTW name |",
        "|---|---|---|---:|---:|---|---|",
    ]
    assisted_scores, rotations, coverage, support = [], [], [], []
    for group, entry in records.items():
        name = Path(entry["path"]).name
        for version in manifest["config"]["representations"]:
            folder = output / group / version
            if not (folder / "complete.json").exists():
                continue
            assisted = json.loads((folder / "assisted.json").read_text())
            model = json.loads((folder / "model.json").read_text())
            prediction = json.loads((folder / "predictions.json").read_text())
            references = [r for r in annotations["reference_sets"] if r["group"] == group]
            support.append(
                {
                    "group": group,
                    "representation": version,
                    "supported_exercises": sorted(model["thresholds"]),
                    "unsupported_reference_classes": sorted(
                        {r["activity"] for r in references} - set(model["thresholds"])
                    ),
                    "training_groups": model["training_groups"],
                }
            )
            for a in assisted:
                rows.append(
                    f"| {name} | {a['reference_id']} | {version} | "
                    f"{a['D1_existing']['count']} | {a['D1_mmfit']['count']} | "
                    f"{a['D2_legacy_window_vote']} | {a['D2_dtw_activity']} |"
                )
                ref = next(r for r in references if r["id"] == a["reference_id"])
                assisted_scores.append(
                    {
                        "group": group,
                        "representation": version,
                        "reference_id": ref["id"],
                        "expected_range": ref["count_range"],
                        "D1_existing_count": a["D1_existing"]["count"],
                        "D1_mmfit_count": a["D1_mmfit"]["count"],
                        "D2_legacy_correct": a["D2_legacy_window_vote"] == ref["activity"],
                        "D2_dtw_correct": a["D2_dtw_activity"] == ref["activity"],
                        "D2_supported": a["D2_support"],
                    }
                )
            coverage.append(
                {
                    "group": group,
                    "representation": version,
                    "candidate_count": len(prediction["candidates"]),
                    "coverage": prediction["coverage"],
                }
            )
            if (folder / "rotated_predictions.json").exists():
                rotated = json.loads((folder / "rotated_predictions.json").read_text())
                x, y = prediction["candidates"], rotated["candidates"]
                rotation = {
                    "group": group,
                    "representation": version,
                    "candidates": len(x),
                    "class_changes": sum(
                        a["activity"] != b["activity"] for a, b in zip(x, y, strict=True)
                    ),
                    "retained_changes": sum(
                        bool(a["rejection_reasons"]) != bool(b["rejection_reasons"])
                        for a, b in zip(x, y, strict=True)
                    ),
                    "original_sets": len(prediction["sets"]),
                    "rotated_sets": len(rotated["sets"]),
                }
                rotations.append(rotation)
    save(output / "assisted_scores.json", assisted_scores)
    save(output / "rotation_summary.json", rotations)
    save(output / "scan_coverage.json", coverage)
    save(output / "fold_support.json", support)
    rows += [
        "",
        "## Rotation sensitivity",
        "",
        "```json",
        json.dumps(rotations, indent=2),
        "```",
        "",
        "## Support and limitations",
        "",
        "Jump templates are unavailable: paired pulses do not establish complete motion cycles. "
        "The squat holdout has no squat training recording. Unsupported folds remain visible; "
        "they are not estimates of closed-set generalisation.",
        "",
        "PCA flags unstable frames and uses a gravity-relative invariant fallback. "
        "This loses some directional information; rotation stability is not recognition accuracy.",
        "",
        "Templates and thresholds use training recordings; no independent inner holdout exists. "
        "The cap and speed grid were fixed before scoring; no count-based selection occurred.",
        "",
        "Whole-recording grouping does not establish independent wearing sessions. "
        "Activity filenames, target labels, expected totals and notes are not passed to inference.",
        "",
        "Inspect rejection reasons, assisted baseline/boundary traces, and fold_support.json. "
        "Unmatched predictions in unlabelled time are unresolved, not confirmed false positives.",
        "",
    ]
    complete = len(coverage) == len(records) * len(manifest["config"]["representations"])
    rows.insert(2, f"Status: {'EXPERIMENT_EXECUTED' if complete else 'PARTIAL_EXECUTION'}.")
    (output / "report.md").write_text("\n".join(rows), encoding="utf-8")
    _plots(output, manifest, annotations, load_sensor)
    return output / "report.md"


def _plots(output, manifest, annotations, load_sensor):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    for entry in manifest["data"]["records"]:
        group = entry["group"]
        folders = [
            output / group / v
            for v in manifest["config"]["representations"]
            if (output / group / v / "complete.json").exists()
        ]
        if not folders:
            continue
        at, acc, gt, gyro = load_sensor(Path(entry["path"]))
        figure, axes = plt.subplots(2 + len(folders), 1, figsize=(13, 8), sharex=True)
        axes[0].plot(at, np.linalg.norm(acc, axis=1), linewidth=0.6)
        axes[0].set_ylabel("ACC mg")
        axes[1].plot(gt, np.linalg.norm(gyro, axis=1), linewidth=0.6)
        axes[1].set_ylabel("Gyro deg/s")
        for ref in [r for r in annotations["reference_sets"] if r["group"] == group]:
            for ax in axes:
                ax.axvspan(ref["start_time_s"], ref["end_time_s"], color="gray", alpha=0.12)
            axes[0].text(
                ref["start_time_s"],
                1.01,
                ref["activity"] + " (approx. interval)",
                transform=axes[0].get_xaxis_transform(),
                fontsize=8,
            )
        for ax, folder in zip(axes[2:], folders, strict=True):
            predictions = json.loads((folder / "predictions.json").read_text())
            for c in predictions["candidates"]:
                if not c["rejection_reasons"]:
                    ax.plot([c["start_time_s"], c["end_time_s"]], [0.5, 0.5], linewidth=4)
            for s in predictions["sets"]:
                ax.text(
                    s["start_time_s"], 0.65, f"{s['activity']} {s['dtw_cycle_count']}", fontsize=8
                )
            ax.set(ylim=(0, 1.3), yticks=[], ylabel=folder.name.replace("_v1", ""))
        axes[-1].set_xlabel("Original session time (s)")
        figure.suptitle(
            Path(entry["path"]).name + " — held-out recording / retrospective experiment"
        )
        figure.tight_layout()
        figure.savefig(output / group / "overview.png", dpi=130)
        plt.close(figure)
        # Detailed independently computed counter trace for each supplied interval.
        for folder in folders:
            assisted = json.loads((folder / "assisted.json").read_text())
            for item in assisted:
                trace = item["D1_mmfit"]
                if "signal" not in trace:
                    continue
                fig, ax = plt.subplots(figsize=(11, 3))
                x = trace["signal"]
                ax.plot(x["time_s"], x["projected_gyro"], lw=0.9)
                for peak in trace["candidate_peaks"]:
                    color = "#b74343" if peak["rejection_reasons"] else "#168052"
                    ax.scatter(peak["time_s"], peak["amplitude"], c=color, s=25)
                ax.set(
                    title=f"{item['reference_id']} | MM-Fit count={trace['count']}; "
                    "green retained, red rejected",
                    xlabel="Session time (s)",
                    ylabel="Standardised gyro PCA",
                )
                fig.tight_layout()
                fig.savefig(folder / f"{item['reference_id']}-counter.png", dpi=120)
                plt.close(fig)
