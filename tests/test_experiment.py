"""Numerical, isolation and evaluation contracts; not physiological accuracy tests."""

import csv
import io
import json
from dataclasses import asdict, replace

import numpy as np
import pytest

pytest.importorskip("tslearn")
pytest.importorskip("scipy")

from polar_activity.experiments.counters import mmfit_counter
from polar_activity.experiments.matcher import (
    MatchConfig,
    compare_representations,
    path_score,
    resolve_overlaps,
    scan,
)
from polar_activity.experiments.recofit import load_subset, subset_bytes
from polar_activity.experiments.runner import load_sensor, rotation_matrix
from polar_activity.experiments.scoring import count_error, score_sets
from polar_activity.experiments.signal import feature_vector, represent, stable_excursions
from polar_activity.experiments.training import fit_templates, training_cycles


def waveform(duration=2, rate=25):
    t = np.arange(0, duration + 0.001, 1 / rate)
    p = 2 * np.pi * t / duration
    a = np.column_stack([250 * np.sin(p), 1000 + 90 * np.cos(p), 70 * np.sin(2 * p)])
    g = np.column_stack([40 * np.cos(p), 65 * np.sin(p), 20 * np.cos(2 * p)])
    return t, a, g


def tiny_model(version="raw_axes_v1"):
    t, a, g = waveform()
    c = {
        "id": "training-only",
        "activity": "push-up",
        "duration_s": 2,
        "group": "train",
        "acc": a,
        "gyro": g,
        "start_time_s": 0,
        "end_time_s": 2,
    }
    return fit_templates([c], [], version, MatchConfig())


def test_dtw_normalization_is_actual_path_rms_and_band_is_enforced():
    x = np.array([[0, 2], [1, 3], [2, 4], [3, 5]], float)
    y = x + [1, 2]
    result = path_score(x, y, 0, include_path=True)
    assert result["path"] == [[i, i] for i in range(4)]
    cost = sum(np.sum((x[i] - y[j]) ** 2) for i, j in result["path"])
    assert result["raw_distance"] ** 2 == pytest.approx(cost)
    assert result["distance"] == pytest.approx(np.sqrt(cost / len(result["path"])))
    assert result["max_warp_samples"] == 0


@pytest.mark.parametrize("seed", [12, 32, 66])
def test_joint_pca_uses_common_proper_frame_and_handles_signs(seed):
    _, a, g = waveform()
    rotation, _ = np.linalg.qr(np.random.default_rng(seed).normal(size=(3, 3)))
    if np.linalg.det(rotation) < 0:
        rotation[:, -1] *= -1
    original = represent(a, g, [200, 50], "joint_pca_v1")
    changed = represent(a @ rotation, g @ rotation, [200, 50], "joint_pca_v1")
    assert np.linalg.det(original["frame"]) == pytest.approx(1)
    comparison = compare_representations(original, changed, "joint_pca_v1", 8)
    assert comparison["distance"] < 1e-8
    assert np.prod(comparison["proper_sign"]) == 1
    assert np.mean(np.linalg.norm(original["values"][:, :3], axis=1)) > 4


def test_degenerate_pca_fallback_is_explicit_and_rotation_invariant():
    t = np.arange(0, 3, 0.04)
    a = np.tile([0.0, 1000, 0], (len(t), 1))
    g = np.column_stack([50 * np.sin(t * 3), np.zeros(len(t)), np.zeros(len(t))])
    rotation = rotation_matrix([1, 2, 3], 73)
    x = represent(a, g, [200, 50], "joint_pca_v1")
    y = represent(a @ rotation, g @ rotation, [200, 50], "joint_pca_v1")
    assert not x["stable_frame"]
    result = compare_representations(x, y, "joint_pca_v1", 8)
    assert result["used_invariant_fallback"]
    assert result["distance"] < 1e-8


def test_richer_gravity_features_invariant_but_raw_pose_is_not():
    _, a, g = waveform()
    r = rotation_matrix([1, 2, 3], 73)
    assert feature_vector(a, g, "gravity_relative_v1") == pytest.approx(
        feature_vector(a @ r, g @ r, "gravity_relative_v1")
    )
    assert not np.allclose(feature_vector(a, g, "frozen"), feature_vector(a @ r, g @ r, "frozen"))


