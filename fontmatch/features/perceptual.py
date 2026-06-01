"""Glyph rendering and perceptual feature extraction."""

from __future__ import annotations

import io

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from fontmatch.fonts.loader import LoadedFont

# Fixed rendering parameters for determinism
RENDER_DPI = 72
FONT_SIZE = 64  # points
CANVAS_WIDTH = 800
CANVAS_HEIGHT = 120
REFERENCE_STRING = "Hamburgefonstiv 0123 ,.?!"

# Perceptual vector: downsampled to this size
DOWNSAMPLE_W = 100
DOWNSAMPLE_H = 15

# Schema versions for cache invalidation
RENDERER_VERSION = "pillow-freetype-v1"
FINGERPRINT_SCHEMA_VERSION = 2


def _font_to_bytes(font: LoadedFont) -> bytes:
    """Serialize a LoadedFont's TTFont to in-memory TTF bytes for Pillow."""
    buf = io.BytesIO()
    font.tt.save(buf)
    buf.seek(0)
    return buf.read()


def render(font: LoadedFont) -> np.ndarray:
    """Render the reference string to a fixed grayscale canvas.

    Returns a 2D uint8 numpy array (grayscale image).
    """
    font_bytes = _font_to_bytes(font)
    pil_font = ImageFont.truetype(io.BytesIO(font_bytes), size=FONT_SIZE)

    # Create grayscale canvas
    img = Image.new("L", (CANVAS_WIDTH, CANVAS_HEIGHT), color=0)
    draw = ImageDraw.Draw(img)

    # Get text bounding box for vertical centering
    bbox = draw.textbbox((0, 0), REFERENCE_STRING, font=pil_font)
    text_h = bbox[3] - bbox[1]
    y_offset = max(0, (CANVAS_HEIGHT - text_h) // 2 - bbox[1])

    draw.text((10, y_offset), REFERENCE_STRING, fill=255, font=pil_font)

    return np.array(img, dtype=np.uint8)


def perceptual(rendered: np.ndarray) -> np.ndarray:
    """Extract a fixed-length perceptual feature vector from a rendered image.

    Uses downsampled pixel intensities as features. This is behind an interface
    so a learned embedding can replace it later.
    """
    # Downsample to fixed size
    img = Image.fromarray(rendered)
    img_resized = img.resize((DOWNSAMPLE_W, DOWNSAMPLE_H), Image.Resampling.LANCZOS)
    pixels = np.array(img_resized, dtype=np.float64).flatten()

    # Normalize to [0, 1]
    max_val = pixels.max()
    if max_val > 0:
        pixels = pixels / max_val

    # Compute horizontal projection profile (row sums)
    img_arr = np.array(img_resized, dtype=np.float64)
    if img_arr.max() > 0:
        img_arr = img_arr / img_arr.max()
    h_profile = img_arr.mean(axis=1)  # shape: (DOWNSAMPLE_H,)

    # Compute vertical projection profile (column sums)
    v_profile = img_arr.mean(axis=0)  # shape: (DOWNSAMPLE_W,)

    # Combine: downsampled pixels + projection profiles
    vec = np.concatenate([pixels, h_profile, v_profile])
    return vec.astype(np.float64)
