"""Evaluate immutable adaptive predictions, then score disclosed reference annotations."""

import argparse
import hashlib
import json
import time
from pathlib import Path

from polar_activity.adaptive import analyse_session, fit_model
from polar_activity.experiments.runner import digest, inventory, save
from polar_activity.experiments.scoring import score_sets


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("experiments/exp_r2.json"))
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    for key in ("dataset_manifest", "annotations", "prior_run", "output_root"):
        config[key] = str((args.config.parent / config[key]).resolve())
    inv = inventory(config)
    repo = Path(__file__).resolve().parents[1]
    source = {str(p.relative_to(repo)): digest(p) for p in (repo / "src").rglob("*.py")}
    source[str(Path(__file__).relative_to(repo))] = digest(Path(__file__))
    manifest = {
        "config": config,
        "source_code": source,
        "inventory": inv,
        "annotations_sha256": digest(Path(config["annotations"])),
    }
    run_id = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()[:16]
    output = Path(config["output_root"]) / run_id
    save(output / "manifest.json", manifest)
    annotations = json.loads(Path(config["annotations"]).read_text())
    records = {r["group"]: r for r in inv["records"]}
    comparisons = []
    for mode in ("leave_one_recording_out", "reference_replay"):
        for group, record in records.items():
            folder = output / mode / group
            complete = folder / "complete.json"
            if complete.exists():
                for name, expected in json.loads(complete.read_text())["files"].items():
                    if digest(folder / name) != expected:
                        raise ValueError("Saved evaluation changed; refusing to overwrite")
                result = json.loads((folder / "analysis.json").read_text())
            else:
                train = {
                    g
                    for g, r in records.items()
                    if r["allow_training"] and (mode == "reference_replay" or g != group)
                }
                refs = [
                    {
                        "session": records[r["group"]]["path"],
                        "activity": r["activity"],
                        "start_time_s": r["start"],
                        "end_time_s": r["end"],
                        "boundary_source": r["provenance"],
                    }
                    for r in annotations["training_intervals"]
                    if r["group"] in train
                ]
                spec = {
                    "subject": record["subject"],
                    "sensor_position": record["placement"],
                    "jump_count_convention": "paired_impacts",
                    "references": refs,
                }
                save(folder / "references.json", spec)
                fit_model(folder / "references.json", folder / "model.json")
                begin = time.perf_counter()
                result = analyse_session(
                    Path(record["path"]), folder / "model.json", folder, plot=True
                )
                save(
                    folder / "complete.json",
                    {
                        "elapsed_s": time.perf_counter() - begin,
                        "files": {p.name: digest(p) for p in folder.glob("*.json")},
                        "training_groups": sorted(train),
                    },
                )
            # The automatic result is already saved and hashed before scoring receives counts.
            references = [r for r in annotations["reference_sets"] if r["group"] == group]
            background = [r for r in annotations["background"] if r["group"] == group]
            score = score_sets(result["sets"], references, background, "rep_estimate")
            comparisons.append(
                {
                    "group": group,
                    "recording": Path(record["path"]).name,
                    "mode": mode,
                    "score": score,
                }
            )
            print(
                mode,
                Path(record["path"]).name,
                [
                    (
                        s["activity"],
                        s["rep_estimate"],
                        s.get("complete_cycles"),
                        round(s["start_time_s"], 2),
                        round(s["end_time_s"], 2),
                    )
                    for s in result["sets"]
                ],
                "background FP",
                score["background_false_sets"],
                score["background_false_reps"],
                flush=True,
            )
    save(output / "scores.json", comparisons)
    lines = [
        "# Adaptive analyser evaluation",
        "",
        f"Run `{run_id}`; retrospective development only.",
        "",
        "| Mode | Recording | Correct / reference sets | Missed / wrong | Count errors | "
        "Background false sets / reps / seconds |",
        "|---|---|---:|---:|---|---:|",
    ]
    for row in comparisons:
        s = row["score"]
        lines.append(
            f"| {row['mode']} | {row['recording']} | "
            f"{s['correctly_named_sets']}/{s['reference_sets']} | "
            f"{s['missed_sets']}/{len(s['wrong_class_predictions'])} | "
            f"{[e['end_to_end_absolute_error'] for e in s['events']]} | "
            f"{s['background_false_sets']} / {s['background_false_reps']} / "
            f"{s['background_seconds']:.2f} |"
        )
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print("Run saved:", output)


if __name__ == "__main__":
    main()
