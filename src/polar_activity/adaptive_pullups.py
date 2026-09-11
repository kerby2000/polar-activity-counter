"""Observed pull-up motion boundaries and rest grouping, after class inference."""

import numpy as np

from .counter import _smooth
from .experiments.signal import stable_excursions


def pullup_bouts(t, acc, gyro, start, end, max_pause_s=3.0):
    bouts, trace = _bounded_bouts(t, acc, gyro, start, end, max_pause_s)
    if not bouts or trace["cutoff_time_s"] is not None:
        return bouts, trace
    last = bouts[-1]
    returned = [e for e in last["cycles"] if e["return_observed"]]
    if len(returned) < 2 or not last["cycles"][-1]["return_observed"]:
        return bouts, trace
    # A slower final effort can fall outside the activity classifier's window.
    # Only an established set earns bounded extra context, with its original pose.
    pause_limit = min(
        5.0,
        max(max_pause_s, 2 * np.median([e["end_time_s"] - e["start_time_s"] for e in returned])),
    )
    if end - last["end_time_s"] > pause_limit:
        return bouts, trace
    _, extended = _bounded_bouts(
        t, acc, gyro, start, end, max_pause_s, event_end=min(t[-1], end + 8)
    )
    peak_reference = float(np.median([e["peak_departure_deg"] for e in returned]))
    direction = _smooth(acc, max(3, round(0.44 / np.median(np.diff(t))) // 2 * 2 + 1))
    direction /= np.maximum(np.linalg.norm(direction, axis=1)[:, None], 1)
    peaks = direction[[np.searchsorted(t, e["peak_time_s"]) for e in returned]]
    peak_pose = peaks.mean(0)
    peak_pose /= max(np.linalg.norm(peak_pose), 1e-9)
    accepted, rejected = [], []
    for event in extended["refined_events"]:
        if event["start_time_s"] < last["end_time_s"] - 0.08:
            continue
        gap = event["start_time_s"] - last["end_time_s"]
        peak = direction[np.searchsorted(t, event["peak_time_s"])]
        pose_difference = float(np.degrees(np.arccos(np.clip(peak @ peak_pose, -1, 1))))
        if not 0 <= gap <= pause_limit:
            reason = "continuation_pause_limit"
        elif event["peak_departure_deg"] < 0.8 * peak_reference:
            reason = "continuation_excursion_too_small"
        elif pose_difference > 25:
            reason = "continuation_peak_pose_changed"
        else:
            reason = None
        if reason:
            rejected.append({**event, "rejection_reasons": [reason]})
            break
        event = {**event, "continuation_after_classifier_boundary": True}
        if gap > 0.2:
            last["pauses"].append(
                {"start_time_s": last["end_time_s"], "end_time_s": event["start_time_s"]}
            )
        last["cycles"].append(event)
        last["end_time_s"] = event["end_time_s"]
        accepted.append(event)
        if not event["return_observed"]:
            break
    trace["continuation"] = {
        "search_end_time_s": min(float(t[-1]), end + 8),
        "pause_limit_s": float(pause_limit),
        "min_relative_excursion": 0.8,
        "max_peak_pose_difference_deg": 25,
        "accepted_events": accepted,
        "rejected_events": rejected,
        "evidence": extended,
    }
    if accepted:
        starts = {e["start_time_s"] for e in accepted}
        trace["isolated_events"] = [
            e for e in trace["isolated_events"] if e["start_time_s"] not in starts
        ]
    return bouts, trace


def _bounded_bouts(t, acc, gyro, start, end, max_pause_s, *, event_end=None):
    trace = stable_excursions(t, acc, gyro, start, end, event_end=event_end)
    trace.update(boundary_rejections=[], refined_events=[], isolated_events=[])
    if trace["baseline"] is None or trace["rejection_reasons"]:
        return [], trace
    rate = 1 / float(np.median(np.diff(t)))
    width = max(3, round(0.44 * rate) // 2 * 2 + 1)
    direction = _smooth(acc, width)
    direction /= np.maximum(np.linalg.norm(direction, axis=1)[:, None], 1)
    depth = 1 - direction @ np.asarray(trace["baseline"]["direction"])
    bounded_end = min(event_end or end + 2, trace["cutoff_time_s"] or t[-1])
    refined, rejected = [], []
    for event in trace["events"]:
        first = int(np.searchsorted(t, event["start_time_s"]))
        last = int(np.searchsorted(t, event["end_time_s"]))
        before = np.flatnonzero(
            (t >= max(start - 2, t[first] - 3)) & (t < t[first]) & (depth <= 0.05)
        )
        # A return from a posture already above threshold is not an observed
        # outward-and-return cycle. Never invent its departure at a classifier edge.
        if not len(before):
            rejected.append({**event, "rejection_reasons": ["no_observed_origin_before_departure"]})
            continue
        after = np.flatnonzero(
            (t >= t[last]) & (t <= min(bounded_end, t[last] + 3)) & (depth <= 0.05)
        )
        returned = bool(event["return_observed"] and len(after))
        refined.append(
            {
                **event,
                "threshold_start_time_s": event["start_time_s"],
                "threshold_end_time_s": event["end_time_s"],
                "start_time_s": float(t[before[-1]]),
                "end_time_s": float(t[after[0]]) if returned else event["end_time_s"],
                "return_observed": returned,
                "boundary_status": "observed_near_baseline_return"
                if returned
                else "incomplete_return",
            }
        )
    groups = []
    for event in refined:
        gap = event["start_time_s"] - groups[-1][-1]["end_time_s"] if groups else None
        if gap is None or gap > max_pause_s or gap < -0.08:
            groups.append([])
        groups[-1].append(event)
    trace["boundary_rejections"] = rejected
    trace["refined_events"] = refined
    trace["isolated_events"] = [e for g in groups if len(g) < 2 for e in g]
    return [
        {
            "cycles": g,
            "start_time_s": g[0]["start_time_s"],
            "end_time_s": g[-1]["end_time_s"],
            "pauses": [
                {"start_time_s": a["end_time_s"], "end_time_s": b["start_time_s"]}
                for a, b in zip(g, g[1:], strict=False)
                if b["start_time_s"] - a["end_time_s"] > 0.2
            ],
        }
        for g in groups
        if len(g) >= 2
    ], trace
