"""Social preview cards (og:image, 1200x630) for /similar-to pages.

The card shows what the page is about set in the recommended free font
itself, so a shared link previews the font. Rendered on first request and
cached on disk; the cache key covers everything drawn on the card.
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from fontmatch.fonts.variable import apply_instance, choose_instance
from fontmatch.samples import SAMPLE_TEXT

WIDTH, HEIGHT = 1200, 630
MARGIN = 72
OG_VERSION = 1  # bump when the layout changes
PAPER = (255, 255, 255)
INK = (26, 26, 26)
MUTED = (107, 114, 128)
ACCENT = (37, 99, 235)


def _face(path: Path, size: int, style: str = "") -> ImageFont.FreeTypeFont:
    from fontTools.ttLib import TTFont

    face = ImageFont.truetype(str(path), size)
    with TTFont(str(path), lazy=True) as tt:
        if "fvar" in tt:
            chosen = choose_instance(tt, ([style] if style else []) + ["Regular"])
            if chosen:
                apply_instance(face, tt, chosen[1])
    return face


def _fit(path: Path, text: str, size: int, style: str, max_width: int, min_size: int = 24):
    """Largest face <= size at which ``text`` fits in max_width."""
    while True:
        face = _face(path, size, style)
        if face.getlength(text) <= max_width or size <= min_size:
            return face
        size = max(min_size, int(size * max_width / face.getlength(text)) - 1)


def render_card(font_path: Path, style: str, kicker: str, title: str) -> bytes:
    """PNG bytes: ``kicker`` (small), ``title`` (large) and a pangram, all
    set in the font, plus the site name."""
    img = Image.new("RGB", (WIDTH, HEIGHT), PAPER)
    draw = ImageDraw.Draw(img)
    width = WIDTH - 2 * MARGIN
    draw.rectangle((0, 0, WIDTH, 12), fill=ACCENT)
    kicker_face = _fit(font_path, kicker, 44, style, width)
    title_face = _fit(font_path, title, 132, style, width)
    pangram_face = _fit(font_path, SAMPLE_TEXT, 52, style, width)
    site_face = _face(font_path, 34, style)
    draw.text((MARGIN, 96), kicker, font=kicker_face, fill=MUTED, anchor="ls")
    draw.text((MARGIN, 300), title, font=title_face, fill=INK, anchor="ls")
    draw.text((MARGIN, 420), SAMPLE_TEXT, font=pangram_face, fill=INK, anchor="ls")
    draw.text((MARGIN, HEIGHT - 60), "dupefont.com", font=site_face, fill=ACCENT, anchor="ls")
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


def card_filename(*parts: str) -> str:
    digest = hashlib.sha1("\0".join((str(OG_VERSION),) + parts).encode("utf-8", "replace"))
    return digest.hexdigest()[:24] + ".png"
