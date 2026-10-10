"""Render the ChatGPT plugin logo (512x512 PNG) from the site's own favicon.svg design.

The SVG is a blue (#2563eb) rounded square with two overlapping "A"s in Inter 800, the
back one at 35% white. Pillow cannot rasterise SVG, so this redraws the same parameters,
instancing Inter 800 from the variable font the site already ships.

    venv/bin/python dev/make_plugin_logo.py

Writes dev/chatgpt-plugin/assets/logo.png; dev/chatgpt-plugin-devmode/ uses a copy of it.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
WOFF2 = ROOT / "fontmatch" / "static" / "fonts" / "inter-latin.woff2"
OUT = ROOT / "dev" / "chatgpt-plugin" / "assets" / "logo.png"
SIZE, FS = 512, 300
OFFSET_RATIO = 6.0 / 18.0  # favicon.svg: the A's sit at x=13 and x=19 at font-size 18
BLUE = "#2563eb"
BACK_ALPHA, FRONT_ALPHA = 89, 255  # 35% and 100% white


def _inter_800() -> ImageFont.FreeTypeFont:
    font = TTFont(WOFF2)
    if "fvar" in font:
        font = instantiateVariableFont(font, {"wght": 800})
    font.flavor = None
    with tempfile.NamedTemporaryFile(suffix=".ttf", delete=False) as tmp:
        font.save(tmp.name)
        path = tmp.name
    try:
        return ImageFont.truetype(path, FS)
    finally:
        Path(path).unlink(missing_ok=True)


def main() -> int:
    font = _inter_800()
    layer = Image.new("RGBA", (FS * 3, FS * 3), (0, 0, 0, 0))
    for dx, alpha in ((0, BACK_ALPHA), (round(FS * OFFSET_RATIO), FRONT_ALPHA)):
        glyph = Image.new("RGBA", layer.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(glyph)
        draw.text((FS + dx, FS), "A", font=font, fill=(255, 255, 255, alpha), anchor="ls")
        layer = Image.alpha_composite(layer, glyph)
    mark = layer.crop(layer.getbbox())

    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    radius = int(SIZE * 8 / 34)
    ImageDraw.Draw(img).rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], radius=radius, fill=BLUE)
    img.alpha_composite(mark, ((SIZE - mark.width) // 2, (SIZE - mark.height) // 2))
    img.save(OUT)
    print(f"wrote {OUT.relative_to(ROOT)} {img.size}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
