import asyncio
import json
import logging
from functools import partial

import pytest
from conftest import FakeClient

from polar_activity.device import Packet, SenseDevice
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


def test_empty_acc_start_ack_records_both_streams_before_ready(
    tmp_path, fake_factory, ble_device, capsys
):
    FakeClient.omit_acc_factor = True
    path = tmp_path / "empty-acc-start"
    asyncio.run(
        record_session(
            path,
            "s",
            "upper_arm_left",
            "left",
            0.03,
            interactive=False,
            device_factory=fake_factory,
            selected_device=ble_device,
        )
    )
    meta = json.loads((path / "metadata.json").read_text())
    assert meta["status"] == "complete"
    assert meta["scale_factors"] == {"acc": 1.0, "gyro": 0.125}
    assert meta["scale_factor_sources"] == {"acc": "polar_sdk_default", "gyro": "device"}
    assert meta["quality"]["acc"]["samples"] == meta["quality"]["gyro"]["samples"] == 208
    assert float(read_rows(path / "acc.csv")[0]["acc_z_mg"]) == 1000
    assert meta["recording_ready_time_s"] >= meta["streaming_started_time_s"]
    output = capsys.readouterr().out
    assert "READY: receiving ACC and GYRO" in output
    assert "208 / 208 samples saved" in output
    assert any(exchange["response_hex"] == ["f002020000"] for exchange in meta["control_exchanges"])


def test_bad_samples_never_announce_ready(tmp_path, fake_factory, ble_device, capsys):
    FakeClient.malformed = True
    with pytest.raises(AcquisitionError):
        asyncio.run(
            record_session(
                tmp_path / "bad-start",
                "s",
                "arm",
                "unknown",
                0.03,
                interactive=False,
                device_factory=fake_factory,
                selected_device=ble_device,
            )
        )
    assert "READY:" not in capsys.readouterr().out


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


def test_quality_allows_nominal_rate_variation(recorded):
    rows = read_rows(recorded / "gyro.csv")
    origin = int(rows[0]["device_timestamp_ns"])
    for row in rows:
        for key in ("device_timestamp_ns", "packet_timestamp_ns"):
            row[key] = str(origin + (int(row[key]) - origin) * 982 // 1000)
    quality = stream_quality(rows, 52)
    assert quality["packet_endpoint_rate_hz"] == pytest.approx(52 / 0.982)
    assert quality["frame_discontinuities"] == 0
    assert quality["frame_estimated_missing_samples"] == 0


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


@pytest.mark.parametrize("phase", ["startup", "streaming"])
def test_cancellation_finalizes_and_disconnects(tmp_path, fake_factory, ble_device, phase):
    directory = tmp_path / "cancelled"

    async def run():
        devices = []
        inspecting = asyncio.Event()

        def factory(*args, **kwargs):
            device = fake_factory(*args, **kwargs)
            devices.append(device)
            if phase == "startup":

                async def inspect():
                    inspecting.set()
                    await asyncio.Event().wait()

                device.inspect = inspect
            return device

        task = asyncio.create_task(
            record_session(
                directory,
                "s",
                "arm",
                "unknown",
                300,
                interactive=False,
                device_factory=factory,
                selected_device=ble_device,
            )
        )
        # Synchronize on the acquisition stage, not a Windows scheduling delay.
        async with asyncio.timeout(2):
            if phase == "startup":
                await inspecting.wait()
            else:
                while not devices or "hr" not in devices[0].active:
                    await asyncio.sleep(0.005)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert not FakeClient.instances[-1].is_connected
    meta = json.loads((directory / "metadata.json").read_text())
    assert meta["status"] == "interrupted"
    assert meta["quality"]["acc"]["samples"] == (208 if phase == "streaming" else 0)


def test_normal_finish_records_termination_before_cleanup(recorded):
    meta = json.loads((recorded / "metadata.json").read_text())
    assert meta["termination"]["reason"] == "duration_reached"
    assert not meta["termination"]["disconnected_before_cleanup"]
    assert set(meta["termination"]["last_notification_age_s"]) == {"acc", "gyro", "hr"}
    disconnect = next(e for e in meta["connection_events"] if e["event"] == "disconnected")
    assert disconnect["during_cleanup"]
    assert "cleanup_started" in (recorded / "connection.log").read_text()


def test_live_disconnect_records_evidence_and_preserves_packets(tmp_path, ble_device):
    class DisconnectClient(FakeClient):
        async def start_notify(self, uuid, callback):
            await super().start_notify(uuid, callback)
            if uuid.endswith("2a37-0000-1000-8000-00805f9b34fb"):
                asyncio.get_running_loop().call_later(0.05, self.lose_connection)

        def lose_connection(self):
            logging.getLogger("bleak.backends.winrt.client").debug("Synthetic GATT CLOSED")
            self.is_connected = False
            self.disconnected_callback(self)

        async def stop_notify(self, uuid):
            assert self.is_connected, "Do not attempt HR cleanup after link loss"
            await super().stop_notify(uuid)

    path = tmp_path / "link-loss"
    logger = logging.getLogger("bleak.backends.winrt.client")
    original = (logger.level, logger.propagate, list(logger.handlers))
    with pytest.raises(AcquisitionError, match="Sensor disconnected"):
        asyncio.run(
            record_session(
                path,
                "synthetic",
                "arm",
                "unknown",
                1,
                interactive=False,
                device_factory=partial(SenseDevice, client_factory=DisconnectClient),
                selected_device=ble_device,
            )
        )
    meta = json.loads((path / "metadata.json").read_text())
    assert meta["termination"]["reason"] == "disconnected"
    assert meta["termination"]["disconnected_before_cleanup"]
    assert meta["quality"]["acc"]["samples"] == 208
    assert not meta["warnings"]
    disconnect = next(e for e in meta["connection_events"] if e["event"] == "disconnected")
    assert not disconnect["during_cleanup"]
    assert "Synthetic GATT CLOSED" in (path / "connection.log").read_text()
    assert original == (logger.level, logger.propagate, list(logger.handlers))
