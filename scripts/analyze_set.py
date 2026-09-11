"""Reproducible exploratory analysis; candidate peaks are not validated repetitions."""

import argparse
import csv
import hashlib
import json
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


def smooth(values: np.ndarray, seconds: float, rate: float) -> np.ndarray:
    width = max(3, int(seconds * rate) // 2 * 2 + 1)
    return np.convolve(
        np.pad(values, (width // 2, width // 2), mode="edge"),
        np.ones(width) / width,
        mode="valid",
    )


def candidate_peaks(
    times: np.ndarray, values: np.ndarray, height: float, distance: float
) -> list[int]:
    """Local maxima above a height, keeping the tallest within minimum separation."""
    candidates = (
        np.flatnonzero(
            (values[1:-1] > values[:-2]) & (values[1:-1] >= values[2:]) & (values[1:-1] > height)
        )
        + 1
    )
    selected: list[int] = []
    for index in sorted(candidates, key=lambda i: -values[i]):
        if all(abs(times[index] - times[j]) >= distance for j in selected):
            selected.append(int(index))
    return sorted(selected)


def periodicity(values: np.ndarray, rate: float) -> tuple[dict, tuple]:
    centered = values - np.mean(values)
    taper = np.hanning(len(centered))
    spectrum = abs(np.fft.rfft(centered * taper)) ** 2 / (rate * np.sum(taper**2))
    spectrum[1 : -1 if len(values) % 2 == 0 else None] *= 2
    frequency = np.fft.rfftfreq(len(values), 1 / rate)
    band = (frequency >= 0.25) & (frequency <= 2)
    correlation = np.correlate(centered, centered, mode="full")[len(values) - 1 :]
    correlation = correlation / correlation[0] if correlation[0] > 0 else correlation * 0
    lag = np.arange(len(values)) / rate
    lag_band = (lag >= 0.5) & (lag <= 2.5)
    best_lag = int(np.flatnonzero(lag_band)[np.argmax(correlation[lag_band])])
    result = {
        "dominant_frequency_hz": float(frequency[band][np.argmax(spectrum[band])]),
        "frequency_bin_hz": rate / len(values),
        "autocorrelation_lag_s": float(lag[best_lag]),
        "autocorrelation_at_lag": float(correlation[best_lag]),
        "frequency_search_hz": [0.25, 2],
        "lag_search_s": [0.5, 2.5],
    }
    return result, (frequency, spectrum, lag, correlation)


def statistics(values: np.ndarray) -> dict:
    return {
        "samples": len(values),
        **{
            key: function(values, axis=0).tolist()
            for key, function in (
                ("min", np.min),
                ("max", np.max),
                ("range", np.ptp),
                ("mean", np.mean),
                ("std", np.std),
            )
        },
    }


def analyze(
    session: Path,
    output: Path,
    set_id: str,
    start: float,
    end: float,
    quiet: tuple[float, float] | None = None,
) -> dict:
    metadata = json.loads((session / "metadata.json").read_text())
    with (session / "labels.csv").open(newline="") as handle:
        label = next(row for row in csv.DictReader(handle) if row["set_id"] == set_id)
    left, right = float(label["start_time_s"]), float(label["end_time_s"])
    if not np.isfinite([start, end]).all() or not left <= start < end <= right or end - start < 5:
        raise ValueError("Analysis window must be at least 5s and lie inside the labelled set")
    if quiet is not None and (not np.isfinite(quiet).all() or quiet[0] >= quiet[1]):
        raise ValueError("Quiet interval must have finite increasing boundaries")
    output.mkdir(parents=True, exist_ok=True)
    streams = {}
    for stream, unit in (("acc", "mg"), ("gyro", "dps")):
        with (session / f"{stream}.csv").open(newline="") as handle:
            rows = list(csv.DictReader(handle))
        times = np.array([float(row["time_s"]) for row in rows])
        values = np.array([[float(row[f"{stream}_{a}_{unit}"]) for a in "xyz"] for row in rows])
        if not (np.isfinite(values).all() and np.all(np.diff(times) > 0)):
            raise ValueError("Analysis requires finite samples and increasing timestamps")
        if times[0] > left or times[-1] < right:
            raise ValueError("Both streams must cover the complete labelled set")
        if quiet is not None and not times[0] <= quiet[0] < quiet[1] <= times[-1]:
            raise ValueError("Quiet interval must lie inside both streams")
        if np.max(np.diff(times)) > 0.1:
            raise ValueError("Refusing to interpolate across a gap above 100ms")
        streams[stream] = (times, values, unit)
    rate = float(np.mean([metadata["quality"][s]["packet_endpoint_rate_hz"] for s in streams]))
    if not np.isfinite(rate) or rate <= 0:
        raise ValueError("A measured positive sample rate is required")
    # A derived uniform grid for spectral analysis only. Raw CSVs are never changed.
    grid = np.arange(start, end, 1 / rate)
    result = {
        "session_id": metadata["session_id"],
        "label": label,
        "window_s": [start, end],
        "window_selection": "manual exploratory inspection",
        "quiet_window_s": quiet,
        "derived_rate_hz": rate,
        "smoothing_s": 0.18,
        "expected_count_used_by_detector": False,
        "source_sha256": {
            name: hashlib.sha256((session / name).read_bytes()).hexdigest()
            for name in (
                "acc.csv",
                "gyro.csv",
                "hr.csv",
                "labels.csv",
                "metadata.json",
                "packets.jsonl",
            )
        },
        "statistics": {},
        "candidates": {},
    }
    plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False})
    fig, axes = plt.subplots(4, 1, figsize=(13, 10), sharex=True)
    for row_index, (stream, (times, values, unit)) in enumerate(streams.items()):
        magnitude = np.linalg.norm(values, axis=1)
        all_values = np.column_stack((values, magnitude))
        intervals = [("label", left, right), ("motion", start, end)]
        if quiet is not None:
            intervals.append(("quiet", *quiet))
        result["statistics"][stream] = {
            name: statistics(all_values[(times >= lo) & (times <= hi)])
            for name, lo, hi in intervals
        }
        result["statistics"][stream]["columns"] = ["x", "y", "z", "magnitude"]
        for axis, name in enumerate("xyz"):
            axes[row_index * 2].plot(times, values[:, axis], label=name.upper(), lw=0.8)
        axes[row_index * 2].set_ylabel(f"{stream.upper()} ({unit})")
        axes[row_index * 2].legend(loc="upper right", ncol=3)
        axes[row_index * 2 + 1].plot(times, magnitude, color="#535d70", lw=0.8)
        axes[row_index * 2 + 1].set_ylabel(f"Magnitude ({unit})")
    for axis in axes:
        axis.axvspan(left, right, color="#e3e8f0", alpha=0.35)
        axis.axvspan(start, end, color="#2a9d8f", alpha=0.15)
        axis.grid(alpha=0.2)
    axes[-1].set(xlim=(left - 1, right + 1), xlabel="Original session time (s)")
    fig.suptitle(
        f"Set {set_id}: {label['activity']} | user label: {label['expected_rep_count']} reps\n"
        f"Green = manually selected periodic interval {start:g}–{end:g}s"
    )
    fig.tight_layout()
    fig.savefig(output / "overview.png", dpi=140)
    plt.close(fig)

    fig, axes = plt.subplots(2, 1, figsize=(13, 7), sharex=True)
    spectral_fig, spectral_axes = plt.subplots(2, 2, figsize=(12, 7))
    candidate_rows = []
    # Signed axes chosen by inspection of this placement, not a universal axis rule.
    for index, (stream, column, sign, height) in enumerate(
        (("gyro", 2, 1, 40), ("acc", 0, -1, 140))
    ):
        times, values, unit = streams[stream]
        derived = sign * np.interp(grid, times, values[:, column])
        filtered = smooth(derived, 0.18, rate)
        filtered -= np.mean(filtered)
        selected = candidate_peaks(grid, filtered, height, 0.8)
        peak_times = grid[selected]
        intervals = np.diff(peak_times)
        periodic, curves = periodicity(derived, rate)
        sensitivity = []
        for smoothing in (0.12, 0.18, 0.25):
            alternative = smooth(derived, smoothing, rate)
            alternative -= np.mean(alternative)
            for distance in (0.65, 0.8, 1.0):
                for threshold in (25, 40, 60) if stream == "gyro" else (80, 140, 200):
                    sensitivity.append(
                        {
                            "smoothing_s": smoothing,
                            "distance_s": distance,
                            "height": threshold,
                            "count": len(candidate_peaks(grid, alternative, threshold, distance)),
                        }
                    )
        full_grid = np.arange(left, right, 1 / rate)
        full_signal = smooth(sign * np.interp(full_grid, times, values[:, column]), 0.18, rate)
        full_signal -= np.mean(full_signal)
        result["candidates"][stream] = {
            "axis": "xyz"[column],
            "sign": sign,
            "height": height,
            "min_distance_s": 0.8,
            "count": len(selected),
            "times_s": peak_times.tolist(),
            "median_interval_s": float(np.median(intervals)) if len(intervals) else None,
            "min_interval_s": float(np.min(intervals)) if len(intervals) else None,
            "max_interval_s": float(np.max(intervals)) if len(intervals) else None,
            "full_label_naive_count": len(candidate_peaks(full_grid, full_signal, height, 0.8)),
            "periodicity": periodic,
            "sensitivity": sensitivity,
        }
        axes[index].plot(grid, filtered, color="#235789", lw=1.2)
        axes[index].scatter(peak_times, filtered[selected], color="#b43c27", s=28, zorder=3)
        for number, point in enumerate(selected, 1):
            axes[index].annotate(
                str(number),
                (grid[point], filtered[point]),
                xytext=(0, 8),
                textcoords="offset points",
                ha="center",
                fontsize=9,
            )
            candidate_rows.append(
                {
                    "signal": f"{stream}_{'xyz'[column]}",
                    "candidate": number,
                    "time_s": grid[point],
                    "centered_value": filtered[point],
                }
            )
        axes[index].set_ylabel(
            f"{'−' if sign < 0 else ''}{stream.upper()} {'XYZ'[column]} ({unit})"
        )
        axes[index].grid(alpha=0.2)
        axes[index].margins(y=0.2)
        freq, power, lag, correlation = curves
        spectral_axes[index, 0].plot(freq, power, color="#235789")
        spectral_axes[index, 0].set(
            xlim=(0, 3), xlabel="Frequency (Hz)", ylabel=f"{stream.upper()} power ({unit}²/Hz)"
        )
        spectral_axes[index, 1].plot(lag, correlation, color="#2a9d8f")
        spectral_axes[index, 1].set(xlim=(0, 5), xlabel="Lag (s)", ylabel="Autocorrelation")
    axes[-1].set(xlabel="Original session time (s)", xlim=(start, end))
    fig.suptitle(
        "Exploratory candidate peaks — not confirmed full repetitions\n"
        "Fixed peak settings; the user's count is not a detector input"
    )
    fig.tight_layout()
    fig.savefig(output / "candidate_peaks.png", dpi=150)
    plt.close(fig)
    spectral_fig.suptitle(
        "Periodicity in the selected interval | raw axes, demeaned\n"
        "Hann periodogram; normalized biased autocorrelation"
    )
    for axis in spectral_axes.flat:
        axis.grid(alpha=0.2)
    spectral_fig.tight_layout()
    spectral_fig.savefig(output / "periodicity.png", dpi=140)
    plt.close(spectral_fig)
    with (output / "candidate_peaks.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["signal", "candidate", "time_s", "centered_value"]
        )
        writer.writeheader()
        writer.writerows(candidate_rows)
    (output / "analysis.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--set", default="001", dest="set_id")
    parser.add_argument("--start", required=True, type=float)
    parser.add_argument("--end", required=True, type=float)
    parser.add_argument("--quiet", nargs=2, type=float, metavar=("START", "END"))
    args = parser.parse_args()
    analysis = analyze(args.session, args.output, args.set_id, args.start, args.end, args.quiet)
    print(
        json.dumps(
            {
                k: {f: v for f, v in value.items() if f != "sensitivity"}
                for k, value in analysis["candidates"].items()
            },
            indent=2,
        )
    )
