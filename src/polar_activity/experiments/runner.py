"""Reproducible folds, array-only inference, then separate on-disk scoring."""

import hashlib
import importlib.metadata
import json
import platform
import subprocess
import time
from pathlib import Path

import numpy as np

from ..analyser import analyse_session
from ..counting import load_motion
from ..diagnostics import read_rows
from ..recognition import motion_blocks, source_hashes
from .counters import existing_counter, mmfit_counter
from .matcher import MatchConfig, scan
from .scoring import failure_stages, rejection_funnel, score_sets
from .signal import feature_vector, stable_excursions
from .training import fit_templates, training_cycles


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    def default(x):
        if isinstance(x, np.ndarray):
            return x.tolist()
        if isinstance(x, np.generic):
            return x.item()
        raise TypeError(type(x).__name__)

    text = json.dumps(value, indent=2, allow_nan=False, default=default) + "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def load_config(path):
    path = Path(path).resolve()
    config = json.loads(path.read_text(encoding="utf-8"))
    for key in ("dataset_manifest", "annotations", "frozen_model", "output_root"):
        config[key] = str((path.parent / config[key]).resolve())
    MatchConfig(**config["match"])
    return config


def load_sensor(path):
    """Reuse the original reader and verify a common integer device-time anchor.

    The inference caller receives only arrays, not paths, labels or metadata.
    Original session-relative time is retained so existing annotations keep meaning.
    """
    data = load_motion(path)
    acc_rows, gyro_rows = (read_rows(path / f"{s}.csv") for s in ("acc", "gyro"))
    anchor = int(acc_rows[0]["device_timestamp_ns"])
    result = []
    for rows, supplied_t, values in ((acc_rows, data[0], data[1]), (gyro_rows, data[2], data[3])):
        times = np.array(
            [(int(r["device_timestamp_ns"]) - anchor) / 1e9 + data[0][0] for r in rows]
        )
        if not np.allclose(times, supplied_t, rtol=0, atol=2e-6):
            raise ValueError("ACC/GYRO clocks disagree with the common device-time anchor")
        # Validated original time avoids introducing microsecond rounding changes
        # when reproducing existing grid alignment.
        result.extend((supplied_t, values))
    return tuple(result)


def inventory(config):
    manifest = json.loads(Path(config["dataset_manifest"]).read_text())
    records = []
    for entry in manifest["records"]:
        path = Path(entry["path"])
        data = load_sensor(path)
        blocks = motion_blocks(data)
        records.append(
            {
                **entry,
                "source_sha256": source_hashes(path),
                "samples": {"acc": len(data[0]), "gyro": len(data[2])},
                "rate_hz": {
                    "acc": 1 / float(np.median(np.diff(data[0]))),
                    "gyro": 1 / float(np.median(np.diff(data[2]))),
                },
                "blocks": [
                    {"start_time_s": float(t[0]), "end_time_s": float(t[-1]), "samples": len(t)}
                    for t, _, _ in blocks
                ],
                "overlap_s": sum(float(t[-1] - t[0]) for t, _, _ in blocks),
            }
        )
    return {
        "records": records,
        "grouping": manifest["grouping"],
        "total_recordings": len(records),
        "total_overlap_s": sum(r["overlap_s"] for r in records),
    }


def feature_bank(recordings, refs, version):
    bank = []
    for ref in refs:
        for t, acc, gyro in recordings[ref["group"]]:
            if not t[0] <= ref["start"] < ref["end"] <= t[-1]:
                continue
            for start in np.arange(ref["start"], ref["end"] - 4 + 1e-6, 1):
                m = (t >= start) & (t < start + 4)
                bank.append(
                    {
                        "activity": ref["activity"],
                        "group": ref["group"],
                        "start_time_s": float(start),
                        "features": feature_vector(acc[m], gyro[m], version),
                    }
                )
    return bank


