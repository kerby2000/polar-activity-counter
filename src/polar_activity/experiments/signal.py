"""Auditable representations and context baselines. No labels or counts are read."""

from dataclasses import asdict, dataclass

import numpy as np

from ..counter import CounterConfig, _antialias, _smooth
from ..recognition import features


def validate_block(t, acc, gyro):
    if len(t) < 3 or acc.shape != (len(t), 3) or gyro.shape != acc.shape:
        raise ValueError("Need at least three paired XYZ samples")
    if not all(np.isfinite(v).all() for v in (t, acc, gyro)) or np.any(np.diff(t) <= 0):
        raise ValueError("Non-finite data or non-increasing timestamps")
    if np.max(np.diff(t)) > 1.5 * np.median(np.diff(t)):
        raise ValueError("Split source gaps before processing an interval")


def feature_vector(acc, gyro, version):
    original = features(acc, gyro)
    if version == "frozen":
        return original
    if version == "no_mean_xyz":
        return original[3:]
    if version == "invariant6":
        return original[9:]
    if version != "gravity_relative_v1":
        raise ValueError("Unknown feature representation")
    gravity = acc.mean(0)
    gravity /= max(np.linalg.norm(gravity), 1e-9)
    ap, gp = acc @ gravity, gyro @ gravity
    at = np.sqrt(np.maximum(0, np.sum(acc * acc, axis=1) - ap * ap))
    gt = np.sqrt(np.maximum(0, np.sum(gyro * gyro, axis=1) - gp * gp))
    return np.r_[
        original[9:],
        np.linalg.norm(acc.mean(0)) / 500,
        ap.std() / 400,
        np.sqrt(np.mean(at * at)) / 400,
        gp.mean() / 80,
        gp.std() / 80,
        gt.mean() / 80,
        gt.std() / 80,
    ]


def invariant_sequence(acc, gyro, scales):
    gravity = acc.mean(0)
    gravity /= max(np.linalg.norm(gravity), 1e-9)
    ap, gp = acc @ gravity, gyro @ gravity
    an, gn = np.linalg.norm(acc, axis=1), np.linalg.norm(gyro, axis=1)
    return np.column_stack(
        [
            an / scales[0],
            ap / scales[0],
            np.sqrt(np.maximum(0, an * an - ap * ap)) / scales[0],
            gn / scales[1],
            gp / scales[1],
            np.sqrt(np.maximum(0, gn * gn - gp * gp)) / scales[1],
        ]
    )


def represent(acc, gyro, scales, version, points=64):
    """One spatial transform shared by both modalities; ACC retains gravity.

    Input has already been antialias-filtered to 25 Hz. Interpolation to a common
    cycle grid is a shape representation, not a replacement for physical duration.
    """
    values = np.column_stack([acc / scales[0], gyro / scales[1]])
    centered = [v - v.mean(0) for v in (values[:, :3], values[:, 3:])]
    covariance = sum(v.T @ v for v in centered) / len(values)
    eigenvalues, frame = np.linalg.eigh(covariance)
    order = np.argsort(eigenvalues)[::-1]
    eigenvalues, frame = eigenvalues[order], frame[:, order]
    if np.linalg.det(frame) < 0:
        frame[:, -1] *= -1
    separations = (eigenvalues[:-1] - eigenvalues[1:]) / max(eigenvalues.sum(), 1e-9)
    stable = bool(separations[0] >= 0.05 and separations[1] >= 0.01)
    if version == "raw_axes_v1":
        transformed = values
    elif version == "joint_pca_v1":
        transformed = np.column_stack([values[:, :3] @ frame, values[:, 3:] @ frame])
    else:
        raise ValueError("Unknown cycle representation")

    def grid(x):
        if len(x) > points:
            # Additional antialiasing when a long cycle's 64-point grid is below
            # the common 25 Hz grid. No samples outside the candidate are invented.
            effective_rate = 25 * (points - 1) / (len(x) - 1)
            x = _antialias(np.arange(len(x)) / 25, x, CounterConfig(grid_hz=effective_rate))
        return np.column_stack(
            [np.interp(np.linspace(0, 1, points), np.linspace(0, 1, len(x)), axis) for axis in x.T]
        )

    return {
        "values": grid(transformed),
        "fallback": grid(invariant_sequence(acc, gyro, scales)),
        "stable_frame": stable,
        "eigenvalues": eigenvalues.tolist(),
        "eigenvalue_separations": separations.tolist(),
        "frame": frame.tolist(),
        "amplitudes": [float(np.linalg.norm(acc.std(0))), float(np.linalg.norm(gyro.std(0)))],
    }


@dataclass(frozen=True)
class BaselineConfig:
    window_s: float = 0.8
    max_gyro_mean_dps: float = 40.0
    max_acc_norm_std_mg: float = 80.0
    max_direction_spread_deg: float = 15.0
    ambiguity_angle_deg: float = 45.0
    ambiguity_gyro_ratio: float = 1.10


