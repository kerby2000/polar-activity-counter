"""Gravity-relative personal recognition with independent, context-aware counters."""

import hashlib
import json
from pathlib import Path

import numpy as np

from .adaptive_counter import short_return_bouts
from .adaptive_pullups import pullup_bouts
from .analyser import _jump_sets, plot_analysis, plot_motion_quality
from .counting import _write_csv, load_motion
from .experiments.signal import feature_vector
from .motion_quality import compare_excursions
from .recognition import CLASSES, activity_intervals, motion_blocks, source_hashes
from .storage import write_json

VERSION = "personal-adaptive-v1"
FEATURE_VERSION = "gravity_relative_v1"
EXERCISES = {"push-up", "pull-up", "squat", "jump"}


def fit_model(reference_path, output):
    spec = json.loads(Path(reference_path).read_text(encoding="utf-8"))
    if not spec.get("subject") or not spec.get("sensor_position") or not spec.get("references"):
        raise ValueError("References need subject, sensor_position and nonempty references")
    model = {
        "version": VERSION,
        "feature_version": FEATURE_VERSION,
        "subject": spec["subject"],
        "sensor_position": spec["sensor_position"],
        "max_distance": 0.85,
        "window_s": 4.0,
        "edge_context_s": 2.0,
        "max_class_gap_s": 3.0,
        "minimum_class_seconds": 2.0,
        "jump_count_convention": spec.get("jump_count_convention", "unconfirmed"),
        "sources": [],
        "examples": [],
        "expected_counts_used_for_fitting": False,
    }
    cache = {}
    for ref in spec["references"]:
        path = (Path(reference_path).parent / ref["session"]).resolve()
        if path not in cache:
            meta = json.loads((path / "metadata.json").read_text(encoding="utf-8"))
            if any(meta.get(k) != model[k] for k in ("subject", "sensor_position")):
                raise ValueError("Reference subject/placement differs from personal model")
            cache[path] = motion_blocks(load_motion(path))
        start, end = float(ref["start_time_s"]), float(ref["end_time_s"])
        if ref["activity"] not in CLASSES or not np.isfinite([start, end]).all() or end - start < 4:
            raise ValueError("Invalid reference class or interval")
        block = next((b for b in cache[path] if b[0][0] <= start < end <= b[0][-1]), None)
        if block is None:
            raise ValueError("Reference interval is outside intact ACC/gyro overlap")
        source_index = len(model["sources"])
        model["sources"].append(
            {
                "session": str(path),
                "sha256": source_hashes(path),
                "activity": ref["activity"],
                "start_time_s": start,
                "end_time_s": end,
                "boundary_source": ref.get("boundary_source", "explicit training manifest"),
            }
        )
        t, acc, gyro = block
        for left in np.arange(start, end - 4 + 1e-6, 1):
            mask = (t >= left) & (t < left + 4)
            model["examples"].append(
                {
                    "activity": ref["activity"],
                    "source_index": source_index,
                    "start_time_s": float(left),
                    "features": feature_vector(acc[mask], gyro[mask], FEATURE_VERSION).tolist(),
                }
            )
    model["classes"] = sorted({e["activity"] for e in model["examples"]})
    validate_model(model)
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    write_json(Path(output), model)
    return model


def validate_model(model):
    if model.get("version") != VERSION or model.get("feature_version") != FEATURE_VERSION:
        raise ValueError("Unsupported adaptive model; train with --engine adaptive")
    examples = model.get("examples", [])
    vectors = np.asarray([e.get("features") for e in examples], float)
    if not examples or vectors.shape != (len(examples), 13) or not np.isfinite(vectors).all():
        raise ValueError("Invalid adaptive feature vectors")
    if any(e.get("activity") not in CLASSES for e in examples):
        raise ValueError("Invalid adaptive reference class")
    if not 0 < model.get("max_distance", 0) <= 2:
        raise ValueError("Invalid adaptive distance threshold")
    for key, expected in (
        ("window_s", 4),
        ("edge_context_s", 2),
        ("max_class_gap_s", 3),
        ("minimum_class_seconds", 2),
    ):
        if model.get(key) != expected:
            raise ValueError(f"Unsupported adaptive setting: {key}")
    if model.get("jump_count_convention") not in {"unconfirmed", "paired_impacts", "single_impact"}:
        raise ValueError("Invalid jump convention")
    return model


