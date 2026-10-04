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


MIN_CONTRAST = 4.0  # Lab delta-E below this is "no text"
MIN_LEVEL_FRACTION = 0.5  # a letter's ink level is at least this x the global level
MIN_COMPONENT_PX = 12  # smaller specks use the global level


def _mode_colour(lab: np.ndarray, rgb: np.ndarray) -> np.ndarray:
    """Median Lab colour of the most common (coarsely quantised) RGB colour."""
    q = (rgb.reshape(-1, 3) // 32).astype(np.int32)
    keys = q[:, 0] * 64 + q[:, 1] * 8 + q[:, 2]
    top = np.bincount(keys, minlength=512).argmax()
    return np.median(lab.reshape(-1, 3)[keys == top], axis=0)


def _largest_share(core: np.ndarray) -> float:
    """Share of the ink held by its largest connected component."""
    from scipy import ndimage

    labels, n = ndimage.label(core)
    if n == 0:
        return 1.0
    sizes = np.bincount(labels.ravel())[1:]
    return float(sizes.max() / sizes.sum())


def _distance_from(lab: np.ndarray, bg: np.ndarray) -> np.ndarray:
    return np.sqrt(((lab - bg) ** 2).sum(axis=2)).astype(np.float32)


def ink_map(img: Image.Image) -> np.ndarray:
    """Soft ink coverage in [0, 1] (1 = text colour, 0 = background).

    Ink is the colour distance (CIE Lab delta-E) from the background, so text
    in any colour (or several: logos colour letters individually) counts,
    light-on-dark included. Each letter is normalised by its own ink level,
    so a yellow and a blue letter both reach 1; anti-aliasing is kept, which
    carries most of the stroke detail in small text.

    Background: the most common colour on the image border. If that makes
    most of the image "ink", the crop may be text on a banner that doesn't
    fill it; the whole image's most common colour is then tried, and the
    reading that looks like text wins: letters are several separate
    components, a background is one region holding nearly all the "ink"
    (see _largest_share).
    """
    from scipy import ndimage
    from skimage.color import rgb2lab

    rgb = np.asarray(img.convert("RGB"))
    lab = rgb2lab(rgb.astype(np.float32) / 255.0).astype(np.float32)
    border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
    border_lab = np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]])
    dist = _distance_from(lab, _mode_colour(border_lab[None], border[None]))
    if dist.max() < MIN_CONTRAST:
        return np.zeros(dist.shape, dtype=np.float32)
    core = dist > max(_otsu(dist), MIN_CONTRAST)
    if core.mean() > 0.5:
        alt = _distance_from(lab, _mode_colour(lab, rgb))
        if alt.max() >= MIN_CONTRAST:
            alt_core = alt > max(_otsu(alt), MIN_CONTRAST)
            if alt_core.any() and _largest_share(alt_core) < _largest_share(core):
                dist, core = alt, alt_core
    if not core.any():
        return np.zeros(dist.shape, dtype=np.float32)

    # Ink level = mean distance of the upper half of a component's pixels
    # (its stroke cores, not its anti-aliased rim).
    labels, n = ndimage.label(core)
    idx = np.arange(1, n + 1)
    median = np.asarray(ndimage.median(dist, labels, idx), dtype=np.float32)
    upper = np.where(core & (dist >= median[np.maximum(labels, 1) - 1]), labels, 0)
    level = np.asarray(ndimage.mean(dist, upper, idx), dtype=np.float32)
    level = np.nan_to_num(level, nan=0.0)
    sizes = np.asarray(ndimage.sum(core, labels, idx))
    global_level = float(np.mean(dist[core & (dist >= np.median(dist[core]))]))
    level = np.where(sizes >= MIN_COMPONENT_PX, level, global_level)
    level = np.maximum(level, MIN_LEVEL_FRACTION * global_level)

    level_map = np.zeros(dist.shape, dtype=np.float32)
    level_map[core] = level[labels[core] - 1]
    # Anti-aliased fringes (outside the core) take the nearest letter's level;
    # core pixels keep their own letter's.
    spread = ndimage.maximum_filter(level_map, size=5)
    level_map = np.where(core, level_map, spread)
    level_map[level_map == 0] = global_level
    return np.clip(dist / level_map, 0.0, 1.0).astype(np.float32)


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
