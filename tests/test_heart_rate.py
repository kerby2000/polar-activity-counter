"""Offline HR protocol/lifecycle and display-only use of auxiliary measurements."""

import asyncio
import hashlib
import json
import struct
from functools import partial

import matplotlib
import numpy as np
import pytest
from test_offline import MemoryTransfer, OfflineClient, Sensor, options, rec_file

from polar_activity.cli import parser
from polar_activity.device import CP, SenseDevice
from polar_activity.diagnostics import read_rows
from polar_activity.heart_rate import plot_heart_rate, summary
from polar_activity.offline import MANIFEST, run_offline, select_new_files
from polar_activity.offline_data import export_recordings, parse_recording
from polar_activity.protocol import AcquisitionError
from polar_activity.usb_sync import prepare_output, select_files

matplotlib.use("Agg")


@pytest.fixture(autouse=True)
def reset_fake_client(fake_factory):
    # Other protocol tests deliberately alter FakeClient's failure modes.
    pass


def hr_file(values=b"\x00\xff\x32\x32\x33\x33\x34\x35\x55", frame_type=0):
    # Polar's HrOfflineMockData fixture: empty settings, zero frame timestamp.
    header = b"\0" + struct.pack("<IIII", 0x3D7C4C2B, 1, 0, 0x14CEFB08)
    header += b"2022-12-07 07:04:28\0\0\0"
    frame = b"\x0e" + bytes(8) + bytes([frame_type]) + values
    return header + struct.pack("<H", len(frame)) + frame


def test_sdk_hr_frames_keep_unsigned_samples_and_quality():
    record = parse_recording(hr_file(), "hr")
    assert record["config"] is None and record["factor"] is None
    assert record["frames"][0]["last"] == 0
    assert [s["hr_bpm"] for s in record["frames"][0]["samples"]] == [
        0,
        255,
        50,
        50,
        51,
        51,
        52,
        53,
        85,
    ]
    samples = parse_recording(hr_file(bytes([72, 86, 71, 81, 64, 82]), 1), "hr")
    assert samples["frames"][0]["samples"] == [
        {"hr_bpm": 72, "ppg_quality": 86, "corrected_hr_bpm": 71},
        {"hr_bpm": 81, "ppg_quality": 64, "corrected_hr_bpm": 82},
    ]


@pytest.mark.parametrize("values,kind", [(b"", 0), (b"\x48\x56", 1), (b"\x48", 2), (b"\x48", 128)])
def test_bad_hr_frames_rejected(values, kind):
    with pytest.raises(AcquisitionError):
        parse_recording(hr_file(values, kind), "hr")


class HrClient(OfflineClient):
    async def read_gatt_char(self, uuid):
        if uuid == CP:
            return bytes([15, 0x24, 0x40])
        return await super().read_gatt_char(uuid)

    async def write_gatt_char(self, uuid, data, response):
        if data == b"\x05":
            self.sensor.requests.append(bytes(data))
            payload = bytes(k | (128 if k in self.sensor.active else 0) for k in (2, 5, 14))
            self.callbacks[CP](None, bytearray([240, 5, 255, 0, 0]) + payload)
        elif data[:2] == b"\x02\x8e":
            assert data == b"\x02\x8e"  # no IMU settings, PPI or SDK-mode command
            self.sensor.requests.append(bytes(data))
            if getattr(self.sensor, "reject_hr", False):
                self.callbacks[CP](None, bytearray([240, 2, 14, 5, 0]))
            else:
                self.sensor.active.add(14)
                if not getattr(self.sensor, "lose_hr_ack", False):
                    self.callbacks[CP](None, bytearray([240, 2, 14, 0, 0]))
        elif data == b"\x03\x0e":
            self.sensor.requests.append(bytes(data))
            self.sensor.active.discard(14)
            self.sensor.files["/U/0/20260910/R/130000/HR.REC"] = hr_file()
            self.callbacks[CP](None, bytearray([240, 3, 14, 0, 0]))
        else:
            await super().write_gatt_char(uuid, data, response)


