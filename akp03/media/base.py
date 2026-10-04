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
    volume: Optional[int] = None  # 0-100
    muted: bool = False

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

    def close(self) -> None:
        pass
