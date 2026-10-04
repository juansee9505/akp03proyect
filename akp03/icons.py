"""Iconos vectoriales dibujados con Pillow (no requieren archivos externos)."""

from __future__ import annotations

from functools import lru_cache

from PIL import Image, ImageDraw

_SS = 4  # supersampling para bordes suaves


def _poly(d: ImageDraw.ImageDraw, pts, s: float, fill):
    d.polygon([(x * s, y * s) for x, y in pts], fill=fill)


def _rect(d: ImageDraw.ImageDraw, x0, y0, x1, y1, s: float, fill, r: float = 0.0):
    d.rounded_rectangle((x0 * s, y0 * s, x1 * s, y1 * s), radius=r * s, fill=fill)


def _speaker(d, s, fill):
    _rect(d, 0.14, 0.38, 0.30, 0.62, s, fill, 0.02)
    _poly(d, [(0.28, 0.38), (0.48, 0.22), (0.48, 0.78), (0.28, 0.62)], s, fill)


def _draw(name: str, d: ImageDraw.ImageDraw, s: float, fill) -> None:
    if name == "play":
        _poly(d, [(0.30, 0.20), (0.80, 0.50), (0.30, 0.80)], s, fill)
    elif name == "pause":
        _rect(d, 0.27, 0.22, 0.43, 0.78, s, fill, 0.03)
        _rect(d, 0.57, 0.22, 0.73, 0.78, s, fill, 0.03)
    elif name == "next":
        _poly(d, [(0.14, 0.25), (0.44, 0.50), (0.14, 0.75)], s, fill)
        _poly(d, [(0.42, 0.25), (0.72, 0.50), (0.42, 0.75)], s, fill)
        _rect(d, 0.73, 0.25, 0.83, 0.75, s, fill, 0.02)
    elif name == "previous":
        _poly(d, [(0.86, 0.25), (0.56, 0.50), (0.86, 0.75)], s, fill)
        _poly(d, [(0.58, 0.25), (0.28, 0.50), (0.58, 0.75)], s, fill)
        _rect(d, 0.17, 0.25, 0.27, 0.75, s, fill, 0.02)
    elif name in ("volume", "volume_up", "volume_down"):
        _speaker(d, s, fill)
        w = max(1, int(0.06 * s))
        for r in (0.14, 0.26):
            d.arc(((0.50 - r) * s, (0.50 - r) * s, (0.50 + r) * s, (0.50 + r) * s),
                  -50, 50, fill=fill, width=w)
        if name != "volume":
            # signo +/- en la esquina superior derecha
            _rect(d, 0.64, 0.12, 0.92, 0.20, s, fill, 0.02)
            if name == "volume_up":
                _rect(d, 0.74, 0.02, 0.82, 0.30, s, fill, 0.02)
    elif name == "mute":
        _speaker(d, s, fill)
        w = max(1, int(0.07 * s))
        d.line((0.60 * s, 0.36 * s, 0.86 * s, 0.64 * s), fill=fill, width=w)
        d.line((0.60 * s, 0.64 * s, 0.86 * s, 0.36 * s), fill=fill, width=w)
    elif name == "music":
        d.ellipse((0.18 * s, 0.58 * s, 0.42 * s, 0.80 * s), fill=fill)
        d.ellipse((0.58 * s, 0.50 * s, 0.82 * s, 0.72 * s), fill=fill)
        _rect(d, 0.36, 0.22, 0.42, 0.70, s, fill)
        _rect(d, 0.76, 0.14, 0.82, 0.62, s, fill)
        _poly(d, [(0.36, 0.22), (0.82, 0.12), (0.82, 0.24), (0.36, 0.34)], s, fill)
    elif name == "plus":
        _rect(d, 0.44, 0.20, 0.56, 0.80, s, fill, 0.03)
        _rect(d, 0.20, 0.44, 0.80, 0.56, s, fill, 0.03)


@lru_cache(maxsize=128)
def icon(name: str, size: int, color: tuple[int, int, int, int] = (255, 255, 255, 255)) -> Image.Image:
    """Devuelve un icono RGBA de `size`x`size` píxeles."""
    big = size * _SS
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    _draw(name, ImageDraw.Draw(img), big, color)
    return img.resize((size, size), Image.LANCZOS)


ICON_FOR_TYPE = {
    "previous": "previous",
    "next": "next",
    "volume_up": "volume_up",
    "volume_down": "volume_down",
    "mute": "mute",
}
