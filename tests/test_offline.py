"""Sensor-memory protocol, loss handling and persistent start/download lifecycle."""

import asyncio
import hashlib
import json
import random
import struct
from functools import partial
from types import SimpleNamespace

import pytest
from conftest import EPOCH, FakeClient, packet

from polar_activity.counting import count_session
from polar_activity.device import CP, SenseDevice
from polar_activity.diagnostics import read_rows
from polar_activity.offline import MANIFEST, run_offline, select_new_files, verified_download
from polar_activity.offline_data import export_recordings, parse_recording
from polar_activity.pftp import MTU, FileTransfer, TransferError, field, frames, protobuf
from polar_activity.protocol import AcquisitionError


def rec_file(kind=2, start=0, batches=3):
    settings = (
        bytes([0, 1, 52, 0, 1, 1, 16, 0, 2, 1])
        + struct.pack("<H", 8 if kind == 2 else 2000)
        + bytes([4, 1, 3, 5, 1])
        + struct.pack("<f", 1 if kind == 2 else 0.07)
    )
    header = b"\x00" + struct.pack("<IIII", 0x3D7C4C2B, 1, 0, 0x1234) + b"2026-09-10 13:00:00\x00"
    data = header + bytes([len(settings)]) + settings + b"\x00"
    for index in range(start, start + batches):
        value = packet(kind, EPOCH + index * 10**9)
        if kind == 2:
            value = value[:9] + b"\x81" + value[10:]
        data += struct.pack("<H", len(value)) + value
    return data


def test_offline_file_retains_units_headers_and_original_packets():
    acc, gyro = parse_recording(rec_file(), "acc"), parse_recording(rec_file(5), "gyro")
    assert acc["header"]["version"] == 1
    assert acc["frames"][0]["samples"][0] == [0, 0, 1000]
    assert gyro["frames"][0]["samples"][0][2] == pytest.approx(70)
    assert len(acc["frames"]) == 3
    assert acc["frames"][-1]["last"] == EPOCH + 2 * 10**9
    for frame in acc["frames"]:
        assert (
            rec_file()[frame["offset"] : frame["offset"] + len(frame["payload"])]
            == frame["payload"]
        )


@pytest.mark.parametrize(
    "damage",
    ["truncated", "signature", "encrypted", "version", "date", "wrong_type", "frame_length"],
)
def test_offline_file_rejects_corruption(damage):
    raw = bytearray(rec_file())
    stream = "acc"
    if damage == "truncated":
        raw = raw[:-1]
    elif damage == "signature":
        raw[1] ^= 1
    elif damage == "encrypted":
        raw[0] = 2
    elif damage == "version":
        raw[5] = 99
    elif damage == "date":
        raw[36] = 1
    elif damage == "wrong_type":
        stream = "gyro"
    else:
        first_offset = parse_recording(bytes(raw), stream)["frames"][0]["offset"]
        raw[first_offset - 2 : first_offset] = b"\x00\x00"
    with pytest.raises(AcquisitionError):
        parse_recording(bytes(raw), stream)


@pytest.mark.parametrize("data", [b"\x80", b"\x0a\x04x", b"\x00", b"\x0b", b"\x08" + b"\xff" * 10])
def test_protobuf_rejects_malformed_fields(data):
    with pytest.raises(AcquisitionError):
        protobuf(data)


def test_known_protobuf_fields_and_rfc76_frames():
    assert protobuf(b"\x08\x80\x02\x12\x03ACC\x08\x01") == {1: [256, 1], 2: [b"ACC"]}
    assert list(frames(b"abcdef", size=4)) == [b"\x06abc", b"\x13def"]
    assert list(frames(b"", size=20)) == [b"\x02"]


class TransferClient:
    is_connected = True

    def __init__(self):
        self.writes = []

    async def write_gatt_char(self, uuid, data, response):
        self.writes.append(bytes(data))
        if len(self.writes) == 1:
            for reply in self.replies:
                self.ftp.queue.put_nowait(reply)


def transfer_fixture(replies):
    client = TransferClient()
    device = SimpleNamespace(client=client, lock=asyncio.Lock())
    ftp = FileTransfer(device, timeout=0.01)
    client.ftp, client.replies = ftp, replies
    return ftp


