"""Independent out-and-back motion fixtures; no captured exercise or manual labels needed."""

import csv
import json

import numpy as np
import pytest

from polar_activity.cli import main
from polar_activity.counter import CounterConfig, detect_sets
from polar_activity.counting import count_session


def motion(sets=((5.0, 8, 1.5),), duration=30, rate=52):
    times = np.arange(0, duration, 1 / rate)
    angle = np.zeros(len(times))
    velocity = np.zeros(len(times))
    for start, cycles, period in sets:
        mask = (times >= start) & (times <= start + cycles * period)
        phase = 2 * np.pi * (times[mask] - start) / period
        angle[mask] = 0.7 * (1 - np.cos(phase)) / 2
        velocity[mask] = np.rad2deg(0.7 * np.pi / period * np.sin(phase))
    acc = np.column_stack((1000 * np.sin(angle), np.zeros(len(times)), 1000 * np.cos(angle)))
    gyro = np.column_stack((np.zeros(len(times)), velocity, np.zeros(len(times))))
    return times, acc, times.copy(), gyro


@pytest.mark.parametrize("cycles,period", [(5, 1.5), (8, 0.8), (13, 1.5), (6, 2.2)])
def test_known_complete_cycles_and_automatic_boundaries(cycles, period):
    result = detect_sets(*motion(((5.0, cycles, period),), duration=cycles * period + 12))
    assert len(result["sets"]) == 1
    bout = result["sets"][0]
    assert bout["complete_cycles"] == cycles
    assert bout["start_time_s"] == pytest.approx(5, abs=0.3)
    assert bout["end_time_s"] == pytest.approx(5 + cycles * period, abs=0.3)
    assert all(c["end_time_s"] > c["start_time_s"] for c in bout["cycles"])


def test_two_sets_separated_by_rest():
    result = detect_sets(*motion(((4, 7, 1.5), (24, 6, 1.8)), duration=42))
    assert [s["complete_cycles"] for s in result["sets"]] == [7, 6]
    assert result["sets"][0]["end_time_s"] < result["sets"][1]["start_time_s"]


def paused_motion(pause=1.5):
    return motion(((4, 6, 1.5), (13 + pause, 8, 1.7)), duration=38)


@pytest.mark.parametrize("pause", [1.2, 2.0, 2.8])
def test_short_quiet_rest_groups_existing_cycles_without_counting_the_rest(pause):
    data = paused_motion(pause)
    separated = detect_sets(*data, config=CounterConfig(max_pause_s=0))
    grouped = detect_sets(*data)
    assert [s["complete_cycles"] for s in separated["sets"]] == [6, 8]
    assert [s["complete_cycles"] for s in grouped["sets"]] == [14]
    bout = grouped["sets"][0]
    assert len(bout["motion_blocks"]) == 2
    assert len(bout["pauses"]) == 1
    assert bout["pause_duration_s"] == pytest.approx(pause, abs=0.25)
    original_cycles = [c for s in separated["sets"] for c in s["cycles"]]
    assert [{k: v for k, v in c.items() if k != "block_id"} for c in bout["cycles"]] == [
        {k: v for k, v in c.items() if k != "block_id"} for c in original_cycles
    ]
    rest = bout["pauses"][0]
    assert not any(
        c["start_time_s"] < rest["end_time_s"] and c["end_time_s"] > rest["start_time_s"]
        for c in bout["cycles"]
    )


def test_multiple_short_rests_stay_in_one_set():
    result = detect_sets(*motion(((4, 6, 1.5), (15, 6, 1.5), (26, 6, 1.5)), duration=42))
    assert [s["complete_cycles"] for s in result["sets"]] == [18]
    assert len(result["sets"][0]["pauses"]) == 2


@pytest.mark.parametrize("reason", ["long", "different_axis", "moving", "missing_data"])
def test_incompatible_or_unobserved_break_does_not_join_sets(reason):
    times, acc, gt, gyro = paused_motion(4 if reason == "long" else 1.5)
    if reason == "different_axis":
        # Change the next movement plane around gravity, without a resting pose jump.
        after = times > 13.7
        rotation = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]])
        acc[after] = acc[after] @ rotation
        gyro[after] = gyro[after] @ rotation
    elif reason == "moving":
        gyro[(gt >= 13.3) & (gt <= 14.2), 0] = 60
    elif reason == "missing_data":
        keep = ~((gt >= 13.3) & (gt <= 14.2))
        gt, gyro = gt[keep], gyro[keep]
    result = detect_sets(times, acc, gt, gyro)
    assert [s["complete_cycles"] for s in result["sets"]] == [6, 8]
    assert not any(s["pauses"] for s in result["sets"])


def test_rotation_and_sensor_bias_do_not_change_count():
    times, acc, gt, gyro = motion()
    rng = np.random.default_rng(3)
    rotation, _ = np.linalg.qr(rng.normal(size=(3, 3)))
    acc = acc @ rotation + rng.normal(0, 2, acc.shape)
    gyro = gyro @ rotation + np.array([3, -2, 1]) + rng.normal(0, 0.1, gyro.shape)
    result = detect_sets(times, acc, gt, gyro)
    assert result["total_complete_cycles"] == 8