def feature_evidence(blocks, bank, version):
    rows = []
    for block_id, (t, acc, gyro) in enumerate(blocks):
        for start in np.arange(t[0], t[-1] - 4 + 1e-6, 1):
            m = (t >= start) & (t < start + 4)
            vector = feature_vector(acc[m], gyro[m], version)
            scores, neighbours = {}, {}
            for activity in sorted({b["activity"] for b in bank}):
                options = sorted(
                    [
                        (float(np.sqrt(np.mean((vector - b["features"]) ** 2))), b)
                        for b in bank
                        if b["activity"] == activity
                    ],
                    key=lambda v: (v[0], v[1]["group"], v[1]["start_time_s"]),
                )[:3]
                scores[activity] = float(np.mean([d for d, _ in options]))
                neighbours[activity] = [
                    {"distance": d, "group": b["group"], "start_time_s": b["start_time_s"]}
                    for d, b in options
                ]
            best = min(scores, key=lambda k: (scores[k], k)) if scores else "unknown"
            quiet = np.linalg.norm(gyro[m].std(0)) < 8 and np.linalg.norm(acc[m], axis=1).std() < 30
            label = (
                "stationary" if quiet else best if scores and scores[best] <= 0.85 else "unknown"
            )
            rows.append(
                {
                    "start_time_s": float(start),
                    "end_time_s": float(start + 4),
                    "block_id": block_id,
                    "activity": label,
                    "feature_version": version,
                    "features": vector.tolist(),
                    "class_scores": scores,
                    "nearest_sources": neighbours,
                }
            )
    return rows


def attach_counters(result, blocks, model):
    rows = []
    for s in result["sets"]:
        t, acc, gyro = blocks[s["block_id"]]
        durations = [c["duration_s"] for c in model["templates"] if c["activity"] == s["activity"]]
        bounds = (min(durations) * 0.6, max(durations) * 1.6)
        legacy = existing_counter(t, acc, gyro, s["start_time_s"], s["end_time_s"], s["activity"])
        mmfit = mmfit_counter(t, acc, gyro, s["start_time_s"], s["end_time_s"], bounds)
        rows.append(
            {
                **{k: v for k, v in s.items() if k != "cycles"},
                "cycle_candidate_ids": [c["candidate_id"] for c in s["cycles"]],
                "E1": legacy,
                "E2": mmfit,
            }
        )
    return rows


def assisted(blocks, refs, model, bank):
    """Explicit interval/class assisted diagnostics; caller strips count fields."""
    output = []
    for ref in refs:
        start, end = ref["start_time_s"], ref["end_time_s"]
        for t, acc, gyro in blocks:
            if not t[0] <= start < end <= t[-1]:
                continue
            m = (t >= start) & (t <= end)
            crop = [(t[m], acc[m], gyro[m])]
            detected = scan(crop, model)
            classes = {}
            for s in detected["sets"]:
                classes[s["activity"]] = classes.get(s["activity"], 0) + s["dtw_cycle_count"]
            predicted = max(classes, key=lambda k: (classes[k], k)) if classes else "unknown"
            evidence = feature_evidence(crop, bank, "frozen")
            legacy_votes = {}
            for row in evidence:
                legacy_votes[row["activity"]] = legacy_votes.get(row["activity"], 0) + 1
            legacy_activity = (
                max(legacy_votes, key=lambda k: (legacy_votes[k], k)) if legacy_votes else "unknown"
            )
            durations = [
                c["duration_s"] for c in model["templates"] if c["activity"] == ref["activity"]
            ]
            bounds = (min(durations) * 0.6, max(durations) * 1.6) if durations else (0.5, 6)
            row = {
                "reference_id": ref["id"],
                "start_time_s": start,
                "end_time_s": end,
                "mode": "assisted_diagnostic",
                "D1_supplied_activity": ref["activity"],
                "D1_existing": existing_counter(t, acc, gyro, start, end, ref["activity"]),
                "D1_mmfit": mmfit_counter(t, acc, gyro, start, end, bounds),
                "D2_legacy_window_vote": legacy_activity,
                "D2_dtw_activity": predicted,
                "D2_class_cycle_votes": classes,
                "D2_candidates": detected["candidates"],
                "D2_support": ref["activity"] in model["thresholds"],
            }
            if ref["activity"] == "pull-up":
                row["stable_baseline"] = stable_excursions(t, acc, gyro, start, end)
                row["boundary_stress"] = [
                    {
                        "shift_s": offset,
                        "legacy": existing_counter(
                            t, acc, gyro, start + offset, end, ref["activity"]
                        ),
                        "stable": stable_excursions(t, acc, gyro, start + offset, end),
                    }
                    for offset in (-1, 1)
                ]
            output.append(row)
    return output


