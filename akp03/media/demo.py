"""Backend simulado: útil para probar la interfaz sin música ni dispositivo."""

from __future__ import annotations

import io
import threading
import time

from .base import MediaBackend, MediaState

_PLAYLIST = [
    ("Bohemian Rhapsody", "Queen", "A Night at the Opera", "Spotify", 355, (120, 40, 160)),
    ("Lo-fi beats to relax/study to", "Lofi Girl", "", "Chrome", 3600, (40, 110, 170)),
    ("Despacito", "Luis Fonsi ft. Daddy Yankee", "Vida", "Spotify", 229, (210, 120, 30)),
]


def _make_art(color: tuple[int, int, int]) -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (200, 200), color)
    d = ImageDraw.Draw(img)
    for i in range(0, 200, 20):
        d.ellipse((i // 2, i // 2, 200 - i // 2, 200 - i // 2),
                  outline=tuple(min(255, c + i) for c in color), width=3)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class DemoBackend(MediaBackend):
    name = "demo"

    def __init__(self):
        self._lock = threading.Lock()
        self._index = 0
        self._playing = True
        self._pos = 12.0
        self._t = time.monotonic()
        self._volume = 40
        self._muted = False
        self._arts = [_make_art(item[5]) for item in _PLAYLIST]

    def _advance(self):
        now = time.monotonic()
        if self._playing:
            self._pos += now - self._t
            if self._pos >= _PLAYLIST[self._index][4]:
                self._index = (self._index + 1) % len(_PLAYLIST)
                self._pos = 0.0
        self._t = now

    def get_state(self) -> MediaState:
        with self._lock:
            self._advance()
            title, artist, album, app, dur, _ = _PLAYLIST[self._index]
            return MediaState(
                title=title, artist=artist, album=album, app=app,
                status="playing" if self._playing else "paused",
                position=self._pos, duration=float(dur), sampled_at=self._t,
                art=self._arts[self._index], art_key=f"demo{self._index}",
                volume=self._volume, muted=self._muted,
            )

    def play_pause(self):
        with self._lock:
            self._advance()
            self._playing = not self._playing

    def next(self):
        with self._lock:
            self._index = (self._index + 1) % len(_PLAYLIST)
            self._pos, self._t = 0.0, time.monotonic()

    def previous(self):
        with self._lock:
            self._advance()
            if self._pos < 3:
                self._index = (self._index - 1) % len(_PLAYLIST)
            self._pos, self._t = 0.0, time.monotonic()

    def seek(self, delta_seconds):
        with self._lock:
            self._advance()
            self._pos = max(0.0, min(_PLAYLIST[self._index][4] - 1, self._pos + delta_seconds))

    def get_volume(self):
        return self._volume

    def set_volume(self, percent):
        self._volume = max(0, min(100, int(percent)))

    def toggle_mute(self):
        self._muted = not self._muted
