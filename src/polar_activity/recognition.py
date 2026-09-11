"""Small, explicit personal reference model. Prediction never opens session labels.

Distances are waveform-feature distances, not probabilities. Four-second windows
are kept together by source recording; overlapping windows are not test subjects.
"""

import hashlib
import json
from pathlib import Path

import numpy as np

from .counter import CounterConfig, _antialias, _parts
from .counting import load_motion
from .storage import write_json

MODEL_VERSION = "personal-reference-v1"
WINDOW_S = 4.0
FEATURES = [
    "mean_acc_x/500",
    "mean_acc_y/500",
    "mean_acc_z/500",
    "std_acc_x/400",
    "std_acc_y/400",
    "std_acc_z/400",
    "std_gyro_x/80",
    "std_gyro_y/80",
    "std_gyro_z/80",
    "acc_norm_p10/500",
    "acc_norm_p50/500",
    "acc_norm_p90/500",
    "log1p_acc_norm_std/2",
    "log1p_gyro_std_norm/2",
    "gyro_principal_fraction",
]
CLASSES = {
    "push-up",
    "pull-up",
    "squat",
    "jump",
    "walking",
    "stairs",
    "household",
    "standing_or_sitting",
}


def source_hashes(session: Path) -> dict:
    return {
        name: hashlib.sha256((session / name).read_bytes()).hexdigest()
        for name in ("acc.csv", "gyro.csv")
    }


def motion_blocks(data: tuple) -> list[tuple]:
    """Filter and resample only intact native overlap; never interpolate over loss."""
    at, acc, gt, gyro = data
    ap, _ = _parts(at, acc)
    gp, _ = _parts(gt, gyro)
    blocks = []
    i = j = 0
    while i < len(ap) and j < len(gp):
        a_times, g_times = at[ap[i]], gt[gp[j]]
        left, right = max(a_times[0], g_times[0]), min(a_times[-1], g_times[-1])
        if right - left >= WINDOW_S:
            t = np.arange(left, right, 0.04)
            av = _antialias(a_times, acc[ap[i]], CounterConfig())
            gv = _antialias(g_times, gyro[gp[j]], CounterConfig())
            a = np.column_stack([np.interp(t, a_times, v) for v in av.T])
            g = np.column_stack([np.interp(t, g_times, v) for v in gv.T])
            blocks.append((t, a, g))
        if a_times[-1] <= g_times[-1]:
            i += 1
        else:
            j += 1
    return blocks


def features(acc: np.ndarray, gyro: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(acc, axis=1)
    s = np.linalg.svd(gyro - gyro.mean(axis=0), compute_uv=False)
    fraction = s[0] ** 2 / (np.sum(s * s) + 1e-9)
    return np.r_[
        acc.mean(0) / 500,
        acc.std(0) / 400,
        gyro.std(0) / 80,
        np.percentile(norm, [10, 50, 90]) / 500,
        np.log1p(norm.std()) / 2,
        np.log1p(np.linalg.norm(gyro.std(0))) / 2,
        fraction,
    ]


def fit_model(reference_path: Path, output: Path) -> dict:
    """Fit only declared intervals, with traceable source hashes. Counts are ignored."""
    spec = json.loads(reference_path.read_text(encoding="utf-8"))
    examples, sources = [], []
    subject, position = spec.get("subject"), spec.get("sensor_position")
    if not subject or not position or not spec.get("references"):
        raise ValueError("References need subject, sensor_position and nonempty references")
    cache = {}
    for ref in spec["references"]:
        session = (reference_path.parent / ref["session"]).resolve()
        if session not in cache:
            metadata = json.loads((session / "metadata.json").read_text(encoding="utf-8"))
            if metadata.get("subject") != subject or metadata.get("sensor_position") != position:
                raise ValueError(f"Reference subject/placement mismatch: {session.name}")
            cache[session] = (motion_blocks(load_motion(session)), source_hashes(session), metadata)
        blocks, hashes, metadata = cache[session]
        activity = ref["activity"]
        left, right = float(ref["start_time_s"]), float(ref["end_time_s"])
        if (
            activity not in CLASSES
            or not np.isfinite([left, right]).all()
            or right - left < WINDOW_S
        ):
            raise ValueError(f"Invalid activity or reference interval: {ref}")
        block = next((b for b in blocks if b[0][0] <= left and right <= b[0][-1]), None)
        if block is None:
            raise ValueError(f"Reference interval lies outside intact IMU overlap: {session.name}")
        source_index = len(sources)
        sources.append(
            {
                "session": str(session),
                "session_id": metadata.get("session_id"),
                "sha256": hashes,
                "activity": activity,
                "start_time_s": left,
                "end_time_s": right,
                "boundary_source": ref.get("boundary_source", "explicit reference manifest"),
            }
        )
        t, acc, gyro = block
        for start in np.arange(left, right - WINDOW_S + 1e-6, 1.0):
            mask = (t >= start) & (t < start + WINDOW_S)
            examples.append(
                {
                    "activity": activity,
                    "source_index": source_index,
                    "start_time_s": float(start),
                    "features": features(acc[mask], gyro[mask]).tolist(),
                }
            )
    if not examples:
        raise ValueError("No usable reference windows")
    model = {
        "version": MODEL_VERSION,
        "subject": subject,
        "sensor_position": position,
        "features": FEATURES,
        "window_s": WINDOW_S,
        "max_distance": 0.85,
        "jump_count_convention": spec.get("jump_count_convention", "unconfirmed"),
        "jump_convention_source": spec.get("jump_convention_source", "explicit reference manifest"),
        "classes": sorted({e["activity"] for e in examples}),
        "sources": sources,
        "examples": examples,
        "reference_manifest_sha256": hashlib.sha256(reference_path.read_bytes()).hexdigest(),
        "expected_counts_used_for_fitting": False,
        "limitations": [
            "Personal development model; no independent blind accuracy established.",
            "Sensor orientation and placement must match the references.",
            "Standing and sitting are deliberately combined; "
            "upper-arm motion does not establish posture.",
            "Stairs direction, tread count and handrail use "
            "are not established by the activity label.",
            "Feature distance is not a probability; similar unseen activity may be mistaken.",
        ],
    }
    if model["jump_count_convention"] not in {"unconfirmed", "paired_impacts", "single_impact"}:
        raise ValueError("Unknown jump_count_convention")
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, model)
    return model