def test_fast_vibration_cannot_alias_into_an_exercise_rhythm():
    times = np.arange(0, 30, 1 / 52.94)
    # At a naive 25Hz resampling step this vibration appears close to 0.67Hz.
    phase = 2 * np.pi * (25 + 2 / 3) * times
    acc = np.column_stack((400 * np.cos(phase), np.zeros(len(times)), np.full(len(times), 1000)))
    gyro = np.column_stack((np.zeros(len(times)), 120 * np.sin(phase), np.zeros(len(times))))
    assert detect_sets(times, acc, times, gyro)["total_complete_cycles"] == 0


def test_partial_last_repetition_does_not_add_one():
    result = detect_sets(*motion(((5, 8.4, 1.5),), duration=25))
    assert result["total_complete_cycles"] == 8


def test_recording_cut_mid_motion_does_not_extrapolate_boundaries():
    times, acc, gt, gyro = motion(((5, 10, 1.5),), duration=24)
    keep = (times >= 5.4) & (times <= 19.5)
    result = detect_sets(times[keep], acc[keep], gt[keep], gyro[keep])
    assert 0 < result["total_complete_cycles"] < 10
    assert any(s["near_recording_or_gap_edge"] for s in result["sets"])
    assert all(
        times[keep][0] < s["start_time_s"] < s["end_time_s"] < times[keep][-1]
        for s in result["sets"]
    )


def test_missing_samples_split_sets_and_never_form_a_cycle_over_the_gap():
    times, acc, gt, gyro = motion(((5, 18, 1.5),), duration=38)
    keep = ~((times >= 18) & (times <= 19))
    result = detect_sets(times, acc, gt[keep], gyro[keep])
    assert result["gaps"] and len(result["sets"]) == 2
    assert result["total_complete_cycles"] < 18
    assert not any(
        c["start_time_s"] < 19 and c["end_time_s"] > 18 for s in result["sets"] for c in s["cycles"]
    )


@pytest.mark.parametrize("kind", ["quiet", "noise", "single", "one_direction", "drift"])
def test_background_and_incomplete_movements_do_not_count(kind):
    times, acc, gt, gyro = motion((), duration=30)
    if kind == "single":
        times, acc, gt, gyro = motion(((5, 1, 1.5),), duration=30)
    elif kind == "noise":
        rng = np.random.default_rng(13)
        acc += rng.normal(0, 350, acc.shape)
        gyro += rng.normal(0, 100, gyro.shape)
    elif kind == "one_direction":
        times, acc, gt, gyro = motion()
        gyro[:, 1] = abs(gyro[:, 1])
    elif kind == "drift":
        acc[:, 0] += times * 40
        gyro[:, 1] += times * 4
    assert detect_sets(times, acc, gt, gyro)["total_complete_cycles"] == 0


@pytest.mark.parametrize("bad", ["nan", "duplicate", "backward"])
def test_invalid_data_is_rejected(bad):
    times, acc, gt, gyro = motion()
    if bad == "nan":
        acc[40, 0] = np.nan
    else:
        gt[40] = gt[39] if bad == "duplicate" else gt[38]
    with pytest.raises(ValueError):
        detect_sets(times, acc, gt, gyro)


def save_fixture(path, data=None):
    path.mkdir()
    times, acc, gt, gyro = motion() if data is None else data
    for stream, unit, t, values in (("acc", "mg", times, acc), ("gyro", "dps", gt, gyro)):
        with (path / f"{stream}.csv").open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                ["time_s", "device_timestamp_ns", *[f"{stream}_{a}_{unit}" for a in "xyz"]]
            )
            for stamp, xyz in zip(t, values, strict=True):
                writer.writerow([stamp, 800_000_000_000_000_123 + round(stamp * 1e9), *xyz])
    (path / "metadata.json").write_text(
        json.dumps({"session_id": "synthetic", "status": "complete"})
    )


def test_command_exports_and_never_uses_labels_or_alters_input(tmp_path, capsys):
    path = tmp_path / "session"
    save_fixture(path)
    original = {f.name: f.read_bytes() for f in path.iterdir()}
    assert main(["count", str(path)]) == 0
    assert "8 complete cycles" in capsys.readouterr().out
    output = path / "automatic-count"
    result = json.loads((output / "counts.json").read_text())
    assert (output / "automatic_count.png").read_bytes().startswith(b"\x89PNG")
    assert len(list(csv.DictReader((output / "cycles.csv").open()))) == 8
    (path / "labels.csv").write_text("This deliberately malformed label must never be read")
    (path / "label_events.jsonl").write_text("not json")
    repeated = count_session(path, plot=False)
    assert repeated["sets"] == result["sets"]
    assert all((path / name).read_bytes() == value for name, value in original.items())


def test_command_exports_grouped_rest_and_block_references(tmp_path, capsys):
    path = tmp_path / "paused"
    save_fixture(path, paused_motion())
    assert main(["count", str(path)]) == 0
    assert "1 short rest(s)" in capsys.readouterr().out
    output = path / "automatic-count"
    result = json.loads((output / "counts.json").read_text())
    assert result["schema_version"] == 2
    assert result["algorithm"] == "periodic-return-v2"
    assert [s["complete_cycles"] for s in result["sets"]] == [14]
    with (output / "pauses.csv").open() as handle:
        assert len(list(csv.DictReader(handle))) == 1
    with (output / "cycles.csv").open() as handle:
        cycles = list(csv.DictReader(handle))
    assert len(cycles) == 14
    assert {c["block_id"] for c in cycles} == {"001", "002"}
    assert (output / "automatic_count.png").read_bytes().startswith(b"\x89PNG")
