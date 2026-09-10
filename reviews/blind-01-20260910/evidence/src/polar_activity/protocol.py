"""Strict, narrowly scoped Verity Sense IMU decoding (see docs/protocol.md)."""

import math
import struct
from dataclasses import dataclass

from polar_python.constants import PmdMeasurementType, PmdSettingType
from polar_python.models import MeasurementSettings


class AcquisitionError(RuntimeError):
    """An actionable acquisition/protocol failure."""


@dataclass(frozen=True)
class StreamConfig:
    sample_rate: int
    resolution: int
    range: int
    channels: int

    def settings(self, kind: PmdMeasurementType) -> MeasurementSettings:
        values = [self.sample_rate, self.resolution, self.range, self.channels]
        keys = [
            PmdSettingType.SAMPLE_RATE,
            PmdSettingType.RESOLUTION,
            PmdSettingType.RANGE,
            PmdSettingType.CHANNELS,
        ]
        return MeasurementSettings(
            kind,
            [MeasurementSettings.SettingType(k, [v]) for k, v in zip(keys, values, strict=True)],
        )


def parse_settings(payload: bytes) -> dict[str, list[int]]:
    """Validate all fields before using the public library settings decoder."""
    offset = 0
    while offset < len(payload):
        if offset + 2 > len(payload):
            raise AcquisitionError("Truncated PMD settings header")
        kind = PmdSettingType(payload[offset])
        if kind == PmdSettingType.UNKNOWN:
            raise AcquisitionError(f"Unsupported PMD setting {payload[offset]}; retain capture")
        offset += 2 + payload[offset + 1] * kind.field_size
        if offset > len(payload):
            raise AcquisitionError("Truncated PMD settings values")
    response = MeasurementSettings.from_bytes(bytearray(b"\xf0\x01\x02\x00\x00" + payload))
    result: dict[str, list[int]] = {}
    for setting in response.settings:
        result.setdefault(setting.type.name.lower(), []).extend(setting.values)
    return result


def choose_config(settings: dict[str, list[int]], stream: str) -> StreamConfig:
    desired = dict(sample_rate=52, resolution=16, range=8 if stream == "acc" else 2000, channels=3)
    for key, value in desired.items():
        if value not in settings.get(key, []):
            raise AcquisitionError(
                f"{stream.upper()} does not advertise required {key}={value}; "
                f"reported {settings}. Use normal sensor mode, stop other recordings, "
                "then verify again. SDK mode is never enabled automatically."
            )
    return StreamConfig(**desired)


def conversion_factor(settings: dict[str, list[int]]) -> float:
    # Polar's BlePMDClient.getFactor() uses 1.0 when FACTOR is absent.
    # A successful start acknowledgement is allowed to contain no settings.
    if "factor" not in settings:
        return 1.0
    values = settings.get("factor", [])
    if len(values) != 1:
        raise AcquisitionError("Invalid IMU scale factor field: expected exactly one value")
    factor = struct.unpack("<f", values[0].to_bytes(4, "little"))[0]
    if not math.isfinite(factor) or factor <= 0:
        raise AcquisitionError(f"Invalid device scale factor: {factor}")
    return factor


def decode_delta(
    payload: bytes, resolution: int = 16, channels: int = 3, *, wrap_32bit: bool = False
) -> list[list[int]]:
    """Decode reference + little-endian signed delta blocks, rejecting partial data."""
    width = (resolution + 7) // 8
    offset = width * channels
    if len(payload) < offset:
        raise AcquisitionError("Truncated IMU reference sample")
    samples = [
        [
            int.from_bytes(payload[i * width : (i + 1) * width], "little", signed=True)
            for i in range(channels)
        ]
    ]
    while offset < len(payload):
        if offset + 2 > len(payload):
            raise AcquisitionError("Truncated IMU delta header")
        bits, count = payload[offset : offset + 2]
        offset += 2
        if not 1 <= bits <= 32 or count == 0:
            raise AcquisitionError(f"Invalid IMU delta block: bits={bits}, count={count}")
        length = (bits * count * channels + 7) // 8
        if offset + length > len(payload):
            raise AcquisitionError("Truncated IMU delta payload")
        packed = int.from_bytes(payload[offset : offset + length], "little")
        mask = (1 << bits) - 1
        for _ in range(count):
            sample = []
            for channel in range(channels):
                delta = packed & mask
                packed >>= bits
                if delta & (1 << (bits - 1)):
                    delta -= 1 << bits
                value = samples[-1][channel] + delta
                if wrap_32bit:
                    # Float frames delta-encode the IEEE-754 bit patterns as Int32.
                    value = (value + 2**31) % 2**32 - 2**31
                elif not -(2**31) <= value < 2**31:
                    raise AcquisitionError("IMU delta accumulation exceeds signed 32-bit range")
                sample.append(value)
            samples.append(sample)
        offset += length
    return samples


