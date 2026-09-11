"""Short-set behavior, physical boundaries and reference/inference separation."""

import json

import numpy as np
import pytest
from test_counter import motion, paused_motion
from test_motion_quality import arm_set
from test_recognition import labelled_fixture

from polar_activity.adaptive import (
    analyse_arrays,
    analyse_session,
    candidate_intervals,
    fit_model,
    validate_model,
)
from polar_activity.adaptive_counter import short_return_bouts
from polar_activity.adaptive_pullups import pullup_bouts
from polar_activity.cli import main
from polar_activity.recognition import motion_blocks


def model_fixture(tmp_path):
    labelled_fixture(tmp_path / "reference", motion(((5, 8, 1.5),), duration=30))
    spec = {
        "subject": "test",
        "sensor_position": "upper_arm_left",
        "references": [
            {
                "session": "reference",
                "activity": "push-up",
                "start_time_s": 5,
                "end_time_s": 17,
            }
        ],
    }
    refs, output = tmp_path / "references.json", tmp_path / "adaptive.json"
    refs.write_text(json.dumps(spec))
    return refs, output, fit_model(refs, output)


@pytest.mark.parametrize("count,period", [(2, 1.5), (3, 3.0), (5, 1.4), (10, 2.2)])
def test_two_cycle_seeds_and_longer_scale_preserve_complete_returns(count, period):
    block = motion_blocks(motion(((5, count, period),), duration=count * period + 12))[0]
    bouts, _ = short_return_bouts(*block)
    assert [len(s["cycles"]) for s in bouts] == [count]
    assert bouts[0]["start_time_s"] == pytest.approx(5, abs=0.3)
    assert bouts[0]["end_time_s"] == pytest.approx(5 + count * period, abs=0.3)


def test_short_pause_is_not_a_repetition():
    bouts, _ = short_return_bouts(*motion_blocks(paused_motion(2))[0])
    assert len(bouts) == 1
    assert bouts[0]["complete_cycles"] == 14
    assert len(bouts[0]["pauses"]) == 1


@pytest.mark.parametrize("kind", ["single", "one_direction", "noise", "drift", "partial"])
def test_incomplete_and_nonreturn_motion_is_not_promoted(kind):
    data = motion(
        ((5, 5.4 if kind == "partial" else 1 if kind == "single" else 5, 1.5),), duration=24
    )
    t, acc, gt, gyro = data
    if kind == "one_direction":
        gyro[:, 1] = abs(gyro[:, 1])
    if kind == "noise":
        rng = np.random.default_rng(31)
        acc[:] = rng.normal(0, 350, acc.shape) + [0, 0, 1000]
        gyro[:] = rng.normal(0, 100, gyro.shape)
    if kind == "drift":
        acc[:] = np.c_[40 * t, np.zeros(len(t)), np.full(len(t), 1000)]
        gyro[:] = np.c_[np.zeros(len(t)), 4 * t, np.zeros(len(t))]
    bouts, _ = short_return_bouts(*motion_blocks((t, acc, gt, gyro))[0])
    assert sum(len(b["cycles"]) for b in bouts) == (5 if kind == "partial" else 0)


def test_raised_posture_at_candidate_edge_is_not_an_observed_departure():
    t, a, g = arm_set()
    # Enter the candidate halfway through its first excursion, without an
    # observed origin in the available recording. The other returns remain.
    m = (t >= 6) & (t < 20)
    bouts, trace = pullup_bouts(t[m], a[m], g[m], 6, 19)
    assert trace["boundary_rejections"]
    assert trace["boundary_rejections"][0]["rejection_reasons"] == [
        "no_observed_origin_before_departure"
    ]
    complete = [c for b in bouts for c in b["cycles"] if c["return_observed"]]
    assert len(complete) == 2
    assert min(c["start_time_s"] for c in complete) > 8


