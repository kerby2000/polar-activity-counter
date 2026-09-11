"""Polar magnetic-field decoding and inspection; no heading/altitude is inferred."""

import hashlib
import math
from collections import Counter
from pathlib import Path

import numpy as np

from .protocol import AcquisitionError, decode_delta

CALIBRATION_STATUS = {0: "unknown", 1: "poor", 2: "ok", 3: "good"}


def decode_mag(data, config, factor):
    if len(data) < 10 or data[0] & 0x3F != 6 or data[9] not in (0x80, 0x81):
        raise AcquisitionError("Unsupported or truncated magnetometer frame")
    if config.resolution != 16 or config.channels not in (3, 4):
        raise AcquisitionError("Unsupported magnetometer resolution/channels")
    if not math.isfinite(factor) or factor <= 0:
        raise AcquisitionError("Invalid magnetometer scale factor")
    kind = data[9] & 0x7F
    raw = decode_delta(data[10:], 16, 3 if kind == 0 else 4)
    # SDK MagData: type 0 is Gauss after FACTOR; type 1 is milliGauss.
    # Store microtesla in both cases: 1 G = 100 uT, 1 mG = 0.1 uT.
    scale = factor * (100 if kind == 0 else 0.1)
    samples = [[v * scale for v in sample[:3]] for sample in raw]
    if any(not math.isfinite(v) for xyz in samples for v in xyz):
        raise AcquisitionError("Non-finite magnetometer sample")
    statuses = [sample[3] if kind == 1 else None for sample in raw]
    return int.from_bytes(data[1:9], "little"), samples, statuses


def calibration_fields(value):
    return {
        "calibration_status_raw": "" if value is None else value,
        "calibration_status": (
            "not_reported" if value is None else CALIBRATION_STATUS.get(value, "unrecognized")
        ),
    }


def read_magnetometer(session):
    from .diagnostics import read_rows

    path = Path(session) / "mag.csv"
    rows = read_rows(path)
    values = np.array([[float(r[f"mag_{a}_ut"]) for a in "xyz"] for r in rows])
    valid = np.isfinite(values).all(axis=1) if rows else np.array([], dtype=bool)
    norms = np.linalg.norm(values[valid], axis=1) if valid.any() else np.array([])
    p5, median, p95 = np.percentile(norms, [5, 50, 95]).tolist() if len(norms) else (None,) * 3
    return rows, {
        "samples": len(rows),
        "valid_samples": int(valid.sum()),
        "field_magnitude_min_ut": float(norms.min()) if len(norms) else None,
        "field_magnitude_max_ut": float(norms.max()) if len(norms) else None,
        "field_magnitude_p5_ut": p5,
        "field_magnitude_median_ut": median,
        "field_magnitude_p95_ut": p95,
        "field_relative_p5_p95_span": (p95 - p5) / median if median else None,
        "heading_calibration_verified": False,
        "calibration_status_counts": dict(
            Counter(r.get("calibration_status", "not_reported") for r in rows)
        ),
        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None,
        "used_for_recognition": False,
        "caveat": "Field measurements; not calibrated heading, altitude or an activity decision.",
    }


def plot_magnetometer(axis, rows):
    times, values = [], []
    previous = None
    for row in rows:
        t = float(row["time_s"])
        xyz = [float(row[f"mag_{a}_ut"]) for a in "xyz"]
        if previous is not None and (t <= previous or t - previous > 0.15):
            times.append(np.nan)
            values.append([np.nan] * 3)
        times.append(t)
        values.append(xyz)
        previous = t
    if values:
        values = np.asarray(values)
        for i, (name, color) in enumerate(
            zip("XYZ", ("#2563a6", "#b05e13", "#247348"), strict=True)
        ):
            axis.plot(
                times, values[:, i], lw=0.8, color=color, label=name, marker=".", markersize=2
            )
        axis.plot(
            times,
            np.linalg.norm(values, axis=1),
            lw=1,
            color="#604093",
            label="Magnitude",
            marker=".",
            markersize=2,
        )
        axis.legend(loc="upper right", ncol=4, fontsize=8)
        axis.margins(y=0.2)
    else:
        axis.text(0.02, 0.9, "Magnetometer not recorded", transform=axis.transAxes)
    axis.set_ylabel("Magnetic field (µT)")
    axis.grid(alpha=0.2)