def test_transfer_accepts_empty_more_and_reassembles_payload():
    async def check():
        ftp = transfer_fixture([b"\x06", b"\x17hello", b"\x23world"])
        assert await ftp.query(5) == b"helloworld"
        assert ftp.client.writes == [b"\x02\x05\x80"]

    asyncio.run(check())


def test_transfer_decodes_full_16bit_error():
    async def check():
        ftp = transfer_fixture([b"\x00\x2f\x01"])
        with pytest.raises(TransferError) as error:
            await ftp.query(5)
        assert error.value.code == 303

    asyncio.run(check())


@pytest.mark.parametrize("bad_tail", [False, True])
def test_repeated_terminal_payload_cannot_contaminate_next_get(bad_tail):
    async def check():
        ftp = transfer_fixture([b"\x06abc", b"\x13end", b"\x23" + (b"bad" if bad_tail else b"end")])
        if bad_tail:
            with pytest.raises(AcquisitionError, match="after file-transfer end"):
                await ftp.get("/ACC.REC")
            assert ftp.poisoned
        else:
            assert await ftp.get("/ACC.REC") == b"abcendend"
            assert ftp.queue.empty()
            assert len(ftp.last_packets) == 3
            assert ftp.exchanges[-1]["received_bytes"] == 9

    asyncio.run(check())


def varied_rec_file():
    raw = bytearray(rec_file())
    rng = random.Random(103)
    for f in parse_recording(bytes(raw), "acc")["frames"]:
        start = f["offset"] + 18  # compressed deltas after reference/bits/count
        end = f["offset"] + len(f["payload"])
        raw[start:end] = bytes(rng.randrange(256) for _ in range(end - start))
    return bytes(raw)


def duplicated_transfer(raw, duplicate_at):
    payloads = [p[1:] for p in frames(raw, size=80)]
    payloads.insert(duplicate_at, payloads[duplicate_at - 1])
    return [
        bytes([(i % 16) << 4 | (i > 0) | (2 if i == len(payloads) - 1 else 6)]) + p
        for i, p in enumerate(payloads)
    ]


@pytest.mark.parametrize(
    "scenario", ["recover", "disagree", "missing", "clean_repeated", "transport_error"]
)
def test_verified_download_preserves_attempts_and_requires_matching_files(tmp_path, scenario):
    original = rec_file() if scenario == "clean_repeated" else varied_rec_file()

    class Downloads:
        calls = 0
        last_packets = []

        async def get(self, path):
            self.calls += 1
            raw = original
            if scenario == "disagree" and self.calls == 2:
                raw = raw[:-1] + bytes([raw[-1] ^ 1])
            self.last_packets = (
                list(frames(raw, size=80))
                if scenario == "clean_repeated"
                else duplicated_transfer(raw, 2 + self.calls)
            )
            if scenario == "missing":
                self.last_packets.pop(0)
            if scenario == "transport_error":
                self.last_packets = self.last_packets[:2]
                raise AcquisitionError("Simulated interrupted transfer")
            return b"".join(p[1:] for p in self.last_packets)

    async def check():
        ftp, manifest = Downloads(), {}
        entry = {"path": "/ACC.REC", "stream": "acc", "size": len(original)}
        if scenario in ("disagree", "missing", "transport_error"):
            with pytest.raises(AcquisitionError):
                await verified_download(ftp, tmp_path, entry, manifest, lambda: None)
        else:
            data, info = await verified_download(ftp, tmp_path, entry, manifest, lambda: None)
            assert data == original
            assert ("two_matching" in info["verification"]) == (scenario == "recover")
        expected_calls = 2 if scenario in ("recover", "disagree") else 1
        assert ftp.calls == expected_calls
        assert len(manifest["download_attempts"]) == expected_calls
        for attempt in manifest["download_attempts"]:
            received = (tmp_path / attempt["raw_path"]).read_bytes()
            assert len(received) == attempt["received_bytes"]
            assert hashlib.sha256(received).hexdigest() == attempt["sha256"]
            assert (tmp_path / attempt["transport_path"]).exists()

    asyncio.run(check())


