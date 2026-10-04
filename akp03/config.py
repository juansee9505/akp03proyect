"""Configuración persistente en JSON."""

from __future__ import annotations

import copy
import json
import logging
import os
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

NUM_KEYS = 6
NUM_BUTTONS = 3
NUM_KNOBS = 3

# Tipos de tecla con pantalla.
KEY_TYPES = [
    "cover",        # carátula del álbum / miniatura del video
    "now_playing",  # título + artista + progreso (las teclas contiguas se unen en un panel)
    "previous",
    "play_pause",
    "next",
    "volume",       # muestra el volumen general
    "volume_up",
    "volume_down",
    "mute",
    "mic",          # estado del micrófono (pulsar = silenciar)
    "clock",
    "image",        # imagen o animación GIF propia
    "none",
]

OBS_ACTIONS = [
    "obs_record", "obs_record_pause", "obs_stream", "obs_replay",
    "obs_virtualcam", "obs_scene", "obs_mute",
]

# Acciones que puede disparar una tecla, botón o pulsación de perilla.
ACTIONS = [
    "play_pause", "next", "previous",
    "app_mute",
    "volume_up", "volume_down", "mute",
    "mic_mute",
    "brightness_up", "brightness_down",
    *OBS_ACTIONS,
    "open", "none",
]

# Acciones al girar una perilla.
KNOB_TURN_ACTIONS = ["app_volume", "volume", "track", "seek", "brightness",
                     "obs_scene_cycle", "obs_volume", "none"]

# Acciones que usan el campo "target" (escena / fuente de OBS, programa…).
TARGET_ACTIONS = {"obs_scene", "obs_mute", "obs_volume", "open"}

# Acción por defecto al pulsar cada tipo de tecla.
DEFAULT_KEY_ACTION = {
    "cover": "play_pause",
    "now_playing": "play_pause",
    "previous": "previous",
    "play_pause": "play_pause",
    "next": "next",
    "volume": "mute",
    "volume_up": "volume_up",
    "volume_down": "volume_down",
    "mute": "mute",
    "mic": "mic_mute",
    "clock": "none",
    "image": "none",
    "none": "none",
}

# Valores por defecto de la versión 1 (para migrar configuraciones sin tocar
# las que el usuario ya personalizó).
_V1_KNOBS = [
    {"turn": "volume", "press": "mute"},
    {"turn": "track", "press": "play_pause"},
    {"turn": "brightness", "press": "none"},
]
_V1_BUTTONS = [
    {"action": "previous", "target": None},
    {"action": "play_pause", "target": None},
    {"action": "next", "target": None},
]

CONFIG_VERSION = 4

DEFAULT_CONFIG: dict[str, Any] = {
    "version": CONFIG_VERSION,
    "device": {
        # null = detección automática
        "vid": None,
        "pid": None,
        # 0 / null = automático según el modelo detectado (ver device.KNOWN_DEVICES).
        "packet_size": 0,
        "key_size": 0,
        # Rotación (grados, horario) y espejo aplicados a la imagen antes de enviarla.
        "rotation": None,
        "flip": False,
        "brightness": 80,
        # Número que el dispositivo usa para cada tecla con pantalla (tecla 1..6).
        "image_key_ids": [1, 2, 3, 4, 5, 6],
        "jpeg_quality": 90,
        # Sobrescribe el mapa de códigos de entrada, p. ej. {"0x25": "button1"}.
        "input_map": {},
    },
    "display": {
        "fps": 12,
        "theme_color": "#1DB954",
        "background_color": "#101014",
        "scroll_speed": 28,
        "volume_overlay_seconds": 1.5,
        "dim_cover_when_paused": True,
    },
    "keys": [
        {"type": "cover", "image": None, "overlay": True, "action": None, "target": None},
        {"type": "now_playing", "image": None, "overlay": True, "action": None, "target": None},
        {"type": "now_playing", "image": None, "overlay": True, "action": None, "target": None},
        {"type": "previous", "image": None, "overlay": True, "action": None, "target": None},
        {"type": "play_pause", "image": None, "overlay": True, "action": None, "target": None},
        {"type": "next", "image": None, "overlay": True, "action": None, "target": None},
    ],
    # Los 3 botones de abajo: OBS.
    "buttons": [
        {"action": "obs_record", "target": None},
        {"action": "obs_stream", "target": None},
        {"action": "obs_record_pause", "target": None},
    ],
    # Perilla 1: volumen de la app que suena (Spotify, YouTube en el navegador…).
    # Perilla 2: volumen general del PC; al presionar silencia el micrófono.
    # Perilla 3: escenas de OBS; al presionar inicia/detiene la grabación.
    "knobs": [
        {"turn": "app_volume", "press": "play_pause", "target": None},
        {"turn": "volume", "press": "mic_mute", "target": None},
        {"turn": "obs_scene_cycle", "press": "obs_record", "target": None},
    ],
    "volume_step": 2,
    "seek_step": 5,
    "obs_volume_step_db": 2,
    "obs": {
        "host": "localhost",
        "port": 4455,
        "password": "",
    },
    "media": {
        "backend": "auto",
        # Segundos entre consultas de «qué suena». El progreso se interpola entre
        # consultas y los botones refrescan al instante, así que 1 s basta.
        "poll_interval": 1.0,
        "prefer_playing": True,
    },
    "app": {
        "start_minimized": False,
        "minimize_to_tray": True,
    },
}