def predict_windows(blocks, model):
    validate_model(model)
    examples = model["examples"]
    bank = {
        c: np.array([e["features"] for e in examples if e["activity"] == c])
        for c in sorted({e["activity"] for e in examples})
    }
    rows = []
    for block_id, (t, acc, gyro) in enumerate(blocks):
        block_rows = []
        for start in np.arange(t[0], t[-1] - 4 + 1e-6, 1):
            m = (t >= start) & (t < start + 4)
            vector = feature_vector(acc[m], gyro[m], FEATURE_VERSION)
            scores = {
                c: float(np.sort(np.sqrt(np.mean((v - vector) ** 2, axis=1)))[:3].mean())
                for c, v in bank.items()
            }
            ranked = sorted(scores, key=lambda c: (scores[c], c))
            best = ranked[0]
            quiet = np.linalg.norm(gyro[m].std(0)) < 8 and np.linalg.norm(acc[m], axis=1).std() < 30
            label = (
                "stationary"
                if quiet
                else best
                if scores[best] <= model["max_distance"]
                else "unknown"
            )
            if label == "standing_or_sitting":
                label = "other_movement"
            block_rows.append(
                {
                    "block_id": block_id,
                    "start_time_s": float(start + 1.5),
                    "end_time_s": float(start + 2.5),
                    "evidence_start_time_s": float(start),
                    "evidence_end_time_s": float(start + 4),
                    "activity": label,
                    "raw_activity": label,
                    "class_scores": scores,
                    "distance": scores[best],
                    "margin": scores[ranked[1]] - scores[best] if len(ranked) > 1 else None,
                }
            )
        labels = [r["activity"] for r in block_rows]
        for i in range(1, len(labels) - 1):
            if labels[i - 1] == labels[i + 1] != labels[i]:
                block_rows[i]["activity"] = labels[i - 1]
                block_rows[i]["smoothed"] = True
        rows.extend(block_rows)
    return rows


def candidate_intervals(windows, model):
    intervals = activity_intervals(windows)
    candidates = []
    for interval in intervals:
        if interval["activity"] not in EXERCISES:
            continue
        previous = candidates[-1] if candidates else None
        between = [
            w
            for w in windows
            if previous
            and w["block_id"] == interval["block_id"]
            and previous["end_time_s"] <= w["start_time_s"] < interval["start_time_s"]
        ]
        bridge = (
            previous
            and previous["activity"] == interval["activity"]
            and previous["block_id"] == interval["block_id"]
            and interval["start_time_s"] - previous["end_time_s"] <= model["max_class_gap_s"]
            and all(w["activity"] in {"stationary", "unknown", "other_movement"} for w in between)
        )
        support = sum(
            1
            for w in windows
            if w["block_id"] == interval["block_id"]
            and interval["start_time_s"] <= w["start_time_s"] < interval["end_time_s"]
        )
        if bridge:
            previous["end_time_s"] = interval["end_time_s"]
            previous["class_support_s"] += support
        else:
            candidates.append({**interval, "class_support_s": support})
    for i, interval in enumerate(candidates):
        interval["candidate_id"] = i
        interval["rejection_reasons"] = (
            []
            if interval["class_support_s"] >= model["minimum_class_seconds"]
            else ["insufficient_class_support"]
        )
    return candidates