class HrTransfer(MemoryTransfer):
    async def list_recordings(self):
        return [
            {"path": p, "size": len(v), "stream": p.rsplit("/", 1)[1][:-4].lower()}
            for p, v in self.sensor.files.items()
        ]


@pytest.mark.parametrize("failure", [None, "reject_hr", "lose_hr_ack"])
def test_hr_lifecycle_owns_three_streams_and_recovers_failed_starts(tmp_path, ble_device, failure):
    async def check():
        sensor = Sensor()
        if failure:
            setattr(sensor, failure, True)
        factory = partial(
            SenseDevice, client_factory=partial(HrClient, sensor=sensor), timeout=0.03
        )
        run = partial(
            run_offline, selected_device=ble_device, device_factory=factory, ftp_factory=HrTransfer
        )
        path = tmp_path / "hr"
        args = options("start", path)
        args.no_hr = False
        if failure:
            with pytest.raises(AcquisitionError):
                await run(args)
            manifest = json.loads((path / MANIFEST).read_text())
            assert manifest["start_requests"] == ["acc", "gyro", "hr"]
            if failure == "reject_hr":
                assert not sensor.active
                assert b"\x03\x0e" not in sensor.requests
            else:
                assert sensor.active == {2, 5, 14}
                await run(options("stop", path))
                assert not sensor.active
            return
        await run(args)
        assert sensor.active == {2, 5, 14}
        await run(options("sync", path))
        assert not sensor.active
        metadata = json.loads((path / "metadata.json").read_text())
        assert metadata["hr_enabled"] and metadata["quality"]["hr"]["samples"] == 9
        assert metadata["quality"]["hr"]["valid_samples"] == 8
        assert len(read_rows(path / "hr.csv")) == 9
        assert len(sensor.files) == 3
        before = list(sensor.requests)
        await run(options("sync", path))
        assert sensor.requests == before

    asyncio.run(check())


def test_hr_default_cli_and_unavailable_device_fails_before_start(tmp_path, ble_device):
    args = parser().parse_args(
        [
            "offline",
            "start",
            "--subject",
            "me",
            "--sensor-position",
            "upper_arm_left",
            "--output",
            str(tmp_path / "a"),
        ]
    )
    assert args.no_hr is False
    sensor = Sensor()
    with pytest.raises(AcquisitionError, match="--no-hr"):
        asyncio.run(
            run_offline(
                args,
                selected_device=ble_device,
                device_factory=partial(
                    SenseDevice, client_factory=partial(OfflineClient, sensor=sensor)
                ),
                ftp_factory=MemoryTransfer,
            )
        )
    assert not any(r[0] in (2, 3) for r in sensor.requests)


def test_hr_export_split_timing_preserves_motion_bytes_and_usb_selection(tmp_path):
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
    for name, stream, raw in [("ACC.REC", "acc", rec_file()), ("GYRO.REC", "gyro", rec_file(5))]:
        (tmp_path / name).write_bytes(raw)
        downloads.append(
            {
                "stream": stream,
                "path": "/U/0/20260910/R/130000/" + name,
                "local_path": name,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size": len(raw),
            }
        )
    export_recordings(tmp_path, manifest, downloads)
    imu_before = [(tmp_path / name).read_bytes() for name in ("acc.csv", "gyro.csv")]
    for index in range(2):
        name, raw = f"HR{index}.REC", hr_file(bytes([70, 71, 72]))
        (tmp_path / name).write_bytes(raw)
        downloads.append(
            {
                "stream": "hr",
                "path": "/U/0/20260910/R/130001/" + name,
                "local_path": name,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size": len(raw),
            }
        )
    assert select_new_files(manifest, downloads) == downloads[:2]  # old manifest cannot claim HR
    manifest["requested_streams"] = ["acc", "gyro", "hr"]
    meta = export_recordings(tmp_path, manifest, downloads)
    assert [(tmp_path / name).read_bytes() for name in ("acc.csv", "gyro.csv")] == imu_before
    hr = read_rows(tmp_path / "hr.csv")
    assert len(hr) == 6
    assert np.diff([float(r["time_s"]) for r in hr]).tolist() == [1] * 5
    assert all(r["host_time_utc"] == "" and r["timestamp_method"].endswith("estimate") for r in hr)
    assert meta["hr_enabled"]
    source = {**manifest, "downloads": downloads}
    assert len(select_files(source, downloads, {})) == 4
    usb = prepare_output(tmp_path / "usb", source, "hash", tmp_path)
    assert usb["requested_streams"] == ["acc", "gyro", "hr"]
    with pytest.raises(AcquisitionError, match="No new HR"):
        select_new_files(manifest, downloads[:2])
    with pytest.raises(AcquisitionError, match="HR recording is missing"):
        export_recordings(tmp_path, manifest, downloads[:2])