def test_pullup_dismount_and_partial_attempt_remain_distinct():
    t, a, g = arm_set()
    # This attempt is still rising at dismount, rather than a long static hold
    # that would create two equally quiet, incompatible baseline candidates.
    partial = (t >= 20) & (t < 24)
    angle = 52 * (1 - np.cos(np.pi * (t[partial] - 20) / 4)) / 2
    a[partial] = (
        1000 * np.c_[np.sin(np.radians(angle)), np.cos(np.radians(angle)), np.zeros(len(angle))]
    )
    g[partial, 2] = -np.gradient(angle, t[partial])
    bouts, trace = pullup_bouts(t, a, g, 4, 24)
    events = [e for b in bouts for e in b["cycles"]] + trace["isolated_events"]
    assert sum(e["return_observed"] for e in events) == 3
    assert sum(not e["return_observed"] for e in events) == 1
    assert all(e["end_time_s"] < 24 for e in events)


def test_two_equally_quiet_incompatible_poses_remain_ambiguous():
    t, a, g = arm_set()
    bouts, trace = pullup_bouts(t, a, g, 4, 24)
    assert not bouts
    assert trace["rejection_reasons"] == ["ambiguous_stable_poses"]


@pytest.mark.parametrize(
    "case", ["returned", "dismount", "weak", "different_plane", "late", "single"]
)
def test_established_pullup_continuation_requires_matching_bounded_motion(case):
    t = np.arange(0, 28, 0.04)
    angle = np.zeros(len(t))
    for start in [9] if case == "single" else [4, 9]:
        m = (t >= start) & (t < start + 3.5)
        angle[m] = 90 * (1 - np.cos(2 * np.pi * (t[m] - start) / 3.5)) / 2
    start = 22 if case == "late" else 16
    m = (t >= start) & (t < start + 4.8)
    amplitude = 50 if case == "weak" else 82
    angle[m] = amplitude * (1 - np.cos(2 * np.pi * (t[m] - start) / 4.8)) / 2
    norm = np.full(len(t), 1000.0)
    if case == "dismount":
        m = (t >= 15.2) & (t < 20.5)
        angle[m] = 82 * (1 - np.cos(np.pi * (t[m] - 15.2) / 5.3)) / 2
        norm[(t >= 20.5) & (t < 20.7)] = 300
        angle[t >= 20.5] = 150  # this impact/posture must not count as a return
    rad = np.radians(angle)
    a = norm[:, None] * np.c_[np.sin(rad), np.cos(rad), np.zeros(len(t))]
    g = np.c_[np.zeros(len(t)), np.zeros(len(t)), -np.gradient(angle, t)]
    if case == "different_plane":
        a[t >= 16] = a[t >= 16][:, [2, 1, 0]]
        g[t >= 16] = g[t >= 16][:, [2, 1, 0]]
    bouts, trace = pullup_bouts(t, a, g, 4, 13.2)
    events = [e for b in bouts for e in b["cycles"]]
    assert len(events) == (3 if case in ("returned", "dismount") else 0 if case == "single" else 2)
    if case in ("returned", "dismount"):
        assert events[-1]["continuation_after_classifier_boundary"]
        assert events[-1]["return_observed"] == (case == "returned")
        assert trace["continuation"]["evidence"]["baseline"] == trace["baseline"]
        assert events[-1]["end_time_s"] < 20.5 if case == "dismount" else True
        rotation, _ = np.linalg.qr(np.random.default_rng(15).normal(size=(3, 3)))
        rotated, _ = pullup_bouts(t, a @ rotation, g @ rotation, 4, 13.2)
        assert [e["start_time_s"] for b in rotated for e in b["cycles"]] == [
            e["start_time_s"] for e in events
        ]


def test_two_seconds_of_feature_support_is_not_lost_to_float_rounding():
    rows = [
        {
            "activity": "push-up",
            "block_id": 0,
            "start_time_s": 7.505832518999999 + i,
            "end_time_s": 8.505832518999999 + i,
        }
        for i in range(2)
    ]
    config = {"max_class_gap_s": 3, "minimum_class_seconds": 2}
    candidates = candidate_intervals(rows, config)
    assert candidates[0]["class_support_s"] == 2
    assert not candidates[0]["rejection_reasons"]


