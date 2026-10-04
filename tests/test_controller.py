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
        self.app_volume = 70
        self.mic = False

    def get_state(self):
        return MediaState(title="Song", artist="Artist", app="Spotify", status="playing",
                          position=1, duration=100, volume=self.volume, muted=self.muted,
                          app_volume=self.app_volume, mic_muted=self.mic)

    def set_app_volume(self, p):
        self.calls.append(("set_app_volume", p))
        self.app_volume = p
        return True

    def toggle_mic_mute(self):
        self.calls.append("mic")
        self.mic = not self.mic
        return self.mic

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


MUSIC_CONTROLS = {
    "version": 2,
    "knobs": [{"turn": "volume", "press": "mute"}, {"turn": "track", "press": "play_pause"},
              {"turn": "brightness", "press": "none"}],
    "buttons": [{"action": "previous"}, {"action": "play_pause"}, {"action": "next"}],
}


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
    ctrl, backend, hid, _ = make_controller(tmp_path, cfg=MUSIC_CONTROLS)
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
    ctrl, backend, hid, _ = make_controller(tmp_path, cfg=MUSIC_CONTROLS)
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


def test_default_knobs_app_volume_and_mic(tmp_path):
    """Asignación por defecto: perilla 1 = volumen de la app, perilla 2 = PC + micrófono."""
    ctrl, backend, hid, _ = make_controller(tmp_path)
    ctrl.start()
    try:
        assert wait_for(lambda: ctrl.state.app_volume == 70)
        hid.reports += [make_report(0x91)] * 2  # perilla 1 +
        assert wait_for(lambda: backend.app_volume == 74)
        assert backend.volume == 40  # el volumen general no cambia
        hid.reports += [make_report(0x50)]  # perilla 2 -
        assert wait_for(lambda: backend.volume == 38)
        hid.reports += [make_report(0x35)]  # presionar perilla 2
        assert wait_for(lambda: backend.mic is True)
        assert wait_for(lambda: ctrl._overlay is not None and ctrl._overlay.text == "Mic OFF")
        assert ctrl.state.mic_muted is True
    finally:
        ctrl.stop()


def test_app_volume_falls_back_to_master_when_no_app(tmp_path):
    ctrl, backend, hid, _ = make_controller(tmp_path)
    ctrl.state = MediaState(volume=50)
    ctrl.change_app_volume(4)
    assert ctrl.state.volume == 54
    assert ctrl._overlay.subtitle == "Volumen PC"


def test_obs_buttons_show_feedback(tmp_path):
    ctrl, backend, hid, _ = make_controller(tmp_path)
    calls = []

    class FakeObs:
        def run(self, action, target=None):
            from akp03.obs import Feedback
            calls.append((action, target))
            return Feedback("record", "Grabando", True)

    ctrl.obs = type("X", (), {"run": FakeObs().run, "client": type("C", (), {"close": lambda s: None,
                                                                              "configure": lambda s, *a: None})()})()
    ctrl.start()
    try:
        hid.reports += [make_report(0x25)]  # botón 1 = OBS grabar
        assert wait_for(lambda: calls == [("obs_record", None)])
        assert wait_for(lambda: ctrl._overlay is not None and ctrl._overlay.text == "Grabando")
    finally:
        ctrl.stop()


def test_obs_error_is_reported_not_raised(tmp_path):
    ctrl, backend, hid, _ = make_controller(tmp_path, cfg={"obs": {"port": 1}})
    ctrl.start()
    try:
        ctrl.do_action("obs_stream")
        assert wait_for(lambda: ctrl.obs_status != "", 5)
        assert ctrl._overlay.subtitle == "OBS" and ctrl._overlay.alert
    finally:
        ctrl.stop()