def test_hr_plot_marks_missing_data_and_does_not_bridge_gaps():
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots()
    rows = [{"time_s": t, "hr_bpm": bpm} for t, bpm in [(0, 80), (1, 0), (2, 82), (10, 90)]]
    plot_heart_rate(ax, rows)
    assert np.isnan(ax.lines[0].get_ydata()).sum() == 2
    assert summary(rows)["mean_bpm"] == 84
    plt.close(fig)
    fig, ax = plt.subplots()
    plot_heart_rate(ax, [])
    assert ax.texts[0].get_text() == "HR not recorded"
    plt.close(fig)


def test_hr_does_not_change_activity_or_counts(tmp_path):
    from test_adaptive import model_fixture
    from test_counter import motion
    from test_recognition import labelled_fixture

    from polar_activity.adaptive import analyse_session

    _, model, _ = model_fixture(tmp_path)
    target = tmp_path / "hr-target"
    labelled_fixture(target, motion(((7, 3, 1.5),), duration=22))
    absent = analyse_session(target, model, plot=False)
    assert absent["sets"][0]["rep_estimate"] == 3
    (target / "hr.csv").write_text("time_s,hr_bpm\n0,60\n5,150\n10,0\n15,90\n")
    present = analyse_session(target, model, plot=True)
    for key in ("sets", "windows", "activities", "candidate_intervals", "source_sha256"):
        assert absent[key] == present[key]
    assert present["heart_rate"]["valid_samples"] == 3
    assert not present["heart_rate"]["used_for_recognition"]
    assert present["heart_rate"]["source_sha256"] != absent["heart_rate"]["source_sha256"]


def test_usb_hr_download_roundtrip_and_retry_preserves_hr(tmp_path):
    from test_usb import DIRECTORY, Hid, run_sync, source_session

    from polar_activity.pftp import field
    from polar_activity.storage import write_json

    session, source, files = source_session(tmp_path, referenced=False)
    files[DIRECTORY + "HR.REC"] = hr_file(bytes([65, 66, 64]))
    names = ("ACC.REC", "GYRO.REC", "HR.REC")
    files[DIRECTORY] = b"".join(
        field(1, field(1, name.encode()) + field(2, len(files[DIRECTORY + name]))) for name in names
    )
    source["requested_streams"] = ["acc", "gyro", "hr"]
    write_json(session / MANIFEST, source)
    output = tmp_path / "usb-hr"
    hid = Hid(files)
    run_sync(session, output, hid)
    assert DIRECTORY + "HR.REC" in hid.gets
    assert [int(r["hr_bpm"]) for r in read_rows(output / "hr.csv")] == [65, 66, 64]
    assert json.loads((output / "metadata.json").read_text())["hr_enabled"]
    saved = (output / "hr.csv").read_bytes()
    idle = Hid(files)
    run_sync(session, output, idle)
    assert not idle.writes and (output / "hr.csv").read_bytes() == saved
