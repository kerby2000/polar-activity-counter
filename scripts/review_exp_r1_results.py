"""Post-hoc EXP-R1 summaries and baseline ablation, without changing saved decisions."""

import argparse
import json
from collections import Counter
from pathlib import Path

import numpy as np

from polar_activity.counter import _smooth
from polar_activity.experiments.runner import digest, load_sensor, save
from polar_activity.experiments.signal import stable_excursions
from polar_activity.motion_quality import arm_excursions
from polar_activity.recognition import (
    activity_intervals,
    load_model,
    motion_blocks,
    predict_windows,
)


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def metric_row(item):
    score = item["score"]
    events = score["events"]
    errors = [e["end_to_end_absolute_error"] for e in events if e["count_range"] is not None]
    exact = [e["exact_count"] for e in events if e["exact_count"] is not None]
    conditional = [
        e["conditional_absolute_error"]
        for e in events
        if e["conditional_absolute_error"] is not None
    ]
    return {
        "group": item["group"],
        "method": item["run"],
        "representation": item["representation"],
        "references": len(events),
        "correctly_named": score["correctly_named_sets"],
        "missed": score["missed_sets"],
        "wrong_class": len(score["wrong_class_predictions"]),
        "end_to_end_errors": errors,
        "end_to_end_mae": float(np.mean(errors)) if errors else None,
        "exact_numerator": sum(exact),
        "exact_denominator": len(exact),
        "within_one_numerator": sum(e <= 1 for e in errors),
        "within_one_denominator": len(errors),
        "conditional_mae": float(np.mean(conditional)) if conditional else None,
        "conditional_denominator": len(conditional),
        "count_abstentions_on_matched_sets": sum(e["count_abstained"] for e in events),
        "background_seconds": score["background_seconds"],
        "background_false_sets": score["background_false_sets"],
        "background_false_reps": score["background_false_reps"],
        "background_unknown_counts": score["background_unknown_counts"],
        "duplicates_or_fragments": len(score["duplicate_or_fragment_predictions"]),
        "unmatched_not_confirmed_background": len(score["unmatched_in_unlabelled_time"]),
    }


def verify(run, manifest):
    repo = Path(__file__).resolve().parents[1]
    baseline = repo / "data/processed/exp-r1/baseline"
    checks = {}
    for name, expected in manifest["source_code"].items():
        checks["source:" + name] = digest(Path(name)) == expected
    for record in manifest["data"]["records"]:
        for name, expected in record["source_sha256"].items():
            checks[f"raw:{record['group']}:{name}"] = (
                digest(Path(record["path"]) / name) == expected
            )
        for version in manifest["config"]["representations"]:
            folder = run / record["group"] / version
            for name, expected in read(folder / "complete.json")["files"].items():
                checks[f"result:{record['group']}:{version}:{name}"] = (
                    digest(folder / name) == expected
                )
            prediction = read(folder / "predictions.json")
            checks[f"coverage:{record['group']}:{version}"] = all(
                b["grid_complete"] for b in prediction["coverage"]
            )
            model = read(folder / "model.json")
            checks[f"isolation:{record['group']}:{version}"] = (
                record["group"] not in model["calibration_groups"]
                and "r09" not in model["calibration_groups"]
                and all(
                    t["group"] != record["group"] and t["group"] != "r09"
                    for t in model["templates"]
                )
            )
    checks["annotation_hash"] = (
        digest(Path(manifest["config"]["annotations"])) == manifest["annotations_sha256"]
    )
    checks["frozen_model_hash"] = (
        digest(Path(manifest["config"]["frozen_model"])) == manifest["model_sha256"]
    )
    snapshot = read(baseline / "snapshot.json")
    for name in ("analyser.py", "counter.py", "counting.py", "motion_quality.py", "recognition.py"):
        relative = "src/polar_activity/" + name
        checks["original_algorithm:" + name] = (
            digest(repo / relative) == snapshot["sha256"][relative]
        )
    original = read(baseline / "blind-01/analysis.json")
    replay = read(run / "r09/F0/analysis.json")
    for key in ("windows", "sets", "activities"):
        checks["baseline_replay:" + key] = original[key] == replay[key]
    for p in (baseline / "blind-01").glob("*.csv"):
        checks["baseline_replay:" + p.name] = (
            p.read_bytes() == (run / "r09/F0" / p.name).read_bytes()
        )
    save(run / "integrity-review.json", {"all_passed": all(checks.values()), "checks": checks})
    if not all(checks.values()):
        raise ValueError(f"Integrity review failed: {[k for k, v in checks.items() if not v]}")
    return len(checks)