def analyse_arrays(data, model):
    blocks = motion_blocks(data)
    windows = predict_windows(blocks, model)
    candidates = candidate_intervals(windows, model)
    sets, traces, unassigned_attempts = [], [], []
    for candidate in candidates:
        if candidate["rejection_reasons"] or candidate["activity"] == "jump":
            continue
        t, acc, gyro = blocks[candidate["block_id"]]
        start, end = candidate["start_time_s"], candidate["end_time_s"]
        activity = candidate["activity"]
        if activity == "pull-up":
            bouts, trace = pullup_bouts(t, acc, gyro, start, end)
            if bouts:
                unassigned_attempts.extend(
                    {
                        **event,
                        "activity": activity,
                        "candidate_id": candidate["candidate_id"],
                        "reason": "incomplete attempt separated from the counted set by rest",
                    }
                    for event in trace.get("isolated_events", [])
                    if not event["return_observed"]
                )
        else:
            m = (t >= start - model["edge_context_s"]) & (t <= end + model["edge_context_s"])
            bouts, trace = short_return_bouts(t[m], acc[m], gyro[m])
        traces.append(
            {"candidate_id": candidate["candidate_id"], "activity": activity, "detail": trace}
        )
        if not bouts:
            candidate["rejection_reasons"].append("no_supported_return_sequence")
        for bout in bouts:
            # The counter may inspect context, but an unrelated neighbouring bout
            # cannot acquire the proposed class just by lying in that context.
            middle = (bout["start_time_s"] + bout["end_time_s"]) / 2
            if not start <= middle <= end:
                continue
            complete = sum(c.get("return_observed", True) for c in bout["cycles"])
            incomplete = len(bout["cycles"]) - complete
            item = {
                **bout,
                "block_id": candidate["block_id"],
                "activity": activity,
                "candidate_id": candidate["candidate_id"],
                "rep_estimate": len(bout["cycles"]),
                "complete_cycles": complete,
                "incomplete_returns": incomplete,
                "count_status": "attempt_estimate" if incomplete else "observed_motion_cycles",
                "method": "stable-context-excursions-v1"
                if activity == "pull-up"
                else "multiscale-return-v1",
            }
            if activity == "pull-up":
                item["motion_quality"] = compare_excursions(bout["cycles"])
            sets.append(item)
    jumps = [c for c in candidates if c["activity"] == "jump" and not c["rejection_reasons"]]
    sets.extend(_jump_sets(data, blocks, jumps, model["jump_count_convention"]))
    # Context extensions may overlap; retain a physical interval only once.
    accepted = []
    for item in sorted(
        sets, key=lambda s: (-(s["end_time_s"] - s["start_time_s"]), s["start_time_s"])
    ):
        if any(
            min(item["end_time_s"], old["end_time_s"])
            > max(item["start_time_s"], old["start_time_s"])
            for old in accepted
        ):
            traces.append({"rejection_reason": "overlapping_exercise_bout", "rejected_set": item})
            continue
        accepted.append(item)
    accepted.sort(key=lambda s: s["start_time_s"])
    for i, item in enumerate(accepted, 1):
        item["set_id"] = f"{i:03d}"
    return {
        "algorithm": VERSION,
        "sets": accepted,
        "windows": windows,
        "activities": activity_intervals(windows),
        "candidate_intervals": candidates,
        "motion_traces": traces,
        "unassigned_attempts": unassigned_attempts,
        "reads_target_labels": False,
        "coverage": [
            {"block_id": i, "start_time_s": float(t[0]), "end_time_s": float(t[-1])}
            for i, (t, _, _) in enumerate(blocks)
        ],
    }


def analyse_session(session, model_path, output=None, plot=True):
    session, model_path = Path(session), Path(model_path)
    output = Path(output) if output else session / "analysis-adaptive"
    if output.resolve() == session.resolve() or output.resolve() in session.resolve().parents:
        raise ValueError("Choose a derived output directory, not a source directory or its parent")
    model = validate_model(json.loads(model_path.read_text(encoding="utf-8")))
    metadata = json.loads((session / "metadata.json").read_text(encoding="utf-8"))
    if any(metadata.get(k) != model.get(k) for k in ("subject", "sensor_position")):
        raise ValueError("Session subject/placement differs from personal model")
    data = load_motion(session)
    result = analyse_arrays(data, model)
    hashes = source_hashes(session)
    reused = any(s["sha256"] == hashes for s in model["sources"])
    result.update(
        {
            "schema_version": 1,
            "source_sha256": hashes,
            "source_session_id": metadata.get("session_id"),
            "is_training_session": reused,
            "model_sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
            "warnings": (["Training replay; not independent validation."] if reused else [])
            + ([] if result["coverage"] else ["No intact ACC/gyro overlap of four seconds."])
            + ["Personal development analyser; activity names and motion counts remain estimates."],
            "output_directory": str(output),
        }
    )
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "analysis.json", result)
    _write_csv(
        output / "sets.csv",
        [
            "set_id",
            "activity",
            "start_time_s",
            "end_time_s",
            "rep_estimate",
            "complete_cycles",
            "incomplete_returns",
            "count_status",
            "method",
        ],
        result["sets"],
    )
    _write_csv(
        output / "activities.csv",
        ["activity", "start_time_s", "end_time_s", "block_id"],
        result["activities"],
    )
    _write_csv(
        output / "repetitions.csv",
        ["set_id", "repetition", "start_time_s", "end_time_s", "return_observed"],
        [
            {"set_id": s["set_id"], "repetition": i, **c}
            for s in result["sets"]
            for i, c in enumerate(s["cycles"], 1)
        ],
    )
    _write_csv(
        output / "unassigned_attempts.csv",
        ["activity", "start_time_s", "end_time_s", "return_observed", "reason"],
        result["unassigned_attempts"],
    )
    if plot:
        plot_analysis(data, result, output / "analysis.png")
        if any(s.get("motion_quality") for s in result["sets"]):
            plot_motion_quality(result["sets"], output / "motion_quality.png")
    return result


def format_analysis(result):
    from .analyser import format_analysis as legacy_format

    text = legacy_format(result)
    attempts = result.get("unassigned_attempts", [])
    if attempts:
        text += "\n" + "\n".join(
            f"Additional {a['activity']} attempt: {a['start_time_s']:.2f}-"
            f"{a['end_time_s']:.2f}s; incomplete return, separated by rest."
            for a in attempts
        )
    return text