def rotation_matrix(axis, degrees):
    v = np.asarray(axis, float)
    v /= np.linalg.norm(v)
    x, y, z = v
    cross = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])
    angle = np.radians(degrees)
    return np.eye(3) + np.sin(angle) * cross + (1 - np.cos(angle)) * cross @ cross


def run(config_path, only_groups=None):
    config = load_config(config_path)
    inv = inventory(config)
    sources = {p.as_posix(): digest(p) for p in Path(__file__).parents[1].rglob("*.py")}
    identity = {
        "config": config,
        "source_code": sources,
        "data": inv,
        "annotations_sha256": digest(config["annotations"]),
        "model_sha256": digest(config["frozen_model"]),
    }
    run_id = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:16]
    output = Path(config["output_root"]) / run_id
    output.mkdir(parents=True, exist_ok=True)
    packages = ("numpy", "scipy", "tslearn", "numba", "scikit-learn", "matplotlib")
    environment = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": {p: importlib.metadata.version(p) for p in packages},
        "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "git_branch": subprocess.check_output(
            ["git", "branch", "--show-current"], text=True
        ).strip(),
    }
    save(output / "manifest.json", {**identity, "environment": environment, "run_id": run_id})
    save(output / "inventory.json", inv)
    annotations = json.loads(Path(config["annotations"]).read_text())
    recordings = {r["group"]: motion_blocks(load_sensor(Path(r["path"]))) for r in inv["records"]}
    for target in inv["records"]:
        group = target["group"]
        if only_groups and group not in only_groups:
            continue
        target_dir = output / group
        target_dir.mkdir(exist_ok=True)
        print(f"{group}: {Path(target['path']).name}", flush=True)
        frozen = target_dir / "F0" / "analysis.json"
        if not frozen.exists():
            analyse_session(
                Path(target["path"]), Path(config["frozen_model"]), frozen.parent, plot=False
            )
        train_groups = {
            r["group"] for r in inv["records"] if r["allow_training"] and r["group"] != group
        }
        train = {g: recordings[g] for g in sorted(train_groups)}
        train_refs = [
            {k: ref[k] for k in ("group", "activity", "start", "end")}
            for ref in annotations["training_intervals"]
            if ref["group"] in train_groups
        ]
        cycles, traces = training_cycles(train, train_refs)
        save(target_dir / "template_proposals.json", traces)
        negatives = []
        for ref in train_refs:
            if ref["activity"] not in {"push-up", "pull-up", "squat", "jump"}:
                for t, acc, gyro in train[ref["group"]]:
                    m = (t >= ref["start"]) & (t <= ref["end"])
                    if m.sum() >= 3:
                        negatives.append((t[m], acc[m], gyro[m]))
        banks = {
            v: feature_bank(train, train_refs, v)
            for v in ("frozen", "no_mean_xyz", "invariant6", "gravity_relative_v1")
        }
        save(
            target_dir / "feature_ablations.json",
            {v: feature_evidence(recordings[group], b, v) for v, b in banks.items()},
        )
        for version in config["representations"]:
            folder = target_dir / version
            done = folder / "complete.json"
            if done.exists():
                hashes = json.loads(done.read_text())["files"]
                if all(
                    (folder / p).exists() and digest(folder / p) == h for p, h in hashes.items()
                ):
                    print(f"  {version}: resume verified", flush=True)
                    continue
                raise ValueError("Completed fold artifact changed; choose a fresh run")
            begin = time.perf_counter()
            model = fit_templates(cycles, negatives, version, MatchConfig(**config["match"]))
            model["source_hashes_by_group"] = {
                r["group"]: r["source_sha256"] for r in inv["records"] if r["group"] in train_groups
            }
            model["calibration_groups"] = sorted(train_groups)
            model["training_intervals"] = train_refs
            save(folder / "model.json", model)
            print(f"  {version}: {len(model['templates'])} templates, scanning", flush=True)
            prediction = scan(recordings[group], model)
            save(folder / "predictions.json", prediction)
            save(folder / "counts.json", attach_counters(prediction, recordings[group], model))
            # Freeze automatic results before passing any target annotation to diagnostics/scoring.
            save(
                folder / "prediction_hashes.json",
                {p: digest(folder / p) for p in ("predictions.json", "counts.json")},
            )
            references = [
                {k: r[k] for k in ("id", "activity", "start_time_s", "end_time_s")}
                for r in annotations["reference_sets"]
                if r["group"] == group
            ]
            save(
                folder / "assisted.json",
                assisted(recordings[group], references, model, banks["frozen"]),
            )
            # Paired rotation on both pull-up transfer recordings and the exposed regression.
            if group in {"r06", "r07", "r09"}:
                matrix = rotation_matrix(config["rotation_axis"], config["rotation_degrees"])
                rotated = [(t, a @ matrix, g @ matrix) for t, a, g in recordings[group]]
                rotated_prediction = scan(rotated, model)
                save(folder / "rotated_predictions.json", rotated_prediction)
            save(
                folder / "complete.json",
                {
                    "elapsed_s": time.perf_counter() - begin,
                    "files": {
                        p.name: digest(p)
                        for p in folder.glob("*.json")
                        if p.name != "complete.json"
                    },
                },
            )
            print(
                f"  {version}: {len(prediction['sets'])} proposed sets, "
                f"{time.perf_counter() - begin:.1f}s",
                flush=True,
            )
    score_run(output)
    from .reporting import report

    report(output)
    print(f"Run saved: {output}", flush=True)
    return output


