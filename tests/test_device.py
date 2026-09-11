import asyncio

import pytest
from conftest import FakeClient

from polar_activity.device import CP, DATA, PmdTransportError, SenseDevice, find_device
from polar_activity.protocol import AcquisitionError, StreamConfig


@pytest.fixture(autouse=True)
def reset_fake_client(fake_factory):
    """Failure flags must not leak between tests or into another test module."""


def test_real_adapter_queries_starts_and_consumes_stops(fake_factory, ble_device):
    async def run():
        device = fake_factory(ble_device, lambda _: None)
        await device.connect()
        report = await device.inspect()
        assert report["battery_percent"] == 78
        assert report["firmware"] == "synthetic-fw"
        assert report["polar_device_id"] == "ABCD1234"
        await device.start("acc", StreamConfig(52, 16, 8, 3))
        await device.start("gyro", StreamConfig(52, 16, 2000, 3))
        await device.close()
        assert device.responses.empty()
        assert not device.client.active
        assert all(request[1] != 9 for request in device.client.requests)

    asyncio.run(run())


def test_rejection_does_not_start_stream(fake_factory, ble_device):
    async def run():
        FakeClient.reject = (2, 5)
        device = fake_factory(ble_device, lambda _: None)
        await device.connect()
        with pytest.raises(AcquisitionError, match="code 5"):
            await device.start("gyro", StreamConfig(52, 16, 2000, 3))
        assert not device.active
        await device.close()

    asyncio.run(run())


def test_timeout_poisoned_connection(fake_factory, ble_device):
    async def run():
        device = fake_factory(ble_device, lambda _: None)
        await device.connect()
        FakeClient.silent = True
        with pytest.raises(AcquisitionError, match="timed out"):
            await device.command(bytes([1, 2]))
        with pytest.raises(AcquisitionError, match="reconnect"):
            await device.command(bytes([1, 2]))
        await device.close()

    asyncio.run(run())


def test_disconnect_during_write_fails_promptly_and_cancels_pending_request(ble_device):
    class DropClient(FakeClient):
        cancelled = False

        async def write_gatt_char(self, uuid, data, response):
            await self.disconnect()
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled = True

    async def run():
        device = SenseDevice(ble_device, lambda _: None, client_factory=DropClient, timeout=10)
        await device.connect()
        with pytest.raises(PmdTransportError, match="disconnected"):
            await asyncio.wait_for(device.command_raw(b"\x05"), 0.5)
        assert device.poisoned and device.client.cancelled
        assert device.exchanges[-1]["outcome"] == "error"
        assert device.exchanges[-1]["elapsed_s"] < 0.5
        assert device.exchanges[-1]["started_utc"]
        with pytest.raises(AcquisitionError, match="reconnect"):
            await device.command_raw(b"\x05")
        cleanup = await device.close()
        assert "Keep the sensor on" in cleanup[0]

    asyncio.run(run())


def test_multipart_and_feature_notifications(ble_device):
    class FragmentClient(FakeClient):
        async def write_gatt_char(self, uuid, data, response):
            self.callbacks[CP](None, bytearray([15, 36]))
            self.callbacks[CP](None, bytearray.fromhex("f0 01 02 00 01 00 02 1a"))
            self.callbacks[CP](None, bytearray.fromhex("f0 01 02 00 00 00 34 00"))

    async def run():
        device = SenseDevice(ble_device, lambda _: None, client_factory=FragmentClient)
        await device.connect()
        assert await device.command(bytes([1, 2])) == {"sample_rate": [26, 52]}
        await device.close()

    asyncio.run(run())


def test_discovery_errors_and_selection(monkeypatch, ble_device):
    async def none(*_):
        return []

    monkeypatch.setattr("polar_activity.device.discover", none)
    with pytest.raises(AcquisitionError, match="not found"):
        asyncio.run(find_device(None, 1))


@pytest.mark.parametrize("error_code", [0, 6])
def test_four_byte_control_response(ble_device, error_code):
    class ShortResponse(FakeClient):
        async def write_gatt_char(self, uuid, data, response):
            self.callbacks[CP](None, bytearray([240, data[0], data[1], error_code]))

    async def run():
        device = SenseDevice(ble_device, lambda _: None, client_factory=ShortResponse)
        await device.connect()
        if error_code:
            with pytest.raises(AcquisitionError, match="code 6"):
                await device.command(bytes([3, 2]))
        else:
            assert await device.command(bytes([3, 2])) == {}
        assert not device.poisoned
        await device.close()

    asyncio.run(run())


def test_multiple_devices_requires_selector(monkeypatch, ble_device):
    async def many(*_):
        return [ble_device, ble_device]

    monkeypatch.setattr("polar_activity.device.discover", many)
    with pytest.raises(AcquisitionError, match="Multiple"):
        asyncio.run(find_device(None, 1))


def test_windows_connection_refreshes_services_without_pairing(ble_device):
    class CachedHandlesClient(FakeClient):
        async def start_notify(self, uuid, callback):
            if self.options["winrt"].get("use_cached_services") is not False:
                raise OSError("Cached CCCD handle is stale")
            await super().start_notify(uuid, callback)

    async def run():
        device = SenseDevice(
            ble_device, lambda _: None, client_factory=CachedHandlesClient, connect_timeout=60
        )
        await device.connect()
        assert not device.client.options["pair"]
        assert device.client.options["timeout"] == 60
        assert CP in device.client.callbacks and DATA in device.client.callbacks
        await device.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("stage", "description"),
    [("connect", "service discovery"), (CP, "control indications"), (DATA, "data notifications")],
)
def test_setup_timeouts_identify_stage_and_disconnect(ble_device, stage, description):
    class TimeoutClient(FakeClient):
        async def connect(self):
            await super().connect()
            if stage == "connect":
                await asyncio.sleep(1)

        async def start_notify(self, uuid, callback):
            if stage == uuid:
                await asyncio.sleep(1)
            await super().start_notify(uuid, callback)

    async def run():
        device = SenseDevice(
            ble_device,
            lambda _: None,
            timeout=0.01,
            connect_timeout=0.01,
            client_factory=TimeoutClient,
        )
        with pytest.raises(AcquisitionError, match=description + " failed: timed out"):
            await device.connect()
        assert not device.client.is_connected

    asyncio.run(run())


def test_windows_notification_cancellation_is_actionable(ble_device):
    class CancelClient(FakeClient):
        async def start_notify(self, uuid, callback):
            error = OSError("The operation was canceled by the user")
            error.winerror = -2147023673
            raise error

    async def run():
        device = SenseDevice(ble_device, lambda _: None, client_factory=CancelClient)
        with pytest.raises(AcquisitionError, match="Windows canceled the GATT request") as error:
            await device.connect()
        assert "PMD control indications" in str(error.value)
        assert not device.client.is_connected

    asyncio.run(run())


def test_feature_read_timeout_is_actionable(ble_device):
    class TimeoutClient(FakeClient):
        async def read_gatt_char(self, uuid):
            raise TimeoutError()

    async def run():
        device = SenseDevice(ble_device, lambda _: None, client_factory=TimeoutClient)
        await device.connect()
        try:
            with pytest.raises(AcquisitionError, match="Reading PMD features timed out"):
                await device.inspect()
        finally:
            await device.close()

    asyncio.run(run())