def baseline_ablation(run, manifest):
    """Same pre-gating classifier intervals; no supplied target labels or times."""
    model = load_model(Path(manifest["config"]["frozen_model"]))
    results = []
    for record in manifest["data"]["records"]:
        blocks = motion_blocks(load_sensor(Path(record["path"])))
        intervals = activity_intervals(predict_windows(blocks, model))
        for interval in intervals:
            start, end = interval["start_time_s"], interval["end_time_s"]
            if interval["activity"] != "pull-up" or end - start < 3:
                continue
            t, acc, gyro = blocks[interval["block_id"]]
            before = (t >= start - 2) & (t < start)
            old = arm_excursions(t, acc, start, end, gyro)
            new = stable_excursions(t, acc, gyro, start, end)
            rate = 1 / float(np.median(np.diff(t)))
            width = max(3, round(0.44 * rate) // 2 * 2 + 1)
            smooth = _smooth(acc, width)
            direction = smooth / np.maximum(np.linalg.norm(smooth, axis=1)[:, None], 1)
            baseline = np.median(direction[before], axis=0) if before.any() else np.zeros(3)
            baseline /= max(np.linalg.norm(baseline), 1e-9)
            region = (t >= start) & (t <= end + 2)
            row = {
                "group": record["group"],
                "interval": interval,
                "old_count": len(old),
                "new_count": len(new["events"]),
                "new_returns": sum(e["return_observed"] for e in new["events"]),
                "old_precontext_gyro_mean_dps": float(np.linalg.norm(gyro[before], axis=1).mean())
                if before.any()
                else None,
                "old_baseline_direction": baseline.tolist(),
                "old_events": old,
                "stable": new,
                "old_signal": {
                    "time_s": t[region].tolist(),
                    "departure_deg": np.degrees(
                        np.arccos(np.clip(direction[region] @ baseline, -1, 1))
                    ).tolist(),
                },
            }
            results.append(row)
    save(
        run / "baseline-ablation.json",
        {
            "policy": (
                "Identical original classifier intervals before count gating. Original model "
                "includes training sessions; this is a component diagnostic, not cross-validation."
            ),
            "rows": results,
        },
    )
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    for i, row in enumerate(results):
        if row["group"] != "r09":
            continue
        fig, ax = plt.subplots(figsize=(11, 3.5))
        for label, signal in [
            ("Original pre-context baseline", row["old_signal"]),
            ("Quiet baseline in candidate", row["stable"].get("signal", {})),
        ]:
            if signal:
                ax.plot(signal["time_s"], signal["departure_deg"], label=label)
        for event in row["stable"]["events"]:
            ax.axvspan(event["start_time_s"], event["end_time_s"], color="green", alpha=0.12)
        ax.set(
            xlabel="Original session time (s)",
            ylabel="Departure from baseline (degrees)",
            title=(
                f"Same automatic classifier interval: original {row['old_count']}, "
                f"stable {row['new_count']} excursions"
            ),
        )
        ax.legend(loc="best")
        fig.tight_layout()
        fig.savefig(run / "r09" / f"baseline-ablation-{i}.png", dpi=130)
        plt.close(fig)
    return [
        {k: r[k] for k in ("group", "interval", "old_count", "new_count", "new_returns")}
        for r in results
    ]


def feature_summary(run, manifest):
    annotations = read(Path(manifest["config"]["annotations"]))
    rows = []
    for record in manifest["data"]["records"]:
        evidence = read(run / record["group"] / "feature_ablations.json")
        for ref in annotations["reference_sets"]:
            if ref["group"] != record["group"]:
                continue
            for version, windows in evidence.items():
                selected = [
                    w
                    for w in windows
                    if ref["start_time_s"] <= w["start_time_s"]
                    and w["end_time_s"] <= ref["end_time_s"]
                ]
                labels = Counter(w["activity"] for w in selected)
                rows.append(
                    {
                        "group": record["group"],
                        "reference_id": ref["id"],
                        "reference_activity": ref["activity"],
                        "version": version,
                        "windows": len(selected),
                        "label_counts": dict(labels),
                        "correct_windows": labels[ref["activity"]],
                    }
                )
    save(
        run / "feature-summary.json",
        {
            "policy": (
                "Whole four-second windows inside supplied intervals. Correlated evidence, "
                "not independent accuracy trials. Frozen features are refitted per recording "
                "fold, not the F0 model."
            ),
            "rows": rows,
        },
    )
    return rows


def recofit_summary(folder):
    report = read(folder / "report.json")
    rows = [
        r
        for r in report["comparisons"]
        if r["reference_count"] is not None and r["reference_count"] > 0
    ]
    known = [r for r in rows if r["predicted_count"] is not None]
    errors = [abs(r["predicted_count"] - r["reference_count"]) for r in known]
    result = {
        "status": report["status"],
        "sessions": report["session_count"],
        "known_count_sets": len(rows),
        "counted_sets": len(known),
        "abstentions": len(rows) - len(known),
        "exact": sum(e == 0 for e in errors),
        "within_one": sum(e <= 1 for e in errors),
        "conditional_mae": float(np.mean(errors)) if errors else None,
        "rows": rows,
        "policy": (
            "Supplied intervals, broad 0.5–6s bounds, no class-specific calibration. "
            "A diagnostic of this adaptation, not a reproduction of published MM-Fit accuracy."
        ),
    }
    # Saved periods are inspected after scoring; reference counts never enter inference.
    traces = [(p, read(p)) for p in folder.glob("s*-v*-set*.json")]
    periods = []
    for row in report["comparisons"]:
        key = (row["subject"], row["visit"])
        if not row["reference_count"]:
            continue
        matches = [
            (p, trace)
            for p, trace in traces
            if p.name.startswith(f"s{key[0]}-v{key[1]}-")
            and "signal" in trace
            and 0 <= trace["signal"]["time_s"][0] - row["start_time_s"] < 0.041
            and 0 <= row["end_time_s"] - trace["signal"]["time_s"][-1] < 0.041
        ]
        if len(matches) != 1:
            raise ValueError(f"Cannot uniquely identify saved counter interval: {row}")
        path, trace = matches[0]
        if trace["count"] != row["predicted_count"]:
            raise ValueError(f"Counter trace and result differ: {path}")
        selected = [e["period_s"] for e in trace["events"] if e.get("period_s")]
        # Only a rough envelope/count period: includes pauses and imperfect label boundaries.
        envelope_period = (row["end_time_s"] - row["start_time_s"]) / row["reference_count"]
        periods.append(
            {
                "subject": key[0],
                "activity": row["activity_original"],
                "trace": path.name,
                "trace_sha256": digest(path),
                "median_selected_period_s": float(np.median(selected)) if selected else None,
                "annotation_envelope_per_rep_s": envelope_period,
                "ratio": float(np.median(selected)) / envelope_period if selected else None,
            }
        )
    result["posthoc_period_check"] = periods
    save(folder / "summary.json", result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    parser.add_argument("--recofit", type=Path)
    args = parser.parse_args()
    manifest = read(args.run / "manifest.json")
    count = verify(args.run, manifest)
    summary = {
        "script_sha256": digest(Path(__file__)),
        "run_id": manifest["run_id"],
        "integrity_checks_passed": count,
        "metrics": [metric_row(r) for r in read(args.run / "scores.json")],
        "feature_rows": feature_summary(args.run, manifest),
        "baseline_rows": baseline_ablation(args.run, manifest),
    }
    if args.recofit:
        summary["recofit"] = recofit_summary(args.recofit)
    save(args.run / "review-summary.json", summary)
    print(f"{count} integrity checks passed. Saved {args.run / 'review-summary.json'}")


if __name__ == "__main__":
    main()
