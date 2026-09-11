"""Training-only exemplar extraction and calibration; no repetition totals as inputs."""

from dataclasses import asdict, replace

import numpy as np

from ..counter import CounterConfig, _detect_block, _smooth
from .matcher import MatchConfig, compare_representations
from .signal import feature_vector, represent, stable_excursions


def cycle_proposals(block, activity, start, end):
    t, acc, gyro = block
    cycles = []
    if activity == "pull-up":
        trace = stable_excursions(t, acc, gyro, start, end)
        if trace["baseline"] is None or trace["rejection_reasons"]:
            return [], trace
        direction = _smooth(acc, 11)
        direction /= np.maximum(np.linalg.norm(direction, axis=1)[:, None], 1)
        depth = 1 - direction @ trace["baseline"]["direction"]
        for event in trace["events"]:
            if not event["return_observed"]:
                continue
            first = int(np.searchsorted(t, event["start_time_s"]))
            last = int(np.searchsorted(t, event["end_time_s"]))
            # Extend threshold occupancy to observed near-baseline return regions.
            before = np.flatnonzero((t >= t[first] - 3) & (t <= t[first]) & (depth <= 0.05))
            after = np.flatnonzero((t >= t[last]) & (t <= t[last] + 3) & (depth <= 0.05))
            if not len(before) or not len(after):
                continue
            left, right = float(t[before[-1]]), float(t[after[0]])
            if left >= start - 2 and right <= end + 2:
                cycles.append(
                    {
                        "start_time_s": left,
                        "end_time_s": right,
                        "source_method": "observed-near-baseline-return",
                        "confidence": "IMU inferred; no anatomical/video verification",
                    }
                )
        return cycles, {k: v for k, v in trace.items() if k != "signal"}
    if activity not in {"push-up", "squat"}:
        return [], {"reason": "No reviewed complete-cycle definition for this variant"}
    for size in (8, 6, 12):
        config = replace(CounterConfig(), window_s=size, max_period_s=min(4, size / 3))
        for bout in _detect_block(t, acc, gyro, config):
            for cycle in bout["cycles"]:
                if start - 1 <= cycle["start_time_s"] and cycle["end_time_s"] <= end + 1:
                    if any(
                        min(cycle["end_time_s"], c["end_time_s"])
                        > max(cycle["start_time_s"], c["start_time_s"])
                        for c in cycles
                    ):
                        continue
                    cycles.append(
                        {
                            "start_time_s": cycle["start_time_s"],
                            "end_time_s": cycle["end_time_s"],
                            "source_method": f"legacy-complete-cycle-{size}s",
                            "confidence": "IMU inferred; phase not independently labelled",
                        }
                    )
    return sorted(cycles, key=lambda c: c["start_time_s"]), {}


def training_cycles(recordings, references):
    """The caller supplies only declared training groups and interval/class annotations."""
    result, trace = [], []
    for ref in references:
        group = ref["group"]
        if group not in recordings:
            raise ValueError("Reference group is outside this training fold")
        for block_id, (t, acc, gyro) in enumerate(recordings[group]):
            if not t[0] <= ref["start"] < ref["end"] <= t[-1]:
                continue
            cycles, detail = cycle_proposals(
                (t, acc, gyro), ref["activity"], ref["start"], ref["end"]
            )
            trace.append(
                {
                    "group": group,
                    "activity": ref["activity"],
                    "interval": [ref["start"], ref["end"]],
                    "cycles": cycles,
                    "detail": detail,
                }
            )
            for cycle in cycles:
                m = (t >= cycle["start_time_s"]) & (t <= cycle["end_time_s"])
                result.append(
                    {
                        **cycle,
                        "activity": ref["activity"],
                        "group": group,
                        "block_id": block_id,
                        "acc": acc[m],
                        "gyro": gyro[m],
                        "duration_s": float(t[m][-1] - t[m][0]),
                        "id": f"{group}:{cycle['start_time_s']:.6f}",
                    }
                )
    return result, trace