def decode_imu(data: bytes, config: StreamConfig, factor: float) -> tuple[int, list[list[float]]]:
    if len(data) < 10:
        raise AcquisitionError("Truncated PMD packet header")
    kind, frame = data[0] & 0x3F, data[9] & 0x7F
    compressed = bool(data[9] & 0x80)
    if (
        kind not in (2, 5)
        or (kind == 2 and frame not in (0, 1, 2))
        or (kind == 2 and compressed and frame == 2)
        or (kind == 5 and (not compressed or frame not in (0, 1)))
    ):
        raise AcquisitionError(f"Unsupported IMU format type={kind}, frame={frame}")
    if compressed:
        float_frame = kind == 5 and frame == 1
        samples = decode_delta(data[10:], 32 if float_frame else 16, 3, wrap_32bit=float_frame)
        if float_frame:
            samples = [
                [struct.unpack("<f", v.to_bytes(4, "little", signed=True))[0] for v in sample]
                for sample in samples
            ]
        scale = factor * (1000 if kind == 2 and frame == 0 else 1)
    else:
        # Raw ACC types 0/1/2 are already signed milliG, per Polar AccData.
        width = frame + 1
        payload = data[10:]
        if not payload or len(payload) % (3 * width):
            raise AcquisitionError("Truncated raw IMU samples")
        samples = [
            [
                int.from_bytes(payload[j : j + width], "little", signed=True)
                for j in range(i, i + 3 * width, width)
            ]
            for i in range(0, len(payload), 3 * width)
        ]
        scale = 1.0
    scaled = [[v * scale for v in s] for s in samples]
    if any(not math.isfinite(value) for sample in scaled for value in sample):
        raise AcquisitionError("Non-finite IMU sample; raw packet retained")
    return int.from_bytes(data[1:9], "little"), scaled


def decode_hr(data: bytes) -> dict:
    if len(data) < 2:
        raise AcquisitionError("Truncated HR packet")
    flags = data[0]
    width = 2 if flags & 1 else 1
    offset = 1 + width
    if len(data) < offset:
        raise AcquisitionError("Truncated HR value")
    result = {
        "hr_bpm": int.from_bytes(data[1:offset], "little"),
        "contact_supported": bool(flags & 4),
        "contact_detected": bool(flags & 2) if flags & 4 else None,
        "energy_expended": None,
        "rr_intervals_ms": [],
    }
    if flags & 8:
        if len(data) < offset + 2:
            raise AcquisitionError("Truncated HR energy field")
        result["energy_expended"] = int.from_bytes(data[offset : offset + 2], "little")
        offset += 2
    if flags & 16:
        if (len(data) - offset) % 2:
            raise AcquisitionError("Truncated RR interval")
        result["rr_intervals_ms"] = [
            int.from_bytes(data[i : i + 2], "little") * 1000 / 1024
            for i in range(offset, len(data), 2)
        ]
    elif len(data) != offset:
        raise AcquisitionError("Unexpected HR payload bytes")
    return result


def frame_interval_matches(delta_ns: int, count: int, rate: float) -> bool:
    """Allow 2% nominal clock/rate variation or half a sample, whichever is larger.

    Without sequence numbers, small losses cannot be distinguished from rate
    variation. Larger gaps are preserved instead of interpolated away.
    """
    return delta_ns > 0 and abs(delta_ns * rate - count * 10**9) <= max(
        5 * 10**8, count * 20_000_000
    )


class TimestampReconstructor:
    def __init__(self, rate: int):
        self.rate = rate
        self.previous: int | None = None

    def reconstruct(self, last: int, count: int) -> tuple[list[int], str]:
        if count <= 0 or last <= 0:
            raise AcquisitionError("Empty batch or zero device timestamp")
        previous = self.previous
        self.previous = last
        # Compare integer differences, never convert epoch-sized values to floats.
        if previous is not None and frame_interval_matches(last - previous, count, self.rate):
            stamps = [
                previous + ((last - previous) * i + count // 2) // count
                for i in range(1, count + 1)
            ]
            return stamps, "interpolated"
        status = (
            "nominal_first"
            if previous is None
            else ("non_monotonic_frame" if last <= previous else "discontinuity_nominal")
        )
        stamps = [
            last - ((count - 1 - i) * 10**9 + self.rate // 2) // self.rate for i in range(count)
        ]
        if stamps[0] < 0:
            raise AcquisitionError("Device timestamp too small for batch duration")
        if previous is not None and stamps[0] <= previous:
            status = "non_monotonic_frame"
        return stamps, status
