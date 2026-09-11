import struct

import pytest
from conftest import EPOCH, packet

from polar_activity.protocol import (
    AcquisitionError,
    StreamConfig,
    TimestampReconstructor,
    choose_config,
    conversion_factor,
    decode_delta,
    decode_hr,
    decode_imu,
    parse_settings,
)


def test_signed_one_bit_and_multiple_blocks():
    # One-bit [-1,0,-1], then 4-bit [2,-2,3], LSB first.
    payload = struct.pack("<hhh", 10, -10, 0) + bytes.fromhex("01 01 05 04 01 e2 03")
    assert decode_delta(payload) == [[10, -10, 0], [9, -10, -1], [11, -12, 2]]


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        bytes(5),
        bytes(6) + b"\x08",
        bytes(6) + b"\x08\x01\x00",
        bytes(6) + b"\x21\x01",
        bytes(6) + b"\x08\x00",
    ],
)
def test_truncated_or_invalid_deltas_rejected(payload):
    with pytest.raises(AcquisitionError):
        decode_delta(payload)


def test_scale_preserves_fraction_and_original_timestamp():
    raw = bytes([2]) + EPOCH.to_bytes(8, "little") + b"\x80" + struct.pack("<hhh", 3, -3, 10)
    last, samples = decode_imu(raw, StreamConfig(52, 16, 8, 3), 0.00025)
    assert last == EPOCH
    assert samples == [[0.75, -0.75, 2.5]]
    _, samples = decode_imu(packet(5), StreamConfig(52, 16, 2000, 3), 0.125)
    assert len(samples) == 52 and samples[-1] == [0, 0, 125]


@pytest.mark.parametrize("raw", [b"", packet()[:-1], bytes([7]) + packet()[1:]])
def test_bad_packets_rejected(raw):
    with pytest.raises(AcquisitionError):
        decode_imu(raw, StreamConfig(52, 16, 8, 3), 1)


def test_large_epoch_interpolation_no_float_precision_loss():
    recon = TimestampReconstructor(52)
    first, method = recon.reconstruct(EPOCH, 52)
    second, method2 = recon.reconstruct(EPOCH + 10**9, 52)
    assert first[-1] == EPOCH and second[-1] == EPOCH + 10**9
    assert method == "nominal_first" and method2 == "interpolated"
    assert second[0] - EPOCH in (19_230_769, 19_230_770)
    assert all(a < b for a, b in zip(first + second, (first + second)[1:], strict=False))


def test_missing_batch_not_interpolated_away():
    recon = TimestampReconstructor(52)
    recon.reconstruct(EPOCH, 52)
    stamps, method = recon.reconstruct(EPOCH + 2 * 10**9, 52)
    assert method == "discontinuity_nominal"
    assert stamps[0] - EPOCH > 10**9
    assert recon.reconstruct(EPOCH, 52)[1] == "non_monotonic_frame"
    assert recon.reconstruct(EPOCH, 52)[1] == "non_monotonic_frame"


def test_increasing_endpoint_with_overlapping_batch_is_rejected():
    recon = TimestampReconstructor(52)
    recon.reconstruct(EPOCH, 52)
    _, method = recon.reconstruct(EPOCH + 100_000_000, 52)
    assert method == "non_monotonic_frame"


def test_actual_sensor_rate_does_not_create_backwards_samples():
    # Real packet counts/intervals from the failed startup check, with a neutral epoch.
    recon = TimestampReconstructor(52)
    stamps, _ = recon.reconstruct(EPOCH, 89)
    next_stamps, method = recon.reconstruct(EPOCH + 1_567_735_600, 83)
    assert method == "interpolated"
    assert next_stamps[0] > stamps[-1]
    final_stamps, method = recon.reconstruct(EPOCH + 1_718_848_530, 8)
    assert method == "interpolated"
    assert final_stamps[0] > next_stamps[-1]
    assert final_stamps[-1] == EPOCH + 1_718_848_530


def test_slower_nominal_clock_is_not_a_missing_packet():
    recon = TimestampReconstructor(52)
    recon.reconstruct(EPOCH, 100)
    last = EPOCH + 1_942_307_692  # 1% slower than nominal, still within tolerance.
    stamps, method = recon.reconstruct(last, 100)
    assert method == "interpolated" and stamps[0] > EPOCH and stamps[-1] == last