def test_overlap_resolution_keeps_adjacent_cycles_and_no_double_class_credit():
    def proposal(i, left, right, score, activity="push-up"):
        return {
            "candidate_id": i,
            "start_time_s": left,
            "end_time_s": right,
            "best": {"distance": score, "threshold": 1},
            "activity": activity,
            "rejection_reasons": [],
        }

    candidates = [
        proposal(0, 0, 2, 0.1),
        proposal(1, 2, 4, 0.1),
        proposal(2, 0, 4, 0.8),
        proposal(3, 0, 2, 0.4, "pull-up"),
    ]
    selected = resolve_overlaps(candidates)
    assert [c["candidate_id"] for c in selected] == [0, 1]
    assert all(c["rejection_reasons"] for c in candidates[2:])


def test_single_cycle_is_not_removed_by_a_minimum_set_rule():
    model = tiny_model()
    block = waveform()
    result = scan([block], model)
    assert result["sets"]
    assert result["sets"][0]["dtw_cycle_count"] == 1
    assert result["sets"][0]["isolated_cycle"]


def test_budget_reports_incomplete_scan_and_gaps_are_not_bridged():
    model = tiny_model()
    model["settings"] = asdict(replace(MatchConfig(), max_candidates=2))
    t, a, g = waveform(4)
    result = scan([(t, a, g), (t + 10, a, g)], model)
    assert not all(c["grid_complete"] for c in result["coverage"])
    with pytest.raises(ValueError, match="Split source gaps"):
        scan([(np.r_[t[:40], t[40:] + 2], a, g)], tiny_model())


def test_training_rejects_a_reference_outside_training_groups():
    with pytest.raises(ValueError, match="outside this training fold"):
        training_cycles(
            {"train": [waveform()]},
            [{"group": "heldout", "activity": "push-up", "start": 0, "end": 2}],
        )
    model = tiny_model()
    assert model["training_groups"] == ["train"]
    assert all(t["group"] == "train" for t in model["templates"])
    assert model["expected_counts_used"] is False


def write_sensor(path, block, gyro_offset=0):
    path.mkdir()
    t, a, g = block
    epoch = 812345678901234567
    for stream, unit, values in (("acc", "mg", a), ("gyro", "dps", g)):
        with (path / f"{stream}.csv").open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                ["time_s", "device_timestamp_ns", *[f"{stream}_{s}_{unit}" for s in "xyz"]]
            )
            for tm, xyz in zip(t, values, strict=True):
                stamp = epoch + round(tm * 1e9) + (gyro_offset if stream == "gyro" else 0)
                writer.writerow([tm, stamp, *xyz])


def test_expected_counts_notes_and_activity_filenames_do_not_change_predictions(tmp_path):
    first, renamed = tmp_path / "blind", tmp_path / "999-pullups"
    write_sensor(first, waveform())
    write_sensor(renamed, waveform())
    (first / "labels.csv").write_text("activity,expected_rep_count\npull-up,999\n")
    (first / "metadata.json").write_text(json.dumps({"notes": "Do 500 pullups"}))
    outputs = []
    for path in (first, renamed):
        at, acc, gt, gyro = load_sensor(path)
        assert np.array_equal(at, gt)
        outputs.append(scan([(at, acc, gyro)], tiny_model()))
    assert outputs[0] == outputs[1]


def test_common_integer_anchor_rejects_cross_stream_offset(tmp_path):
    path = tmp_path / "bad-clock"
    write_sensor(path, waveform(), gyro_offset=1_000_000_000)
    with pytest.raises(ValueError, match="common device-time anchor"):
        load_sensor(path)


def test_counter_distinguishes_quiet_zero_and_unsupported_short_interval():
    t, a, g = waveform()
    assert mmfit_counter(t, a, g * 0, 0, 2)["count"] == 0
    assert mmfit_counter(t, a, g, 0, 0.1)["count"] is None
    assert mmfit_counter(t, a, g, 0, 2)["candidate_peaks"]


def test_stable_baseline_handles_moving_pre_context_and_common_rotation():
    t = np.arange(0, 23, 0.04)
    angle = np.full(len(t), 120.0)
    angle[t >= 3] = 0
    for start in (5, 10, 15):
        mask = (t >= start) & (t < start + 4)
        angle[mask] = 85 * (1 - np.cos(2 * np.pi * (t[mask] - start) / 4)) / 2
    radians = np.radians(angle)
    acc = 1000 * np.column_stack([np.sin(radians), np.cos(radians), np.zeros(len(t))])
    gyro = np.column_stack([np.zeros(len(t)), np.zeros(len(t)), np.gradient(angle, t)])
    result = stable_excursions(t, acc, gyro, 3, 20)
    rotation = rotation_matrix([1, 2, 3], 73)
    transformed = stable_excursions(t, acc @ rotation, gyro @ rotation, 3, 20)
    assert len(result["events"]) == len(transformed["events"]) == 3
    assert all(e["return_observed"] for e in result["events"])
    assert [e["peak_departure_deg"] for e in result["events"]] == pytest.approx(
        [e["peak_departure_deg"] for e in transformed["events"]]
    )