def config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        return base / "AKP03Controller"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "AKP03Controller"
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "akp03controller"


def default_config_path() -> Path:
    return config_dir() / "config.json"


def _merge(defaults: Any, data: Any) -> Any:
    """Mezcla `data` sobre `defaults` conservando las claves que falten."""
    if isinstance(defaults, dict) and isinstance(data, dict):
        out = copy.deepcopy(defaults)
        for k, v in data.items():
            out[k] = _merge(defaults[k], v) if k in defaults else v
        return out
    if isinstance(defaults, list) and isinstance(data, list):
        # Listas de dicts (keys/buttons/knobs): se mezcla elemento a elemento y
        # se mantiene la longitud de los valores por defecto.
        if defaults and all(isinstance(d, dict) for d in defaults):
            out = []
            for i, d in enumerate(defaults):
                out.append(_merge(d, data[i]) if i < len(data) else copy.deepcopy(d))
            return out
        return copy.deepcopy(data)
    if data is None or defaults is None or type(data) is type(defaults):
        return copy.deepcopy(data)
    if isinstance(defaults, (int, float)) and isinstance(data, (int, float)) and not isinstance(data, bool):
        return data
    log.warning("Valor de configuración ignorado (tipo inválido): %r", data)
    return copy.deepcopy(defaults)


def _migrate(data: dict) -> dict:
    data = copy.deepcopy(data)
    if int(data.get("version", 1) or 1) < 2:
        # v2: perillas y botones nuevos, salvo que el usuario los hubiera cambiado.
        knobs = [{k: v for k, v in kn.items() if k in ("turn", "press")} for kn in data.get("knobs", [])]
        if not knobs or knobs == _V1_KNOBS[:len(knobs)]:
            data.pop("knobs", None)
        buttons = [{"action": b.get("action"), "target": b.get("target")} for b in data.get("buttons", [])]
        if not buttons or buttons == _V1_BUTTONS[:len(buttons)]:
            data.pop("buttons", None)
    if int(data.get("version", 1) or 1) < 3:
        # v3: tamaño de paquete, de tecla y rotación automáticos por modelo. Sólo se
        # conservan valores que el usuario cambió respecto a los antiguos por defecto.
        dev = data.get("device")
        if isinstance(dev, dict):
            if dev.get("packet_size") == 512:
                dev["packet_size"] = 0
            if dev.get("key_size") == 60:
                dev["key_size"] = 0
            if dev.get("rotation") == 0:
                dev["rotation"] = None
    if int(data.get("version", 1) or 1) < 4:
        media = data.get("media")
        if isinstance(media, dict) and media.get("poll_interval") == 0.5:
            media["poll_interval"] = 1.0
    data["version"] = CONFIG_VERSION
    return data


def normalize(cfg: dict) -> dict:
    """Completa y valida una configuración."""
    cfg = _merge(DEFAULT_CONFIG, _migrate(cfg or {}))
    for key in cfg["keys"]:
        if key.get("type") not in KEY_TYPES:
            key["type"] = "none"
        if key.get("action") not in ACTIONS:
            key["action"] = None
    for btn in cfg["buttons"]:
        if btn.get("action") not in ACTIONS:
            btn["action"] = "none"
    for knob in cfg["knobs"]:
        if knob.get("turn") not in KNOB_TURN_ACTIONS:
            knob["turn"] = "none"
        if knob.get("press") not in ACTIONS:
            knob["press"] = "none"
    dev = cfg["device"]
    dev["brightness"] = max(0, min(100, int(dev["brightness"])))
    if dev["rotation"] is not None:
        dev["rotation"] = int(dev["rotation"]) % 360 // 90 * 90
    dev["packet_size"] = int(dev["packet_size"] or 0)
    dev["key_size"] = int(dev["key_size"] or 0)
    if len(dev["image_key_ids"]) != NUM_KEYS:
        dev["image_key_ids"] = list(DEFAULT_CONFIG["device"]["image_key_ids"])
    cfg["display"]["fps"] = max(1, min(30, int(cfg["display"]["fps"])))
    return cfg


def load_config(path: Path | None = None) -> dict:
    path = Path(path or default_config_path())
    if path.exists():
        try:
            with open(path, encoding="utf-8") as fh:
                return normalize(json.load(fh))
        except (OSError, ValueError) as exc:
            log.error("No se pudo leer %s (%s); se usan valores por defecto", path, exc)
    return normalize({})


def save_config(cfg: dict, path: Path | None = None) -> None:
    path = Path(path or default_config_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def import_media_file(src: str | Path, base_dir: Path | None = None) -> str:
    """Copia una imagen/GIF elegida por el usuario a la carpeta de la app.

    Así la configuración sigue funcionando aunque el archivo original se mueva.
    """
    src = Path(src)
    dest_dir = (base_dir or config_dir()) / "media"
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        if src.resolve().parent == dest_dir.resolve():
            return str(src)
    except OSError:
        pass
    dest = dest_dir / f"{src.stem[:40]}-{uuid.uuid4().hex[:8]}{src.suffix.lower()}"
    shutil.copyfile(src, dest)
    return str(dest)
