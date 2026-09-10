import asyncio
import struct
from functools import partial
from types import SimpleNamespace

import pytest
from bleak.backends.device import BLEDevice

from polar_activity.device import CP, DATA, HR, SenseDevice

EPOCH = 800_000_000_000_000_123


def packet(kind=2, last=EPOCH, count=52):
    # Seed [0, 0, 1000], constant signal encoded as 8-bit deltas.
    return (
        bytes([kind])
        + last.to_bytes(8, "little")
        + b"\x80"
        + struct.pack("<hhh", 0, 0, 1000)
        + bytes([8, count - 1])
        + bytes(3 * (count - 1))
    )


class FakeClient:
    instances = []
    reject = None
    hr_fails = False
    disconnect_during_stream = False
    silent = False
    malformed = False
    omit_acc_factor = False

    def __init__(self, device, disconnected_callback, *, pair=False, timeout=30, winrt=None):
        self.device = device
        self.options = {"pair": pair, "timeout": timeout, "winrt": winrt}
        self.disconnected_callback = disconnected_callback
        self.is_connected = False
        self.callbacks = {}
        self.requests = []
        self.active = set()
        self.services = SimpleNamespace(get_characteristic=lambda _: object())
        self.instances.append(self)

    async def connect(self):
        self.is_connected = True

    async def disconnect(self):
        self.is_connected = False
        self.disconnected_callback(self)

    async def start_notify(self, uuid, callback):
        if uuid == HR and self.hr_fails:
            raise RuntimeError("Synthetic HR unavailable")
        self.callbacks[uuid] = callback
        if uuid == HR:
            callback(None, bytearray([0, 70]))

    async def stop_notify(self, uuid):
        self.callbacks.pop(uuid, None)

    async def read_gatt_char(self, uuid):
        if uuid == CP:
            return bytes([15, (1 << 2) | (1 << 5)])
        if "2a19" in uuid:
            return bytes([78])
        if "2a26" in uuid:
            return b"synthetic-fw"
        if "2a28" in uuid:
            return b"synthetic-fw"
        return b"SYNTHETIC Verity Sense"

    async def write_gatt_char(self, uuid, data, response):
        assert uuid == CP and response is True
        self.requests.append(bytes(data))
        operation, kind = data[:2]
        if self.silent:
            return
        error = 5 if self.reject == (operation, kind) else 0
        payload = b""
        if operation == 1:
            payload = (
                bytes([0, 1, 52, 0, 1, 1, 16, 0, 2, 1])
                + (8 if kind == 2 else 2000).to_bytes(2, "little")
                + bytes([4, 1, 3])
            )
        elif operation == 2:
            if not (kind == 2 and self.omit_acc_factor):
                payload = bytes([5, 1]) + struct.pack("<f", 0.001 if kind == 2 else 0.125)
            if not error:
                self.active.add(kind)
        elif operation == 3:
            self.active.discard(kind)
        self.callbacks[CP](None, bytearray([240, operation, kind, error, 0]) + payload)
        if operation == 2 and kind == 5 and not error:
            for index in range(4):
                for measurement in (2, 5):
                    value = packet(
                        measurement, EPOCH + index * 10**9 + (1000 if measurement == 5 else 0)
                    )
                    if measurement == 2 and self.omit_acc_factor:
                        value = value[:9] + b"\x81" + value[10:]
                    self.callbacks[DATA](None, bytearray(value[:-1] if self.malformed else value))
            if self.disconnect_during_stream:
                self.is_connected = False
                self.disconnected_callback(self)


@pytest.fixture
def fake_factory():
    FakeClient.instances = []
    for name in ("hr_fails", "disconnect_during_stream", "silent", "malformed", "omit_acc_factor"):
        setattr(FakeClient, name, False)
    FakeClient.reject = None
    return partial(SenseDevice, client_factory=FakeClient, timeout=0.05)


@pytest.fixture
def ble_device():
    return BLEDevice("00:11:22:33:44:55", "Polar Sense ABCD1234", {})


@pytest.fixture
def recorded(tmp_path, fake_factory, ble_device):
    from polar_activity.recorder import record_session

    directory = tmp_path / "session"
    asyncio.run(
        record_session(
            directory,
            "synthetic",
            "upper_arm_left",
            "left",
            0.03,
            interactive=False,
            device_factory=fake_factory,
            selected_device=ble_device,
        )
    )
    return directory