def stable_excursions(t, acc, gyro, start, end, config=None, *, event_end=None):
    """Stable-context ablation, independent of the legacy pre-boundary baseline.

    Normalised vector mean is rotation-equivariant; componentwise vector medians
    need not be. Quietest qualifying region is a motion proxy, not anatomical pose.
    """
    config = config or BaselineConfig()
    validate_block(t, acc, gyro)
    rate = 1 / float(np.median(np.diff(t)))
    width = max(3, round(0.44 * rate) // 2 * 2 + 1)
    smooth = _smooth(acc, width)
    direction = smooth / np.maximum(np.linalg.norm(smooth, axis=1)[:, None], 1)
    scan_end = end + 2 if event_end is None else max(end + 2, event_end)
    event_region = np.flatnonzero((t >= start) & (t <= scan_end))
    magnitude, speed = np.linalg.norm(acc, axis=1), np.linalg.norm(gyro, axis=1)
    shock = (magnitude < 450) | ((magnitude > 1800) & (speed > 250))
    found = event_region[shock[event_region]]
    cutoff = float(t[found[0]] - (width // 2 + 1) / rate) if len(found) else None
    if cutoff is not None:
        event_region = event_region[t[event_region] < cutoff]
    # Extra event context must not select a new resting pose after the set.
    region = event_region[t[event_region] <= end + 2]
    candidates = []
    size = max(3, round(config.window_s * rate))
    for first in region:
        last = first + size
        if last > len(t) or not len(region) or last - 1 > region[-1]:
            break
        pose = direction[first:last].mean(0)
        pose /= max(np.linalg.norm(pose), 1e-9)
        spread = float(np.degrees(np.arccos(np.clip(direction[first:last] @ pose, -1, 1))).max())
        mean_speed = float(speed[first:last].mean())
        norm_std = float(magnitude[first:last].std())
        eligible = (
            mean_speed <= config.max_gyro_mean_dps
            and norm_std <= config.max_acc_norm_std_mg
            and spread <= config.max_direction_spread_deg
            and 800 <= magnitude[first:last].mean() <= 1200
        )
        candidates.append(
            {
                "start_time_s": float(t[first]),
                "end_time_s": float(t[last - 1]),
                "gyro_mean_dps": mean_speed,
                "acc_norm_std_mg": norm_std,
                "direction_spread_deg": spread,
                "eligible": bool(eligible),
                "direction": pose.tolist(),
            }
        )
    valid = sorted(
        (c for c in candidates if c["eligible"]),
        key=lambda c: (c["gyro_mean_dps"], c["start_time_s"]),
    )
    result = {
        "method": "stable-context-excursions-v1",
        "settings": asdict(config),
        "baseline": valid[0] if valid else None,
        "baseline_alternatives": valid[:5],
        "baseline_windows_evaluated": len(candidates),
        "cutoff_time_s": cutoff,
        "events": [],
        "rejected_events": [],
        "rejection_reasons": [],
    }
    if not valid:
        result["rejection_reasons"] = ["no_stable_baseline"]
        return result
    baseline = np.array(valid[0]["direction"])
    ambiguous = [
        c
        for c in valid[1:]
        if c["gyro_mean_dps"] <= max(1, valid[0]["gyro_mean_dps"]) * config.ambiguity_gyro_ratio
        and np.degrees(np.arccos(np.clip(np.dot(baseline, c["direction"]), -1, 1)))
        > config.ambiguity_angle_deg
    ]
    if ambiguous:
        result["rejection_reasons"] = ["ambiguous_stable_poses"]
        return result
    region = event_region
    depth = 1 - direction @ baseline
    angle = np.degrees(np.arccos(np.clip(direction @ baseline, -1, 1)))

    def append(first, last, returned):
        peak = first + int(np.argmax(angle[first : last + 1]))
        duration = float(t[last] - t[first])
        strong = depth[peak] >= 0.5
        established = sum(e["peak_departure_deg"] >= 60 for e in result["events"]) >= 2
        reasons = []
        if not 0.8 <= duration <= 8:
            reasons.append("threshold_occupancy_duration")
        if not strong and not (established and angle[peak] >= 45):
            reasons.append("insufficient_departure")
        event = {
            "start_time_s": float(t[first]),
            "end_time_s": float(t[last]),
            "peak_time_s": float(t[peak]),
            "peak_departure_deg": float(angle[peak]),
            "threshold_duration_s": duration,
            "return_observed": returned,
            "ended_at_impact_cutoff": not returned and cutoff is not None,
            "rejection_reasons": reasons,
        }
        result["rejected_events" if reasons else "events"].append(event)

    opened = None
    for i in region:
        if opened is None and depth[i] >= 0.25:
            opened = int(i)
        elif opened is not None and depth[i] <= 0.20:
            append(opened, int(i), True)
            opened = None
    if opened is not None:
        append(opened, int(region[-1]), False)
    result["signal"] = {"time_s": t[region].tolist(), "departure_deg": angle[region].tolist()}
    return result
