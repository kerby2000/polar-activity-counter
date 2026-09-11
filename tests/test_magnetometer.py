"""Known Polar MAG vectors, stream ownership and motion-independent exports."""

import asyncio
import hashlib
import json
import struct
from functools import partial

import numpy as np
import pytest
from conftest import EPOCH, FakeClient
from test_heart_rate import HrClient, HrTransfer
from test_offline import Sensor, options, rec_file

from polar_activity.cli import parser
from polar_activity.device import CP, DATA, SenseDevice
from polar_activity.diagnostics import read_rows
from polar_activity.magnetometer import calibration_fields, decode_mag, read_magnetometer
from polar_activity.offline import MANIFEST, run_offline, select_new_files
from polar_activity.offline_data import export_recordings, parse_recording
from polar_activity.protocol import AcquisitionError, StreamConfig, choose_config
from polar_activity.recorder import record_session

CONFIG = StreamConfig(20, 16, 50, 3)
SETTINGS = bytes.fromhex("000114000101100002013200040103")
FACTOR = 0.0015


def test_usb_mag_copy_export_and_integrity_retry(tmp_path):
    from test_usb import DIRECTORY, Hid, run_sync, source_session

    from polar_activity.pftp import field
    from polar_activity.storage import write_json

    session, source, files = source_session(tmp_path, referenced=False)
    source["requested_streams"] = ["acc", "gyro", "mag"]
    files[DIRECTORY + "MAG.REC"] = mag_file()
    files[DIRECTORY] = b"".join(
        field(1, field(1, name.encode()) + field(2, len(files[DIRECTORY + name])))
        for name in ("ACC.REC", "GYRO.REC", "MAG.REC")
    )
    write_json(session / MANIFEST, source)
    out = tmp_path / "usb-mag"
    hid = Hid(files)
    run_sync(session, out, hid)
    assert DIRECTORY + "MAG.REC" in hid.gets
    assert len(read_rows(out / "mag.csv")) == 60
    assert json.loads((out / "metadata.json").read_text())["mag_enabled"]
    idle = Hid(files)
    run_sync(session, out, idle)
    assert not idle.writes
    (out / "mag.csv").write_text("modified")
    with pytest.raises(AcquisitionError, match="changed"):
        run_sync(session, out, Hid(files))


def test_mag_data_cannot_influence_predictions(tmp_path):
    from test_adaptive import model_fixture
    from test_counter import motion
    from test_recognition import labelled_fixture

    from polar_activity.adaptive import analyse_session

    _, model, _ = model_fixture(tmp_path)
    target = tmp_path / "with-mag"
    labelled_fixture(target, motion(((7, 3, 1.5),), duration=22))
    before = analyse_session(target, model, plot=False)
    (target / "mag.csv").write_text("time_s,mag_x_ut,mag_y_ut,mag_z_ut\n0,30,20,40\n1,40,-20,30\n")
    after = analyse_session(target, model, plot=True)
    for key in ("sets", "windows", "activities", "candidate_intervals", "source_sha256"):
        assert before[key] == after[key]
    assert after["sets"][0]["rep_estimate"] == 3
    assert after["magnetometer"]["samples"] == 2
    assert not after["magnetometer"]["used_for_recognition"]


@pytest.fixture(autouse=True)
def reset_fake(fake_factory):
    pass


