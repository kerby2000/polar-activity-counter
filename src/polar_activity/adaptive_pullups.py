"""Observed pull-up motion boundaries and rest grouping, after class inference."""

import numpy as np

from .counter import _smooth
from .experiments.signal import stable_excursions


def pullup_bouts(t, acc, gyro, start, end, max_pause_s=3.0):
    trace = stable_excursions(t, acc, gyro, start, end)
    trace.update(boundary_rejections=[], refined_events=[], isolated_events=[])
    if trace["baseline"] is None or trace["rejection_reasons"]:
        return [], trace
    rate = 1 / float(np.median(np.diff(t)))
    width = max(3, round(0.44 * rate) // 2 * 2 + 1)
    direction = _smooth(acc, width)
    direction /= np.maximum(np.linalg.norm(direction, axis=1)[:, None], 1)
    depth = 1 - direction @ np.asarray(trace["baseline"]["direction"])
    bounded_end = min(end + 2, trace["cutoff_time_s"] or t[-1])
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
