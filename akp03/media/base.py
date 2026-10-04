"""Interfaz común de los backends de medios."""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from typing import Optional

_APP_NAMES = {
    "spotify": "Spotify",
    "chrome": "Chrome",
    "msedge": "Edge",
    "firefox": "Firefox",
    "308046b0af4a39cb": "Firefox",
    "opera": "Opera",
    "brave": "Brave",
    "vlc": "VLC",
    "zunemusic": "Media Player",
    "microsoft.zunemusic": "Media Player",
    "applemusic": "Apple Music",
    "itunes": "iTunes",
    "deezer": "Deezer",
    "tidal": "TIDAL",
    "youtubemusic": "YouTube Music",
}


def pretty_app_name(app_id: str) -> str:
    """'Spotify.exe' -> 'Spotify', 'SpotifyAB.SpotifyMusic_x!Spotify' -> 'Spotify'."""
    if not app_id:
        return ""
    name = app_id.split("!")[-1]
    name = name.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
    if name.lower().endswith(".exe"):
        name = name[:-4]
    key = name.lower()
    for k, v in _APP_NAMES.items():
        if key == k or key.startswith(k + ".") or k in key.split("."):
            return v
    for k, v in _APP_NAMES.items():
        if k in app_id.lower():
            return v
    return name[:1].upper() + name[1:]


_PROCESS_STEMS = {
    "308046b0af4a39cb": "firefox",
    "msedge": "msedge",
    "chrome": "chrome",
    "firefox": "firefox",
    "spotify": "spotify",
    "brave": "brave",
    "opera": "opera",
    "vivaldi": "vivaldi",
    "vlc": "vlc",
}


def process_stems(app_id: str) -> set[str]:
    """Posibles nombres de proceso (sin .exe) para un identificador de app.

    'Spotify.exe' -> {'spotify'}, 'SpotifyAB.SpotifyMusic_x!Spotify' -> {'spotify'},
    'MSEdge' -> {'msedge'}, '308046B0AF4A39CB' -> {'firefox'}.
    """
    if not app_id:
        return set()
    stems = set()
    aid = app_id.lower()
    last = aid.split("!")[-1].rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
    if last.endswith(".exe"):
        last = last[:-4]
    stems.add(last)
    for key, stem in _PROCESS_STEMS.items():
        if key in aid:
            stems.add(stem)
    first = aid.split(".")[0].split("_")[0]
    if first and "!" not in first:
        stems.add(first)
    return {s for s in stems if s}


@dataclass
class MediaState:
    title: str = ""
    artist: str = ""
    album: str = ""
    app: str = ""
    status: str = "none"  # playing | paused | stopped | none
    position: Optional[float] = None  # segundos
    duration: Optional[float] = None
    sampled_at: float = field(default_factory=time.monotonic)
    art: Optional[bytes] = None  # imagen (PNG/JPEG) de la carátula
    art_key: str = ""
    volume: Optional[int] = None  # volumen general del PC, 0-100
    muted: bool = False
    app_volume: Optional[int] = None  # volumen de la app que suena (Spotify, Chrome…)
    app_muted: bool = False
    mic_muted: Optional[bool] = None  # micrófono predeterminado

    @property
    def playing(self) -> bool:
        return self.status == "playing"

    @property
    def has_media(self) -> bool:
        return bool(self.title or self.artist)

    @property
    def track_key(self) -> str:
        return f"{self.app}|{self.title}|{self.artist}"

    def position_at(self, now: float | None = None) -> Optional[float]:
        if self.position is None:
            return None
        pos = self.position
        if self.playing:
            pos += (now if now is not None else time.monotonic()) - self.sampled_at
        if self.duration:
            pos = min(pos, self.duration)
        return max(0.0, pos)

    def copy(self, **changes) -> "MediaState":
        return replace(self, **changes)


class MediaBackend:
    """Base: todos los métodos son síncronos y seguros para llamar desde hilos."""

    name = "base"

    def get_state(self) -> MediaState:
        return MediaState()

    def play_pause(self) -> None:
        pass

    def next(self) -> None:
        pass

    def previous(self) -> None:
        pass

    def seek(self, delta_seconds: float) -> None:
        pass

    def get_volume(self) -> Optional[int]:
        return None

    def set_volume(self, percent: int) -> None:
        pass

    def change_volume(self, delta_percent: int) -> None:
        current = self.get_volume()
        if current is not None:
            self.set_volume(max(0, min(100, current + delta_percent)))

    def toggle_mute(self) -> None:
        pass

    # Volumen sólo de la aplicación que está sonando.
    def set_app_volume(self, percent: int) -> bool:
        """Devuelve False si no se encontró la aplicación en el mezclador."""
        return False

    def toggle_app_mute(self) -> bool:
        return False

    def toggle_mic_mute(self) -> Optional[bool]:
        """Silencia/activa el micrófono. Devuelve el nuevo estado (True = silenciado)."""
        return None

    def close(self) -> None:
        pass
