"""Motion consistency proxies distinguish weak attempts from dismount rotation."""

import numpy as np
import pytest

from polar_activity.motion_quality import arm_excursions, compare_excursions


def arm_set(rate=25, dismount=True):
    t = np.arange(0, 29, 1 / rate)
    angle = np.zeros(len(t))
    for start, amplitude in ((4, 90), (9, 90), (14, 85)):
        mask = (t >= start) & (t < start + 4)
        angle[mask] = amplitude * (1 - np.cos(2 * np.pi * (t[mask] - start) / 4)) / 2
    mask = (t >= 20) & (t < 22)
    angle[mask] = 52 * (1 - np.cos(np.pi * (t[mask] - 20) / 2)) / 2
    angle[t >= 22] = 52
    norm = np.full(len(t), 1000.0)
    if dismount:
        angle[t >= 24] = 150
        norm[(t >= 24) & (t < 24.2)] = 300
        norm[(t >= 24.2) & (t < 24.4)] = 2800
    radians = np.radians(angle)
    acc = norm[:, None] * np.column_stack([np.sin(radians), np.cos(radians), np.zeros(len(t))])
    gyro = np.column_stack([np.zeros(len(t)), np.zeros(len(t)), -np.gradient(angle, t)])
    return t, acc, gyro


@pytest.mark.parametrize("rate", [25, 52, 100])
def test_three_returns_and_reduced_unclosed_attempt_exclude_dismount(rate):
    t, a, g = arm_set(rate)
    events = arm_excursions(t, a, 4, 24, g)
    assert len(events) == 4
    assert [e["return_observed"] for e in events] == [True, True, True, False]
    assert events[-1]["peak_departure_deg"] == pytest.approx(52, abs=1)
    assert events[-1]["end_time_s"] < 24
    report = compare_excursions(events)
    assert report["assesses_technique"] is False
    assert report["repetitions"][-1]["relative_excursion"] == pytest.approx(52 / 90, abs=0.02)
    assert report["repetitions"][-1]["flags"] == ["reduced_arm_excursion", "return_not_observed"]
    assert all(not r["flags"] for r in report["repetitions"][:3])


def test_arm_quality_is_independent_of_sensor_axis_rotation():
    t, a, g = arm_set()
    rotation, _ = np.linalg.qr(np.random.default_rng(32).normal(size=(3, 3)))
    original = arm_excursions(t, a, 4, 24, g)
    rotated = arm_excursions(t, a @ rotation, 4, 24, g @ rotation)
    assert len(original) == len(rotated) == 4
    for first, second in zip(original, rotated, strict=True):
        assert first["peak_departure_deg"] == pytest.approx(second["peak_departure_deg"])
        assert first["start_time_s"] == second["start_time_s"]


def test_without_return_reference_no_relative_quality_score():
    t, a, g = arm_set(dismount=False)
    events = arm_excursions(t, a, 4, 7, g)[:1]
    report = compare_excursions(events)
    assert report["reference_departure_deg"] is None
    assert report["repetitions"][0]["relative_excursion"] is None
    assert compare_excursions([])["repetitions"] == []


def test_post_dismount_motion_does_not_add_exercise_attempts():
    t, a, g = arm_set()
    after = t >= 25
    a[after] = 1000 * np.column_stack(
        [np.sin(t[after] * 6), np.cos(t[after] * 6), np.zeros(sum(after))]
    )
    g[after, 2] = 350
    assert len(arm_excursions(t, a, 4, 27, g)) == 4


def test_quiet_or_weak_movement_cannot_establish_repeated_pullups():
    t, a, g = arm_set(dismount=False)
    assert arm_excursions(t, np.tile([0, 1000, 0], (len(t), 1)), 4, 24, g) == []
    angle = 0.3 * (1 - np.cos(t)) / 2
    weak = 1000 * np.column_stack([np.sin(angle), np.cos(angle), np.zeros(len(t))])
    assert arm_excursions(t, weak, 4, 24, g) == []


def test_dismount_after_three_reps_does_not_invent_a_fourth_attempt():
    t, a, g = arm_set()
    rest = (t >= 20) & (t < 24)
    a[rest] = [0, 1000, 0]
    g[rest] = 0
    assert len(arm_excursions(t, a, 4, 24, g)) == 3