def test_hr_flags_energy_and_rr():
    result = decode_hr(bytes.fromhex("1f 2c 01 0a 00 00 04 00 02"))
    assert result["hr_bpm"] == 300
    assert result["energy_expended"] == 10
    assert result["rr_intervals_ms"] == [1000, 500]
    assert result["contact_detected"] is True
    assert decode_hr(bytes([0, 70]))["contact_detected"] is None
    with pytest.raises(AcquisitionError):
        decode_hr(bytes.fromhex("10 46 01"))


def test_settings_and_factors():
    assert parse_settings(bytes.fromhex("00 02 1a 00 34 00")) == {"sample_rate": [26, 52]}
    for payload in (bytes.fromhex("00 02 34 00"), bytes([255, 0]), bytes([0])):
        with pytest.raises(AcquisitionError):
            parse_settings(payload)
    assert (
        conversion_factor({"factor": [int.from_bytes(struct.pack("<f", 0.125), "little")]}) == 0.125
    )
    assert conversion_factor({}) == 1.0
    for settings in ({"factor": []}, {"factor": [0]}, {"factor": [0x7FC00000]}):
        with pytest.raises(AcquisitionError):
            conversion_factor(settings)
    with pytest.raises(AcquisitionError, match="does not advertise"):
        choose_config({"sample_rate": [104]}, "acc")


def test_acc_type1_without_factor_preserves_milligravity():
    # Matches the sensor's successful empty start response f002020000 and 0x81 frame.
    factor = conversion_factor(parse_settings(b""))
    raw = b"\x02" + EPOCH.to_bytes(8, "little") + b"\x81" + struct.pack("<hhh", -14, 142, -1012)
    last, samples = decode_imu(raw, StreamConfig(52, 16, 8, 3), factor)
    assert last == EPOCH
    assert samples == [[-14, 142, -1012]]


@pytest.mark.parametrize("frame", [0, 1, 2])
def test_raw_acc_is_already_milligravity(frame):
    width = frame + 1
    payload = b"".join(value.to_bytes(width, "little", signed=True) for value in [-12, 7, 100])
    raw = b"\x02" + EPOCH.to_bytes(8, "little") + bytes([frame]) + payload
    _, samples = decode_imu(raw, StreamConfig(52, 16, 8, 3), 0.001)
    assert samples == [[-12, 7, 100]]


def test_gyro_type1_float_delta_vector():
    # IEEE-754 bit-pattern deltas: [1,10,20] -> [10,20,1.1].
    payload = bytes.fromhex("0000803f000020410000a0411c010000a001000008cdccec0d")
    raw = b"\x05" + EPOCH.to_bytes(8, "little") + b"\x81" + payload
    last, samples = decode_imu(raw, StreamConfig(52, 16, 2000, 3), 0.5)
    assert last == EPOCH
    assert samples[0] == [0.5, 5, 10]
    assert samples[1] == pytest.approx([5, 10, 0.55])


def test_gyro_float_delta_wraps_bit_patterns_across_sign():
    # IEEE-754 1.0 plus a signed -2**31 bit delta is -1.0 (Int32 wrap).
    payload = struct.pack("<fff", 1, 1, 1) + bytes([32, 1]) + struct.pack("<iii", -(2**31), 0, 0)
    raw = b"\x05" + EPOCH.to_bytes(8, "little") + b"\x81" + payload
    _, samples = decode_imu(raw, StreamConfig(52, 16, 2000, 3), 1)
    assert samples == [[1, 1, 1], [-1, 1, 1]]


def test_unsupported_raw_gyro_and_nonfinite_float_are_rejected():
    header = b"\x05" + EPOCH.to_bytes(8, "little")
    for raw in (
        header + b"\x00" + bytes(6),
        header + b"\x81" + struct.pack("<fff", float("nan"), 0, 0),
    ):
        with pytest.raises(AcquisitionError):
            decode_imu(raw, StreamConfig(52, 16, 2000, 3), 1)
