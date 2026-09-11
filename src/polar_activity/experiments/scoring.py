"""Post-prediction scoring. Unlabelled time never becomes confirmed background."""

import numpy as np
from scipy.optimize import linear_sum_assignment


def overlap(a, b):
    return max(0, min(a["end_time_s"], b["end_time_s"]) - max(a["start_time_s"], b["start_time_s"]))


def count_error(predicted, count_range):
    if count_range is None or predicted is None:
        return None
    low, high = count_range
    return max(low - predicted, predicted - high, 0)


def score_sets(predictions, references, background, count_key="count", minimum_iou=0.2):
    """One-to-one maximum-overlap matching before examining activity/count agreement."""
    matrix = np.zeros((len(references), len(predictions)))
    for i, ref in enumerate(references):
        for j, pred in enumerate(predictions):
            intersection = overlap(ref, pred)
            union = max(ref["end_time_s"], pred["end_time_s"]) - min(
                ref["start_time_s"], pred["start_time_s"]
            )
            iou = intersection / union if union else 0
            matrix[i, j] = iou if iou >= minimum_iou else 0
    pairs = {}
    if matrix.size:
        rows, columns = linear_sum_assignment(-matrix)
        pairs = {int(i): int(j) for i, j in zip(rows, columns, strict=True) if matrix[i, j] > 0}
    events = []
    for i, ref in enumerate(references):
        pred = predictions[pairs[i]] if i in pairs else None
        correct = pred is not None and pred["activity"] == ref["activity"]
        predicted_count = pred.get(count_key) if pred else None
        credited = predicted_count if correct and predicted_count is not None else 0
        expected = ref.get("count_range")
        error = count_error(credited, expected)
        conditional = count_error(predicted_count, expected) if correct else None
        events.append(
            {
                "reference_id": ref["id"],
                "activity": ref["activity"],
                "prediction_index": pairs.get(i),
                "detected": pred is not None,
                "predicted_activity": pred["activity"] if pred else "missed",
                "correct_activity": correct,
                "predicted_count": predicted_count,
                "count_abstained": pred is not None and predicted_count is None,
                "count_range": expected,
                "credited_reps": credited,
                "end_to_end_absolute_error": error,
                "conditional_absolute_error": conditional,
                "exact_count": credited == expected[0]
                if expected and expected[0] == expected[1]
                else None,
                "within_one": error <= 1 if error is not None else None,
                "annotation_provenance": ref.get("provenance"),
                "timing_uncertainty_s": ref.get("timing_uncertainty_s"),
                "iou": float(matrix[i, pairs[i]]) if pred else 0,
            }
        )
    unmatched = [j for j in range(len(predictions)) if j not in pairs.values()]
    wrong = [pairs[i] for i, e in enumerate(events) if e["detected"] and not e["correct_activity"]]
    background_seconds = sum(b["end_time_s"] - b["start_time_s"] for b in background)
    # Count only predictions fully inside explicitly negative intervals; overlapping
    # boundaries remain visible separately rather than assigning all reps to background.
    confirmed_false = [
        j
        for j, p in enumerate(predictions)
        if any(
            b["start_time_s"] <= p["start_time_s"] and p["end_time_s"] <= b["end_time_s"]
            for b in background
        )
    ]
    boundary_false = [
        j
        for j, p in enumerate(predictions)
        if j not in confirmed_false and any(overlap(p, b) > 0 for b in background)
    ]
    false_reps = sum(predictions[j].get(count_key) or 0 for j in confirmed_false)
    return {
        "matching_policy": {
            "one_to_one": True,
            "minimum_iou": minimum_iou,
            "class_blind_assignment": True,
        },
        "reference_sets": len(references),
        "detected_sets": len(pairs),
        "correctly_named_sets": sum(e["correct_activity"] for e in events),
        "missed_sets": len(references) - len(pairs),
        "wrong_class_predictions": wrong,
        "unmatched_predictions": unmatched,
        "duplicate_or_fragment_predictions": [
            j for j in unmatched if any(overlap(predictions[j], r) > 0 for r in references)
        ],
        "unmatched_in_unlabelled_time": [
            j for j in unmatched if j not in confirmed_false and j not in boundary_false
        ],
        "background_seconds": background_seconds,
        "background_false_sets": len(confirmed_false),
        "background_false_reps": false_reps,
        "background_unknown_counts": sum(
            predictions[j].get(count_key) is None for j in confirmed_false
        ),
        "background_false_sets_per_hour": len(confirmed_false) * 3600 / background_seconds
        if background_seconds
        else None,
        "background_false_reps_per_hour": false_reps * 3600 / background_seconds
        if background_seconds
        else None,
        "predictions_crossing_background_boundary": boundary_false,
        "events": events,
    }


def rejection_funnel(result):
    candidates = result["candidates"]
    reasons = {}
    for c in candidates:
        reason = c["rejection_reasons"][0] if c["rejection_reasons"] else "retained_cycle"
        reasons[reason] = reasons.get(reason, 0) + 1
    return {
        "candidates": len(candidates),
        "first_rejection_reason": reasons,
        "accepted_sets": len(result["sets"]),
        "scan_complete": all(b["grid_complete"] for b in result["coverage"]),
    }


def failure_stages(result, references):
    rows = []
    for ref in references:
        covered = [
            c
            for c in result["candidates"]
            if overlap(c, ref) >= 0.5 * (c["end_time_s"] - c["start_time_s"])
        ]
        plausible = [c for c in covered if c["best"] is not None]
        named = [c for c in plausible if c["best"]["activity"] == ref["activity"]]
        accepted = [c for c in named if not c["rejection_reasons"]]
        reason = (
            "candidate_coverage"
            if not covered
            else "duration_support"
            if not plausible
            else "exercise_identity"
            if not named
            else "acceptance_or_overlap"
            if not accepted
            else "cycles_survive; inspect_set_grouping_and_counter"
        )
        rows.append(
            {
                "reference_id": ref["id"],
                "earliest_failure": reason,
                "covered_candidates": len(covered),
                "correct_best_class": len(named),
                "retained_cycles": len(accepted),
            }
        )
    return rows
