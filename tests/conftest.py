import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from akp03.device import AKP03Device  # noqa: E402


class FakeHid:
    """Simula el handle HID: guarda lo escrito y devuelve reportes en cola."""

    def __init__(self):
        self.writes: list[bytes] = []
        self.reports: list[bytes] = []
        self.closed = False
        self.fail = False

    def write(self, data: bytes) -> int:
        if self.fail:
            raise OSError("desconectado")
        self.writes.append(bytes(data))
        return len(data)

    def read(self, size: int, timeout_ms: int) -> bytes:
        if self.fail:
            raise OSError("desconectado")
        if self.reports:
            return self.reports.pop(0)
        import time
        time.sleep(min(timeout_ms, 20) / 1000)
        return b""

    def close(self):
        self.closed = True


def make_report(code: int, state: int = 1, size: int = 512) -> bytes:
    data = bytearray(size)
    data[0:9] = b"ACK\x00\x00OK\x00\x00"
    data[9] = code
    data[10] = state
    return bytes(data)


@pytest.fixture
def fake_hid():
    return FakeHid()


@pytest.fixture
def fake_device(fake_hid):
    return AKP03Device(fake_hid, packet_size=512, info={"product_string": "Fake AKP03"})
