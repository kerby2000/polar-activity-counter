"""Opt-in, bounded prefix import of original RecoFit continuous MAT-v5 cells.

The release is one 1.57 GB compressed variable. Decode only its initial subject
cells, rebuild a valid smaller MAT container, then use SciPy's public MAT reader.
Original cell bytes are retained. This is not a download of the entire LFS object.
"""

import struct
import urllib.request
import zlib
from pathlib import Path

import numpy as np
from scipy.io import loadmat

from ..counter import detect_sets
from ..recognition import motion_blocks
from .counters import mmfit_counter
from .runner import digest, save

COMMIT = "fd4c44508c0f76118d8e8234a2832acafc64e767"
OBJECT_SHA256 = "d14a8fa3a6ddb6740ff09f7aa4a3039d3ee5524513a8d4b65ac66e00d14ab509"
SIZE = 1571881721
URL = (
    "https://media.githubusercontent.com/media/microsoft/"
    f"Exercise-Recognition-from-Wearable-Sensors/{COMMIT}/exercise_data.50.0000_multionly.mat"
)


def element(kind, data):
    return struct.pack("<II", kind, len(data)) + data + b"\0" * ((-len(data)) % 8)


class InflatedPrefix:
    def __init__(self, stream, budget=128 * 1024 * 1024):
        self.stream, self.budget = stream, budget
        self.decoder = zlib.decompressobj()
        self.buffer = bytearray()
        self.compressed_read = 0

    def read(self, n):
        if n > 256 * 1024 * 1024:
            raise ValueError("MAT element exceeds bounded subset memory")
        while len(self.buffer) < n:
            chunk = self.stream.read(min(65536, self.budget - self.compressed_read))
            if not chunk:
                raise ValueError("Prefix budget/stream exhausted before a complete subject cell")
            self.compressed_read += len(chunk)
            self.buffer.extend(self.decoder.decompress(chunk))
        data = bytes(self.buffer[:n])
        del self.buffer[:n]
        return data

    def tag(self):
        header = self.read(8)
        kind, size = struct.unpack("<II", header)
        if kind >> 16:
            small_size = kind >> 16
            if small_size > 4:
                raise ValueError("Malformed small MAT element")
            return kind & 0xFFFF, header[4 : 4 + small_size], header
        payload = self.read(size)
        padding = self.read((-size) % 8)
        return kind, payload, header + payload + padding


