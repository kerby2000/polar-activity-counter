"""Reference/inference separation and physically independent event fixtures."""

import csv
import json

import numpy as np
import pytest
from test_counter import motion, save_fixture

from polar_activity.analyser import _jump_sets, analyse_session, jump_impacts, short_excursions
from polar_activity.cli import main
from polar_activity.recognition import (
    classify,
    fit_model,
    load_model,
    motion_blocks,
    predict_windows,
    source_hashes,
)


def labelled_fixture(path, data=None):
    save_fixture(path, motion() if data is None else data)
    metadata = json.loads((path / "metadata.json").read_text())
    metadata.update(subject="test", sensor_position="upper_arm_left")
    (path / "metadata.json").write_text(json.dumps(metadata))


def fit_fixture(tmp_path):
    session = tmp_path / "reference"
    labelled_fixture(session)
    spec = {
        "subject": "test",
        "sensor_position": "upper_arm_left",
        "references": [
            {"session": "reference", "activity": "push-up", "start_time_s": 5, "end_time_s": 17}
        ],
    }
    refs = tmp_path / "references.json"
    refs.write_text(json.dumps(spec))
    model_path = tmp_path / "models" / "personal.json"
    model = fit_model(refs, model_path)
    return session, refs, model_path, model


def test_training_provenance_and_expected_counts_not_fitted(tmp_path):
    session, refs, output, model = fit_fixture(tmp_path)
    assert model["sources"][0]["sha256"] == source_hashes(session)
    assert model["expected_counts_used_for_fitting"] is False
    spec = json.loads(refs.read_text())
    spec["references"][0]["expected_rep_count"] = 999
    refs.write_text(json.dumps(spec))
    (session / "labels.csv").write_text("deliberately malformed; not training input")
    changed = fit_model(refs, output)
    assert changed["examples"] == model["examples"]
    assert changed["sources"] == model["sources"]


def test_new_commands_no_target_labels_or_raw_mutations(tmp_path, capsys):
    _, refs, output, _ = fit_fixture(tmp_path)
    assert main(["train", str(refs), "--output", str(output)]) == 0
    target = tmp_path / "test"
    labelled_fixture(target, motion(((7, 6, 1.8),), duration=30))
    (target / "labels.csv").write_text("not csv")
    (target / "label_events.jsonl").write_text("not json")
    before = {p.name: p.read_bytes() for p in target.iterdir()}
    assert main(["analyze", str(target), "--model", str(output)]) == 0
    assert "6 estimated repetitions" in capsys.readouterr().out
    result = json.loads((target / "analysis/analysis.json").read_text())
    assert result["is_training_session"] is False
    assert result["reads_target_labels"] is False
    assert result["sets"][0]["activity"] == "push-up"
    assert result["sets"][0]["rep_estimate"] == 6
    assert (target / "analysis/analysis.png").read_bytes().startswith(b"\x89PNG")
    with (target / "analysis/repetitions.csv").open() as handle:
        assert len(list(csv.DictReader(handle))) == 6
    assert all((target / name).read_bytes() == content for name, content in before.items())
    (target / "labels.csv").write_text(
        "activity,start_time_s,end_time_s,expected_rep_count\nsquat,0,30,999"
    )
    repeated = analyse_session(target, output, plot=False)
    assert repeated["sets"] == result["sets"]


def test_replaying_reference_is_not_reported_as_blind_test(tmp_path):
    session, _, output, _ = fit_fixture(tmp_path)
    result = analyse_session(session, output, plot=False)
    assert result["is_training_session"]
    assert any("training replay" in w for w in result["warnings"])


def test_too_short_recording_is_reported_as_unevaluated(tmp_path):
    _, _, output, _ = fit_fixture(tmp_path)
    target = tmp_path / "short"
    labelled_fixture(target, motion((), duration=3))
    result = analyse_session(target, output, plot=False)
    assert result["sets"] == []
    assert result["windows"] == []
    assert any("No intact ACC/gyro overlap" in w for w in result["warnings"])


@pytest.mark.parametrize("field,value", [("subject", "other"), ("sensor_position", "wrist_right")])
def test_prediction_refuses_mismatched_person_or_placement(tmp_path, field, value):
    session, _, output, _ = fit_fixture(tmp_path)
    p = session / "metadata.json"
    metadata = json.loads(p.read_text())
    metadata[field] = value
    p.write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="differs from personal model"):
        analyse_session(session, output, plot=False)


def test_training_refuses_a_reference_crossing_missing_data(tmp_path):
    _, refs, output, _ = fit_fixture(tmp_path)
    t, a, gt, g = motion()
    mask = (gt < 9) | (gt > 10)
    # save_fixture intentionally requires a fresh folder.
    labelled_fixture(tmp_path / "gap", (t, a, gt[mask], g[mask]))
    spec = json.loads(refs.read_text())
    spec["references"][0]["session"] = "gap"
    refs.write_text(json.dumps(spec))
    with pytest.raises(ValueError, match="outside intact"):
        fit_model(refs, output)


def test_unknown_motion_can_be_rejected_and_stillness_is_not_a_posture_guess(tmp_path):
    _, _, _, model = fit_fixture(tmp_path)
    rng = np.random.default_rng(4)
    g = rng.normal(0, 1000, (100, 3))
    a = rng.normal(0, 5000, (100, 3))
    assert classify(a, g, model)["activity"] == "unknown"
    assert (
        classify(np.tile([200, 900, 300], (100, 1)), np.zeros((100, 3)), model)["activity"]
        == "stationary"
    )


