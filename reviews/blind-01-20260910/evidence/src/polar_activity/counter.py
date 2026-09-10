"""Offline repeated-motion segmentation and complete out-and-back cycle detection.

This module has no access to activity labels, manual boundaries or expected counts.
"""

from dataclasses import asdict, dataclass

import numpy as np


@dataclass(frozen=True)
class CounterConfig:
    grid_hz: float = 25.0
    lowpass_hz: float = 6.0
    smoothing_s: float = 0.2
    window_s: float = 8.0
    step_s: float = 0.48
    min_period_s: float = 0.48
    max_period_s: float = 2.64
    min_cycles: int = 3
    min_gyro_std_dps: float = 15.0
    min_acc_std_mg: float = 40.0
    min_gyro_axis_fraction: float = 0.65
    min_gyro_periodicity: float = 0.8
    min_acc_periodicity: float = 0.7
    min_acc_shape: float = 0.8
    min_gyro_shape: float = 0.85
    max_pause_s: float = 3.0
    max_pause_gyro_dps: float = 15.0
    max_pause_rotation_deg: float = 20.0
    max_pause_acc_change_mg: float = 150.0
    min_resume_axis_similarity: float = 0.9
    min_resume_shape_similarity: float = 0.85


def _smooth(values: np.ndarray, width: int) -> np.ndarray:
    return np.column_stack(
        [
            np.convolve(
                np.pad(axis, (width // 2, width // 2), mode="edge"),
                np.ones(width) / width,
                mode="valid",
            )
            for axis in values.T
        ]
    )


def _antialias(times: np.ndarray, values: np.ndarray, config: CounterConfig) -> np.ndarray:
    """Symmetric windowed-sinc low-pass before reduction to the analysis grid."""
    rate = 1 / float(np.median(np.diff(times)))
    cutoff = min(config.lowpass_hz, 0.4 * rate, 0.4 * config.grid_hz)
    width = max(5, round(0.8 * rate) // 2 * 2 + 1)
    positions = np.arange(width) - (width - 1) / 2
    kernel = 2 * cutoff / rate * np.sinc(2 * cutoff / rate * positions) * np.blackman(width)
    kernel /= kernel.sum()
    return np.column_stack(
        [
            np.convolve(np.pad(axis, (width // 2, width // 2), mode="edge"), kernel, mode="valid")
            for axis in values.T
        ]
    )


def _correlation(a: np.ndarray, b: np.ndarray) -> float:
    a = a - a.mean()
    b = b - b.mean()
    denominator = np.linalg.norm(a) * np.linalg.norm(b)
    return float(np.dot(a, b) / denominator) if denominator > 1e-10 else 0.0


def _principal(values: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    centered = values - values.mean(axis=0)
    _, strength, axes = np.linalg.svd(centered, full_matrices=False)
    axis = axes[0]
    axis *= 1 if axis[np.argmax(abs(axis))] >= 0 else -1
    energy = np.sum(strength**2)
    fraction = float(strength[0] ** 2 / energy) if energy else 0.0
    return centered @ axis, axis, fraction


def _windows(acc: np.ndarray, gyro: np.ndarray, config: CounterConfig) -> list[dict]:
    size = round(config.window_s * config.grid_hz)
    step = max(1, round(config.step_s * config.grid_hz))
    lower = max(2, round(config.min_period_s * config.grid_hz))
    upper = min(round(config.max_period_s * config.grid_hz), size // 3)
    candidates = []
    for first in range(0, len(acc) - size + 1, step):
        stop = first + size
        g, ga, fraction = _principal(gyro[first:stop])
        a, aa, _ = _principal(acc[first:stop])
        if (
            np.std(g) < config.min_gyro_std_dps
            or np.std(a) < config.min_acc_std_mg
            or fraction < config.min_gyro_axis_fraction
        ):
            continue
        lags = np.arange(lower - 1, upper + 2)
        correlations = np.array([_correlation(g[:-lag], g[lag:]) for lag in lags])
        # Evaluate neighbors outside the search band too; a monotonic correlation
        # curve must not acquire an artificial maximum at a band endpoint.
        peaks = (
            np.flatnonzero(
                (correlations[1:-1] > correlations[:-2]) & (correlations[1:-1] >= correlations[2:])
            )
            + 1
        )
        if not len(peaks):
            continue
        best = int(peaks[np.argmax(correlations[peaks])])
        lag = int(lags[best])
        gc = float(correlations[best])
        ac = _correlation(a[:-lag], a[lag:])
        if gc < config.min_gyro_periodicity or ac < config.min_acc_periodicity:
            continue
        candidates.append(
            {
                "first": first,
                "stop": stop,
                "period_s": lag / config.grid_hz,
                "acc_axis": aa,
                "gyro_axis": ga,
                "gyro_periodicity": gc,
                "acc_periodicity": ac,
                "score": gc + ac,
            }
        )
    return candidates


def _cycle_candidates(
    times: np.ndarray,
    acc: np.ndarray,
    gyro: np.ndarray,
    seed: dict,
    config: CounterConfig,
) -> tuple[list[dict], dict]:
    first, stop = seed["first"], seed["stop"]
    period = seed["period_s"]
    position = acc @ seed["acc_axis"]
    # Use the least-variable one-second interval only if it is actually quiet.
    second = round(config.grid_hz)
    starts = list(range(0, len(gyro) - second + 1, second))
    deviations = [np.linalg.norm(np.std(gyro[i : i + second], axis=0)) for i in starts]
    quiet = starts[int(np.argmin(deviations))]
    if min(deviations) <= 3.0:
        bias = np.median(gyro[quiet : quiet + second], axis=0)
        bias_method = "quiet_interval"
    else:
        bias = gyro[first:stop].mean(axis=0)
        bias_method = "periodic_window_mean"
    velocity = (gyro - bias) @ seed["gyro_axis"]
    low, high = np.percentile(position[first:stop], [10, 90])
    span = high - low
    low_band, high_band = low + 0.15 * span, high - 0.15 * span
    rotation = float(np.percentile(abs(velocity[first:stop]), 90))
    neutral = abs(velocity) <= max(3.0, 0.15 * rotation)
    # A sign change brackets neutral even if no sample lands exactly on zero.
    neutral[1:] |= velocity[1:] * velocity[:-1] < 0
    events = []
    for index, value in enumerate(position):
        state = 1 if value >= high_band else -1 if value <= low_band else 0
        if state and (not events or state != events[-1][1]):
            events.append((index, state))
    records = []
    extension = max(1, round(0.4 * period * config.grid_hz))
    for (entry, state), (opposite, _), (returned, _) in zip(
        events,
        events[1:],
        events[2:],
        strict=False,
    ):
        # Last departure from the origin band avoids counting waiting time as motion.
        origin_band = (
            position[entry:opposite] >= high_band
            if state == 1
            else position[entry:opposite] <= low_band
        )
        departure = entry + int(np.flatnonzero(origin_band)[-1])
        before_start = max(0, departure - extension)
        before = np.flatnonzero(neutral[before_start : departure + 1]) + before_start
        after = np.flatnonzero(neutral[returned : min(len(times), returned + extension)]) + returned
        if not len(before) or not len(after):
            continue  # Incomplete outward/return motion; no extrapolation at the ends.
        start, end = int(before[-1]), int(after[0])
        if start <= 0 or end >= len(times) - 1:
            continue  # Cannot infer a missing boundary outside the recorded interval.
        duration = float(times[end] - times[start])
        if not 0.5 * period <= duration <= 1.5 * period:
            continue
        x, y = position[start : end + 1], velocity[start : end + 1]
        if not 0.65 * span <= np.ptp(x) <= 1.7 * span:
            continue
        if min(np.max(y), -np.min(y)) < 0.35 * rotation:
            continue  # A full cycle needs rotation in both directions.
        if abs(np.sum(y)) / (np.sum(abs(y)) + 1e-10) > 0.35:
            continue  # Reject large net rotation instead of return movement.
        phase = np.linspace(0, 1, len(x))
        normalized_grid = np.linspace(0, 1, 64)
        records.append(
            {
                "first": start,
                "last": end,
                "state": state,
                "duration_s": duration,
                "acc_shape": np.interp(normalized_grid, phase, x),
                "gyro_shape": np.interp(normalized_grid, phase, y),
            }
        )
    return records, {
        "gyro_bias_dps": bias.tolist(),
        "bias_method": bias_method,
        "acc_low_band_mg": float(low_band),
        "acc_high_band_mg": float(high_band),
    }


def _chains(
    times: np.ndarray,
    records: list[dict],
    seed: dict,
    config: CounterConfig,
) -> list[list[dict]]:
    chains = []
    for state in (-1, 1):
        reference = [
            r
            for r in records
            if r["state"] == state and seed["first"] <= r["first"] < r["last"] < seed["stop"]
        ]
        if len(reference) < 2:
            continue
        acc_shape = np.median([r["acc_shape"] for r in reference], axis=0)
        gyro_shape = np.median([r["gyro_shape"] for r in reference], axis=0)
        chain: list[dict] = []
        for record in records:
            if record["state"] != state:
                continue
            record = record.copy()
            ac = _correlation(record["acc_shape"], acc_shape)
            gc = _correlation(record["gyro_shape"], gyro_shape)
            if ac < config.min_acc_shape or gc < config.min_gyro_shape:
                continue
            record["acc_similarity"], record["gyro_similarity"] = ac, gc
            if chain:
                gap = times[record["first"]] - times[chain[-1]["last"]]
                cadence_ratio = record["duration_s"] / chain[-1]["duration_s"]
                if gap > 0.6 * seed["period_s"] or gap < -0.08 or not 0.65 <= cadence_ratio <= 1.55:
                    chains.append(chain)
                    chain = []
            chain.append(record)
        if chain:
            chains.append(chain)
    return [
        chain
        for chain in chains
        if len(chain) >= config.min_cycles
        and chain[0]["first"] < seed["stop"]
        and chain[-1]["last"] > seed["first"]
    ]


def _detect_block(
    times: np.ndarray, acc: np.ndarray, gyro: np.ndarray, config: CounterConfig
) -> list:
    width = max(3, round(config.smoothing_s * config.grid_hz) // 2 * 2 + 1)
    acc, gyro = _smooth(acc, width), _smooth(gyro, width)
    windows = _windows(acc, gyro, config)
    # Adjacent passing windows describe the same periodic region. Each region gets
    # its own axes and cycle template, allowing different placements in later sets.
    regions: list[list[dict]] = []
    for window in windows:
        if not regions or window["first"] - regions[-1][-1]["first"] > config.grid_hz:
            regions.append([])
        regions[-1].append(window)
    candidates = []
    for region in regions:
        seed = max(region, key=lambda w: w["score"])
        records, details = _cycle_candidates(times, acc, gyro, seed, config)
        for chain in _chains(times, records, seed, config):
            start, end = float(times[chain[0]["first"]]), float(times[chain[-1]["last"]])
            similarity = float(
                np.mean([(c["acc_similarity"] + c["gyro_similarity"]) / 2 for c in chain])
            )
            candidates.append(
                {
                    "start_time_s": start,
                    "end_time_s": end,
                    "complete_cycles": len(chain),
                    "activity": "unclassified_repetitive_motion",
                    "shape_similarity": similarity,
                    "period_s": seed["period_s"],
                    "seed_start_time_s": float(times[seed["first"]]),
                    "seed_end_time_s": float(times[seed["stop"] - 1]),
                    "acc_periodicity": seed["acc_periodicity"],
                    "gyro_periodicity": seed["gyro_periodicity"],
                    "acc_axis": seed["acc_axis"].tolist(),
                    "gyro_axis": seed["gyro_axis"].tolist(),
                    **details,
                    "cycles": [
                        {
                            "start_time_s": float(times[c["first"]]),
                            "end_time_s": float(times[c["last"]]),
                            "duration_s": c["duration_s"],
                            "acc_similarity": c["acc_similarity"],
                            "gyro_similarity": c["gyro_similarity"],
                        }
                        for c in chain
                    ],
                }
            )
    # Opposite phase origins often describe the same motion. Retain the strongest
    # complete coverage once; never add both phase counts together.
    selected = []
    for candidate in sorted(
        candidates, key=lambda b: -(b["end_time_s"] - b["start_time_s"]) * b["shape_similarity"]
    ):
        if any(
            min(candidate["end_time_s"], other["end_time_s"])
            > max(candidate["start_time_s"], other["start_time_s"])
            for other in selected
        ):
            continue
        selected.append(candidate)
    return _group_bouts(times, acc, gyro, sorted(selected, key=lambda b: b["start_time_s"]), config)


def _boundary_template(times: np.ndarray, values: np.ndarray, cycles: list[dict]) -> np.ndarray:
    shapes = []
    for cycle in cycles:
        grid = np.linspace(cycle["start_time_s"], cycle["end_time_s"], 64)
        shape = np.column_stack([np.interp(grid, times, axis) for axis in values.T])
        # Center each physical axis so static gravity does not inflate similarity.
        shapes.append(shape - shape.mean(axis=0))
    return np.median(shapes, axis=0).ravel()


def _pause_evidence(
    times: np.ndarray,
    acc: np.ndarray,
    gyro: np.ndarray,
    previous: dict,
    following: dict,
    config: CounterConfig,
) -> dict | None:
    """Join only observed quiet rests between already qualifying motion blocks."""
    left, right = previous["end_time_s"], following["start_time_s"]
    duration = right - left
    if not 0 < duration <= config.max_pause_s:
        return None
    cadence_ratio = following["period_s"] / previous["period_s"]
    if not 0.65 <= cadence_ratio <= 1.55:
        return None
    axis_fit = {
        stream: abs(float(np.dot(previous[f"{stream}_axis"], following[f"{stream}_axis"])))
        for stream in ("acc", "gyro")
    }
    if min(axis_fit.values()) < config.min_resume_axis_similarity:
        return None
    # Skip at most 0.2s at each boundary for residual movement/filter settling.
    margin = min(0.2, duration / 4)
    inside = (times >= left + margin) & (times <= right - margin)
    full = (times >= left) & (times <= right)
    if np.count_nonzero(inside) < 3:
        return None
    bias = (np.array(previous["gyro_bias_dps"]) + following["gyro_bias_dps"]) / 2
    speed = np.linalg.norm(gyro - bias, axis=1)
    quiet_speed = float(np.percentile(speed[inside], 95))
    rotation = float(np.trapezoid(speed[full], times[full]))
    start_acc = np.median(acc[(times >= left) & (times <= left + margin)], axis=0)
    end_acc = np.median(acc[(times >= right - margin) & (times <= right)], axis=0)
    posture_change = float(np.linalg.norm(end_acc - start_acc))
    pause_acc_range = float(np.linalg.norm(np.ptp(acc[inside], axis=0)))
    if (
        quiet_speed > config.max_pause_gyro_dps
        or rotation > config.max_pause_rotation_deg
        or max(posture_change, pause_acc_range) > config.max_pause_acc_change_mg
    ):
        return None
    shape_fit = {
        stream: _correlation(
            _boundary_template(times, values, previous["cycles"][-3:]),
            _boundary_template(times, values, following["cycles"][:3]),
        )
        for stream, values in (("acc", acc), ("gyro", gyro))
    }
    if min(shape_fit.values()) < config.min_resume_shape_similarity:
        return None
    return {
        "start_time_s": left,
        "end_time_s": right,
        "duration_s": duration,
        "gyro_p95_dps": quiet_speed,
        "integrated_rotation_deg": rotation,
        "posture_change_mg": posture_change,
        "acc_range_mg": pause_acc_range,
        "axis_similarity": axis_fit,
        "shape_similarity": shape_fit,
        "cadence_ratio": cadence_ratio,
    }


def _group_bouts(
    times: np.ndarray, acc: np.ndarray, gyro: np.ndarray, bouts: list[dict], config: CounterConfig
) -> list[dict]:
    # Called within one intact overlap only. Never merge across missing samples,
    # relax the full-cycle gates, or rescue subthreshold chains through grouping.
    groups: list[tuple[list[dict], list[dict]]] = []
    for bout in bouts:
        pause = (
            _pause_evidence(times, acc, gyro, groups[-1][0][-1], bout, config) if groups else None
        )
        if pause is None:
            groups.append(([bout], []))
        else:
            groups[-1][0].append(bout)
            groups[-1][1].append(pause)
    result = []
    for blocks, pauses in groups:
        cycles = [
            {**cycle, "block_id": f"{index:03d}"}
            for index, block in enumerate(blocks, 1)
            for cycle in block["cycles"]
        ]
        result.append(
            {
                "start_time_s": blocks[0]["start_time_s"],
                "end_time_s": blocks[-1]["end_time_s"],
                "complete_cycles": len(cycles),
                "activity": "unclassified_repetitive_motion",
                "shape_similarity": float(
                    np.mean([(c["acc_similarity"] + c["gyro_similarity"]) / 2 for c in cycles])
                ),
                "motion_blocks": [
                    {
                        "block_id": f"{index:03d}",
                        **{k: v for k, v in block.items() if k != "cycles"},
                    }
                    for index, block in enumerate(blocks, 1)
                ],
                "pauses": pauses,
                "pause_duration_s": sum(p["duration_s"] for p in pauses),
                "cycles": cycles,
            }
        )
    return result


def _parts(times: np.ndarray, values: np.ndarray) -> tuple[list[slice], list[dict]]:
    if times.ndim != 1 or values.shape != (len(times), 3) or len(times) < 2:
        raise ValueError("Each IMU stream needs at least two timestamped XYZ samples")
    if not np.isfinite(times).all() or not np.isfinite(values).all():
        raise ValueError("Non-finite sample values or timestamps cannot be counted")
    intervals = np.diff(times)
    if np.any(intervals <= 0):
        raise ValueError("Duplicate/backward timestamps: fix the source before counting")
    typical = float(np.median(intervals))
    if typical > 0.05:
        raise ValueError("This counter requires IMU sampling at 20 Hz or higher")
    cuts = np.flatnonzero(intervals > 1.5 * typical) + 1
    bounds = [0, *cuts.tolist(), len(times)]
    parts = [slice(a, b) for a, b in zip(bounds, bounds[1:], strict=False) if b - a >= 2]
    gaps = [{"start_time_s": float(times[i - 1]), "end_time_s": float(times[i])} for i in cuts]
    return parts, gaps


def detect_sets(
    acc_times: np.ndarray,
    acc: np.ndarray,
    gyro_times: np.ndarray,
    gyro: np.ndarray,
    config: CounterConfig | None = None,
) -> dict:
    """Scan all intact overlap, rejecting incomplete cycles and splitting at gaps."""
    config = config or CounterConfig()
    ap, ag = _parts(acc_times, acc)
    gp, gg = _parts(gyro_times, gyro)
    result = {
        "algorithm": "periodic-return-v2",
        "settings": asdict(config),
        "uses_manual_labels": False,
        "sets": [],
        "segments": [],
        "gaps": [{"stream": "acc", **g} for g in ag] + [{"stream": "gyro", **g} for g in gg],
    }
    i = j = 0
    while i < len(ap) and j < len(gp):
        at, gt = acc_times[ap[i]], gyro_times[gp[j]]
        left, right = max(at[0], gt[0]), min(at[-1], gt[-1])
        if left < right:
            grid = np.arange(left, right, 1 / config.grid_hz)
            eligible = len(grid) >= round(config.window_s * config.grid_hz)
            result["segments"].append(
                {"start_time_s": float(left), "end_time_s": float(right), "evaluated": eligible}
            )
            if eligible:
                filtered_acc = _antialias(at, acc[ap[i]], config)
                filtered_gyro = _antialias(gt, gyro[gp[j]], config)
                av = np.column_stack(
                    [np.interp(grid, at, filtered_acc[:, axis]) for axis in range(3)]
                )
                gv = np.column_stack(
                    [np.interp(grid, gt, filtered_gyro[:, axis]) for axis in range(3)]
                )
                result["sets"].extend(_detect_block(grid, av, gv, config))
        if at[-1] <= gt[-1]:
            i += 1
        else:
            j += 1
    result["sets"].sort(key=lambda b: b["start_time_s"])
    for index, bout in enumerate(result["sets"], 1):
        bout["set_id"] = f"{index:03d}"
        segment = next(
            s
            for s in result["segments"]
            if s["start_time_s"] <= bout["start_time_s"] < bout["end_time_s"] <= s["end_time_s"]
        )
        bout["near_recording_or_gap_edge"] = (
            bout["start_time_s"] - segment["start_time_s"] < bout["motion_blocks"][0]["period_s"]
            or segment["end_time_s"] - bout["end_time_s"] < bout["motion_blocks"][-1]["period_s"]
        )
    result["total_complete_cycles"] = sum(s["complete_cycles"] for s in result["sets"])
    return result
