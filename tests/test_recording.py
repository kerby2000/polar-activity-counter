import asyncio
import json

import pytest
from conftest import FakeClient

from polar_activity.device import Packet
from polar_activity.diagnostics import read_rows, stream_quality
from polar_activity.protocol import AcquisitionError
from polar_activity.recorder import PacketBuffer, record_session


def test_end_to_end_lossless_separate_streams(recorded):
    meta = json.loads((recorded / "metadata.json").read_text())
    assert meta["status"] == "complete"
    assert meta["hr_enabled"] is True
    assert meta["device"]["firmware"] == "synthetic-fw"
    assert meta["arm"] == "left"
    for stream in ("acc", "gyro"):
        result = meta["quality"][stream]
        assert result["samples"] == 208
        assert result["packet_endpoint_rate_hz"] == 52
        assert result["estimated_missing_samples"] == 0
        assert result["non_monotonic_timestamps"] == 0
    assert (
        read_rows(recorded / "acc.csv")[0]["device_timestamp_ns"]
        != read_rows(recorded / "gyro.csv")[0]["device_timestamp_ns"]
    )
    raw = [json.loads(line) for line in (recorded / "packets.jsonl").read_text().splitlines()]
    assert len(raw) == 9 and all(bytes.fromhex(item["payload_hex"]) for item in raw)
    assert meta["clock_mapping"]["alignment_uncertainty_ms"] is None


@pytest.mark.parametrize("failure", ["disconnect_during_stream", "malformed"])
def test_partial_failure_preserves_bytes(tmp_path, fake_factory, ble_device, failure):
    setattr(FakeClient, failure, True)
    path = tmp_path / "failed"
    with pytest.raises(AcquisitionError):
        asyncio.run(
            record_session(
                path,
                "s",
                "upper_arm_right",
                "right",
                0.03,
                interactive=False,
                device_factory=fake_factory,
                selected_device=ble_device,
            )
        )
    meta = json.loads((path / "metadata.json").read_text())
    assert meta["status"] == "failed"
    assert len((path / "packets.jsonl").read_text().splitlines()) == 9
    assert not FakeClient.instances[-1].is_connected


def test_hr_failure_does_not_stop_imu(tmp_path, fake_factory, ble_device):
    FakeClient.hr_fails = True
    path = tmp_path / "imu"
    asyncio.run(
        record_session(
            path,
            "s",
            "arm",
            "unknown",
            0.03,
            interactive=False,
            device_factory=fake_factory,
            selected_device=ble_device,
        )
    )
    meta = json.loads((path / "metadata.json").read_text())
    assert not meta["hr_enabled"] and meta["quality"]["gyro"]["samples"] == 208


def test_buffer_overflow_is_counted():
    buffer = PacketBuffer(1)
    packet = Packet("hr", b"\x00\x46", 1, "synthetic")
    buffer.put(packet)
    buffer.put(packet)
    assert buffer.dropped == 1 and buffer.queue.qsize() == 1


def test_quality_ignores_host_jitter_and_finds_missing_duplicate_backwards(recorded):
    rows = read_rows(recorded / "acc.csv")
    for index, row in enumerate(rows):
        row["host_monotonic_ns"] = str(index % 3 * 10**12)
    assert stream_quality(rows, 52)["packet_endpoint_rate_hz"] == 52
    missing = rows[:52] + rows[104:]
    assert stream_quality(missing, 52)["frame_estimated_missing_samples"] == 52
    assert stream_quality(rows + [rows[-1]], 52)["duplicate_timestamps"] == 1
    assert stream_quality(rows + [rows[0]], 52)["non_monotonic_timestamps"] == 1


def test_partial_startup_stops_already_started_acc(tmp_path, fake_factory, ble_device):
    FakeClient.reject = (2, 5)
    directory = tmp_path / "startup-failed"
    with pytest.raises(AcquisitionError, match="code 5"):
        asyncio.run(
            record_session(
                directory,
                "s",
                "arm",
                "unknown",
                0.1,
                interactive=False,
                device_factory=fake_factory,
                selected_device=ble_device,
            )
        )
    assert bytes([3, 2]) in FakeClient.instances[-1].requests
    assert not FakeClient.instances[-1].active
    assert json.loads((directory / "metadata.json").read_text())["status"] == "failed"


def test_cancellation_finalizes_and_disconnects(tmp_path, fake_factory, ble_device):
    directory = tmp_path / "cancelled"

    async def run():
        task = asyncio.create_task(
            record_session(
                directory,
                "s",
                "arm",
                "unknown",
                300,
                interactive=False,
                device_factory=fake_factory,
                selected_device=ble_device,
            )
        )
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert not FakeClient.instances[-1].is_connected
    meta = json.loads((directory / "metadata.json").read_text())
    assert meta["status"] == "interrupted" and meta["quality"]["acc"]["samples"] == 208
