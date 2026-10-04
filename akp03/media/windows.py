"""Backend de Windows 10/11.

Usa la API "Global System Media Transport Controls" (la misma que alimenta el
panel multimedia de Windows), así que funciona con Spotify, YouTube en
Chrome/Edge/Firefox, la app Música, VLC, etc. El volumen maestro se controla
con pycaw (Core Audio). Si alguna librería no está disponible se recurre a
las teclas multimedia virtuales.
"""

from __future__ import annotations

import asyncio
import ctypes
import inspect
import logging
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from .base import MediaBackend, MediaState, pretty_app_name, process_stems

log = logging.getLogger(__name__)

VK_VOLUME_MUTE = 0xAD
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_PLAY_PAUSE = 0xB3
KEYEVENTF_KEYUP = 0x2

# GlobalSystemMediaTransportControlsSessionPlaybackStatus
_STATUS = {0: "none", 1: "stopped", 2: "paused", 3: "stopped", 4: "playing", 5: "paused"}
_PLAYING = 4


def press_media_key(vk: int) -> None:
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


def _import_winrt():
    """Devuelve (media.control, storage.streams) de pywinrt o winsdk."""
    try:
        from winrt.windows.media import control as mc  # type: ignore
        from winrt.windows.storage import streams  # type: ignore
        return mc, streams
    except ImportError:
        from winsdk.windows.media import control as mc  # type: ignore
        from winsdk.windows.storage import streams  # type: ignore
        return mc, streams


def _seconds(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, timedelta):
        return value.total_seconds()
    if hasattr(value, "duration"):  # TimeSpan antiguo
        return value.duration / 10_000_000
    try:
        return float(value) / 10_000_000
    except (TypeError, ValueError):
        return None


class _Volume:
    """Volumen con pycaw (Core Audio). Debe usarse desde un único hilo."""

    def __init__(self):
        sys.coinit_flags = 0  # COINIT_MULTITHREADED, compatible con WinRT
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume  # type: ignore
        from comtypes import CLSCTX_ALL  # type: ignore

        self._utils = AudioUtilities
        self._iface = IAudioEndpointVolume
        self._clsctx = CLSCTX_ALL
        self._endpoint(self._utils.GetSpeakers())  # falla aquí si algo no funciona

    def _endpoint(self, device):
        """IAudioEndpointVolume de un dispositivo (pycaw nuevo o antiguo)."""
        if device is None:
            return None
        ep = getattr(device, "EndpointVolume", None)  # AudioDevice (pycaw >= 2024)
        if ep is not None:
            return ep
        iface = device.Activate(self._iface._iid_, self._clsctx, None)  # IMMDevice
        return iface.QueryInterface(self._iface)

    def _master(self):
        return self._endpoint(self._utils.GetSpeakers())

    # ---- volumen general
    def get(self) -> tuple[int, bool]:
        ep = self._master()
        return round(ep.GetMasterVolumeLevelScalar() * 100), bool(ep.GetMute())

    def set(self, percent: int) -> None:
        ep = self._master()
        ep.SetMasterVolumeLevelScalar(max(0, min(100, percent)) / 100.0, None)
        if percent > 0 and ep.GetMute():
            ep.SetMute(0, None)

    def toggle_mute(self) -> None:
        ep = self._master()
        ep.SetMute(0 if ep.GetMute() else 1, None)

    # ---- micrófono predeterminado
    def _mic(self):
        try:
            return self._endpoint(self._utils.GetMicrophone())
        except Exception:  # noqa: BLE001  (sin micrófono)
            return None

    def mic_muted(self) -> Optional[bool]:
        ep = self._mic()
        return None if ep is None else bool(ep.GetMute())

    def toggle_mic(self) -> Optional[bool]:
        ep = self._mic()
        if ep is None:
            return None
        new = not bool(ep.GetMute())
        ep.SetMute(1 if new else 0, None)
        return new

    # ---- volumen por aplicación (mezclador de volumen de Windows)
    def _app_sessions(self, stems: set[str]) -> list:
        if not stems:
            return []
        found = []
        for session in self._utils.GetAllSessions():
            try:
                proc = session.Process
                name = proc.name().lower() if proc is not None else ""
            except Exception:  # noqa: BLE001  (proceso terminado / sin permisos)
                continue
            if name.endswith(".exe"):
                name = name[:-4]
            if name in stems:
                found.append(session)
        return found

    def get_app(self, stems: set[str]) -> Optional[tuple[int, bool]]:
        sessions = self._app_sessions(stems)
        if not sessions:
            return None
        vol = sessions[0].SimpleAudioVolume
        return round(vol.GetMasterVolume() * 100), bool(vol.GetMute())

    def set_app(self, stems: set[str], percent: int) -> bool:
        sessions = self._app_sessions(stems)
        for session in sessions:  # Spotify/Chrome pueden tener varias sesiones
            vol = session.SimpleAudioVolume
            vol.SetMasterVolume(max(0, min(100, percent)) / 100.0, None)
            if percent > 0 and vol.GetMute():
                vol.SetMute(0, None)
        return bool(sessions)

    def toggle_app_mute(self, stems: set[str]) -> bool:
        sessions = self._app_sessions(stems)
        if not sessions:
            return False
        new = 0 if sessions[0].SimpleAudioVolume.GetMute() else 1
        for session in sessions:
            session.SimpleAudioVolume.SetMute(new, None)
        return True