@pytest.mark.parametrize("replies", [[b"\x06abc", b"\x23def"], [b"\x06abc"], [b"\x04abc"]])
def test_transfer_aborts_loss_or_timeout_and_requires_reconnect(replies):
    async def check():
        ftp = transfer_fixture(replies)
        with pytest.raises((AcquisitionError, TimeoutError)):
            await ftp.query(5)
        assert ftp.poisoned
        assert ftp.client.writes[-1] == b"\x00\x00\x00"
        with pytest.raises(AcquisitionError, match="desynchronized"):
            await ftp.query(5)

    asyncio.run(check())


@pytest.fixture(autouse=True)
def reset_fake_client(fake_factory):
    """Each offline lifecycle starts with a clean fake radio."""


class Sensor:
    def __init__(self):
        self.active = set()
        self.files = {}
        self.requests = []
        self.reject_gyro = False
        self.fail_download = False
        self.lose_start_ack = False


class OfflineClient(FakeClient):
    def __init__(self, *args, sensor, **kwargs):
        super().__init__(*args, **kwargs)
        self.sensor = sensor

    async def write_gatt_char(self, uuid, data, response):
        operation = data[0]
        self.sensor.requests.append(bytes(data))
        if operation == 1:
            return await super().write_gatt_char(uuid, bytes([1, data[1] & 0x3F]), response)
        kind = data[1] & 0x3F if len(data) > 1 else 255
        error = 0
        payload = b""
        if operation == 5:
            payload = bytes(k | (128 if k in self.sensor.active else 0) for k in (2, 5))
        elif operation == 2:
            assert data[1] & 128
            if kind == 5 and self.sensor.reject_gyro:
                error = 5
            else:
                self.sensor.active.add(kind)
                if self.sensor.lose_start_ack:
                    self.sensor.lose_start_ack = False
                    return
        elif operation == 3:
            self.sensor.active.discard(kind)
            name = "ACC" if kind == 2 else "GYRO"
            self.sensor.files[f"/U/0/20260910/R/130000/{name}.REC"] = rec_file(kind)
        self.callbacks[CP](None, bytearray([240, operation, kind, error, 0]) + payload)


class MemoryTransfer:
    def __init__(self, device):
        self.sensor = device.client.sensor
        self.exchanges = []

    async def connect(self):
        pass

    async def disk_space(self):
        return {"total_bytes": 16 * 1024**2, "free_bytes": 10 * 1024**2}

    async def list_recordings(self):
        return [
            {"path": p, "size": len(v), "stream": "acc" if "ACC" in p else "gyro"}
            for p, v in self.sensor.files.items()
        ]

    async def get(self, path):
        if self.sensor.fail_download:
            self.sensor.fail_download = False
            raise AcquisitionError("Simulated download disconnect")
        return self.sensor.files[path]


def options(action, path):
    return SimpleNamespace(
        offline_command=action,
        output=path,
        session=path,
        subject="synthetic",
        sensor_position="upper_arm_left",
        notes="",
        device=None,
        scan_timeout=1,
        connect_timeout=1,
        no_hr=True,
    )


def test_offline_round_trip_disconnects_retries_and_never_deletes(tmp_path, ble_device):
    async def check():
        sensor = Sensor()
        old = "/U/0/20260909/R/120000/ACC.REC"
        sensor.files[old] = rec_file()
        factory = partial(SenseDevice, client_factory=partial(OfflineClient, sensor=sensor))
        path = tmp_path / "offline"
        run = partial(
            run_offline,
            selected_device=ble_device,
            device_factory=factory,
            ftp_factory=MemoryTransfer,
        )
        await run(options("start", path))
        assert sensor.active == {2, 5}  # BLE close must NOT stop the recording
        assert not any(p[0] == 3 for p in sensor.requests)
        sensor.fail_download = True
        with pytest.raises(AcquisitionError, match="Simulated download"):
            await run(options("sync", path))
        assert not sensor.active
        await run(options("sync", path))
        assert sensor.files[old] == rec_file()
        assert len(sensor.files) == 3
        metadata = json.loads((path / "metadata.json").read_text())
        assert metadata["quality"]["acc"]["samples"] == 156
        assert metadata["quality"]["gyro"]["samples"] == 156
        assert metadata["quality"]["warnings"] == []
        assert metadata["capture_mode"] == "sensor_memory"
        assert count_session(path, plot=False)["sets"] == []
        assert all(r["host_time_utc"] == "" for r in read_rows(path / "acc.csv"))
        requests = list(sensor.requests)
        await run(options("sync", path))
        assert sensor.requests == requests  # idempotent, no BLE call after success
        assert json.loads((path / MANIFEST).read_text())["state"] == "downloaded"

    asyncio.run(check())


