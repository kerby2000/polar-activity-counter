"""Bounded single-cycle DTW. Inference sees arrays and a frozen model only."""

from bisect import bisect_right
from dataclasses import asdict, dataclass

import numpy as np
from tslearn.metrics import dtw_path

from .signal import represent, validate_block


@dataclass(frozen=True)
class MatchConfig:
    stride_s: float = 0.25
    points: int = 64
    band_radius: int = 8
    speed_factors: tuple = (0.75, 1.0, 1.25)
    duration_ratio: tuple = (0.6, 1.6)
    amplitude_ratio: tuple = (0.35, 2.8)
    min_relative_margin: float = 0.08
    max_pause_s: float = 3.0
    max_candidates: int = 30000
    default_threshold: float = 1.0

    def __post_init__(self):
        positive = [self.stride_s, self.default_threshold, *self.speed_factors]
        if not np.isfinite(positive).all() or min(positive) <= 0:
            raise ValueError("Stride, threshold and speed factors must be finite and positive")
        if self.points < 8 or not 0 <= self.band_radius < self.points or self.max_candidates < 1:
            raise ValueError("Invalid cycle grid, warping band or candidate budget")
        for bounds in (self.duration_ratio, self.amplitude_ratio):
            if len(bounds) != 2 or not np.isfinite(bounds).all() or not 0 < bounds[0] <= bounds[1]:
                raise ValueError("Invalid physical acceptance bounds")
        if not 0 <= self.min_relative_margin < 1 or not 0 <= self.max_pause_s < float("inf"):
            raise ValueError("Invalid class margin or set pause")


SIGNS = np.array([[1, 1, 1], [1, -1, -1], [-1, 1, -1], [-1, -1, 1]])


def path_score(a, b, radius, include_path=False):
    path, distance = dtw_path(
        a, b, global_constraint="sakoe_chiba", sakoe_chiba_radius=radius, be="numpy"
    )
    length = len(path)
    result = {
        "raw_distance": float(distance),
        "path_length": length,
        "distance": float(distance / np.sqrt(length)),
        "max_warp_samples": max(abs(i - j) for i, j in path),
        "path_expansion": float(length / max(len(a), len(b))),
    }
    if include_path:
        result["path"] = [[int(i), int(j)] for i, j in path]
    return result


def compare_representations(a, b, version, radius, include_path=False):
    fallback = version == "joint_pca_v1" and not (a["stable_frame"] and b["stable_frame"])
    x, y = (a["fallback"], b["fallback"]) if fallback else (a["values"], b["values"])
    signs = SIGNS if version == "joint_pca_v1" and not fallback else SIGNS[:1]
    options = []
    for sign in signs:
        score = path_score(np.asarray(x) * np.tile(sign, 2), np.asarray(y), radius, include_path)
        options.append({**score, "proper_sign": sign.tolist()})
    best = min(options, key=lambda s: (s["distance"], s["proper_sign"]))
    return {**best, "used_invariant_fallback": fallback}


def match_cycle(acc, gyro, duration, model, include_path=False):
    cfg = MatchConfig(**model["settings"])
    representation = represent(acc, gyro, model["scales"], model["representation"], cfg.points)
    scores = []
    for template in model["templates"]:
        ratio = duration / template["duration_s"]
        if not cfg.duration_ratio[0] <= ratio <= cfg.duration_ratio[1]:
            continue
        score = compare_representations(
            representation, template, model["representation"], cfg.band_radius, include_path
        )
        amp = np.asarray(representation["amplitudes"]) / np.maximum(template["amplitudes"], 1e-6)
        scores.append(
            {
                **score,
                "activity": template["activity"],
                "template_id": template["id"],
                "duration_ratio": float(ratio),
                "amplitude_ratios": amp.tolist(),
                "amplitude_valid": bool(
                    np.all((amp >= cfg.amplitude_ratio[0]) & (amp <= cfg.amplitude_ratio[1]))
                ),
            }
        )
    per_class = []
    for activity in sorted({s["activity"] for s in scores}):
        per_class.append(
            min(
                (s for s in scores if s["activity"] == activity),
                key=lambda s: (s["distance"], s["template_id"]),
            )
        )
    per_class.sort(key=lambda s: (s["distance"], s["activity"]))
    reasons = []
    best = per_class[0] if per_class else None
    if best is None:
        reasons.append("no_template_with_plausible_duration")
    else:
        threshold = model["thresholds"][best["activity"]]
        if best["distance"] > threshold:
            reasons.append("template_distance")
        if not best["amplitude_valid"]:
            reasons.append("physical_amplitude")
        margin = (
            ((per_class[1]["distance"] - best["distance"]) / max(per_class[1]["distance"], 1e-9))
            if len(per_class) > 1
            else None
        )
        if margin is not None and margin < cfg.min_relative_margin:
            reasons.append("ambiguous_exercise_class")
        best = {**best, "threshold": threshold, "relative_margin": margin}
    return {
        "activity": best["activity"] if best and not reasons else "unknown",
        "best": best,
        "class_scores": per_class,
        "rejection_reasons": reasons,
        "stable_frame": representation["stable_frame"],
        "eigenvalue_separations": representation["eigenvalue_separations"],
    }


