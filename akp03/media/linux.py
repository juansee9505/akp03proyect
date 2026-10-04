"""Backend de Linux: MPRIS mediante `playerctl` y volumen con `wpctl`/`pactl`."""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
from typing import Optional

from .base import MediaBackend, MediaState, pretty_app_name, process_stems

log = logging.getLogger(__name__)

_FMT = "\t".join([
    "{{status}}", "{{playerName}}", "{{xesam:title}}", "{{xesam:artist}}",
    "{{xesam:album}}", "{{position}}", "{{mpris:length}}", "{{mpris:artUrl}}",
])


def _run(*args: str, timeout: float = 2.0) -> Optional[str]:
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip()


class LinuxMediaBackend(MediaBackend):
    name = "linux"

    def __init__(self, prefer_playing: bool = True):
        if not shutil.which("playerctl"):
            raise RuntimeError("playerctl no está instalado")
        self.prefer_playing = prefer_playing
        self._player: Optional[str] = None
        self._art_cache: dict[str, Optional[bytes]] = {}
        self._wpctl = bool(shutil.which("wpctl"))
        self._pactl = bool(shutil.which("pactl"))

    def _players(self) -> list[str]:
        out = _run("playerctl", "-l")
        return [p for p in (out or "").splitlines() if p]

    def _pick_player(self) -> Optional[str]:
        players = self._players()
        if not players:
            return None
        if self.prefer_playing:
            for p in players:
                if _run("playerctl", "-p", p, "status") == "Playing":
                    return p
        return players[0]

    def _ctl(self, *args: str) -> None:
        cmd = ["playerctl"]
        if self._player:
            cmd += ["-p", self._player]
        _run(*cmd, *args)

    def _fetch_art(self, url: str) -> Optional[bytes]:
        if not url:
            return None
        if url in self._art_cache:
            return self._art_cache[url]
        data = None
        try:
            if url.startswith("file://"):
                with open(urllib.parse.unquote(urllib.parse.urlparse(url).path), "rb") as fh:
                    data = fh.read()
            elif url.startswith(("http://", "https://")):
                with urllib.request.urlopen(url, timeout=3) as resp:
                    data = resp.read(8 * 1024 * 1024)
        except Exception as exc:  # noqa: BLE001
            log.debug("No se pudo obtener la carátula %s: %s", url, exc)
        if len(self._art_cache) > 30:
            self._art_cache.clear()
        self._art_cache[url] = data
        return data

    def get_state(self) -> MediaState:
        state = MediaState()
        vol = self._read_volume()
        if vol:
            state.volume, state.muted = vol
        state.mic_muted = self._mic_muted()
        self._player = self._pick_player()
        if not self._player:
            return state
        out = _run("playerctl", "-p", self._player, "metadata", "--format", _FMT)
        if out is None:
            return state
        parts = (out.split("\t") + [""] * 8)[:8]
        status, player, title, artist, album, pos, length, art_url = parts
        state.status = {"Playing": "playing", "Paused": "paused", "Stopped": "stopped"}.get(status, "none")
        state.app = pretty_app_name(player)
        state.title, state.artist, state.album = title, artist, album
        try:
            state.position = int(pos) / 1_000_000 if pos else None
            state.duration = int(length) / 1_000_000 if length else None
        except ValueError:
            pass
        state.art_key = state.track_key + "|" + art_url
        state.art = self._fetch_art(art_url)
        app_vol = self._app_volume(player)
        if app_vol:
            state.app_volume, state.app_muted = app_vol[1], app_vol[2]
        return state

    def play_pause(self):
        self._ctl("play-pause")

    def next(self):
        self._ctl("next")

    def previous(self):
        self._ctl("previous")

    def seek(self, delta_seconds):
        sign = "+" if delta_seconds >= 0 else "-"
        self._ctl("position", f"{abs(delta_seconds)}{sign}")

    # ----------------------------------------------------------- volumen
    def _read_volume(self) -> Optional[tuple[int, bool]]:
        if self._wpctl:
            out = _run("wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@")
            m = re.search(r"([\d.]+)", out or "")
            if m:
                return round(float(m.group(1)) * 100), "MUTED" in (out or "")
        if self._pactl:
            out = _run("pactl", "get-sink-volume", "@DEFAULT_SINK@")
            m = re.search(r"(\d+)%", out or "")
            mute = _run("pactl", "get-sink-mute", "@DEFAULT_SINK@") or ""
            if m:
                return int(m.group(1)), "yes" in mute
        return None

    def get_volume(self):
        vol = self._read_volume()
        return vol[0] if vol else None

    def set_volume(self, percent):
        percent = max(0, min(100, int(percent)))
        if self._wpctl:
            _run("wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{percent / 100:.2f}")
        elif self._pactl:
            _run("pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{percent}%")

    def toggle_mute(self):
        if self._wpctl:
            _run("wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "toggle")
        elif self._pactl:
            _run("pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle")

    # ----------------------------------------------------------- micrófono
    def _mic_muted(self) -> Optional[bool]:
        if self._wpctl:
            out = _run("wpctl", "get-volume", "@DEFAULT_AUDIO_SOURCE@")
            if out is not None:
                return "MUTED" in out
        if self._pactl:
            out = _run("pactl", "get-source-mute", "@DEFAULT_SOURCE@")
            if out is not None:
                return "yes" in out
        return None

    def toggle_mic_mute(self) -> Optional[bool]:
        if self._wpctl:
            _run("wpctl", "set-mute", "@DEFAULT_AUDIO_SOURCE@", "toggle")
        elif self._pactl:
            _run("pactl", "set-source-mute", "@DEFAULT_SOURCE@", "toggle")
        return self._mic_muted()

    # ----------------------------------------------------------- volumen por app
    def _sink_inputs(self, player: str) -> list[dict]:
        if not self._pactl or not player:
            return []
        out = _run("pactl", "-f", "json", "list", "sink-inputs")
        try:
            items = json.loads(out or "[]")
        except ValueError:
            return []
        stems = process_stems(player) | {player.split(".")[0].lower()}
        found = []
        for item in items:
            props = item.get("properties", {})
            names = {str(props.get("application.process.binary", "")).lower(),
                     str(props.get("application.name", "")).lower()}
            if names & stems:
                found.append(item)
        return found

    def _app_volume(self, player: str):
        inputs = self._sink_inputs(player)
        if not inputs:
            return None
        item = inputs[0]
        channels = list((item.get("volume") or {}).values())
        try:
            pct = int(str(channels[0]["value_percent"]).rstrip("%")) if channels else None
        except (KeyError, ValueError, TypeError):
            pct = None
        if pct is None:
            return None
        return item, pct, bool(item.get("mute"))

    def set_app_volume(self, percent) -> bool:
        inputs = self._sink_inputs(self._player or "")
        for item in inputs:
            _run("pactl", "set-sink-input-volume", str(item["index"]), f"{max(0, min(100, int(percent)))}%")
        return bool(inputs)

    def toggle_app_mute(self) -> bool:
        inputs = self._sink_inputs(self._player or "")
        for item in inputs:
            _run("pactl", "set-sink-input-mute", str(item["index"]), "toggle")
        return bool(inputs)
