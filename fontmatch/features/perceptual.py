"""Glyph rendering and perceptual feature extraction.

v4: Multi-glyph rendering + CLIP (OpenCLIP ViT-B/32) embeddings.
Each font is rendered as individual diagnostic glyphs, composed into a
glyph sheet, then encoded via CLIP's vision encoder into a 512-dim vector.
"""

from __future__ import annotations

import io
import math

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from fontmatch.fonts.loader import LoadedFont

# Fixed rendering parameters for determinism
RENDER_DPI = 72
FONT_SIZE = 80  # points — large enough for CLIP to see stroke detail
GLYPH_SIZE = 128  # each glyph rendered on a 128x128 canvas

# Diagnostic characters chosen for their typographic distinctiveness:
# - lowercase: single/double story a/g, bowl shapes, ascenders/descenders
# - uppercase: stroke contrast, curves, diagonals
# - digits/symbols: proportions, style consistency
DIAGNOSTIC_CHARS = list("aegnosfilbdpqRSHOQBWM01589&@")

# Grid layout for the glyph sheet
_GRID_COLS = 7
_GRID_ROWS = math.ceil(len(DIAGNOSTIC_CHARS) / _GRID_COLS)
SHEET_WIDTH = _GRID_COLS * GLYPH_SIZE   # 896
SHEET_HEIGHT = _GRID_ROWS * GLYPH_SIZE  # 512

# Schema versions for cache invalidation
# v4 lost the outer glyph columns to CLIP's center crop. v5 padded the whole
# sheet to a square (all glyphs, but at ~half the resolution; measured no
# better than v4). v6 embeds square tiles at full glyph resolution and
# averages them. See docs/image-matching.md.
RENDERER_VERSION = "pillow-freetype-clip-v6-tiles"
FINGERPRINT_SCHEMA_VERSION = 6

# CLIP embedding dimension
CLIP_DIM = 512

# Legacy rendering parameters (kept for backward compat)
CANVAS_WIDTH = 1400
CANVAS_HEIGHT = 120
REFERENCE_STRING = "The quick brown fox jumps over the lazy dog."
DOWNSAMPLE_W = 100
DOWNSAMPLE_H = 15


def _font_to_bytes(font: LoadedFont) -> bytes:
    """Serialize a LoadedFont's TTFont to in-memory TTF bytes for Pillow."""
    buf = io.BytesIO()
    font.tt.save(buf)
    buf.seek(0)
    return buf.read()


def pil_font(font: LoadedFont, size: int) -> ImageFont.FreeTypeFont:
    """Pillow font for rendering. Variable fonts get their Regular (or Italic)
    named instance: the default instance is often Thin or Light."""
    from fontmatch.fonts.variable import apply_instance, choose_instance

    pil = ImageFont.truetype(io.BytesIO(_font_to_bytes(font)), size=size)
    if "fvar" in font.tt:
        is_italic = "italic" in font.subfamily.lower() or "italic" in font.postscript_name.lower()
        wanted = ["Italic", "Regular Italic"] if is_italic else ["Regular"]
        chosen = choose_instance(font.tt, wanted)
        if chosen is not None:
            apply_instance(pil, font.tt, chosen[1])
    return pil


