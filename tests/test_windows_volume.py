"""Pruebas de la lógica de volumen de Windows con pycaw/comtypes/psutil simulados."""

import sys
import types

import pytest


class FakeEndpoint:
    def __init__(self, level=0.5, mute=0):
        self.level, self.mute = level, mute

    def GetMasterVolumeLevelScalar(self):
        return self.level

    def SetMasterVolumeLevelScalar(self, v, ctx):
        self.level = v

    def GetMute(self):
        return self.mute

    def SetMute(self, m, ctx):
        self.mute = m


class FakeSimple:
    def __init__(self, level=0.8):
        self.level, self.mute = level, 0

    def GetMasterVolume(self):
        return self.level

    def SetMasterVolume(self, v, ctx):
        self.level = v

    def GetMute(self):
        return self.mute

    def SetMute(self, m, ctx):
        self.mute = m


class FakeSession:
    def __init__(self, pid):
        self.ProcessId = pid
        self.SimpleAudioVolume = FakeSimple()


class RawMic:
    """GetMicrophone() devuelve un IMMDevice «en crudo» (sin EndpointVolume)."""

    def __init__(self, ep):
        self.ep = ep

    def Activate(self, iid, ctx, params):
        ep = self.ep
        return types.SimpleNamespace(QueryInterface=lambda iface: ep)


@pytest.fixture
def fake_audio(monkeypatch):
    calls = {"speakers": 0, "mic": 0, "sessions": 0, "process": 0}
    master, mic = FakeEndpoint(0.4), FakeEndpoint(0.9, 0)
    sessions = [FakeSession(10), FakeSession(11), FakeSession(20)]
    names = {10: "Spotify.exe", 11: "Spotify.exe", 20: "chrome.exe"}

    class AudioUtilities:
        @staticmethod
        def GetSpeakers():
            calls["speakers"] += 1
            return types.SimpleNamespace(EndpointVolume=master)

        @staticmethod
        def GetMicrophone():
            calls["mic"] += 1
            return RawMic(mic)

        @staticmethod
        def GetAllSessions():
            calls["sessions"] += 1
            return list(sessions)

    class Process:
        def __init__(self, pid):
            calls["process"] += 1
            self.pid = pid

        def name(self):
            return names[self.pid]

    pycaw = types.ModuleType("pycaw")
    pycaw_pycaw = types.ModuleType("pycaw.pycaw")
    pycaw_pycaw.AudioUtilities = AudioUtilities
    pycaw_pycaw.IAudioEndpointVolume = types.SimpleNamespace(_iid_="iid")
    comtypes = types.ModuleType("comtypes")
    comtypes.CLSCTX_ALL = 23
    psutil = types.ModuleType("psutil")
    psutil.Process = Process
    monkeypatch.setitem(sys.modules, "pycaw", pycaw)
    monkeypatch.setitem(sys.modules, "pycaw.pycaw", pycaw_pycaw)
    monkeypatch.setitem(sys.modules, "comtypes", comtypes)
    monkeypatch.setitem(sys.modules, "psutil", psutil)

    from akp03.media.windows import _Volume
    return _Volume(), calls, master, mic, sessions


def test_master_volume_and_caching(fake_audio):
    vol, calls, master, mic, sessions = fake_audio
    for _ in range(20):  # 20 consultas seguidas (≈ 20 s de uso)
        assert vol.get() == (40, False)
    assert calls["speakers"] == 1  # el altavoz se pidió una sola vez
    vol.set(55)
    assert round(master.level, 2) == 0.55


def test_microphone_raw_device(fake_audio):
    vol, calls, master, mic, sessions = fake_audio
    assert vol.mic_muted() is False
    assert vol.toggle_mic() is True and mic.mute == 1
    assert vol.mic_muted() is True
    assert vol.toggle_mic() is False and mic.mute == 0


def test_app_volume_and_process_name_cache(fake_audio):
    vol, calls, master, mic, sessions = fake_audio
    for _ in range(20):
        assert vol.get_app({"spotify"}) == (80, False)
    assert calls["sessions"] == 1  # la lista de apps no se recorre en cada consulta
    assert calls["process"] == 3  # un nombre de proceso por PID, no por consulta
    assert vol.set_app({"spotify"}, 30) is True
    assert sessions[0].SimpleAudioVolume.level == sessions[1].SimpleAudioVolume.level == 0.3
    assert sessions[2].SimpleAudioVolume.level == 0.8  # Chrome no se toca
    assert vol.toggle_app_mute({"chrome"}) is True and sessions[2].SimpleAudioVolume.mute == 1
    assert vol.get_app({"firefox"}) is None and vol.set_app({"firefox"}, 10) is False


def test_cache_expires_and_invalidates(fake_audio, monkeypatch):
    vol, calls, *_ = fake_audio
    vol.get()
    vol.get_app({"spotify"})
    vol.invalidate()
    vol.get()
    vol.get_app({"spotify"})
    assert calls["speakers"] == 2 and calls["sessions"] == 2
    assert calls["process"] == 3  # los nombres por PID siguen sirviendo
