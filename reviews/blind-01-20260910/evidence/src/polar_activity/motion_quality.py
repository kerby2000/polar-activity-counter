"""Within-set arm-motion comparisons, not anatomical form or safety scores."""

import numpy as np

from .counter import _smooth


def arm_excursions(t, acc, start, end, gyro=None) -> list[dict]:
    """Model-gated departures/returns with an impact cutoff for dismounts.

    A reduced departure after two large cycles may be an attempt. The number of
    expected repetitions and user form judgements are never inputs.
    """
    if len(t) < 3:
        return []
    rate = 1 / float(np.median(np.diff(t)))
    width = max(3, round(0.44 * rate) // 2 * 2 + 1)
    values = _smooth(acc, width)
    direction = values / np.maximum(np.linalg.norm(values, axis=1)[:, None], 1)
    before = (t >= start - 2) & (t < start)
    if np.count_nonzero(before) < round(0.8 * rate):
        return []
    baseline = np.median(direction[before], axis=0)
    baseline /= max(np.linalg.norm(baseline), 1e-9)
    depth = 1 - direction @ baseline
    angle = np.degrees(np.arccos(np.clip(direction @ baseline, -1, 1)))
    region = np.flatnonzero((t >= start) & (t <= end + 2))
    if not len(region):
        return []
    cutoff = None
    if gyro is not None:
        magnitude = np.linalg.norm(acc, axis=1)
        shock = (magnitude < 450) | ((magnitude > 1800) & (np.linalg.norm(gyro, axis=1) > 250))
        found = region[shock[region]]
        if len(found):
            # Keep the dismount/impact out of an unfinished repetition's range.
            # Symmetric smoothing sees future samples. Exclude its half-window
            # as well, so a landing cannot leak into the pre-dismount angle.
            cutoff = float(t[found[0]] - (width // 2 + 1) / rate)
            region = region[t[region] < cutoff]
    events = []

    def append(first, last, returned):
        if last <= first or not 0.8 <= t[last] - t[first] <= 8:
            return
        peak = first + int(np.argmax(angle[first : last + 1]))
        strong = depth[peak] >= 0.5
        established = sum(e["peak_departure_deg"] >= 60 for e in events) >= 2
        if not strong and not (established and angle[peak] >= 45):
            return
        events.append(
            {
                "start_time_s": float(t[first]),
                "end_time_s": float(t[last]),
                "return_observed": returned,
                "peak_time_s": float(t[peak]),
                "peak_departure_deg": float(angle[peak]),
                "end_departure_deg": float(angle[last]),
                "threshold_duration_s": float(t[last] - t[first]),
                "ended_at_impact_cutoff": bool(not returned and cutoff is not None),
            }
        )

    opened = None
    for index in region:
        if opened is None and depth[index] >= 0.25:
            opened = int(index)
        elif opened is not None and depth[index] <= 0.20:
            append(opened, int(index), True)
            opened = None
    if opened is not None and len(region):
        append(opened, int(region[-1]), False)
    return events


def compare_excursions(events: list[dict]) -> dict:
    returned = [e["peak_departure_deg"] for e in events if e["return_observed"]]
    reference = float(np.median(returned)) if len(returned) >= 2 else None
    repetitions = []
    for index, event in enumerate(events, 1):
        ratio = event["peak_departure_deg"] / reference if reference else None
        flags = []
        if ratio is not None and ratio < 0.75:
            flags.append("reduced_arm_excursion")
        if not event["return_observed"]:
            flags.append("return_not_observed")
        repetitions.append(
            {"repetition": index, **event, "relative_excursion": ratio, "flags": flags}
        )
    peaks = np.array([e["peak_time_s"] for e in events])
    return {
        "method": "within-set-arm-motion-v1",
        "assesses_technique": False,
        "reference": "Median peak departure of observed returned excursions in this set",
        "reference_departure_deg": reference,
        "peak_to_peak_intervals_s": np.diff(peaks).tolist(),
        "repetitions": repetitions,
        "limitations": [
            "Acceleration-direction departure is a sensor-motion proxy, "
            "not joint range or body height.",
            "Return means crossing a signal threshold, "
            "not verified elbow extension or chin clearance.",
            "A dismount impact truncates an open attempt; "
            "it cannot improve the attempt's measured range.",
            "No technique, kipping, injury-risk or safety score "
            "can be established from these comparisons.",
        ],
    }