def render_glyphs(font: LoadedFont) -> dict[str, np.ndarray]:
    """Render each diagnostic character as an individual grayscale image.

    Returns a dict mapping character to a GLYPH_SIZE x GLYPH_SIZE uint8 array.
    Characters missing from the font's cmap are skipped.
    """
    face = pil_font(font, FONT_SIZE)

    # Check which chars the font supports via its cmap
    cmap = font.tt.getBestCmap() or {}

    glyphs = {}
    for char in DIAGNOSTIC_CHARS:
        if ord(char) not in cmap:
            continue

        img = Image.new("L", (GLYPH_SIZE, GLYPH_SIZE), color=0)
        draw = ImageDraw.Draw(img)

        # Get bounding box for centering
        bbox = draw.textbbox((0, 0), char, font=face)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        # Center the glyph on the canvas
        x_offset = max(0, (GLYPH_SIZE - text_w) // 2 - bbox[0])
        y_offset = max(0, (GLYPH_SIZE - text_h) // 2 - bbox[1])

        draw.text((x_offset, y_offset), char, fill=255, font=face)
        glyphs[char] = np.array(img, dtype=np.uint8)

    return glyphs


def compose_sheet(glyphs: dict[str, np.ndarray]) -> np.ndarray:
    """Compose individual glyph images into a fixed-size grid sheet.

    The grid always uses the canonical DIAGNOSTIC_CHARS order. Missing glyphs
    leave their cell black (zeros). This ensures deterministic layout
    regardless of which glyphs a font supports.
    """
    sheet = np.zeros((SHEET_HEIGHT, SHEET_WIDTH), dtype=np.uint8)

    for idx, char in enumerate(DIAGNOSTIC_CHARS):
        if char not in glyphs:
            continue
        row = idx // _GRID_COLS
        col = idx % _GRID_COLS
        y = row * GLYPH_SIZE
        x = col * GLYPH_SIZE
        sheet[y : y + GLYPH_SIZE, x : x + GLYPH_SIZE] = glyphs[char]

    return sheet


def pad_to_square(img: np.ndarray) -> np.ndarray:
    """Center a 2D image on a black square canvas (side = longest edge).

    CLIP's preprocess resizes the short side to 224 and then center-crops,
    so a non-square input loses its edges. Padding first keeps all content.
    """
    h, w = img.shape
    side = max(h, w)
    out = np.zeros((side, side), dtype=img.dtype)
    y = (side - h) // 2
    x = (side - w) // 2
    out[y : y + h, x : x + w] = img
    return out


def square_tiles(img: np.ndarray) -> list[np.ndarray]:
    """Split a wide image into square tiles of side = height (last one padded
    with black); a square or tall image becomes one padded square."""
    h, w = img.shape
    if w <= h:
        return [pad_to_square(img)]
    tiles = []
    for x in range(0, w, h):
        tile = np.zeros((h, h), dtype=img.dtype)
        part = img[:, x : x + h]
        tile[:, : part.shape[1]] = part
        tiles.append(tile)
    return tiles


# --- CLIP encoder (lazy-loaded singleton, thread-safe) ---

import threading

_clip_lock = threading.Lock()
_clip_model = None
_clip_preprocess = None


def _get_clip():
    """Lazy-load the CLIP model on first use (thread-safe)."""
    global _clip_model, _clip_preprocess
    if _clip_model is not None:
        return _clip_model, _clip_preprocess
    with _clip_lock:
        # Double-check after acquiring lock
        if _clip_model is not None:
            return _clip_model, _clip_preprocess
        import open_clip
        import torch

        model, _, preprocess = open_clip.create_model_and_transforms(
            "ViT-B-32", pretrained="laion2b_s34b_b79k"
        )
        model.eval()
        # Force CPU — no GPU dependency
        model = model.to("cpu")
        _clip_model = model
        _clip_preprocess = preprocess
    return _clip_model, _clip_preprocess


def perceptual(rendered: np.ndarray) -> np.ndarray:
    """Extract a 512-dim CLIP embedding from a rendered glyph sheet.

    The sheet (grayscale uint8 ndarray) is converted to RGB, preprocessed
    for CLIP, and encoded via the vision encoder. The output is a unit-
    normalized float64 vector suitable for cosine distance comparison.

    Returns a zero vector if the sheet is blank (rendering failure),
    which is filtered out by store.build_index().
    """
    import torch

    # Guard against blank sheets (no glyphs rendered)
    if rendered.max() == 0:
        return np.zeros(CLIP_DIM, dtype=np.float64)

    model, preprocess = _get_clip()

    # Square tiles so CLIP's center crop discards nothing; one batched pass.
    batch = torch.stack(
        [preprocess(Image.fromarray(tile).convert("RGB")) for tile in square_tiles(rendered)]
    )

    with torch.no_grad():
        features = model.encode_image(batch)
        features = features / features.norm(dim=-1, keepdim=True)
        pooled = features.mean(dim=0)
        # L2-normalize (safe: CLIP never produces zero vectors for real images)
        pooled = pooled / pooled.norm()

    return pooled.cpu().numpy().astype(np.float64)


# --- Legacy render function (backward compatibility) ---

def render(font: LoadedFont) -> np.ndarray:
    """Render the reference string to a fixed grayscale canvas.

    LEGACY — kept for backward compatibility. New code should use
    render_glyphs() + compose_sheet() + perceptual().

    Returns a 2D uint8 numpy array (grayscale image).
    """
    font_bytes = _font_to_bytes(font)
    pil_font = ImageFont.truetype(io.BytesIO(font_bytes), size=64)

    img = Image.new("L", (CANVAS_WIDTH, CANVAS_HEIGHT), color=0)
    draw = ImageDraw.Draw(img)

    bbox = draw.textbbox((0, 0), REFERENCE_STRING, font=pil_font)
    text_h = bbox[3] - bbox[1]
    y_offset = max(0, (CANVAS_HEIGHT - text_h) // 2 - bbox[1])

    draw.text((10, y_offset), REFERENCE_STRING, fill=255, font=pil_font)

    return np.array(img, dtype=np.uint8)
