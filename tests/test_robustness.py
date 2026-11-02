"""Pruebas de los arreglos de robustez (una sola copia, reintentos, cerrojos…)."""

import socket
import threading
import time

from akp03.config import load_config, normalize, save_config
from akp03.controller import Controller
from akp03.device import AKP03Device, DeviceError
from akp03.instance import SingleInstance
from akp03.media import RetryingBackend
from akp03.media.base import MediaBackend, MediaState
from akp03.power import WM_ENDSESSION, PowerEvents
from akp03.render import Renderer, clean_text, text_script
from conftest import FakeHid
from test_controller import RecordingBackend, wait_for


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_single_instance_second_copy_shows_first():
    port = _free_port()
    first = SingleInstance(port)
    shown = threading.Event()
    first.on_show = shown.set
    assert first.acquire() is True
    second = SingleInstance(port)
    assert second.acquire() is False  # ya hay una copia: no arranca
    assert shown.wait(2)  # y la primera muestra su ventana
    first.release()


def test_single_instance_port_taken_by_other_program():
    port = _free_port()
    other = socket.socket()
    other.bind(("127.0.0.1", port))
    other.listen(1)

    def silent_server():  # acepta pero no contesta como nosotros
        conn, _ = other.accept()
        conn.recv(64)
        conn.close()

    threading.Thread(target=silent_server, daemon=True).start()
    try:
        assert SingleInstance(port).acquire() is True  # no se bloquea por otro programa
    finally:
        other.close()


def test_retrying_backend_recovers_after_failures():
    attempts = []

    class Real(MediaBackend):
        name = "real"

        def get_state(self):
            return MediaState(title="ok")

    def factory():
        attempts.append(1)
        if len(attempts) < 3:
            raise OSError("servicio de audio aún no listo")
        return Real()

    rb = RetryingBackend(factory, MediaBackend(), interval=0.01)
    deadline = time.monotonic() + 2
    while rb.get_state().title != "ok" and time.monotonic() < deadline:
        time.sleep(0.02)
    assert rb.get_state().title == "ok" and rb.name == "real" and len(attempts) == 3


def test_retrying_backend_gives_up_and_uses_fallback():
    def factory():
        raise OSError("nunca")

    class Keys(MediaBackend):
        name = "teclas"

    rb = RetryingBackend(factory, Keys(), interval=0.0, max_attempts=2)
    for _ in range(5):
        rb.get_state()
    assert rb.name == "teclas"


def _ctrl(tmp_path, opener=None):
    hid = FakeHid()
    ctrl = Controller(normalize({}), RecordingBackend(), config_path=tmp_path / "c.json",
                      device_opener=opener or (lambda c: AKP03Device(
                          hid, info={"vendor_id": 0x0300, "product_id": 0x3002})))
    return ctrl, hid


def test_brightness_change_does_not_redraw_keys(tmp_path):
    ctrl, hid = _ctrl(tmp_path)
    ctrl.start()
    try:
        assert wait_for(lambda: sum(w[6:9] == b"BAT" for w in hid.writes) >= 6)
        time.sleep(0.3)
        n = len(hid.writes)
        for b in range(10, 60, 5):  # arrastrar el control de brillo
            cfg = normalize(dict(ctrl.config))
            cfg["device"] = dict(cfg["device"], brightness=b)
            ctrl.apply_config(cfg)
        time.sleep(0.4)
        new = [w[6:9] for w in hid.writes[n:]]
        assert new.count(b"LIG") == 10
        assert new.count(b"BAT") <= 2  # sólo lo que el panel cambie solo (progreso)
    finally:
        ctrl.stop()


def test_failed_initialize_closes_usb_handle(tmp_path):
    handles = []

    class BadInit(AKP03Device):
        def initialize(self, brightness):
            raise DeviceError("no responde")

    def opener(cfg):
        hid = FakeHid()
        handles.append(hid)
        return BadInit(hid, info={"vendor_id": 0x0300, "product_id": 0x3002})

    ctrl, _ = _ctrl(tmp_path, opener)
    ctrl._try_connect()
    ctrl._try_connect()
    assert len(handles) == 2 and all(h.closed for h in handles)
    assert ctrl.device is None and "no responde" in ctrl.status


def test_concurrent_saves_do_not_fail(tmp_path):
    path = tmp_path / "config.json"
    errors = []

    def worker(i):
        try:
            for _ in range(20):
                save_config(normalize({"volume_step": i + 1}), path)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert load_config(path)["volume_step"] in (1, 2, 3, 4)
    assert not list(tmp_path.glob("*.tmp"))


def test_end_session_requests_exit():
    calls = []
    ev = PowerEvents(lambda: calls.append("shutdown"), lambda: None, lambda: None,
                     lambda on: None, on_exit=lambda: calls.append("exit"))
    ev.dispatch(WM_ENDSESSION, 1)
    assert calls == ["shutdown", "exit"]


def test_text_cleanup_and_scripts():
    assert clean_text("🔥 LOFI 🎧  beats ✨") == "LOFI beats"
    assert clean_text("") == ""
    assert text_script("夜に駆ける") == "cjk"
    assert text_script("방탄소년단") == "hangul"
    assert text_script("Café Tacvba") == "latin"


def test_idle_panel_shows_clock_and_cjk_titles_render():
    r = Renderer(normalize({}))
    idle = r.render(MediaState(volume=40), now=1.0)
    playing = r.render(MediaState(title="夜に駆ける 🔥", artist="YOASOBI", status="playing"), now=1.0)
    assert idle[1].tobytes() != playing[1].tobytes()
    assert len(r.render(MediaState(title="🔥🔥", artist=""), now=1.0)) == 6  # sólo emojis: no falla
