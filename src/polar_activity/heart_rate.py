"""Heart-rate presentation only; these values never enter the motion classifier."""

import csv
import hashlib
import math
from pathlib import Path

import numpy as np

HR_FIELDS = [
    "time_s",
    "host_monotonic_ns",
    "host_time_utc",
    "packet_id",
    "hr_bpm",
    "contact_supported",
    "contact_detected",
    "energy_expended",
    "rr_intervals_ms",
]
OFFLINE_HR_TIMING = (
    "Approximate HR timing: recording header + sample index at nominal 1 Hz; "
    "the header has one-second resolution and HR has no per-sample timestamps. "
    "First-sample phase, clock drift and missing internal samples are not measured."
)


def valid_bpm(row):
    try:
        bpm = float(row["hr_bpm"])
        if not math.isfinite(bpm) or bpm <= 0:
            return None
        if row.get("contact_supported", "").lower() in ("true", "1") and row.get(
            "contact_detected", ""
        ).lower() in ("false", "0"):
            return None
        return bpm
    except (ValueError, TypeError, KeyError):
        return None


def summary(rows):
    values = [bpm for row in rows if (bpm := valid_bpm(row)) is not None]
    return {
        "samples": len(rows),
        "valid_samples": len(values),
        "invalid_or_no_contact_samples": len(rows) - len(values),
        "mean_bpm": sum(values) / len(values) if values else None,
        "min_bpm": min(values, default=None),
        "max_bpm": max(values, default=None),
    }


def read_heart_rate(session):
    path = Path(session) / "hr.csv"
    if path.exists():
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    else:
        rows = []
    approximate = any(r.get("timestamp_method") == "offline_header_1hz_estimate" for r in rows)
    return rows, {
        **summary(rows),
        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None,
        "used_for_recognition": False,
        "timing": OFFLINE_HR_TIMING
        if approximate
        else "Online host arrival time"
        if rows
        else None,
    }


def plot_heart_rate(axis, rows):
    """Break lines at missing/invalid readings instead of inventing continuity."""
    times, values = [], []
    previous = None
    for row in rows:
        try:
            t = float(row["time_s"])
        except (ValueError, TypeError, KeyError):
            times.append(np.nan)
            values.append(np.nan)
            previous = None
            continue
        if not math.isfinite(t):
            continue
        if previous is not None and (t <= previous or t - previous > 3):
            times.append(np.nan)
            values.append(np.nan)
        times.append(t)
        value = valid_bpm(row)
        values.append(value if value is not None else np.nan)
        previous = t
    axis.plot(times, values, color="#b12657", lw=1.3, marker=".", markersize=2)
    axis.set_ylabel("Heart rate (bpm)")
    axis.margins(y=0.22)
    axis.grid(alpha=0.2)
    stats = summary(rows)
    if not stats["valid_samples"]:
        label = "HR not recorded" if not rows else "No valid HR readings (check sensor contact)"
    else:
        label = (
            f"Mean {stats['mean_bpm']:.0f} bpm · "
            f"range {stats['min_bpm']:g}–{stats['max_bpm']:g} bpm"
        )
        if any(r.get("timestamp_method") == "offline_header_1hz_estimate" for r in rows):
            label += " · approximate 1 Hz timing"
    axis.text(
        0.01,
        0.96,
        label,
        transform=axis.transAxes,
        va="top",
        fontsize=9,
        bbox={"facecolor": "white", "alpha": 0.8, "edgecolor": "none"},
    )