@pytest.mark.parametrize(
    "failure", ["disconnect", "timeout", "twice", "malformed", "rejected", "after_stop"]
)
def test_sync_retries_only_initial_status_transport_failure(
    tmp_path, ble_device, fake_factory, failure
):
    class StatusClient(OfflineClient):
        failures = 0
        connections = 0

        async def connect(self):
            type(self).connections += 1
            await super().connect()

        async def write_gatt_char(self, uuid, data, response):
            can_fail = failure != "after_stop" or any(r[0] == 3 for r in self.sensor.requests)
            if data == b"\x05" and self.failures and can_fail:
                type(self).failures -= 1
                self.sensor.requests.append(bytes(data))
                if failure == "timeout":
                    return
                if failure == "malformed":
                    self.callbacks[CP](None, bytearray.fromhex("f0 05 ff 00 00 82 82"))
                elif failure == "rejected":
                    self.callbacks[CP](None, bytearray.fromhex("f0 05 ff 05"))
                else:
                    await self.disconnect()
                return
            await super().write_gatt_char(uuid, data, response)

    async def check():
        sensor = Sensor()
        path = tmp_path / "status-retry"
        run = partial(
            run_offline,
            selected_device=ble_device,
            device_factory=partial(
                SenseDevice, client_factory=partial(StatusClient, sensor=sensor), timeout=0.1
            ),
            ftp_factory=MemoryTransfer,
        )
        await run(options("start", path))
        StatusClient.failures = 2 if failure == "twice" else 1
        before = StatusClient.connections
        if failure in ("disconnect", "timeout"):
            await run(options("sync", path))
        else:
            with pytest.raises(AcquisitionError):
                await run(options("sync", path))
        manifest = json.loads((path / MANIFEST).read_text())
        retried = failure in ("disconnect", "timeout", "twice")
        assert StatusClient.connections - before == (2 if retried else 1)
        assert len(manifest.get("recoveries", [])) == int(retried)
        assert len([r for r in sensor.requests if r[0] == 2]) == 2  # never restart
        assert len([r for r in sensor.requests if r[0] == 3]) == (
            2 if failure in ("disconnect", "timeout", "after_stop") else 0
        )
        if failure in ("disconnect", "timeout"):
            assert manifest["state"] == "downloaded"
            assert manifest["output_sha256"]
        else:
            assert manifest["errors"]
            assert not manifest.get("downloads")
        if failure in ("twice", "malformed", "rejected"):
            assert sensor.active == {2, 5}

    asyncio.run(check())


def test_start_with_lost_ack_can_be_stopped_after_reconnect(tmp_path, ble_device):
    async def check():
        sensor = Sensor()
        sensor.lose_start_ack = True
        factory = partial(
            # Allow Windows event-loop scheduling before the deliberately lost ACK.
            SenseDevice,
            client_factory=partial(OfflineClient, sensor=sensor),
            timeout=0.1,
        )
        run = partial(
            run_offline,
            selected_device=ble_device,
            device_factory=factory,
            ftp_factory=MemoryTransfer,
        )
        path = tmp_path / "uncertain"
        with pytest.raises(AcquisitionError, match="timed out"):
            await run(options("start", path))
        manifest = json.loads((path / MANIFEST).read_text())
        assert manifest["state"] == "start_uncertain"
        assert manifest["start_requests"] == ["acc"]
        assert sensor.active == {2}
        await run(options("stop", path))
        assert not sensor.active
        assert b"\x03\x05" not in sensor.requests

    asyncio.run(check())


