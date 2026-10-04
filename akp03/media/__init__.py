"""Selección del backend de medios según el sistema operativo."""

from __future__ import annotations

import logging
import sys

from .base import MediaBackend, MediaState, pretty_app_name

log = logging.getLogger(__name__)

__all__ = ["MediaBackend", "MediaState", "create_backend", "pretty_app_name"]


def create_backend(name: str = "auto", prefer_playing: bool = True) -> MediaBackend:
    if name == "demo":
        from .demo import DemoBackend
        return DemoBackend()

    if sys.platform == "win32" and name in ("auto", "windows"):
        try:
            from .windows import WindowsMediaBackend
            return WindowsMediaBackend(prefer_playing=prefer_playing)
        except Exception as exc:  # noqa: BLE001
            log.error("No se pudo iniciar la API multimedia de Windows (%s). "
                      "Sólo funcionarán las teclas multimedia.", exc)
            from .windows import WindowsMediaKeysBackend
            return WindowsMediaKeysBackend()

    if sys.platform.startswith("linux") and name in ("auto", "linux"):
        try:
            from .linux import LinuxMediaBackend
            return LinuxMediaBackend(prefer_playing=prefer_playing)
        except Exception as exc:  # noqa: BLE001
            log.error("Backend Linux no disponible: %s", exc)

    log.warning("Sin backend de medios para esta plataforma; no se mostrará información")
    return MediaBackend()