def subset_bytes(header, stream, subjects=2):
    if len(header) != 136 or header[126:128] != b"IM":
        raise ValueError("Expected little-endian MAT-v5; not an LFS pointer")
    kind, _ = struct.unpack("<II", header[128:136])
    if kind != 15:
        raise ValueError("Expected the compressed RecoFit subject-data variable")
    decoder = InflatedPrefix(stream)
    kind, _ = struct.unpack("<II", decoder.read(8))
    if kind != 14:
        raise ValueError("Expected MAT matrix")
    _, flags, flags_raw = decoder.tag()
    _, dims, _ = decoder.tag()
    _, name, name_raw = decoder.tag()
    shape = struct.unpack("<" + "i" * (len(dims) // 4), dims)
    if name != b"subject_data" or flags[0] != 1 or len(shape) != 2 or shape[1] != 1:
        raise ValueError("Unexpected multionly cell-matrix schema")
    cells = []
    for _ in range(min(subjects, shape[0])):
        kind, _, raw = decoder.tag()
        if kind != 14:
            raise ValueError("Expected a complete subject cell")
        cells.append(raw)
    payload = flags_raw + element(5, struct.pack("<ii", len(cells), 1)) + name_raw + b"".join(cells)
    return header[:128] + element(14, payload), {
        "original_shape": list(shape),
        "selected_subject_indices_1_based": list(range(1, len(cells) + 1)),
        "compressed_prefix_bytes_read": decoder.compressed_read + 136,
        "complete_source_object_downloaded": False,
    }


def _visits(cell):
    if isinstance(cell, dict):
        return [cell]
    if isinstance(cell, (list, tuple, np.ndarray)):
        return [x for x in np.asarray(cell, dtype=object).ravel() if isinstance(x, dict)]
    return []


def _plain(value):
    # simplify_cells leaves mat_struct values inside some nested cell arrays.
    if hasattr(value, "_fieldnames"):
        return {name: _plain(getattr(value, name)) for name in value._fieldnames}
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, np.ndarray) and value.dtype == object:
        return [_plain(v) for v in value]
    return value


def load_subset(path):
    if Path(path).read_bytes()[:40].startswith(b"version https://git-lfs"):
        raise ValueError("Git LFS pointer is not sensor data")
    loaded = loadmat(path, simplify_cells=True)
    cells = _plain(loaded["subject_data"])
    if isinstance(cells, dict):
        cells = [cells]
    sessions = []
    for subject, cell in enumerate(cells, 1):
        for visit, record in enumerate(_visits(cell), 1):
            a = np.asarray(record["data"]["accelDataMatrix"], float)
            g = np.asarray(record["data"]["gyroDataMatrix"], float)
            if a.ndim != 2 or g.ndim != 2 or a.shape[1] != 4 or g.shape[1] != 4:
                raise ValueError("Expected timestamp and XYZ matrices")
            if not np.isfinite(a).all() or not np.isfinite(g).all():
                raise ValueError("Non-finite public IMU data")
            if np.any(np.diff(a[:, 0]) <= 0) or np.any(np.diff(g[:, 0]) <= 0):
                raise ValueError("Public IMU timestamps not increasing")
            activities = np.asarray(record["activityStartMatrix"], dtype=object)
            if activities.ndim == 1:
                activities = activities[None, :]
            if activities.ndim != 2 or activities.shape[1] < 5:
                raise ValueError("Expected at least five activity annotation columns")
            labels = []
            left, right = max(a[0, 0], g[0, 0]), min(a[-1, 0], g[-1, 0])
            for label, start, end, _, count in activities[:, :5]:
                start, end = float(start), float(end)
                if not np.isfinite([start, end]).all() or start >= end:
                    raise ValueError("Invalid public label bounds")
                # Some labels include preparation outside one stream's overlap.
                if start < left or end > right:
                    timing = "outside_stream_overlap; excluded from scoring"
                else:
                    timing = "within_overlap"
                try:
                    number = float(count)
                    number = (
                        int(number)
                        if np.isfinite(number) and number >= 0 and number.is_integer()
                        else None
                    )
                except (TypeError, ValueError):
                    number = None
                labels.append(
                    {
                        "activity_original": str(label),
                        "start_time_s": start,
                        "end_time_s": end,
                        "reference_count": number,
                        "timing": timing,
                    }
                )
            sessions.append(
                {
                    "subject": subject,
                    "visit": visit,
                    "arrays": (a[:, 0], a[:, 1:] * 1000, g[:, 0], g[:, 1:]),
                    "labels": labels,
                    "placement": "arm-worn; precise placement unverified",
                    "acc_conversion": "g to mg x1000; gyro retained degrees/s",
                }
            )
    return sessions


def run_external(output, subjects=2):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    subset = output / "recofit-continuous-prefix.mat"
    if not subset.exists():
        request = urllib.request.Request(URL, headers={"Range": "bytes=0-134217727"})
        with urllib.request.urlopen(request, timeout=60) as stream:
            if stream.status != 206 or not stream.headers.get("Content-Range", "").startswith(
                "bytes 0-"
            ):
                raise ValueError("Server did not supply the bounded prefix range")
            etag = stream.headers.get("ETag", "").strip('"')
            if etag != OBJECT_SHA256:
                raise ValueError("Unexpected upstream object identity")
            small_mat, provenance = subset_bytes(stream.read(136), stream, subjects)
        subset.write_bytes(small_mat)
        save(
            output / "source.json",
            {
                "url": URL,
                "commit": COMMIT,
                "object_size": SIZE,
                "provider_object_sha256_etag": etag,
                "subset_sha256": digest(subset),
                **provenance,
                "license": "CDLA-Permissive-2.0; official repository LICENSE",
            },
        )
    sessions = [s for s in load_subset(subset) if s["visit"] == 1]
    if not sessions:
        raise ValueError("No usable public sessions were decoded; external reference not executed")
    results = []
    for session in sessions:
        continuous = detect_sets(*session["arrays"])
        save(output / f"s{session['subject']}-v{session['visit']}-continuous.json", continuous)
        blocks = motion_blocks(session["arrays"])
        for i, label in enumerate(session["labels"]):
            if (
                label["timing"] != "within_overlap"
                or label["activity_original"].lower() == "non-exercise"
            ):
                continue
            for t, acc, gyro in blocks:
                if t[0] <= label["start_time_s"] < label["end_time_s"] <= t[-1]:
                    estimate = mmfit_counter(
                        t, acc, gyro, label["start_time_s"], label["end_time_s"]
                    )
                    save(
                        output / f"s{session['subject']}-v{session['visit']}-set{i}.json", estimate
                    )
                    results.append(
                        {
                            "subject": session["subject"],
                            "visit": session["visit"],
                            **label,
                            "predicted_count": estimate["count"],
                            "status": estimate["status"],
                        }
                    )
    if not results:
        raise ValueError("No public intervals could be evaluated; external reference not executed")
    save(
        output / "report.json",
        {
            "status": "EXTERNAL_REFERENCE_EXECUTED",
            "session_count": len(sessions),
            "comparisons": results,
            "training_mixed_with_polar": False,
            "method": "Assisted MM-Fit-inspired counter, generic 0.5–6s period limits",
            "semantic_mapping": "Original exercise names retained; no equivalence to Polar classes",
            "selection": "First complete visit from each of the first selected subject cells",
        },
    )
    return output