def test_stale_session_cannot_stop_a_restarted_recording(tmp_path, ble_device):
    async def check():
        sensor = Sensor()
        factory = partial(SenseDevice, client_factory=partial(OfflineClient, sensor=sensor))
        run = partial(
            run_offline,
            selected_device=ble_device,
            device_factory=factory,
            ftp_factory=MemoryTransfer,
        )
        path = tmp_path / "stale"
        await run(options("start", path))
        await run(options("stop", path))
        sensor.active = {2, 5}  # another session began after ours stopped
        requests = list(sensor.requests)
        with pytest.raises(AcquisitionError, match="restarted"):
            await run(options("sync", path))
        assert sensor.active == {2, 5}
        assert sensor.requests[len(requests) :] == [b"\x05"]

    asyncio.run(check())


def test_sensor_directory_listing_reads_nested_protobuf_and_filters_files():
    def directory(*entries):
        return b"".join(
            field(1, field(1, name.encode()) + field(2, size)) for name, size in entries
        )

    class Directories(FileTransfer):
        async def get(self, path):
            return {
                "/U/0/": directory(("20260910/", 0), ("CONFIG/", 0)),
                "/U/0/20260910/": directory(("R/", 0)),
                "/U/0/20260910/R/": directory(("130000/", 0)),
                "/U/0/20260910/R/130000/": directory(
                    ("ACC0.REC", 300), ("ACC1.REC", 200), ("GYRO.REC", 500), ("HR.REC", 30)
                ),
            }[path]

    async def check():
        ftp = Directories(SimpleNamespace(client=None, lock=asyncio.Lock()))
        files = await ftp.list_recordings()
        assert [f["path"].rsplit("/", 1)[-1] for f in files] == [
            "ACC0.REC",
            "ACC1.REC",
            "GYRO.REC",
            "HR.REC",
        ]
        assert [f["size"] for f in files] == [300, 200, 500, 30]
        assert select_new_files({"baseline_files": []}, files) == files[:3]

    asyncio.run(check())


