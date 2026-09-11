"""Separate counting adapters; never accept a reference repetition total."""

import numpy as np
from scipy.signal import find_peaks, savgol_filter

from ..counter import CounterConfig, _correlation, _detect_block
from ..motion_quality import arm_excursions
from .signal import validate_block


def existing_counter(t, acc, gyro, start, end, activity):
    validate_block(t, acc, gyro)
    if activity == "pull-up":
        events = arm_excursions(t, acc, start, end, gyro)
        return {
            "method": "legacy-arm-excursions",
            "count": len(events),
            "events": events,
            "status": "attempt_estimate",
            "uses_supplied_class": True,
        }
    if activity == "jump":
        # The old jump branch requires native impacts and a learned pairing convention.
        return {
            "method": "legacy-jump",
            "count": None,
            "events": [],
            "status": "unsupported_without_native_impact_adapter",
        }
    mask = (t >= start - 2) & (t <= end + 2)
    if mask.sum() < 200:
        return {
            "method": "legacy-periodic-return",
            "count": None,
            "events": [],
            "status": "insufficient_eight_second_context",
        }
    bouts = _detect_block(t[mask], acc[mask], gyro[mask], CounterConfig())
    events = [
        c
        for b in bouts
        for c in b["cycles"]
        if start <= (c["start_time_s"] + c["end_time_s"]) / 2 <= end
    ]
    return {
        "method": "legacy-periodic-return",
        "count": len(events),
        "events": events,
        "status": "motion_cycles",
        "context_s": 2,
        "adapter": "Unchanged detector on padded interval; midpoint selects interval events",
    }


def mmfit_counter(t, acc, gyro, start, end, period_bounds=(0.5, 6.0)):
    """MM-Fit section 4.4 adaptation: gyro, SG, PCA, peaks, local ACF suppression.

    Paper does not specify SG width, PCA sign or ACF support. Fixed here: 0.44 s,
    first substantial projected deflection positive, at least 0.5 s lag overlap.
    This counts peaks in a supplied segment, not anatomically verified full reps.
    """
    validate_block(t, acc, gyro)
    idx = np.flatnonzero((t >= start) & (t <= end))
    result = {
        "method": "mmfit-inspired-gyro-v1",
        "count": None,
        "events": [],
        "candidate_peaks": [],
        "status": "insufficient_data",
        "period_bounds_s": list(period_bounds),
    }
    if len(idx) < 7:
        return result
    tt, values = t[idx], gyro[idx]
    rate = 1 / float(np.median(np.diff(tt)))
    spread = values.std(0)
    if np.linalg.norm(spread) < 8:
        return {**result, "count": 0, "status": "flat_or_quiet"}
    standard = (values - values.mean(0)) / np.maximum(spread, 1)
    width = min(round(0.44 * rate) // 2 * 2 + 1, len(tt) // 2 * 2 - 1)
    width = max(5, width)
    smooth = savgol_filter(standard, width, 3, axis=0)
    _, strength, axes = np.linalg.svd(smooth, full_matrices=False)
    signal = smooth @ axes[0]
    first = np.flatnonzero(abs(signal) >= 0.3 * np.max(abs(signal)))
    if len(first) and signal[first[0]] < 0:
        signal = -signal
    peaks = find_peaks(signal)[0]
    records = {
        int(p): {
            "time_s": float(tt[p]),
            "amplitude": float(signal[p]),
            "rejection_reasons": [],
            "period_s": None,
        }
        for p in peaks
    }
    keep = []
    for p in sorted(peaks, key=lambda p: (-signal[p], p)):
        if any(abs(tt[p] - tt[q]) < period_bounds[0] for q in keep):
            records[int(p)]["rejection_reasons"].append("minimum_duration_suppression")
        else:
            keep.append(int(p))
    selected = []
    for p in sorted(keep, key=lambda p: (-signal[p], p)):
        if records[p]["rejection_reasons"]:
            continue
        context = signal[
            max(0, p - round(period_bounds[1] * rate)) : min(
                len(signal), p + round(period_bounds[1] * rate) + 1
            )
        ]
        lower = max(2, round(period_bounds[0] * rate))
        upper = min(round(period_bounds[1] * rate), len(context) - max(5, round(0.5 * rate)))
        if upper < lower:
            records[p]["rejection_reasons"].append("insufficient_autocorrelation_support")
            continue
        lags = np.arange(lower, upper + 1)
        correlations = [_correlation(context[:-lag], context[lag:]) for lag in lags]
        best = int(np.argmax(correlations))
        period = float(lags[best] / rate)
        records[p].update(period_s=period, autocorrelation=float(correlations[best]))
        selected.append(p)
        for q in keep:
            if q != p and signal[q] <= signal[p] and abs(tt[q] - tt[p]) < 0.75 * period:
                if q not in selected:
                    records[q]["rejection_reasons"].append("local_period_suppression")
    if selected:
        screen = float(0.5 * np.percentile(signal[selected], 40))
        for p in selected:
            if signal[p] < screen:
                records[p]["rejection_reasons"].append("amplitude_screen")
        result["amplitude_screen"] = screen
    retained = [p for p in sorted(selected) if not records[p]["rejection_reasons"]]
    result.update(
        count=len(retained) if selected else None,
        status="peak_estimate" if selected else "insufficient_periodicity_support",
        events=[
            {"peak_time_s": float(tt[p]), "period_s": records[p]["period_s"]} for p in retained
        ],
        candidate_peaks=list(records.values()),
        signal={"time_s": tt.tolist(), "projected_gyro": signal.tolist()},
        smoothing_samples=width,
        principal_fraction=float(strength[0] ** 2 / np.sum(strength**2)),
        edge_policy="No extrapolation; boundary peaks are unobserved, not invented",
    )
    return result