def mag_packet(last=EPOCH, kind=0):
    axes = [100, 200, -300] + ([2] if kind == 1 else [])
    delta = bytes([1, 19]) + bytes((19 * len(axes) + 7) // 8)
    return (
        bytes([6])
        + struct.pack("<Q", last)
        + bytes([128 + kind])
        + struct.pack("<" + "h" * len(axes), *axes)
        + delta
    )


def mag_file(start=0, kind=0):
    settings = SETTINGS + bytes([5, 1]) + struct.pack("<f", FACTOR)
    raw = b"\0" + struct.pack("<IIII", 0x3D7C4C2B, 1, 0, 123)
    raw += b"2026-09-10 13:00:00\0" + bytes([len(settings)]) + settings + b"\0"
    for i in range(start, start + 3):
        frame = mag_packet(EPOCH + i * 10**9, kind)
        raw += struct.pack("<H", len(frame)) + frame
    return raw


def test_polar_type0_vector_factor_and_microtesla_conversion():
    # Polar SDK MagDataTest: reference -6430,5626,2633; deltas -1,+1,+2.
    frame = bytes.fromhex("06009435770000000080e2e6fa15490a06017f20fc")
    last, samples, statuses = decode_mag(frame, CONFIG, 0.001)
    assert last == 2_000_000_000
    np.testing.assert_allclose(samples, [[-643, 562.6, 263.3], [-643.1, 562.7, 263.5]])
    assert statuses == [None, None]
    assert calibration_fields(None)["calibration_status"] == "not_reported"


def test_polar_type1_vector_preserves_calibration_unscaled():
    # Polar SDK type 1 reference -201,-687,-2452,status0; deltas 0,-1,+3,+1.
    frame = bytes.fromhex("0600943577000000008137ff51fd6cf600000301f802")
    last, samples, statuses = decode_mag(frame, CONFIG, 1)
    assert last == 2_000_000_000
    np.testing.assert_allclose(samples[0], [-20.1, -68.7, -245.2])
    assert statuses == [0, 1]
    assert calibration_fields(statuses[1])["calibration_status"] == "poor"
    assert calibration_fields(9) == {
        "calibration_status_raw": 9,
        "calibration_status": "unrecognized",
    }


@pytest.mark.parametrize("damage", ["truncated", "raw", "frame", "kind", "factor", "resolution"])
def test_invalid_mag_rejected(damage):
    frame = bytearray(mag_packet())
    factor, config = FACTOR, CONFIG
    if damage == "truncated":
        frame = frame[:-1]
    elif damage == "raw":
        frame[9] = 0
    elif damage == "frame":
        frame[9] = 130
    elif damage == "kind":
        frame[0] = 2
    elif damage == "factor":
        factor = float("nan")
    else:
        config = StreamConfig(20, 8, 50, 3)
    with pytest.raises(AcquisitionError):
        decode_mag(bytes(frame), config, factor)


def test_mag_config_and_offline_parser():
    assert (
        choose_config(
            {"sample_rate": [10, 20, 50], "resolution": [16], "range": [50], "channels": [3]}, "mag"
        )
        == CONFIG
    )
    record = parse_recording(mag_file(), "mag")
    assert record["config"] == CONFIG
    assert sum(len(f["samples"]) for f in record["frames"]) == 60
    np.testing.assert_allclose(record["frames"][0]["samples"][0], [15, 30, -45])
    assert record["frames"][0]["calibration"] == [None] * 20


class MagOfflineClient(HrClient):
    async def read_gatt_char(self, uuid):
        return bytes([15, 0x64, 0x40]) if uuid == CP else await super().read_gatt_char(uuid)

    async def write_gatt_char(self, uuid, data, response):
        operation, kind = data[0], data[1] & 63 if len(data) > 1 else 255
        if operation == 5:
            self.sensor.requests.append(bytes(data))
            payload = bytes(k | (128 if k in self.sensor.active else 0) for k in (2, 5, 14, 6))
        elif kind == 6:
            self.sensor.requests.append(bytes(data))
            payload = b""
            if operation == 1:
                payload = SETTINGS
            elif operation == 2:
                assert data[1] == 134
                if getattr(self.sensor, "reject_mag", False):
                    self.callbacks[CP](None, bytearray([240, 2, 6, 5, 0]))
                    return
                self.sensor.active.add(6)
                if getattr(self.sensor, "lose_mag_ack", False):
                    return
            elif operation == 3:
                self.sensor.active.discard(6)
                self.sensor.files["/U/0/20260910/R/130000/MAG.REC"] = mag_file()
        else:
            return await super().write_gatt_char(uuid, data, response)
        self.callbacks[CP](None, bytearray([240, operation, kind, 0, 0]) + payload)


@pytest.mark.parametrize("failure", [None, "reject_mag", "lose_mag_ack"])
def test_four_stream_offline_lifecycle_and_recovery(tmp_path, ble_device, failure):
    async def check():
        sensor = Sensor()
        if failure:
            setattr(sensor, failure, True)
        run = partial(
            run_offline,
            selected_device=ble_device,
            ftp_factory=HrTransfer,
            device_factory=partial(
                # Match the HR lifecycle fixture's hosted-Windows scheduling budget.
                SenseDevice, client_factory=partial(MagOfflineClient, sensor=sensor), timeout=0.5
            ),
        )
        path = tmp_path / "mag"
        args = options("start", path)
        args.no_hr, args.mag = False, True
        if failure:
            with pytest.raises(AcquisitionError):
                await run(args)
            if failure == "lose_mag_ack":
                assert sensor.active == {2, 5, 6, 14}
                await run(options("stop", path))
            else:
                assert b"\x03\x06" not in sensor.requests
            assert not sensor.active
            return
        await run(args)
        assert sensor.active == {2, 5, 6, 14}
        await run(options("sync", path))
        assert not sensor.active
        meta = json.loads((path / "metadata.json").read_text())
        assert meta["quality"]["mag"]["samples"] == 60 and meta["hr_enabled"]
        assert not meta["warnings"]
        manifest = json.loads((path / MANIFEST).read_text())
        assert "mag.csv" in manifest["output_sha256"]
        before = list(sensor.requests)
        await run(options("sync", path))
        assert before == sensor.requests
        (path / "mag.csv").write_text("damaged")
        with pytest.raises(AcquisitionError, match="changed"):
            await run(options("sync", path))

    asyncio.run(check())


class MagOnlineClient(FakeClient):
    async def read_gatt_char(self, uuid):
        return bytes([15, 0x64]) if uuid == CP else await super().read_gatt_char(uuid)

    async def write_gatt_char(self, uuid, data, response):
        operation, kind = data[:2]
        if kind == 6 and operation in (1, 2):
            self.requests.append(bytes(data))
            payload = SETTINGS if operation == 1 else bytes([5, 1]) + struct.pack("<f", FACTOR)
            self.callbacks[CP](None, bytearray([240, operation, kind, 0, 0]) + payload)
            if operation == 2:
                self.active.add(6)
                for i in range(3):
                    self.callbacks[DATA](None, bytearray(mag_packet(EPOCH + i * 10**9)))
                for value in self.delayed:
                    self.callbacks[DATA](None, value)
        elif kind == 5 and operation == 2:
            self.delayed = []
            callback = self.callbacks[DATA]
            self.callbacks[DATA] = lambda _, value: self.delayed.append(value)
            await super().write_gatt_char(uuid, data, response)
            self.callbacks[DATA] = callback
        else:
            await super().write_gatt_char(uuid, data, response)


def test_online_mag_before_imu_keeps_imu_anchor_and_stops_mag(tmp_path, ble_device):
    path = tmp_path / "online"
    asyncio.run(
        record_session(
            path,
            "test",
            "upper_arm_left",
            "left",
            0.03,
            mag=True,
            interactive=False,
            selected_device=ble_device,
            device_factory=partial(SenseDevice, client_factory=MagOnlineClient, timeout=0.1),
        )
    )
    meta = json.loads((path / "metadata.json").read_text())
    assert meta["status"] == "complete"
    assert meta["quality"]["mag"]["samples"] == 60
    assert meta["clock_mapping"]["device_anchor_ns"] == EPOCH
    assert b"\x03\x06" in FakeClient.instances[-1].requests
    values = read_rows(path / "mag.csv")
    assert all(v["calibration_status"] == "not_reported" for v in values)
    assert float(values[0]["mag_x_ut"]) == pytest.approx(15)
    assert all(v["host_time_utc"] for v in values)


def test_optional_mag_does_not_shift_imu_csv_or_claim_old_files(tmp_path):
    manifest = {
        "session_id": "test",
        "started_utc": "2026-09-10T13:00:00Z",
        "subject": "test",
        "sensor_position": "upper_arm_left",
        "arm": "left",
        "notes": "",
        "device": {},
        "baseline_files": [],
    }
    downloads = []
    for name, stream, raw in [
        ("ACC.REC", "acc", rec_file()),
        ("GYRO.REC", "gyro", rec_file(5)),
        ("MAG.REC", "mag", mag_file(start=-2)),
    ]:
        (tmp_path / name).write_bytes(raw)
        downloads.append(
            {
                "path": name,
                "local_path": name,
                "stream": stream,
                "size": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    assert select_new_files(manifest, downloads) == downloads[:2]
    export_recordings(tmp_path, manifest, downloads[:2])
    before = [(tmp_path / n).read_bytes() for n in ("acc.csv", "gyro.csv")]
    manifest["requested_streams"] = ["acc", "gyro", "mag"]
    export_recordings(tmp_path, manifest, downloads)
    assert before == [(tmp_path / n).read_bytes() for n in ("acc.csv", "gyro.csv")]
    rows, summary = read_magnetometer(tmp_path)
    assert float(rows[0]["time_s"]) < 0
    assert summary["valid_samples"] == 60 and not summary["used_for_recognition"]
    with pytest.raises(AcquisitionError, match="No new MAG"):
        select_new_files(manifest, downloads[:2])
    assert (
        parser()
        .parse_args(
            [
                "offline",
                "start",
                "--mag",
                "--subject",
                "me",
                "--sensor-position",
                "arm",
                "--output",
                "x",
            ]
        )
        .mag
    )
