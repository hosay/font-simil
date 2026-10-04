"""Pregenerated font sample images ("The quick brown fox ...").

Shown next to matches in the ChatGPT widget and on the website. Built in bulk
by ``scripts/build_font_samples.py``; a missing sample is rendered on first
request and saved, so the bulk build is an optimisation, not a requirement.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote

from PIL import Image, ImageDraw, ImageFont

from fontmatch.fonts.variable import apply_instance, choose_instance

logger = logging.getLogger(__name__)

SAMPLE_TEXT = "The quick brown fox jumps over the lazy dog"
SAMPLE_PX = 56  # font size; the PNG is shown at about half size (retina)
PAD = 16
INK = (26, 26, 26)
PAPER = (255, 255, 255)
MAX_NAME = 200
MAX_MISSING = 2


def sample_filename(name: str, style: str = "") -> str:
    digest = hashlib.sha1(f"{name}\0{style or ''}".encode("utf-8", "replace")).hexdigest()
    return digest[:24] + ".png"


def sample_url(name: str, style: str = "") -> str:
    url = "/font-sample/" + quote(name, safe="") + ".png"
    return url + ("?style=" + quote(style, safe="") if style else "")


def canonical_style(path: Path, style: str = "") -> str:
    """The named instance a sample of ``style`` is rendered with ("" = the file
    as-is). Many requested styles map to one sample, which bounds the cache:
    static fonts have one sample; variable fonts one per named instance."""
    from fontTools.ttLib import TTFont

    with TTFont(str(path), lazy=True) as tt:
        if "fvar" not in tt:
            return ""
        italic = "italic" in path.name.lower() or "italic" in (style or "").lower()
        fallback = ["Italic", "Regular Italic"] if italic else ["Regular"]
        chosen = choose_instance(tt, ([style] if style else []) + fallback)
        return chosen[0] if chosen else ""


def render_sample(path: Path, style: str = "") -> bytes:
    """PNG bytes of SAMPLE_TEXT set in the font, cropped to the ink + padding."""
    from fontTools.ttLib import TTFont

    instance = canonical_style(path, style)
    with TTFont(str(path), lazy=True) as tt:
        cmap = tt.getBestCmap() or {}
        missing = {c for c in SAMPLE_TEXT if c != " " and ord(c) not in cmap}
        if len(missing) > MAX_MISSING:
            # Non-Latin or symbol font: a row of tofu boxes is worse than no sample.
            raise ValueError(f"{path.name} lacks {len(missing)} sample characters")
        face = ImageFont.truetype(str(path), SAMPLE_PX)
        if instance:
            apply_instance(face, tt, choose_instance(tt, [instance])[1])
    left, top, right, bottom = face.getbbox(SAMPLE_TEXT, anchor="ls")
    width = int(right - left) + 2 * PAD
    ascent, descent = face.getmetrics()
    height = max(int(ascent + descent), int(bottom - top)) + 2 * PAD
    img = Image.new("RGB", (max(width, 1), max(height, 1)), PAPER)
    baseline = PAD + max(ascent, -int(top))
    ImageDraw.Draw(img).text((PAD - left, baseline), SAMPLE_TEXT, font=face, fill=INK, anchor="ls")
    bbox = Image.eval(img.convert("L"), lambda p: 255 - p).getbbox()
    if bbox is None:
        raise ValueError(f"font renders no ink: {path.name}")
    l, t, r, b = bbox
    img = img.crop((max(l - PAD, 0), max(t - PAD, 0), min(r + PAD, img.width), min(b + PAD, img.height)))
    out = io.BytesIO()
    img.convert("L").save(out, "PNG", optimize=True)
    return out.getvalue()


class SampleStore:
    """Sample PNGs on disk, rendered on a miss. ``resolve(name)`` maps a corpus
    font name to its file (or None for fonts we don't have)."""

    def __init__(self, directory: Path, resolve: Callable[[str], Path | None]):
        self.directory = Path(directory)
        self.resolve = resolve

    def path_for(self, name: str, style: str = "") -> Path:
        return self.directory / sample_filename(name, style)

    def get(self, name: str, style: str = "") -> Path | None:
        if not name or len(name) > MAX_NAME or "\0" in name:
            return None
        requested = self.path_for(name, style)
        if requested.is_file():
            return requested
        if self._failed(requested):
            return None
        font_path = self.resolve(name)
        if font_path is None:
            return None
        target = requested
        try:
            canonical = canonical_style(font_path, style)
            target = self.path_for(name, canonical)
            if target.is_file():
                return target
            if self._failed(target):
                return None
            data = render_sample(font_path, canonical)
            self.save(target, data)
        except Exception as exc:  # broken or glyph-less font, or disk trouble
            logger.warning("sample: cannot render %s (%s): %s", name, style, exc)
            self._mark_failed(target)
            return None
        return target

    # Unrenderable fonts get an empty marker so repeat requests stay cheap.
    @staticmethod
    def _marker(target: Path) -> Path:
        return target.with_suffix(".none")

    def _failed(self, target: Path) -> bool:
        return self._marker(target).exists()

    def _mark_failed(self, target: Path) -> None:
        try:
            self._marker(target).parent.mkdir(parents=True, exist_ok=True)
            self._marker(target).touch()
        except OSError:
            pass

    def save(self, target: Path, data: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.chmod(tmp, 0o644)
            os.replace(tmp, target)  # atomic: concurrent requests never see half a file
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
