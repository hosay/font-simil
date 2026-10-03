"""Synthetic text images with known fonts, for evaluation and tests.

Degradation tiers approximate what users will actually upload:

- ``clean``: black text on white, rendered by FreeType (diagnostic only;
  same renderer as the index, so it flatters the matcher)
- ``screenshot``: random colours/polarity, resampling, JPEG compression
- ``photo``: screenshot + small rotation/perspective, blur, noise and
  uneven lighting
"""

from __future__ import annotations

import io
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from fontmatch.features.perceptual import pil_font
from fontmatch.fonts import load
from fontmatch.fonts.loader import LoadedFont

TIERS = ("clean", "screenshot", "photo")

Color = tuple[int, int, int]


def render_text_image(
    font: Path | str | LoadedFont,
    text: str,
    size_px: int = 48,
    fg: Color = (0, 0, 0),
    bg: Color = (255, 255, 255),
) -> Image.Image:
    """Render one line of text with generous padding."""
    loaded = font if isinstance(font, LoadedFont) else load(font)
    face = pil_font(loaded, size_px)
    left, top, right, bottom = face.getbbox(text)
    pad = max(8, size_px // 2)
    img = Image.new("RGB", (right - left + 2 * pad, bottom - top + 2 * pad), bg)
    ImageDraw.Draw(img).text((pad - left, pad - top), text, fill=fg, font=face)
    return img


def _random_colors(rng: random.Random) -> tuple[Color, Color]:
    """A readable fg/bg pair; ~1/3 of the time light text on dark."""
    dark = tuple(rng.randint(0, 70) for _ in range(3))
    light = tuple(rng.randint(185, 255) for _ in range(3))
    return (light, dark) if rng.random() < 0.33 else (dark, light)


def _recolor(img: Image.Image, fg: Color, bg: Color) -> Image.Image:
    """Map a black-on-white render to fg-on-bg, keeping anti-aliasing."""
    alpha = 1.0 - np.asarray(img.convert("L"), dtype=np.float32) / 255.0
    fg_a = np.array(fg, dtype=np.float32)
    bg_a = np.array(bg, dtype=np.float32)
    out = bg_a + alpha[..., None] * (fg_a - bg_a)
    return Image.fromarray(out.clip(0, 255).astype(np.uint8), "RGB")


def _jpeg(img: Image.Image, quality: int) -> Image.Image:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    buf.seek(0)
    return Image.open(buf).convert("RGB")


def _resample(img: Image.Image, scale: float) -> Image.Image:
    w, h = img.size
    small = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.BILINEAR)
    return small


def degrade(img: Image.Image, tier: str, rng: random.Random) -> Image.Image:
    if tier == "clean":
        return img.copy()
    if tier not in TIERS:
        raise ValueError(f"unknown tier {tier!r}")

    fg, bg = _random_colors(rng)
    out = _recolor(img, fg, bg)
    out = _resample(out, rng.uniform(0.55, 1.0))

    if tier == "photo":
        out = out.rotate(
            rng.uniform(-4, 4), resample=Image.Resampling.BICUBIC, expand=True, fillcolor=bg
        )
        w, h = out.size
        dx, dy = w * 0.03, h * 0.06
        quad = (
            rng.uniform(0, dx), rng.uniform(0, dy),
            rng.uniform(0, dx), h - rng.uniform(0, dy),
            w - rng.uniform(0, dx), h - rng.uniform(0, dy),
            w - rng.uniform(0, dx), rng.uniform(0, dy),
        )  # fmt: skip
        out = out.transform(
            (w, h), Image.Transform.QUAD, quad, Image.Resampling.BICUBIC, fillcolor=bg
        )
        out = out.filter(ImageFilter.GaussianBlur(rng.uniform(0.4, 1.1)))
        arr = np.asarray(out, dtype=np.float32)
        gradient = np.linspace(
            rng.uniform(0.8, 1.0), rng.uniform(1.0, 1.15), arr.shape[1], dtype=np.float32
        )
        arr = arr * gradient[None, :, None]
        np_rng = np.random.default_rng(rng.randint(0, 2**31))
        arr = arr + np_rng.normal(0, 6, arr.shape)
        out = Image.fromarray(arr.clip(0, 255).astype(np.uint8), "RGB")
        return _jpeg(out, rng.randint(65, 85))

    return _jpeg(out, rng.randint(60, 90))