def score_run(output):
    """Reads saved, hashed predictions; this step alone consumes expected totals."""
    output = Path(output)
    manifest = json.loads((output / "manifest.json").read_text())
    annotations = json.loads(Path(manifest["config"]["annotations"]).read_text())
    reports = []
    for target in manifest["data"]["records"]:
        group = target["group"]
        refs = [r for r in annotations["reference_sets"] if r["group"] == group]
        bkg = [dict(b) for b in annotations["background"] if b["group"] == group]
        for b in bkg:
            b["start_time_s"] = max(b["start_time_s"], target["blocks"][0]["start_time_s"])
            b["end_time_s"] = min(b["end_time_s"], target["blocks"][-1]["end_time_s"])
        f0_path = output / group / "F0" / "analysis.json"
        if not f0_path.exists():
            continue
        f0 = json.loads(f0_path.read_text())
        reports.append(
            {
                "group": group,
                "run": "F0",
                "representation": "frozen",
                "score": score_sets(f0["sets"], refs, bkg, "rep_estimate"),
            }
        )
        for version in manifest["config"]["representations"]:
            folder = output / group / version
            if not (folder / "complete.json").exists():
                continue
            for p, expected in json.loads((folder / "prediction_hashes.json").read_text()).items():
                if digest(folder / p) != expected:
                    raise ValueError("Prediction changed before scoring")
            pred = json.loads((folder / "predictions.json").read_text())
            counts = json.loads((folder / "counts.json").read_text())
            for method in ("E1", "E2", "DTW_cycles"):
                sets = [
                    {
                        **s,
                        "count": s["dtw_cycle_count"]
                        if method == "DTW_cycles"
                        else s[method]["count"],
                    }
                    for s in counts
                ]
                reports.append(
                    {
                        "group": group,
                        "run": method,
                        "representation": version,
                        "score": score_sets(sets, refs, bkg),
                        "funnel": rejection_funnel(pred),
                        "failure_stages": failure_stages(pred, refs),
                    }
                )
    save(output / "scores.json", reports)
    return reports