def choose_templates(cycles, maximum=3):
    selected = []
    for activity in sorted({c["activity"] for c in cycles}):
        examples = [c for c in cycles if c["activity"] == activity]
        vectors = np.array([feature_vector(c["acc"], c["gyro"], "invariant6") for c in examples])
        distances = np.linalg.norm(vectors[:, None] - vectors[None, :], axis=2)
        # Medoid per source first, then farthest uncovered example. Deterministic ties.
        chosen = []
        for group in sorted({c["group"] for c in examples}):
            indices = [i for i, c in enumerate(examples) if c["group"] == group]
            chosen.append(min(indices, key=lambda i: (distances[i, indices].mean(), i)))
        chosen = chosen[:maximum]
        while len(chosen) < min(maximum, len(examples)):
            remaining = [i for i in range(len(examples)) if i not in chosen]
            chosen.append(max(remaining, key=lambda i: (distances[i, chosen].min(), -i)))
        selected.extend(examples[i] for i in chosen)
    return selected


def fit_templates(cycles, negative_intervals, version, config=None):
    config = config or MatchConfig()
    if not cycles:
        raise ValueError("Training fold has no supported complete cycles")
    scales = [
        max(100, float(np.median([np.linalg.norm(c["acc"].std(0)) for c in cycles]))),
        max(20, float(np.median([np.linalg.norm(c["gyro"].std(0)) for c in cycles]))),
    ]
    chosen = choose_templates(cycles)
    templates = []
    for c in chosen:
        rep = represent(c["acc"], c["gyro"], scales, version, config.points)
        templates.append(
            {
                **{k: v for k, v in c.items() if k not in {"acc", "gyro"}},
                **{k: v.tolist() if isinstance(v, np.ndarray) else v for k, v in rep.items()},
            }
        )
    thresholds, calibration = {}, []
    for activity in sorted({c["activity"] for c in chosen}):
        bank = [c for c in templates if c["activity"] == activity]
        positive, negative = [], []
        for cycle in [c for c in cycles if c["activity"] == activity]:
            others = [c for c in bank if c["id"] != cycle["id"]]
            if not others:
                continue
            rep = represent(cycle["acc"], cycle["gyro"], scales, version, config.points)
            positive.append(
                min(
                    compare_representations(rep, c, version, config.band_radius)["distance"]
                    for c in others
                )
            )
        duration = float(np.median([c["duration_s"] for c in bank]))
        for t, acc, gyro in negative_intervals:
            # Fixed sparse negatives from TRAINING annotations only.
            for start in np.arange(t[0], t[-1] - duration, max(2, duration))[:6]:
                m = (t >= start) & (t <= start + duration)
                rep = represent(acc[m], gyro[m], scales, version, config.points)
                negative.append(
                    min(
                        compare_representations(rep, c, version, config.band_radius)["distance"]
                        for c in bank
                    )
                )
        pos_limit = (
            float(np.percentile(positive, 90) * 1.25) if positive else config.default_threshold
        )
        neg_limit = (
            float(np.percentile(negative, 5) * 0.8) if negative else config.default_threshold
        )
        thresholds[activity] = max(0.05, min(config.default_threshold, pos_limit, neg_limit))
        calibration.append(
            {
                "activity": activity,
                "positive_distances": positive,
                "negative_distances": negative,
                "threshold": thresholds[activity],
                "rule": "min(initial cap, 1.25*positive P90, 0.8*negative P05); floor .05",
                "scope": "training-cycle development, not an independent validation fold",
            }
        )
    return {
        "schema_version": 1,
        "feature_version": "cycle-template-v1",
        "representation": version,
        "settings": asdict(config),
        "scales": scales,
        "scale_source": "Median training-cycle vector spread, same scalar per modality",
        "templates": templates,
        "thresholds": thresholds,
        "calibration": calibration,
        "training_groups": sorted({c["group"] for c in cycles}),
        "expected_counts_used": False,
    }
