import asyncio

import pytest
from conftest import FakeClient

from polar_activity.device import CP, SenseDevice, find_device
from polar_activity.protocol import AcquisitionError, StreamConfig


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
