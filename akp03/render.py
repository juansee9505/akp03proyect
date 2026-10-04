"""Generación de las imágenes de cada tecla."""

from __future__ import annotations

import io
import logging
import os
import sys
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps, ImageSequence

from .config import NUM_KEYS
from .icons import ICON_FOR_TYPE, icon
from .media.base import MediaState

log = logging.getLogger(__name__)

WHITE = (255, 255, 255, 255)
GREY = (175, 175, 185, 255)
RED = (235, 70, 70, 255)


def hex_color(value: str, default=(29, 185, 84)) -> tuple[int, int, int]:
    try:
        v = value.lstrip("#")
        return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except (AttributeError, ValueError, IndexError):
        return default


# --------------------------------------------------------------------- fuentes
_FONT_CANDIDATES = {
    True: ["segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf",
           "Arial Bold.ttf", "Helvetica.ttc"],
    False: ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf",
            "Arial.ttf", "Helvetica.ttc"],
}


@lru_cache(maxsize=64)
def font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    dirs = [""]
    if sys.platform == "win32":
        dirs.append(os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))
    dirs += ["/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/TTF",
             "/usr/share/fonts/dejavu", "/usr/share/fonts/truetype/liberation"]
    for name in _FONT_CANDIDATES[bold]:
        for d in dirs:
            try:
                return ImageFont.truetype(os.path.join(d, name) if d else name, size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def text_width(draw: ImageDraw.ImageDraw, text: str, fnt) -> float:
    return draw.textlength(text, font=fnt)


def fmt_time(seconds: Optional[float]) -> str:
    if seconds is None:
        return ""
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


@dataclass(frozen=True)
class Overlay:
    """Aviso temporal (volumen, micrófono, OBS…) que se dibuja sobre el panel."""

    icon: str
    text: str
    subtitle: str = ""
    frac: Optional[float] = None  # barra 0-1 (p. ej. volumen)
    alert: bool = False  # en rojo (silenciado, grabando…)


def fit_text(draw: ImageDraw.ImageDraw, text: str, max_width: int, size: int,
             min_size: int, bold: bool = True):
    """Fuente más grande que hace caber `text`; si no cabe, se recorta con «…»."""
    for sz in range(size, min_size - 1, -1):
        fnt = font(sz, bold)
        if text_width(draw, text, fnt) <= max_width:
            return fnt, text
    fnt = font(min_size, bold)
    while len(text) > 1 and text_width(draw, text + "…", fnt) > max_width:
        text = text[:-1]
    return fnt, text.rstrip() + "…"


def fit_lines(draw: ImageDraw.ImageDraw, text: str, max_width: int, size: int,
              min_size: int, bold: bool = True):
    """Como fit_text pero permite partir el texto en dos líneas."""
    single_min = max(min_size, int(size * 0.7))
    for sz in range(size, single_min - 1, -1):
        fnt = font(sz, bold)
        if text_width(draw, text, fnt) <= max_width:
            return fnt, [text]
    words = text.split()
    if len(words) > 1:
        for sz in range(single_min, min_size - 1, -1):
            fnt = font(sz, bold)
            best = None
            for i in range(1, len(words)):
                a, b = " ".join(words[:i]), " ".join(words[i:])
                wa, wb = text_width(draw, a, fnt), text_width(draw, b, fnt)
                if wa <= max_width and wb <= max_width:
                    score = max(wa, wb)
                    if best is None or score < best[0]:
                        best = (score, [a, b])
            if best:
                return fnt, best[1]
        # Dos líneas: la segunda se recorta si hace falta.
        fnt = font(min_size, bold)
        first = words[0]
        for i in range(len(words) - 1, 0, -1):
            if text_width(draw, " ".join(words[:i]), fnt) <= max_width:
                first = " ".join(words[:i])
                rest = " ".join(words[i:])
                break
        else:
            rest = " ".join(words[1:])
        _, first = fit_text(draw, first, max_width, min_size, min_size, bold)
        _, rest = fit_text(draw, rest, max_width, min_size, min_size, bold)
        return fnt, [first, rest]
    return fit_text(draw, text, max_width, min_size, min_size, bold)[0], \
        [fit_text(draw, text, max_width, min_size, min_size, bold)[1]]


# --------------------------------------------------------------------- imágenes propias
class AnimatedImage:
    """Imagen estática o animada (GIF/WebP/APNG) cargada en memoria."""

    MAX_FRAMES = 300

    def __init__(self, frames: list[Image.Image], durations: list[float]):
        self.frames = frames
        self.durations = durations
        self.total = sum(durations) or 1.0
        self._resized: dict[tuple[int, int], list[Image.Image]] = {}

    @property
    def animated(self) -> bool:
        return len(self.frames) > 1

    @classmethod
    def from_image(cls, img: Image.Image) -> "AnimatedImage":
        frames, durations = [], []
        for frame in ImageSequence.Iterator(img):
            frames.append(frame.convert("RGBA"))
            durations.append(max(0.02, (frame.info.get("duration") or img.info.get("duration") or 100) / 1000))
            if len(frames) >= cls.MAX_FRAMES:
                break
        return cls(frames, durations)

    @classmethod
    def from_bytes(cls, data: bytes) -> "AnimatedImage":
        return cls.from_image(Image.open(io.BytesIO(data)))

    def frame_index(self, t: float) -> int:
        if not self.animated:
            return 0
        t = t % self.total
        for i, d in enumerate(self.durations):
            if t < d:
                return i
            t -= d
        return len(self.frames) - 1

    def frame(self, t: float, size: tuple[int, int]) -> Image.Image:
        frames = self._resized.get(size)
        if frames is None:
            frames = [ImageOps.fit(f, size, Image.LANCZOS) for f in self.frames]
            if len(self._resized) > 8:
                self._resized.clear()
            self._resized[size] = frames
        return frames[self.frame_index(t)]


_media_cache: dict[str, tuple[float, Optional[AnimatedImage]]] = {}


def load_media(path: Optional[str]) -> Optional[AnimatedImage]:
    if not path:
        return None
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    cached = _media_cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        with Image.open(path) as img:
            media = AnimatedImage.from_image(img)
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo cargar la imagen %s: %s", path, exc)
        media = None
    _media_cache[path] = (mtime, media)
    return media


# --------------------------------------------------------------------- renderer
class Renderer:
    DEFAULT_KEY_SIZE = 60

    def __init__(self, config: dict):
        self._auto_size = self.DEFAULT_KEY_SIZE
        self.update_config(config)
        self._art_key: Optional[str] = None
        self._art: Optional[Image.Image] = None
        self._art_blur: dict[tuple[int, int], Image.Image] = {}
        self._track_key = ""
        self._track_since = time.monotonic()

    def update_config(self, config: dict) -> None:
        self.config = config
        self.size = int(config["device"]["key_size"] or 0) or self._auto_size
        disp = config["display"]
        self.theme = hex_color(disp["theme_color"])
        self.bg = hex_color(disp["background_color"], (16, 16, 20))
        self.scroll_speed = float(disp["scroll_speed"])
        self.dim_paused = bool(disp.get("dim_cover_when_paused", True))

    def set_auto_key_size(self, size: int) -> None:
        """Tamaño de tecla del modelo conectado (se usa si la config dice 0)."""
        self._auto_size = int(size)
        self.update_config(self.config)

    # ------------------------------------------------------------ utilidades
    def _blank(self, w: Optional[int] = None) -> Image.Image:
        return Image.new("RGBA", (w or self.size, self.size), self.bg + (255,))

    def _art_image(self, state: MediaState) -> Optional[Image.Image]:
        if state.art_key != self._art_key:
            self._art_key = state.art_key
            self._art_blur = {}
            self._art = None
            if state.art:
                try:
                    self._art = Image.open(io.BytesIO(state.art)).convert("RGBA")
                except Exception as exc:  # noqa: BLE001
                    log.debug("Carátula inválida: %s", exc)
        elif self._art is None and state.art:
            self._art_key = None
            return self._art_image(state)
        return self._art

    def _custom(self, key_cfg: dict, now: float, size: tuple[int, int]) -> Optional[Image.Image]:
        media = load_media(key_cfg.get("image"))
        return media.frame(now, size) if media else None

    @staticmethod
    def _darken(img: Image.Image, factor: float) -> Image.Image:
        rgb = ImageEnhance.Brightness(img.convert("RGB")).enhance(factor)
        return rgb.convert("RGBA")

    def _paste_icon(self, img: Image.Image, name: str, scale: float = 0.56,
                    color=WHITE, center: Optional[tuple[int, int]] = None) -> None:
        s = max(8, int(self.size * scale))
        ic = icon(name, s, color)
        cx, cy = center or (img.width // 2, img.height // 2)
        img.alpha_composite(ic, (cx - s // 2, cy - s // 2))

    def _marquee(self, img: Image.Image, text: str, fnt, x: int, y: int, width: int,
                 t: float, fill) -> None:
        if not text or width <= 0:
            return
        d = ImageDraw.Draw(img)
        tw = text_width(d, text, fnt)
        if tw <= width:
            d.text((x, y), text, font=fnt, fill=fill)
            return
        gap = max(20, self.size // 2)
        pause = 1.5
        cycle = pause + (tw + gap) / max(1.0, self.scroll_speed)
        phase = t % cycle
        offset = 0.0 if phase < pause else (phase - pause) * self.scroll_speed
        height = int(fnt.size * 1.4) if hasattr(fnt, "size") else 16
        layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        ld.text((-offset, 0), text, font=fnt, fill=fill)
        ld.text((-offset + tw + gap, 0), text, font=fnt, fill=fill)
        img.alpha_composite(layer, (x, y))

    @staticmethod
    def _translucent_rect(img: Image.Image, box, radius: float, fill) -> None:
        """Rectángulo semitransparente mezclado (ImageDraw no mezcla el alfa)."""
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(layer).rounded_rectangle(box, radius=radius, fill=fill)
        img.alpha_composite(layer)

    def _bar(self, img: Image.Image, x0: int, y0: int, x1: int, y1: int, frac: float, color) -> None:
        r = (y1 - y0) / 2
        self._translucent_rect(img, (x0, y0, x1, y1), r, (255, 255, 255, 60))
        d = ImageDraw.Draw(img)
        frac = max(0.0, min(1.0, frac))
        if frac > 0:
            d.rounded_rectangle((x0, y0, x0 + max(y1 - y0, int((x1 - x0) * frac)), y1),
                                radius=r, fill=color)

    # ------------------------------------------------------------ teclas
    def _render_cover(self, key: dict, state: MediaState, now: float) -> Image.Image:
        s = self.size
        art = self._art_image(state)
        if art is not None:
            img = ImageOps.fit(art, (s, s), Image.LANCZOS)
            if self.dim_paused and not state.playing:
                img = self._darken(img, 0.45)
                self._paste_icon(img, "pause", 0.45)
            return img
        custom = self._custom(key, now, (s, s))
        if custom is not None:
            return custom.copy()
        img = self._blank()
        self._paste_icon(img, "music", 0.6, self.theme + (255,))
        return img

    def _panel_background(self, key: dict, state: MediaState, now: float, w: int) -> Image.Image:
        s = self.size
        custom = self._custom(key, now, (w, s))
        if custom is not None:
            return self._darken(custom, 0.55)
        art = self._art_image(state)
        if art is not None:
            blur = self._art_blur.get((w, s))
            if blur is None:
                blur = ImageOps.fit(art, (w, s), Image.LANCZOS).filter(ImageFilter.GaussianBlur(max(2, s // 6)))
                blur = self._darken(blur, 0.40)
                self._art_blur[(w, s)] = blur
            return blur.copy()
        return self._blank(w)

    def _draw_overlay(self, img: Image.Image, n: int, ov: Overlay) -> None:
        s = self.size
        w = s * n
        d = ImageDraw.Draw(img)
        pad = max(3, s // 12)
        color = RED if ov.alert else WHITE
        bar_color = RED if ov.alert else self.theme + (255,)
        if n >= 2:
            # Icono (y subtítulo) en la primera tecla y el texto en las demás: el
            # hueco físico entre teclas no corta las letras.
            if ov.subtitle:
                f, sub = fit_text(d, ov.subtitle, s - 2 * pad, max(7, int(s * 0.16)), 6, False)
                d.text((s // 2, int(s * 0.13)), sub, font=f, fill=GREY, anchor="mm")
            cy = int(s * 0.47) if ov.subtitle else int(s * 0.40)
            self._paste_icon(img, ov.icon, 0.46, color, center=(s // 2, cy))
            f, lines = fit_lines(d, ov.text, w - s - 2 * pad, max(10, int(s * 0.30)), max(7, int(s * 0.16)))
            cx, cy = s + (w - s) // 2, int(s * 0.42)
            if len(lines) == 1:
                d.text((cx, cy), lines[0], font=f, fill=color, anchor="mm")
            else:
                gap = int(f.size * 0.6) if hasattr(f, "size") else 6
                d.text((cx, cy - gap), lines[0], font=f, fill=color, anchor="mm")
                d.text((cx, cy + gap), lines[1], font=f, fill=color, anchor="mm")
        else:
            self._paste_icon(img, ov.icon, 0.36, color, center=(s // 2, int(s * 0.26)))
            f, text = fit_text(d, ov.text, s - 2 * pad, max(9, int(s * 0.24)), max(6, int(s * 0.13)))
            d.text((s // 2, int(s * 0.62)), text, font=f, fill=color, anchor="mm")
        if ov.frac is not None:
            y = int(s * 0.82)
            self._bar(img, pad, y, w - pad, y + max(3, s // 15), ov.frac, bar_color)

    def _render_panel(self, key: dict, n: int, state: MediaState, now: float,
                      overlay: Optional[Overlay]) -> Image.Image:
        s = self.size
        w = s * n
        img = self._panel_background(key, state, now, w)
        d = ImageDraw.Draw(img)
        pad = max(3, s // 12)

        if overlay is not None:
            self._draw_overlay(img, n, overlay)
            return img

        if not state.has_media:
            f = font(max(9, int(s * 0.20)), True)
            d.text((w // 2, s // 2), "Sin música", font=f, fill=GREY, anchor="mm")
            return img

        t = now - self._track_since
        inner = w - 2 * pad
        self._marquee(img, state.title or "—", font(max(9, int(s * 0.24)), True),
                      pad, int(s * 0.05), inner, t, WHITE)
        self._marquee(img, state.artist, font(max(8, int(s * 0.18))),
                      pad, int(s * 0.37), inner, t, GREY)

        small = font(max(7, int(s * 0.14)))
        y_info = int(s * 0.64)
        status = {"playing": "▶ ", "paused": "❚❚ "}.get(state.status, "")
        app = state.app
        if status and not _font_has(small, status.strip()):
            status = ""
        d.text((pad, y_info), f"{status}{app}", font=small, fill=GREY)
        pos = state.position_at(now)
        if pos is not None and state.duration:
            label = fmt_time(pos)
            if w >= s * 2:
                label += " / " + fmt_time(state.duration)
            d.text((w - pad, y_info), label, font=small, fill=GREY, anchor="ra")
            bar_y = int(s * 0.88)
            self._bar(img, pad, bar_y, w - pad, bar_y + max(2, s // 20),
                      pos / state.duration, self.theme + (255,))
        return img

    def _render_button(self, key: dict, state: MediaState, now: float) -> Image.Image:
        s = self.size
        ktype = key["type"]
        custom = self._custom(key, now, (s, s))
        overlay = key.get("overlay", True)
        if custom is not None:
            img = self._darken(custom, 0.65) if overlay else custom.copy()
            if not overlay:
                return img
        else:
            img = self._blank()
            m = max(2, s // 15)
            self._translucent_rect(img, (m, m, s - m - 1, s - m - 1), s // 6, (255, 255, 255, 22))

        if ktype == "play_pause":
            if custom is None:
                d = ImageDraw.Draw(img)
                r = int(s * 0.36)
                c = s // 2
                d.ellipse((c - r, c - r, c + r, c + r), fill=self.theme + (255,))
            self._paste_icon(img, "pause" if state.playing else "play", 0.42,
                             (15, 15, 15, 255) if custom is None else WHITE)
        elif ktype == "mute":
            self._paste_icon(img, "mute", 0.6, RED if state.muted else WHITE)
        elif ktype == "mic":
            self._paste_icon(img, "mic_off" if state.mic_muted else "mic", 0.6,
                             RED if state.mic_muted else WHITE)
        else:
            self._paste_icon(img, ICON_FOR_TYPE.get(ktype, "music"), 0.56)
        return img

    def _render_volume(self, key: dict, state: MediaState, now: float) -> Image.Image:
        s = self.size
        custom = self._custom(key, now, (s, s))
        img = self._darken(custom, 0.5) if custom is not None else self._blank()
        d = ImageDraw.Draw(img)
        self._paste_icon(img, "mute" if state.muted else "volume", 0.36,
                         RED if state.muted else WHITE, center=(s // 2, int(s * 0.24)))
        label = "--" if state.volume is None else f"{state.volume}%"
        d.text((s // 2, int(s * 0.60)), label, font=font(max(9, int(s * 0.26)), True),
               fill=WHITE, anchor="mm")
        if state.volume is not None:
            m = max(3, s // 10)
            self._bar(img, m, int(s * 0.82), s - m, int(s * 0.82) + max(3, s // 15),
                      state.volume / 100, RED if state.muted else self.theme + (255,))
        return img

    def _render_clock(self, key: dict, now: float) -> Image.Image:
        s = self.size
        custom = self._custom(key, now, (s, s))
        img = self._darken(custom, 0.5) if custom is not None else self._blank()
        d = ImageDraw.Draw(img)
        lt = time.localtime()
        d.text((s // 2, int(s * 0.42)), time.strftime("%H:%M", lt),
               font=font(max(10, int(s * 0.30)), True), fill=WHITE, anchor="mm")
        d.text((s // 2, int(s * 0.76)), time.strftime("%d/%m", lt),
               font=font(max(7, int(s * 0.17))), fill=GREY, anchor="mm")
        return img

    def _render_image(self, key: dict, now: float) -> Image.Image:
        s = self.size
        custom = self._custom(key, now, (s, s))
        if custom is not None:
            return custom.copy()
        img = self._blank()
        self._paste_icon(img, "plus", 0.4, (90, 90, 100, 255))
        return img

    # ------------------------------------------------------------ API
    def render(self, state: MediaState, now: Optional[float] = None,
               overlay: Optional[Overlay] = None) -> list[Image.Image]:
        """Devuelve una imagen RGB por tecla (6)."""
        now = time.monotonic() if now is None else now
        if state.track_key != self._track_key:
            self._track_key = state.track_key
            self._track_since = now

        keys = self.config["keys"][:NUM_KEYS]
        out: list[Optional[Image.Image]] = [None] * NUM_KEYS

        # Agrupa las teclas "now_playing" consecutivas en paneles.
        i = 0
        while i < len(keys):
            if keys[i]["type"] == "now_playing":
                j = i
                while j + 1 < len(keys) and keys[j + 1]["type"] == "now_playing":
                    j += 1
                n = j - i + 1
                panel = self._render_panel(keys[i], n, state, now, overlay)
                for k in range(n):
                    out[i + k] = panel.crop((k * self.size, 0, (k + 1) * self.size, self.size))
                i = j + 1
                continue
            i += 1

        has_panel = any(k["type"] == "now_playing" for k in keys)
        for idx, key in enumerate(keys):
            if out[idx] is not None:
                continue
            ktype = key["type"]
            if ktype == "cover":
                if overlay is not None and not has_panel:
                    img = self._darken(self._render_cover(key, state, now), 0.35)
                    self._draw_overlay(img, 1, overlay)
                else:
                    img = self._render_cover(key, state, now)
            elif ktype in ("previous", "next", "play_pause", "mute", "mic", "volume_up", "volume_down"):
                img = self._render_button(key, state, now)
            elif ktype == "volume":
                img = self._render_volume(key, state, now)
            elif ktype == "clock":
                img = self._render_clock(key, now)
            elif ktype == "image":
                img = self._render_image(key, now)
            else:
                img = self._blank()
            out[idx] = img
        return [img.convert("RGB") for img in out]  # type: ignore[union-attr]

    def render_identify(self) -> list[Image.Image]:
        """Teclas numeradas 1..6 para comprobar orden y rotación."""
        s = self.size
        imgs = []
        for i in range(NUM_KEYS):
            img = Image.new("RGB", (s, s), (20, 20, 30))
            d = ImageDraw.Draw(img)
            d.rectangle((0, 0, s - 1, s // 6), fill=self.theme)  # barra = parte de arriba
            d.text((s // 2, s // 2 + s // 12), str(i + 1), font=font(int(s * 0.5), True),
                   fill=(255, 255, 255), anchor="mm")
            imgs.append(img)
        return imgs


@lru_cache(maxsize=32)
def _font_has(fnt, text: str) -> bool:
    """True si la fuente tiene glifos para `text` (evita los cuadros vacíos)."""
    try:
        return all(fnt.getmask(ch).getbbox() is not None for ch in text if not ch.isspace())
    except Exception:  # noqa: BLE001
        return False


def encode_key(img: Image.Image, rotation: int = 0, flip: bool = False, quality: int = 90) -> bytes:
    """Convierte la imagen de una tecla al JPEG que espera el dispositivo."""
    if rotation:
        img = img.rotate(-rotation, expand=True)  # rotación horaria
    if flip:
        img = ImageOps.mirror(img)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=int(quality), optimize=False, progressive=False)
    return buf.getvalue()
