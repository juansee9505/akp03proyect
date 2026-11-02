"""Selección del backend de medios según el sistema operativo."""

from __future__ import annotations

import logging
import sys
import threading
import time
from typing import Callable, Optional

from .base import MediaBackend, MediaState, pretty_app_name

log = logging.getLogger(__name__)

__all__ = ["MediaBackend", "MediaState", "RetryingBackend", "create_backend", "pretty_app_name"]


def create_backend(name: str = "auto", prefer_playing: bool = True) -> MediaBackend:
    if name == "demo":
        from .demo import DemoBackend
        return DemoBackend()

    if sys.platform == "win32" and name in ("auto", "windows"):
        from .windows import WindowsMediaBackend, WindowsMediaKeysBackend

        def factory() -> MediaBackend:
            return WindowsMediaBackend(prefer_playing=prefer_playing)

        try:
            return factory()
        except Exception as exc:  # noqa: BLE001
            # Al iniciar sesión, el programa puede arrancar antes que los servicios
            # multimedia de Windows: se reintenta en segundo plano.
            log.warning("La API multimedia de Windows aún no responde (%s); se reintentará. "
                        "Mientras tanto se usan las teclas multimedia.", exc)
            return RetryingBackend(factory, WindowsMediaKeysBackend())

    if sys.platform.startswith("linux") and name in ("auto", "linux"):
        try:
            from .linux import LinuxMediaBackend
            return LinuxMediaBackend(prefer_playing=prefer_playing)
        except Exception as exc:  # noqa: BLE001
            log.error("Backend Linux no disponible: %s", exc)

    log.warning("Sin backend de medios para esta plataforma; no se mostrará información")
    return MediaBackend()


class RetryingBackend(MediaBackend):
    """Usa ``fallback`` mientras ``factory`` falle, reintentando cada ``interval`` s."""

    def __init__(self, factory: Callable[[], MediaBackend], fallback: MediaBackend,
                 interval: float = 10.0, max_attempts: int = 60):
        self._factory = factory
        self._fallback = fallback
        self._interval = interval
        self._attempts_left = max_attempts
        self._next_try = time.monotonic() + interval
        self._backend: Optional[MediaBackend] = None
        self._lock = threading.Lock()

    @property
    def name(self) -> str:  # type: ignore[override]
        return self._current().name

    def _current(self) -> MediaBackend:
        if self._backend is not None:
            return self._backend
        with self._lock:
            if (self._backend is None and self._attempts_left > 0
                    and time.monotonic() >= self._next_try):
                self._attempts_left -= 1
                self._next_try = time.monotonic() + self._interval
                try:
                    self._backend = self._factory()
                    log.info("API multimedia lista: %s", self._backend.name)
                except Exception as exc:  # noqa: BLE001
                    log.debug("La API multimedia sigue sin responder: %s", exc)
        return self._backend or self._fallback

    def get_state(self) -> MediaState:
        return self._current().get_state()

    def play_pause(self) -> None:
        self._current().play_pause()

    def next(self) -> None:
        self._current().next()

    def previous(self) -> None:
        self._current().previous()

    def seek(self, delta_seconds: float) -> None:
        self._current().seek(delta_seconds)

    def get_volume(self) -> Optional[int]:
        return self._current().get_volume()

    def set_volume(self, percent: int) -> None:
        self._current().set_volume(percent)

    def change_volume(self, delta_percent: int) -> None:
        self._current().change_volume(delta_percent)

    def toggle_mute(self) -> None:
        self._current().toggle_mute()

    def set_app_volume(self, percent: int) -> bool:
        return self._current().set_app_volume(percent)

    def toggle_app_mute(self) -> bool:
        return self._current().toggle_app_mute()

    def toggle_mic_mute(self) -> Optional[bool]:
        return self._current().toggle_mic_mute()

    def close(self) -> None:
        if self._backend is not None:
            self._backend.close()