class WindowsMediaBackend(MediaBackend):
    name = "windows"

    def __init__(self, prefer_playing: bool = True):
        self.prefer_playing = prefer_playing
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._init_error: Optional[BaseException] = None
        self._thread = threading.Thread(target=self._run, name="winrt-media", daemon=True)
        self._thread.start()
        self._ready.wait(15)
        if self._init_error:
            raise self._init_error

        self._session = None
        self._app_stems: set[str] = set()
        self._art_cache: dict[str, bytes] = {}
        self._art_attempts: dict[str, float] = {}

    # ------------------------------------------------------------ hilo propio
    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._mc, self._streams = _import_winrt()
            self._manager = self._loop.run_until_complete(self._await(
                self._mc.GlobalSystemMediaTransportControlsSessionManager.request_async()))
        except BaseException as exc:  # noqa: BLE001
            self._init_error = exc
            self._ready.set()
            return
        try:
            self._volume: Optional[_Volume] = _Volume()
        except Exception as exc:  # noqa: BLE001
            log.warning("pycaw no disponible (%s); el volumen usará teclas multimedia", exc)
            self._volume = None
        self._ready.set()
        self._loop.run_forever()

    @staticmethod
    async def _await(op):
        return await op

    def _call(self, fn: Callable[[], Any], timeout: float = 5.0) -> Any:
        """Ejecuta `fn` (y espera su resultado si es asíncrono) en el hilo WinRT."""

        async def runner():
            result = fn()
            if inspect.isawaitable(result):
                result = await result
            return result

        return asyncio.run_coroutine_threadsafe(runner(), self._loop).result(timeout)

    # ------------------------------------------------------------ estado
    def _pick_session(self):
        sessions = []
        try:
            sessions = list(self._manager.get_sessions())
        except Exception:  # noqa: BLE001
            pass
        if self.prefer_playing:
            for s in sessions:
                try:
                    if int(s.get_playback_info().playback_status) == _PLAYING:
                        return s
                except Exception:  # noqa: BLE001
                    continue
        current = self._manager.get_current_session()
        if current is not None:
            return current
        return sessions[0] if sessions else None

    async def _read_thumbnail(self, ref) -> Optional[bytes]:
        streams = self._streams
        stream = await ref.open_read_async()
        size = int(stream.size)
        if size <= 0 or size > 8 * 1024 * 1024:
            return None
        buf = streams.Buffer(size)
        result = await stream.read_async(buf, size, streams.InputStreamOptions.READ_AHEAD)
        try:
            data = bytes(memoryview(result))
        except TypeError:
            reader = streams.DataReader.from_buffer(result)
            data = bytearray(result.length)
            reader.read_bytes(data)
            data = bytes(data)
        return data or None

    async def _get_state_async(self) -> MediaState:
        state = MediaState()
        if self._volume is not None:
            try:
                state.volume, state.muted = self._volume.get()
            except Exception as exc:  # noqa: BLE001
                log.debug("Error leyendo volumen: %s", exc)
            try:
                state.mic_muted = self._volume.mic_muted()
            except Exception as exc:  # noqa: BLE001
                log.debug("Error leyendo micrófono: %s", exc)

        session = self._pick_session()
        self._session = session
        if session is None:
            self._app_stems = set()
            return state

        app_id = getattr(session, "source_app_user_model_id", "") or ""
        state.app = pretty_app_name(app_id)
        self._app_stems = process_stems(app_id)
        if self._volume is not None:
            try:
                app_vol = self._volume.get_app(self._app_stems)
                if app_vol:
                    state.app_volume, state.app_muted = app_vol
            except Exception as exc:  # noqa: BLE001
                log.debug("Error leyendo volumen de %s: %s", app_id, exc)
        try:
            state.status = _STATUS.get(int(session.get_playback_info().playback_status), "none")
        except Exception:  # noqa: BLE001
            state.status = "none"

        try:
            props = await session.try_get_media_properties_async()
        except Exception as exc:  # noqa: BLE001
            log.debug("Sin propiedades de medios: %s", exc)
            return state
        state.title = props.title or ""
        state.artist = props.artist or props.album_artist or ""
        state.album = props.album_title or ""

        try:
            tl = session.get_timeline_properties()
            start = _seconds(tl.start_time) or 0.0
            end = _seconds(tl.end_time)
            pos = _seconds(tl.position)
            if end and end > start:
                state.duration = end - start
                if pos is not None:
                    pos -= start
                    last = getattr(tl, "last_updated_time", None)
                    if state.playing and isinstance(last, datetime) and last.year > 1601:
                        if last.tzinfo is None:
                            last = last.replace(tzinfo=timezone.utc)
                        elapsed = (datetime.now(timezone.utc) - last).total_seconds()
                        if 0 <= elapsed < 24 * 3600:
                            pos += elapsed
                    state.position = max(0.0, min(pos, state.duration))
                    state.sampled_at = time.monotonic()
        except Exception as exc:  # noqa: BLE001
            log.debug("Sin línea de tiempo: %s", exc)

        key = state.track_key
        state.art_key = key
        if key in self._art_cache:
            state.art = self._art_cache[key]
        elif props.thumbnail is not None and time.monotonic() - self._art_attempts.get(key, 0) > 2:
            # Spotify a veces publica la miniatura un poco después del título.
            self._art_attempts[key] = time.monotonic()
            try:
                art = await self._read_thumbnail(props.thumbnail)
            except Exception as exc:  # noqa: BLE001
                log.debug("No se pudo leer la miniatura: %s", exc)
                art = None
            if art:
                if len(self._art_cache) > 20:
                    self._art_cache.clear()
                    self._art_attempts.clear()
                self._art_cache[key] = art
                state.art = art
        return state

    def get_state(self) -> MediaState:
        return self._call(self._get_state_async)

    # ------------------------------------------------------------ control
    def _control(self, method: str, vk: int, *args) -> None:
        def do():
            session = self._session or self._pick_session()
            if session is None:
                press_media_key(vk)
                return None
            return getattr(session, method)(*args)

        try:
            ok = self._call(do)
            if ok is False:
                press_media_key(vk)
        except Exception as exc:  # noqa: BLE001
            log.debug("%s falló (%s); uso tecla multimedia", method, exc)
            press_media_key(vk)

    def play_pause(self) -> None:
        self._control("try_toggle_play_pause_async", VK_MEDIA_PLAY_PAUSE)

    def next(self) -> None:
        self._control("try_skip_next_async", VK_MEDIA_NEXT_TRACK)

    def previous(self) -> None:
        self._control("try_skip_previous_async", VK_MEDIA_PREV_TRACK)

    def seek(self, delta_seconds: float) -> None:
        def do():
            session = self._session or self._pick_session()
            if session is None:
                return None
            tl = session.get_timeline_properties()
            start = _seconds(tl.start_time) or 0.0
            end = _seconds(tl.end_time) or 0.0
            pos = (_seconds(tl.position) or 0.0) + delta_seconds
            pos = max(start, min(pos, end - 1 if end > start else pos))
            return session.try_change_playback_position_async(int(pos * 10_000_000))

        try:
            self._call(do)
        except Exception as exc:  # noqa: BLE001
            log.debug("seek falló: %s", exc)

    def get_volume(self) -> Optional[int]:
        if self._volume is None:
            return None
        try:
            return self._call(lambda: self._volume.get()[0])
        except Exception:  # noqa: BLE001
            return None

    def set_volume(self, percent: int) -> None:
        if self._volume is None:
            return
        self._call(lambda: self._volume.set(int(percent)))

    def change_volume(self, delta_percent: int) -> None:
        if self._volume is not None:
            return super().change_volume(delta_percent)
        vk = VK_VOLUME_UP if delta_percent > 0 else VK_VOLUME_DOWN
        for _ in range(max(1, abs(delta_percent) // 2)):  # cada pulsación = 2 %
            press_media_key(vk)

    def toggle_mute(self) -> None:
        if self._volume is None:
            press_media_key(VK_VOLUME_MUTE)
            return
        self._call(self._volume.toggle_mute)

    def set_app_volume(self, percent: int) -> bool:
        if self._volume is None:
            return False
        return bool(self._call(lambda: self._volume.set_app(self._app_stems, int(percent))))

    def toggle_app_mute(self) -> bool:
        if self._volume is None:
            return False
        return bool(self._call(lambda: self._volume.toggle_app_mute(self._app_stems)))

    def toggle_mic_mute(self) -> Optional[bool]:
        if self._volume is None:
            return None
        return self._call(self._volume.toggle_mic)

    def close(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)


class WindowsMediaKeysBackend(MediaBackend):
    """Último recurso: sólo controla con teclas multimedia, sin información."""

    name = "windows-keys"

    def play_pause(self):
        press_media_key(VK_MEDIA_PLAY_PAUSE)

    def next(self):
        press_media_key(VK_MEDIA_NEXT_TRACK)

    def previous(self):
        press_media_key(VK_MEDIA_PREV_TRACK)

    def change_volume(self, delta_percent):
        vk = VK_VOLUME_UP if delta_percent > 0 else VK_VOLUME_DOWN
        for _ in range(max(1, abs(delta_percent) // 2)):
            press_media_key(vk)

    def toggle_mute(self):
        press_media_key(VK_VOLUME_MUTE)
