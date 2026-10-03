"""Safe image decoding and text/background separation."""

from __future__ import annotations

import io
import warnings

import numpy as np
from PIL import Image, ImageOps

ALLOWED_FORMATS = ("PNG", "JPEG", "WEBP")
MAX_PIXELS = 40_000_000  # ~40 MP; larger is almost certainly a bomb or a raw photo
MAX_EDGE = 2000


class ImageError(Exception):
    """The upload isn't a usable image. Message is user-presentable."""


def load_image(data: bytes) -> Image.Image:
    """Decode PNG/JPEG/WebP bytes into an upright RGB image, long edge <= MAX_EDGE."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            img = Image.open(io.BytesIO(data), formats=ALLOWED_FORMATS)
            w, h = img.size
            if w * h > MAX_PIXELS:
                raise ImageError("Image is too large (max 40 megapixels)")
            if img.format == "JPEG":
                img.draft("RGB", (MAX_EDGE, MAX_EDGE))  # decode at reduced scale
            img.load()
    except ImageError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ImageError("Image is too large (max 40 megapixels)") from exc
    except Exception as exc:
        raise ImageError("Unsupported image. Please use a PNG, JPEG or WebP file.") from exc

    # Shrink before any full-size mode conversions (a 40 MP RGBA image would
    # otherwise be copied several times at full resolution).
    if max(img.size) > MAX_EDGE:
        img.thumbnail((MAX_EDGE, MAX_EDGE), Image.Resampling.LANCZOS)
    try:
        img = ImageOps.exif_transpose(img)
    except Exception:  # corrupt EXIF: keep the image, skip the rotation
        pass
    if img.mode in ("RGBA", "LA", "P"):
        rgba = img.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        img = Image.alpha_composite(background, rgba)
    img = img.convert("RGB")
    if max(img.size) > MAX_EDGE:
        img.thumbnail((MAX_EDGE, MAX_EDGE), Image.Resampling.LANCZOS)
    return img


def _otsu(gray: np.ndarray) -> float:
    from skimage.filters import threshold_otsu

    if gray.min() == gray.max():
        return float(gray.min())
    return float(threshold_otsu(gray))


def text_mask(img: Image.Image) -> np.ndarray:
    """Boolean mask, True where text ink is.

    Otsu threshold on luminance; the class that dominates the image border is
    background (works for dark-on-light and light-on-dark).
    """
    gray = np.asarray(img.convert("L"), dtype=np.float32)
    dark = gray < _otsu(gray)
    border = np.concatenate([dark[0, :], dark[-1, :], dark[:, 0], dark[:, -1]])
    background_is_dark = border.mean() > 0.5
    return ~dark if background_is_dark else dark


def ink_map(img: Image.Image) -> np.ndarray:
    """Soft ink coverage in [0, 1] (1 = text colour, 0 = background).

    Like ``text_mask`` but keeps anti-aliasing, which carries most of the
    stroke detail in small text.
    """
    gray = np.asarray(img.convert("L"), dtype=np.float32)
    mask = text_mask(img)
    if mask.all() or not mask.any():
        return mask.astype(np.float32)
    fg = float(np.median(gray[mask]))
    bg = float(np.median(gray[~mask]))
    if abs(fg - bg) < 1:
        return mask.astype(np.float32)
    return np.clip((gray - bg) / (fg - bg), 0.0, 1.0).astype(np.float32)


def crop_box(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    rows = np.flatnonzero(mask.any(axis=1))
    cols = np.flatnonzero(mask.any(axis=0))
    if rows.size == 0:
        return None
    return int(rows[0]), int(rows[-1]) + 1, int(cols[0]), int(cols[-1]) + 1


def crop_to_ink(mask: np.ndarray) -> np.ndarray:
    rows = np.flatnonzero(mask.any(axis=1))
    cols = np.flatnonzero(mask.any(axis=0))
    if rows.size == 0:
        return mask[:0, :0]
    return mask[rows[0] : rows[-1] + 1, cols[0] : cols[-1] + 1]


MAX_SKEW_DEGREES = 8.0
_SKEW_STEP = 0.25
MIN_SKEW_ASPECT = 3.0  # lines shorter than 3x their height aren't deskewed
MIN_SKEW_GAIN = 0.02  # required improvement in profile sharpness over 0 deg


def _rotate_mask(mask: np.ndarray, angle: float) -> np.ndarray:
    img = Image.fromarray(mask.astype(np.uint8) * 255)
    out = img.rotate(-angle, resample=Image.Resampling.BILINEAR, expand=True, fillcolor=0)
    return np.asarray(out) > 127


def _rotate_soft(soft: np.ndarray, angle: float) -> np.ndarray:
    img = Image.fromarray(soft.astype(np.float32), mode="F")
    out = img.rotate(-angle, resample=Image.Resampling.BILINEAR, expand=True, fillcolor=0)
    return np.asarray(out, dtype=np.float32)


def estimate_skew(mask: np.ndarray) -> float:
    """Text-line angle in degrees (PIL convention: positive = counter-clockwise),
    found by maximising the sharpness of the horizontal projection profile."""
    cropped = crop_to_ink(mask)
    if cropped.size == 0:
        return 0.0
    # Work on a small copy for speed.
    scale = min(1.0, 400 / max(cropped.shape))
    small = (
        np.asarray(
            Image.fromarray(cropped.astype(np.uint8) * 255).resize(
                (max(1, int(cropped.shape[1] * scale)), max(1, int(cropped.shape[0] * scale))),
                Image.Resampling.BILINEAR,
            )
        )
        > 127
    )
    if cropped.shape[1] < MIN_SKEW_ASPECT * cropped.shape[0]:
        return 0.0  # too short for a reliable angle

    def sharpness(angle: float) -> float:
        profile = _rotate_mask(small, angle).sum(axis=1).astype(np.float64)
        return float((profile**2).sum())

    # Start from 0 deg and only move for a clear improvement: noise and ties
    # must not drag the estimate to the edge of the search range.
    best_angle, best_score = 0.0, sharpness(0.0)
    baseline = best_score
    for angle in np.arange(-MAX_SKEW_DEGREES, MAX_SKEW_DEGREES + 1e-9, _SKEW_STEP):
        score = sharpness(float(angle))
        if score > best_score:
            best_angle, best_score = float(angle), score
    if best_score < baseline * (1 + MIN_SKEW_GAIN):
        return 0.0
    return best_angle


def deskew(mask: np.ndarray) -> np.ndarray:
    angle = estimate_skew(mask)
    if abs(angle) < _SKEW_STEP:
        return mask
    return _rotate_mask(mask, angle)


def deskew_soft(soft: np.ndarray) -> np.ndarray:
    """Deskew a soft ink map (angle estimated on its 0.5 threshold)."""
    angle = estimate_skew(soft > 0.5)
    if abs(angle) < _SKEW_STEP:
        return soft
    return _rotate_soft(soft, angle)


def keep_components_touching(
    soft: np.ndarray, box: tuple[int, int, int, int] | None
) -> np.ndarray:
    """Zero out ink not connected to the text box (left, top, right, bottom):
    descenders of the line above, rules, icons. Soft edges are kept."""
    from scipy.ndimage import binary_dilation, label

    if box is None:
        return soft
    ink = soft > 0.3
    labels, n = label(ink)
    if n == 0:
        return soft
    left, top, right, bottom = box
    inside = labels[max(0, top) : bottom, max(0, left) : right]
    keep_ids = np.unique(inside[inside > 0])
    keep = np.isin(labels, keep_ids)
    keep = binary_dilation(keep, iterations=1)  # keep anti-aliased fringes
    return np.where(keep, soft, 0.0).astype(np.float32)
