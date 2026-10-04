import time

from akp03.config import normalize
from akp03.controller import Controller
from akp03.device import AKP03Device, DeviceError
from akp03.media.base import MediaBackend, MediaState
from conftest import FakeHid, make_report


class RecordingBackend(MediaBackend):
    def __init__(self):
        self.calls = []
        self.volume = 40
        self.muted = False

    def get_state(self):
        return MediaState(title="Song", artist="Artist", app="Spotify", status="playing",
                          position=1, duration=100, volume=self.volume, muted=self.muted)

    def play_pause(self):
        self.calls.append("play_pause")

    def next(self):
        self.calls.append("next")

    def previous(self):
        self.calls.append("previous")

    def seek(self, d):
        self.calls.append(("seek", d))

    def get_volume(self):
        return self.volume

    def set_volume(self, p):
        self.calls.append(("set_volume", p))
        self.volume = p

    def toggle_mute(self):
        self.calls.append("mute")
        self.muted = not self.muted


def wait_for(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def make_controller(tmp_path, hid=None, cfg=None):
    hid = hid or FakeHid()
    backend = RecordingBackend()
    opened = []

    def opener(c):
        dev = AKP03Device(hid, packet_size=512, info={"product_string": "Fake"})
        opened.append(dev)
        return dev

    ctrl = Controller(normalize(cfg or {}), backend, device_opener=opener,
                      config_path=tmp_path / "config.json")
    return ctrl, backend, hid, opened


def test_controller_sends_images_and_handles_inputs(tmp_path):
    ctrl, backend, hid, _ = make_controller(tmp_path)
    ctrl.start()
    try:
        assert wait_for(lambda: ctrl.status.startswith("Conectado"))
        # Se envían imágenes a las 6 teclas y luego STP.
        assert wait_for(lambda: sum(w[6:9] == b"BAT" for w in hid.writes) >= 6)
        key_ids = sorted({w[13] for w in hid.writes if w[6:9] == b"BAT"})
        assert key_ids == [1, 2, 3, 4, 5, 6]
        assert any(w[6:9] == b"STP" for w in hid.writes)

        hid.reports += [make_report(0x06), make_report(0x05), make_report(0x04),
                        make_report(0x25), make_report(0x33)]
        assert wait_for(lambda: backend.calls[:5] == ["next", "play_pause", "previous", "previous", "mute"])

        # Perilla 1: volumen (+2 por paso), con coalescencia de llamadas.
        backend.calls.clear()
        hid.reports += [make_report(0x91)] * 3
        assert wait_for(lambda: backend.volume == 46)
        assert all(c[0] == "set_volume" for c in backend.calls)
    finally:
        ctrl.stop()
    assert hid.closed


def test_unchanged_frames_are_not_resent(tmp_path):
    cfg = {"keys": [{"type": "previous"}, {"type": "next"}, {"type": "none"},
                    {"type": "none"}, {"type": "mute"}, {"type": "none"}]}
    ctrl, backend, hid, _ = make_controller(tmp_path, cfg=cfg)
    ctrl.start()
    try:
        assert wait_for(lambda: sum(w[6:9] == b"BAT" for w in hid.writes) >= 6)
        time.sleep(0.4)
        before = len(hid.writes)
        time.sleep(0.4)
        assert len(hid.writes) - before <= 1  # como mucho un keep-alive
    finally:
        ctrl.stop()


def test_reconnects_after_device_error(tmp_path):
    ctrl, backend, hid, opened = make_controller(tmp_path)
    ctrl.start()
    try:
        assert wait_for(lambda: len(opened) == 1 and ctrl.device is not None)
        hid.fail = True
        assert wait_for(lambda: ctrl.device is None, 3)
        hid.fail = False
        assert wait_for(lambda: len(opened) == 2 and ctrl.device is not None, 6)
    finally:
        ctrl.stop()


def test_no_device_found_keeps_rendering(tmp_path):
    def opener(cfg):
        raise DeviceError("No se encontró ningún AKP03 conectado")

    ctrl = Controller(normalize({}), RecordingBackend(), device_opener=opener,
                      config_path=tmp_path / "c.json")
    ctrl.start()
    try:
        assert wait_for(lambda: ctrl.frame_id > 3)
        assert "No se encontró" in ctrl.status
        assert len(ctrl.last_frame) == 6
    finally:
        ctrl.stop()


def test_knob_track_debounce_and_brightness_saved(tmp_path):
    ctrl, backend, hid, _ = make_controller(tmp_path)
    ctrl.knob_turn("track", 1)
    ctrl.knob_turn("track", 1)
    ctrl.knob_turn("brightness", -1)
    assert ctrl.config["device"]["brightness"] == 75
    ctrl.start()
    try:
        assert wait_for(lambda: "next" in backend.calls)
        time.sleep(0.2)
        assert backend.calls.count("next") == 1
        assert wait_for(lambda: (tmp_path / "config.json").exists(), 4)
    finally:
        ctrl.stop()


def test_apply_config_changes_layout(tmp_path):
    ctrl, backend, hid, _ = make_controller(tmp_path)
    ctrl.start()
    try:
        assert wait_for(lambda: ctrl.device is not None)
        new = normalize({"keys": [{"type": "clock"}]})
        ctrl.apply_config(new)
        assert ctrl.renderer.config is new
        assert ctrl.device is not None
    finally:
        ctrl.stop()
