import time

from akp03.config import normalize
from akp03.controller import Controller
from akp03.device import AKP03Device
from akp03.power import (PBT_APMRESUMEAUTOMATIC, PBT_APMRESUMESUSPEND, PBT_APMSUSPEND,
                         PBT_POWERSETTINGCHANGE, WM_ENDSESSION, WM_POWERBROADCAST,
                         WM_QUERYENDSESSION, PowerEvents, PowerMonitor)
from conftest import FakeHid, make_report
from test_controller import RecordingBackend, wait_for


def make_events(display_enabled=True):
    calls = []
    ev = PowerEvents(on_shutdown=lambda: calls.append("shutdown"),
                     on_suspend=lambda: calls.append("suspend"),
                     on_resume=lambda: calls.append("resume"),
                     on_display=lambda on: calls.append(f"display:{on}"),
                     display_enabled=lambda: display_enabled)
    return ev, calls


def test_shutdown_messages():
    ev, calls = make_events()
    assert ev.dispatch(WM_QUERYENDSESSION, 0) == 1  # no bloquea el apagado de Windows
    assert ev.dispatch(WM_ENDSESSION, 1) == 0
    assert calls == ["shutdown", "shutdown"]
    ev.dispatch(WM_ENDSESSION, 0)  # apagado cancelado
    assert calls[-1] == "resume"


def test_suspend_resume_only_once():
    ev, calls = make_events()
    ev.dispatch(WM_POWERBROADCAST, PBT_APMSUSPEND)
    ev.dispatch(WM_POWERBROADCAST, PBT_APMRESUMEAUTOMATIC)
    ev.dispatch(WM_POWERBROADCAST, PBT_APMRESUMESUSPEND)  # Windows envía los dos
    assert calls == ["suspend", "resume"]


def test_display_state_and_option():
    ev, calls = make_events()
    ev.dispatch(WM_POWERBROADCAST, PBT_POWERSETTINGCHANGE, 0)
    ev.dispatch(WM_POWERBROADCAST, PBT_POWERSETTINGCHANGE, 2)  # atenuado: se ignora
    ev.dispatch(WM_POWERBROADCAST, PBT_POWERSETTINGCHANGE, 1)
    assert calls == ["display:False", "display:True"]
    ev2, calls2 = make_events(display_enabled=False)
    ev2.dispatch(WM_POWERBROADCAST, PBT_POWERSETTINGCHANGE, 0)
    assert calls2 == []


def test_errors_never_break_windows_message_loop():
    def boom():
        raise RuntimeError("x")
    ev = PowerEvents(boom, boom, boom, lambda on: boom())
    assert ev.dispatch(WM_QUERYENDSESSION, 0) == 1
    assert ev.dispatch(0x1234, 0) is None  # otros mensajes: procedimiento por defecto


def test_monitor_is_noop_outside_windows():
    m = PowerMonitor(make_events()[0])
    m.start()
    m.stop()


def _ctrl(tmp_path):
    hid = FakeHid()
    opened = []

    def opener(cfg):
        dev = AKP03Device(hid, info={"vendor_id": 0x0300, "product_id": 0x3002})
        opened.append(dev)
        return dev

    ctrl = Controller(normalize({}), RecordingBackend(), device_opener=opener,
                      config_path=tmp_path / "c.json")
    return ctrl, hid, opened


def _cmds(hid, start=0):
    return [w[6:9] for w in hid.writes[start:]]


def test_pc_shutdown_turns_device_off_and_stops_drawing(tmp_path):
    ctrl, hid, _ = _ctrl(tmp_path)
    ctrl.start()
    try:
        assert wait_for(lambda: _cmds(hid).count(b"BAT") >= 6)
        ctrl.power_shutdown()
        n = len(hid.writes)
        assert _cmds(hid)[-2:] == [b"CLE", b"HAN"]
        assert hid.writes[-2][6:13] == b"CLE\x00\x00DC"
        # Mientras se apaga no se vuelve a dibujar, ni aunque se toque una tecla.
        hid.reports.append(make_report(0x01))
        time.sleep(0.6)
        assert b"BAT" not in _cmds(hid, n)
    finally:
        ctrl.stop()


def test_monitor_off_then_key_press_wakes(tmp_path):
    ctrl, hid, _ = _ctrl(tmp_path)
    ctrl.start()
    try:
        assert wait_for(lambda: _cmds(hid).count(b"BAT") >= 6)
        ctrl.display_changed(False)
        assert ctrl.screen_is_off and _cmds(hid)[-1] == b"HAN"
        n = len(hid.writes)
        time.sleep(0.6)
        assert len(hid.writes) == n  # nada de tráfico con la pantalla apagada
        hid.reports.append(make_report(0x01))  # tocar una tecla la enciende
        assert wait_for(lambda: not ctrl.screen_is_off)
        assert wait_for(lambda: _cmds(hid, n).count(b"BAT") >= 6)  # redibuja todo
        assert _cmds(hid, n)[0] == b"DIS"
    finally:
        ctrl.stop()


def test_resume_reconnects_and_redraws(tmp_path):
    ctrl, hid, opened = _ctrl(tmp_path)
    ctrl.start()
    try:
        assert wait_for(lambda: len(opened) == 1 and _cmds(hid).count(b"BAT") >= 6)
        ctrl.power_suspend()
        assert ctrl.screen_is_off
        ctrl.power_resume()
        assert wait_for(lambda: len(opened) == 2, 5)
        n = len(hid.writes)
        assert wait_for(lambda: _cmds(hid, n).count(b"BAT") >= 6 or _cmds(hid).count(b"BAT") >= 12)
        assert not ctrl.screen_is_off
    finally:
        ctrl.stop()