def resolve_overlaps(candidates):
    """Weighted interval scheduling across all classes, independent of known counts."""
    eligible = sorted(
        (c for c in candidates if not c["rejection_reasons"]),
        key=lambda c: (c["end_time_s"], c["start_time_s"], c["candidate_id"]),
    )
    ends = [c["end_time_s"] for c in eligible]
    previous, weights = [], []
    for i, candidate in enumerate(eligible):
        previous.append(bisect_right(ends, candidate["start_time_s"] + 1e-8, hi=i) - 1)
        quality = max(1e-6, 1 - candidate["best"]["distance"] / candidate["best"]["threshold"])
        weights.append(quality * np.sqrt(candidate["end_time_s"] - candidate["start_time_s"]))
    dp = [0.0]
    for i in range(len(eligible)):
        dp.append(max(dp[-1], weights[i] + dp[previous[i] + 1]))
    selected, i = [], len(eligible) - 1
    while i >= 0:
        if weights[i] + dp[previous[i] + 1] > dp[i] + 1e-12:
            selected.append(eligible[i])
            i = previous[i]
        else:
            i -= 1
    selected_ids = {c["candidate_id"] for c in selected}
    for c in eligible:
        if c["candidate_id"] not in selected_ids:
            c["rejection_reasons"].append("overlapping_better_cycle_hypothesis")
    return sorted(selected, key=lambda c: c["start_time_s"])


def candidate_durations(model):
    cfg = MatchConfig(**model["settings"])
    # At most three duration prototypes per class; derived solely from training cycles.
    values = [
        max(
            0.4,
            round(
                float(
                    np.median(
                        [t["duration_s"] for t in model["templates"] if t["activity"] == activity]
                    )
                )
                * factor
                / 0.2
            )
            * 0.2,
        )
        for activity in sorted(model["thresholds"])
        for factor in cfg.speed_factors
    ]
    return sorted(set(round(v, 3) for v in values))


def scan(blocks, model):
    """Full fallback grid over every intact block; no old-classifier gate or file access."""
    cfg = MatchConfig(**model["settings"])
    durations = candidate_durations(model)
    candidates, coverage = [], []
    for block_id, (t, acc, gyro) in enumerate(blocks):
        validate_block(t, acc, gyro)
        complete = True
        last_scanned = float(t[0])
        for start in np.arange(t[0], t[-1], cfg.stride_s):
            for duration in durations:
                end = start + duration
                if end > t[-1]:
                    continue
                if len(candidates) >= cfg.max_candidates:
                    complete = False
                    break
                mask = (t >= start) & (t <= end)
                match = match_cycle(acc[mask], gyro[mask], duration, model)
                candidates.append(
                    {
                        "candidate_id": len(candidates),
                        "block_id": block_id,
                        "start_time_s": float(start),
                        "end_time_s": float(end),
                        "duration_s": float(duration),
                        **match,
                    }
                )
                last_scanned = float(end)
            if not complete:
                break
        coverage.append(
            {
                "block_id": block_id,
                "start_time_s": float(t[0]),
                "end_time_s": float(t[-1]),
                "grid_complete": complete,
                "last_scanned_end_s": last_scanned,
            }
        )
    selected = []
    for block_id in range(len(blocks)):
        selected.extend(resolve_overlaps([c for c in candidates if c["block_id"] == block_id]))
    sets = []
    for c in selected:
        if (
            sets
            and sets[-1]["activity"] == c["activity"]
            and sets[-1]["block_id"] == c["block_id"]
            and c["start_time_s"] - sets[-1]["end_time_s"] <= cfg.max_pause_s
        ):
            sets[-1]["end_time_s"] = c["end_time_s"]
            sets[-1]["cycles"].append(c)
        else:
            sets.append(
                {
                    "activity": c["activity"],
                    "block_id": c["block_id"],
                    "start_time_s": c["start_time_s"],
                    "end_time_s": c["end_time_s"],
                    "cycles": [c],
                }
            )
    for i, s in enumerate(sets):
        s.update(set_id=i, dtw_cycle_count=len(s["cycles"]), isolated_cycle=len(s["cycles"]) == 1)
    return {
        "schema_version": 1,
        "method": "exp-r1-dtw",
        "settings": asdict(cfg),
        "representation": model["representation"],
        "reads_target_labels": False,
        "candidate_durations_s": durations,
        "coverage": coverage,
        "candidates": candidates,
        "sets": sets,
    }