def test_unavailable_baseline_is_explicit():
    t, a, g = waveform()
    result = stable_excursions(t, a, np.full_like(g, 300), 0, 2)
    assert result["baseline"] is None
    assert result["rejection_reasons"] == ["no_stable_baseline"]


def test_scoring_misses_wrong_names_duplicates_unknown_counts_and_background():
    refs = [
        {
            "id": "a",
            "activity": "push-up",
            "start_time_s": 1,
            "end_time_s": 5,
            "count_range": [5, 5],
        },
        {
            "id": "b",
            "activity": "pull-up",
            "start_time_s": 10,
            "end_time_s": 15,
            "count_range": [3, 4],
        },
    ]
    predictions = [
        {"activity": "pull-up", "start_time_s": 1, "end_time_s": 5, "count": 5},
        {"activity": "push-up", "start_time_s": 1, "end_time_s": 3, "count": None},
        {"activity": "push-up", "start_time_s": 30, "end_time_s": 33, "count": 2},
    ]
    result = score_sets(predictions, refs, [{"start_time_s": 29, "end_time_s": 39}])
    assert result["missed_sets"] == 1
    assert result["wrong_class_predictions"] == [0]
    assert result["events"][0]["end_to_end_absolute_error"] == 5
    assert result["events"][1]["end_to_end_absolute_error"] == 3
    assert result["duplicate_or_fragment_predictions"] == [1]
    assert result["background_false_sets"] == 1
    assert result["background_false_reps"] == 2
    assert count_error(None, [4, 4]) is None
    assert count_error(7, None) is None
    assert count_error(3, [3, 4]) == 0
    assert score_sets([], [], [])["background_false_sets_per_hour"] is None


@pytest.mark.parametrize(
    "setting",
    [
        {"stride_s": 0},
        {"points": 4},
        {"band_radius": 99},
        {"max_candidates": 0},
        {"duration_ratio": [2, 1]},
        {"default_threshold": float("nan")},
    ],
)
def test_invalid_experiment_configuration_is_rejected(setting):
    with pytest.raises(ValueError):
        MatchConfig(**setting)


def test_recofit_prefix_keeps_complete_cells_and_original_units(tmp_path):
    from scipy.io import savemat

    t = np.arange(0, 4.02, 0.02)
    values = np.column_stack([t, np.zeros(len(t)), np.ones(len(t)), np.zeros(len(t))])
    cells = np.empty((2, 1), dtype=object)
    for i in range(2):
        labels = np.empty((1, 7), dtype=object)
        labels[0] = ["Jumping Jacks", 0.0, 4.0, "", 3, 0, {"extra": 0}]
        cells[i, 0] = {
            "data": {"accelDataMatrix": values, "gyroDataMatrix": values * [1, 1, 10, 1]},
            "activityStartMatrix": labels,
        }
    stream = io.BytesIO()
    savemat(stream, {"subject_data": cells}, do_compression=True)
    data = stream.getvalue()
    small, provenance = subset_bytes(data[:136], io.BytesIO(data[136:]), subjects=1)
    path = tmp_path / "subset.mat"
    path.write_bytes(small)
    sessions = load_subset(path)
    assert len(sessions) == 1
    assert provenance["original_shape"] == [2, 1]
    assert provenance["selected_subject_indices_1_based"] == [1]
    assert sessions[0]["arrays"][1][:, 1] == pytest.approx(1000)
    assert sessions[0]["arrays"][3][:, 1] == pytest.approx(10)
    assert sessions[0]["labels"][0]["activity_original"] == "Jumping Jacks"
    assert sessions[0]["labels"][0]["reference_count"] == 3


def test_recofit_lfs_pointer_and_truncated_prefix_are_rejected(tmp_path):
    path = tmp_path / "not-data.mat"
    path.write_text("version https://git-lfs.github.com/spec/v1\n")
    with pytest.raises(ValueError, match="LFS pointer"):
        load_subset(path)
    with pytest.raises(ValueError, match="MAT-v5"):
        subset_bytes(b"incomplete", io.BytesIO())