@pytest.mark.parametrize("corruption", ["version", "nan", "shape", "convention"])
def test_invalid_models_fail_with_clear_error(tmp_path, corruption):
    _, _, output, model = fit_fixture(tmp_path)
    if corruption == "version":
        model["version"] = "future"
    elif corruption == "nan":
        model["examples"][0]["features"][0] = float("nan")
    elif corruption == "shape":
        for example in model["examples"]:
            example["features"] = [1, 2]
    else:
        model["jump_count_convention"] = "guess"
    output.write_text(json.dumps(model))
    with pytest.raises(ValueError):
        load_model(output)


def impact_motion(groups=((4, 4),), rate=52, duration=25):
    t = np.arange(0, duration, 1 / rate)
    norm = np.full(len(t), 1000.0)
    for start, jumps in groups:
        for rep in range(jumps):
            for offset in (0, 0.65):
                peak = start + rep * 1.8 + offset
                norm += 3000 * np.exp(-(((t - peak) / 0.07) ** 2))
                norm -= 800 * np.exp(-(((t - (peak - 0.22)) / 0.10) ** 2))
    a = np.column_stack([norm, np.zeros(len(t)), np.zeros(len(t))])
    g = np.column_stack([np.zeros(len(t)), 40 * np.sin(2 * np.pi * t), np.zeros(len(t))])
    return t, a, t.copy(), g


@pytest.mark.parametrize("rate", [25, 52, 100])
def test_impacts_are_resolved_once_across_sampling_rates_and_axis_rotations(rate):
    t, a, _, _ = impact_motion(rate=rate)
    assert len(jump_impacts(t, a)) == 8
    rotation, _ = np.linalg.qr(np.random.default_rng(5).normal(size=(3, 3)))
    assert len(jump_impacts(t, a @ rotation)) == 8


@pytest.mark.parametrize(
    "convention,expected", [("paired_impacts", 4), ("single_impact", 8), ("unconfirmed", None)]
)
def test_impact_count_convention_is_explicit_not_forced_to_label(convention, expected):
    data = impact_motion()
    blocks = motion_blocks(data)
    intervals = [{"activity": "jump", "block_id": 0, "start_time_s": 2, "end_time_s": 13}]
    sets = _jump_sets(data, blocks, intervals, convention)
    assert len(sets) == 1
    assert sets[0]["impact_count"] == 8
    assert sets[0]["paired_impact_count"] == 4
    assert sets[0]["rep_estimate"] == expected
    if expected is not None:
        assert len(sets[0]["cycles"]) == expected


def test_impacts_without_jump_recognition_do_not_create_exercise_sets():
    data = impact_motion()
    assert _jump_sets(data, motion_blocks(data), [], "paired_impacts") == []


def test_jump_and_feature_windows_never_bridge_gyro_gap(tmp_path):
    _, _, _, model = fit_fixture(tmp_path)
    t, a, gt, g = impact_motion(groups=((4, 6),))
    keep = (gt < 8.2) | (gt > 8.8)
    data = t, a, gt[keep], g[keep]
    blocks = motion_blocks(data)
    windows = predict_windows(blocks, model)
    assert all(w["evidence_end_time_s"] < 8.2 or w["evidence_start_time_s"] > 8.8 for w in windows)
    intervals = [
        {"activity": "jump", "block_id": i, "start_time_s": b[0][0], "end_time_s": b[0][-1]}
        for i, b in enumerate(blocks)
    ]
    sets = _jump_sets(data, blocks, intervals, "paired_impacts")
    assert all(s["end_time_s"] < 8.2 or s["start_time_s"] > 8.8 for s in sets)


def slow_arm_motion(incomplete=False):
    t = np.arange(0, 20, 0.04)
    angle = np.zeros(len(t))
    for start in (5, 10):
        mask = (t >= start) & (t <= start + 4)
        angle[mask] = 1.3 * (1 - np.cos(2 * np.pi * (t[mask] - start) / 4)) / 2
    if incomplete:
        angle[t >= 12] = 1.3
    a = np.column_stack([1000 * np.sin(angle), 1000 * np.cos(angle), np.zeros(len(t))])
    return t, a


@pytest.mark.parametrize("incomplete", [True, False])
def test_two_slow_excursions_keep_an_incomplete_return_distinct(incomplete):
    t, a = slow_arm_motion(incomplete)
    events = short_excursions(t, a, 5, 14)
    assert len(events) == 2
    assert events[0]["return_observed"]
    assert events[1]["return_observed"] is not incomplete


def test_short_detector_does_not_turn_stillness_or_one_way_gesture_into_two_reps():
    t = np.arange(0, 20, 0.04)
    angle = np.clip((t - 5) / 4, 0, 1.3)
    a = np.column_stack([1000 * np.sin(angle), 1000 * np.cos(angle), np.zeros(len(t))])
    assert len(short_excursions(t, a, 5, 10)) <= 1
    assert short_excursions(t, np.tile([0, 1000, 0], (len(t), 1)), 5, 14) == []


def test_stairs_references_produce_activity_without_strength_reps(tmp_path):
    session, refs, output, _ = fit_fixture(tmp_path)
    spec = json.loads(refs.read_text())
    for reference in spec["references"]:
        reference["activity"] = "stairs"
    refs.write_text(json.dumps(spec))
    model = fit_model(refs, output)
    assert model["classes"] == ["stairs"]
    result = analyse_session(session, output)
    assert any(i["activity"] == "stairs" for i in result["activities"])
    assert not result["sets"]
    assert (session / "analysis/analysis.png").exists()
