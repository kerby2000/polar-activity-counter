"""Numerical sanity checks independent of Sergey's manually counted set."""

import importlib.util
from pathlib import Path

import numpy as np
import pytest

spec = importlib.util.spec_from_file_location(
    "analyze_set", Path(__file__).parents[1] / "scripts" / "analyze_set.py"
)
analysis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analysis)


def test_known_cycles_and_cadence_without_label_input():
    rate = 52.94
    times = np.arange(0, 18, 1 / rate)
    values = 80 * np.sin(2 * np.pi * times / 1.5)
    filtered = analysis.smooth(values, 0.18, rate)
    peaks = analysis.candidate_peaks(times, filtered, 40, 0.8)
    assert len(peaks) == 12
    result, _ = analysis.periodicity(values, rate)
    assert result["dominant_frequency_hz"] == pytest.approx(1 / 1.5, abs=0.01)
    assert result["autocorrelation_lag_s"] == pytest.approx(1.5, abs=1 / rate)


def test_quiet_signal_has_no_candidate_peaks():
    times = np.arange(0, 10, 1 / 52)
    assert analysis.candidate_peaks(times, np.zeros(len(times)), 40, 0.8) == []


@pytest.mark.parametrize("size", [520, 521])
def test_one_sided_spectrum_preserves_tapered_energy(size):
    values = np.random.default_rng(32).normal(size=size)
    result, (_, power, _, _) = analysis.periodicity(values, 52)
    taper = np.hanning(size)
    expected = np.sum(((values - values.mean()) * taper) ** 2) / np.sum(taper**2)
    assert power.sum() * result["frequency_bin_hz"] == pytest.approx(expected)