def load_model(path: Path) -> dict:
    model = json.loads(path.read_text(encoding="utf-8"))
    if model.get("version") != MODEL_VERSION or model.get("features") != FEATURES:
        raise ValueError("Unsupported personal model; rebuild it with train")
    examples = model.get("examples", [])
    if not examples or any(e.get("activity") not in CLASSES for e in examples):
        raise ValueError("Personal model has no valid reference examples")
    vectors = np.asarray([e.get("features") for e in examples], dtype=float)
    if vectors.shape != (len(examples), len(FEATURES)) or not np.isfinite(vectors).all():
        raise ValueError("Invalid model feature vectors")
    if not 0 < model.get("max_distance", 0) <= 2:
        raise ValueError("Invalid model distance threshold")
    if model.get("jump_count_convention") not in {"unconfirmed", "paired_impacts", "single_impact"}:
        raise ValueError("Invalid model jump convention")
    return model


def classify(acc: np.ndarray, gyro: np.ndarray, model: dict) -> dict:
    if np.linalg.norm(gyro.std(0)) < 8 and np.std(np.linalg.norm(acc, axis=1)) < 30:
        return {"activity": "stationary", "distance": None, "margin": None}
    vector = features(acc, gyro)
    examples = model["examples"]
    scores = []
    for activity in sorted({e["activity"] for e in examples}):
        distances = [
            float(np.sqrt(np.mean((vector - np.asarray(e["features"])) ** 2)))
            for e in examples
            if e["activity"] == activity
        ]
        scores.append((float(np.mean(sorted(distances)[:3])), activity))
    scores.sort()
    distance, activity = scores[0]
    margin = scores[1][0] - distance if len(scores) > 1 else None
    # These references contain arm gestures while standing/sitting. Matching them
    # cannot establish body posture (or rule out walking with different arm use).
    predicted = "other_movement" if activity == "standing_or_sitting" else activity
    return {
        "activity": predicted if distance <= model["max_distance"] else "unknown",
        "matched_reference": activity,
        "distance": distance,
        "margin": margin,
    }


def predict_windows(blocks: list[tuple], model: dict) -> list[dict]:
    result = []
    for block_id, (t, acc, gyro) in enumerate(blocks):
        windows = []
        for start in np.arange(t[0], t[-1] - WINDOW_S + 1e-6, 1.0):
            mask = (t >= start) & (t < start + WINDOW_S)
            windows.append(
                {
                    "start_time_s": float(start + 1.5),
                    "end_time_s": float(start + 2.5),
                    "evidence_start_time_s": float(start),
                    "evidence_end_time_s": float(start + WINDOW_S),
                    "block_id": block_id,
                    **classify(acc[mask], gyro[mask], model),
                }
            )
        # Only remove isolated one-second class flips, without bridging source gaps.
        labels = [w["activity"] for w in windows]
        for window in windows:
            window["raw_activity"] = window["activity"]
        for i in range(1, len(windows) - 1):
            if labels[i - 1] == labels[i + 1] != labels[i]:
                windows[i]["activity"] = labels[i - 1]
                windows[i]["smoothed"] = True
        result.extend(windows)
    return result


def activity_intervals(windows: list[dict]) -> list[dict]:
    intervals = []
    for window in windows:
        if (
            intervals
            and intervals[-1]["activity"] == window["activity"]
            and intervals[-1]["block_id"] == window["block_id"]
        ):
            intervals[-1]["end_time_s"] = window["end_time_s"]
        else:
            intervals.append(
                {k: window[k] for k in ("activity", "block_id", "start_time_s", "end_time_s")}
            )
    return intervals