def test_model_and_inference_ignore_counts_labels_notes_and_target_name(tmp_path):
    refs, output, model = model_fixture(tmp_path)
    spec = json.loads(refs.read_text())
    spec["references"][0]["expected_rep_count"] = 9876
    spec["references"][0]["notes"] = "Count exactly 9876"
    refs.write_text(json.dumps(spec))
    assert fit_model(refs, output) == model
    target = tmp_path / "unknown"
    labelled_fixture(target, motion(((7, 3, 1.5),), duration=22))
    (target / "labels.csv").write_text("bad labels; no peeking")
    (target / "label_events.jsonl").write_text("not json")
    before = {p.name: p.read_bytes() for p in target.iterdir()}
    original = analyse_session(target, output, plot=False)
    renamed = tmp_path / "definitely-not-pushups"
    target.rename(renamed)
    changed = analyse_session(renamed, output, plot=False)
    for key in ("sets", "windows", "candidate_intervals", "unassigned_attempts"):
        assert original[key] == changed[key]
    assert original["sets"][0]["rep_estimate"] == 3
    assert all((renamed / name).read_bytes() == value for name, value in before.items())


def test_rotation_changes_coordinates_not_activity_or_short_counts(tmp_path):
    _, _, model = model_fixture(tmp_path)
    data = motion(((7, 3, 1.5),), duration=23)
    original = analyse_arrays(data, model)
    q, _ = np.linalg.qr(np.random.default_rng(78).normal(size=(3, 3)))
    q[:, -1] *= np.linalg.det(q)
    t, a, gt, g = data
    rotated = analyse_arrays((t, a @ q, gt, g @ q), model)
    assert [w["activity"] for w in original["windows"]] == [
        w["activity"] for w in rotated["windows"]
    ]
    assert (
        [s["rep_estimate"] for s in original["sets"]]
        == [s["rep_estimate"] for s in rotated["sets"]]
        == [3]
    )
    assert [(s["start_time_s"], s["end_time_s"]) for s in original["sets"]] == [
        (s["start_time_s"], s["end_time_s"]) for s in rotated["sets"]
    ]


def test_missing_data_splits_classification_and_counting(tmp_path):
    _, _, model = model_fixture(tmp_path)
    t, a, gt, g = motion(((5, 12, 1.5),), duration=30)
    mask = (gt < 13) | (gt > 14)
    result = analyse_arrays((t, a, gt[mask], g[mask]), model)
    assert len(result["coverage"]) == 2
    assert not any(
        c["start_time_s"] < 14 and c["end_time_s"] > 13 for s in result["sets"] for c in s["cycles"]
    )


def test_cli_routes_adaptive_model_and_saves_separate_outputs(tmp_path, capsys):
    refs, output, _ = model_fixture(tmp_path)
    assert main(["train", str(refs), "--engine", "adaptive", "--output", str(output)]) == 0
    target = tmp_path / "target"
    labelled_fixture(target, motion(((7, 3, 1.5),), duration=22))
    assert (
        main(["analyze", str(target), "--engine", "adaptive", "--model", str(output), "--no-plot"])
        == 0
    )
    assert "3 estimated repetitions" in capsys.readouterr().out
    assert (target / "analysis-adaptive/unassigned_attempts.csv").exists()
    assert not (target / "analysis").exists()


@pytest.mark.parametrize("bad", ["features", "version", "convention", "config"])
def test_incompatible_adaptive_models_are_rejected(tmp_path, bad):
    _, _, model = model_fixture(tmp_path)
    if bad == "features":
        model["examples"][0]["features"][0] = float("nan")
    elif bad == "version":
        model["version"] = "future"
    elif bad == "convention":
        model["jump_count_convention"] = "guess"
    else:
        model["edge_context_s"] = 300
    with pytest.raises(ValueError):
        validate_model(model)
