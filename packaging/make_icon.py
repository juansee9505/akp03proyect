"""Genera packaging/icon.ico para el ejecutable."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw  # noqa: E402

from akp03.icons import icon  # noqa: E402

size = 256
img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
ImageDraw.Draw(img).rounded_rectangle((0, 0, size - 1, size - 1), radius=56, fill=(29, 185, 84, 255))
img.alpha_composite(icon("music", 176, (15, 15, 15, 255)), (40, 40))
out = Path(__file__).with_name("icon.ico")
img.save(out, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print(out)