@pytest.mark.parametrize("damage", ["none", "backwards", "gap", "hash"])
def test_split_export_preserves_continuity_and_detects_damage(tmp_path, damage):
    downloads = []
    for stream, kind in (("acc", 2), ("gyro", 5)):
        for part in range(2):
            start = part * 3
            if part == 1 and stream == "acc":
                start = {"backwards": 0, "gap": 4}.get(damage, start)
            data = rec_file(kind, start=start)
            name = f"{stream}{part}.REC"
            (tmp_path / name).write_bytes(data)
            downloads.append(
                {
                    "path": name,
                    "local_path": name,
                    "stream": stream,
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
    if damage == "hash":
        (tmp_path / "acc0.REC").write_bytes(b"modified")
    manifest = {
        "session_id": "synthetic",
        "started_utc": "2026-09-10T13:00:00Z",
        "subject": "synthetic",
        "sensor_position": "upper_arm_left",
        "arm": "left",
        "notes": "fixture",
        "device": {},
    }
    if damage in ("backwards", "hash"):
        with pytest.raises(AcquisitionError, match="backwards|hash mismatch"):
            export_recordings(tmp_path, manifest, downloads)
        assert not (tmp_path / "acc.csv").exists()
        return
    result = export_recordings(tmp_path, manifest, downloads)
    assert result["quality"]["acc"]["samples"] == 312
    assert result["status"] == ("partial" if damage == "gap" else "complete")
    assert result["quality"]["acc"]["estimated_missing_samples"] == (52 if damage == "gap" else 0)


def test_partial_start_rolls_back_only_new_recording(tmp_path, ble_device):
    async def check():
        sensor = Sensor()
        sensor.reject_gyro = True
        factory = partial(SenseDevice, client_factory=partial(OfflineClient, sensor=sensor))
        path = tmp_path / "offline"
        with pytest.raises(AcquisitionError, match="rejected"):
            await run_offline(
                options("start", path),
                selected_device=ble_device,
                device_factory=factory,
                ftp_factory=MemoryTransfer,
            )
        assert sensor.active == set()
        assert b"\x03\x02" in sensor.requests and b"\x03\x05" not in sensor.requests

    asyncio.run(check())


def test_existing_recording_is_not_taken_over(tmp_path, ble_device):
    async def check():
        sensor = Sensor()
        sensor.active.add(2)
        factory = partial(SenseDevice, client_factory=partial(OfflineClient, sensor=sensor))
        with pytest.raises(AcquisitionError, match="already recording"):
            await run_offline(
                options("start", tmp_path / "offline"),
                selected_device=ble_device,
                device_factory=factory,
                ftp_factory=MemoryTransfer,
            )
        assert sensor.active == {2}
        assert not any(p[0] in (2, 3) for p in sensor.requests)

    asyncio.run(check())


def test_split_file_selection_refuses_missing_parts():
    entries = [
        {
            "path": f"/U/0/20260910/R/130000/{name}.REC",
            "stream": "acc" if name.startswith("ACC") else "gyro",
            "size": 100,
        }
        for name in ("ACC0", "ACC2", "GYRO")
    ]
    with pytest.raises(AcquisitionError, match="Missing or duplicate"):
        select_new_files({"baseline_files": []}, entries)


def test_direct_offline_transfer_roundtrip_without_full_sync_notifications(tmp_path, ble_device):
    """Firmware 3.0.16 closes BLE after Flow-style sync; use direct read commands."""

    class DirectClient(OfflineClient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.request_bytes = bytearray()

        async def write_gatt_char(self, uuid, data, response):
            if uuid == CP:
                return await super().write_gatt_char(uuid, data, response)
            # No H2D full-sync notifications or device file mutations are allowed.
            assert uuid == MTU and response is True
            if not data[0] & 1:
                self.request_bytes.clear()
            self.request_bytes.extend(data[1:])
            if (data[0] >> 1) & 3 == 3:
                return
            raw = bytes(self.request_bytes)
            if raw == b"\x05\x80":
                answer = field(1, 512) + field(2, 30000) + field(3, 28000)
            else:
                length = int.from_bytes(raw[:2], "little")
                assert len(raw) == length + 2
                operation = protobuf(raw[2:])
                assert operation[1] == [0]  # GET only
                path = operation[2][0].decode("ascii")
                if path.endswith("/"):
                    directory = {
                        "/U/0/": [("20260910/", 0)],
                        "/U/0/20260910/": [("R/", 0)],
                        "/U/0/20260910/R/": [("130000/", 0)],
                        "/U/0/20260910/R/130000/": [
                            (p.rsplit("/", 1)[-1], len(v)) for p, v in self.sensor.files.items()
                        ],
                    }[path]
                    answer = b"".join(
                        field(1, field(1, n.encode()) + field(2, s)) for n, s in directory
                    )
                else:
                    assert not self.sensor.active  # stop acknowledged before reading file
                    answer = self.sensor.files[path]
            for frame in frames(answer, size=20):
                self.callbacks[MTU](None, frame)

    async def check():
        sensor = Sensor()
        factory = partial(SenseDevice, client_factory=partial(DirectClient, sensor=sensor))
        run = partial(run_offline, selected_device=ble_device, device_factory=factory)
        path = tmp_path / "direct-transfer"
        await run(options("start", path))
        assert sensor.active == {2, 5}
        await run(options("sync", path))
        result = json.loads((path / "metadata.json").read_text())
        assert result["status"] == "complete"
        assert result["quality"]["gyro"]["samples"] == 156
        assert (path / "sensor-files/0000-ACC.REC").read_bytes() == rec_file()
        assert len(sensor.files) == 2

    asyncio.run(check())


def test_firmware_uses_polar_application_revision(ble_device):
    class VersionClient(FakeClient):
        async def read_gatt_char(self, uuid):
            if "2a26" in uuid:
                return b"0.1.5"
            if "2a28" in uuid:
                return b"3.0.16"
            return await super().read_gatt_char(uuid)

    async def check():
        device = SenseDevice(ble_device, lambda _: None, client_factory=VersionClient)
        await device.connect()
        try:
            report = await device.inspect()
            assert report["firmware"] == "3.0.16"
            assert report["firmware_revision"] == "0.1.5"
            assert report["firmware_source"] == "2a28"
        finally:
            await device.close()

    asyncio.run(check())
