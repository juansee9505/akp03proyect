import time

from akp03.config import normalize
from akp03.controller import Controller
from akp03.device import AKP03Device
from akp03.gamemode import fullscreen_app_active, lower_process_priority
from akp03.media.base import MediaBackend, MediaState
from conftest import FakeHid
from test_controller import wait_for

LONG = MediaState(title="Un título larguísimo que tiene que desplazarse por las teclas del AKP03",
                  artist="Artista", app="Spotify", status="playing", position=1, duration=300)


class Backend(MediaBackend):
    def get_state(self):
        return LONG


def _run(tmp_path, fullscreen: bool, enabled: bool = True, seconds: float = 2.0):
    hid = FakeHid()
    ctrl = Controller(normalize({"app": {"game_mode": enabled}}), Backend(),
                      config_path=tmp_path / "c.json",
                      device_opener=lambda c: AKP03Device(hid, info={"vendor_id": 0x0300,
                                                                     "product_id": 0x3002}))
    ctrl._fullscreen_check = lambda: fullscreen
    ctrl.start()
    try:
        assert wait_for(lambda: sum(w[6:9] == b"BAT" for w in hid.writes) >= 6)
        time.sleep(2.0)  # que pase la pausa inicial del texto
        n = len(hid.writes)
        time.sleep(seconds)
        images = sum(w[6:9] == b"BAT" for w in hid.writes[n:])
        return ctrl.game_mode, ctrl.renderer.scroll, images / seconds
    finally:
        ctrl.stop()


def test_game_mode_reduces_usb_traffic(tmp_path):
    normal_mode, normal_scroll, normal_rate = _run(tmp_path, fullscreen=False)
    game_mode, game_scroll, game_rate = _run(tmp_path, fullscreen=True)
    assert (normal_mode, normal_scroll) == (False, True)
    assert (game_mode, game_scroll) == (True, False)
    assert normal_rate > 10  # el texto se desplaza: muchas imágenes por segundo
    # En el juego sólo cambia el tiempo de la canción (1 vez/s, hasta 2 teclas).
    assert game_rate <= 2.5 and game_rate < normal_rate / 5


def test_game_mode_can_be_disabled(tmp_path):
    mode, scroll, rate = _run(tmp_path, fullscreen=True, enabled=False, seconds=1.0)
    assert (mode, scroll) == (False, True) and rate > 10


def test_platform_helpers_are_safe_outside_windows():
    assert fullscreen_app_active() is False
    lower_process_priority()  # no hace nada fuera de Windows
