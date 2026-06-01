"""Metric feature extraction from font tables."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from fontmatch.fonts.loader import LoadedFont

# Indices into the metric vector (documented, stable order)
_FIELD_ORDER = [
    "weight_class",
    "width_class",
    "is_italic",
    "italic_angle",
    "cap_height",
    "x_height",
    "ascender",
    "descender",
    "avg_width",
    "glyph_count_log",  # log-scaled for distance sanity
    "serif_score",  # 0=sans, 0.5=mono, 1=serif
]


@dataclass(frozen=True)
class MetricVector:
    """Fixed-length numeric vector extracted from font tables."""

    units_per_em: int
    weight_class: int
    width_class: int
    is_italic: bool
    italic_angle: float
    cap_height: float  # normalized by UPM
    x_height: float  # normalized by UPM
    ascender: float  # normalized by UPM
    descender: float  # normalized by UPM
    avg_width: float  # normalized by UPM
    glyph_count: int
    serif_class: str  # "serif", "sans", or "mono"

    def to_array(self) -> np.ndarray:
        """Convert to a fixed-length float64 vector for distance computation."""
        serif_score = {"sans": 0.0, "mono": 0.5, "serif": 1.0}.get(self.serif_class, 0.0)
        return np.array(
            [
                self.weight_class / 1000.0,  # normalize to ~[0,1]
                self.width_class / 9.0,
                float(self.is_italic),
                self.italic_angle / 45.0,  # normalize
                self.cap_height,
                self.x_height,
                self.ascender,
                self.descender,
                self.avg_width,
                np.log1p(self.glyph_count) / 10.0,  # log-scale
                serif_score,
            ],
            dtype=np.float64,
        )


def _classify_serif(tt) -> str:
    """Classify a font as serif, sans, or mono.

    Strategy:
    1. Check isFixedPitch / monospace width → "mono"
    2. Check PANOSE bSerifStyle when bFamilyType is valid
    3. Fallback: analyze the 'I' glyph outline width ratio
    """
    # Step 1: mono detection
    post = tt.get("post")
    if post and post.isFixedPitch:
        return "mono"

    cmap = tt.getBestCmap()
    if cmap and "hmtx" in tt:
        hmtx = tt["hmtx"]
        widths = set()
        for char in "ABCDEFGHIJabcdefghij0123456789":
            gid = cmap.get(ord(char))
            if gid:
                w, _ = hmtx[gid]
                widths.add(w)
        if len(widths) == 1 and widths:
            return "mono"

    # Step 2: PANOSE
    os2 = tt.get("OS/2")
    if not os2:
        return "sans"
    panose = os2.panose
    if panose.bFamilyType == 2 and panose.bSerifStyle > 0:
        # bSerifStyle 2-10 = various serif styles, 11-15 = sans variants
        if panose.bSerifStyle >= 11:
            return "sans"
        elif panose.bSerifStyle <= 10:
            return "serif"

    # Step 3: I-glyph outline analysis fallback
    if cmap and "glyf" in tt:
        glyph_name = cmap.get(ord("I"))
        if glyph_name:
            glyf = tt["glyf"]
            glyph = glyf[glyph_name]
            coords = glyph.coordinates
            if coords is not None and len(coords) > 0:
                xs = [c[0] for c in coords]
                ys = [c[1] for c in coords]
                x_range = max(xs) - min(xs)
                y_range = max(ys) - min(ys) if ys else 1
                ratio = x_range / y_range if y_range > 0 else 0
                n_points = len(coords)
                # Serif I: wide ratio (serifs extend) AND many points (>= 8)
                # Sans I: narrow ratio, few points (4-6, just a rectangle)
                # Bold sans can have wider stems → use point count as tiebreaker
                if ratio > 0.30:
                    return "serif"
                if ratio > 0.20 and n_points >= 8:
                    return "serif"
                return "sans"

    return "sans"  # default


def _measure_glyph_height(tt, char: str, upm: int) -> float:
    """Measure a glyph's bounding-box height, normalized by UPM."""
    cmap = tt.getBestCmap()
    if not cmap:
        return 0.0
    glyph_name = cmap.get(ord(char))
    if not glyph_name:
        return 0.0
    if "glyf" in tt:
        glyf = tt["glyf"]
        glyph = glyf[glyph_name]
        if glyph.numberOfContours > 0:
            return (glyph.yMax - glyph.yMin) / upm if hasattr(glyph, "yMax") else 0.0
    return 0.0


def metrics(font: LoadedFont) -> MetricVector:
    """Extract metric features from a loaded font."""
    tt = font.tt
    head = tt.get("head")
    os2 = tt.get("OS/2")
    post = tt.get("post")
    maxp = tt.get("maxp")

    upm = head.unitsPerEm if head else 1000
    if upm <= 0:
        upm = 1000  # safe fallback

    cap_height = 0.0
    x_height = 0.0
    if os2:
        s_cap = getattr(os2, "sCapHeight", 0) or 0
        s_x = getattr(os2, "sxHeight", 0) or 0
        cap_height = s_cap / upm if s_cap else 0.0
        x_height = s_x / upm if s_x else 0.0
    if cap_height == 0.0:
        cap_height = _measure_glyph_height(tt, "H", upm)
    if x_height == 0.0:
        x_height = _measure_glyph_height(tt, "x", upm)

    return MetricVector(
        units_per_em=upm,
        weight_class=os2.usWeightClass if os2 else 400,
        width_class=os2.usWidthClass if os2 else 5,
        is_italic=bool(os2.fsSelection & 1) if os2 else False,
        italic_angle=float(post.italicAngle) if post else 0.0,
        cap_height=cap_height,
        x_height=x_height,
        ascender=os2.sTypoAscender / upm if os2 else 0.0,
        descender=os2.sTypoDescender / upm if os2 else 0.0,
        avg_width=os2.xAvgCharWidth / upm if os2 else 0.0,
        glyph_count=maxp.numGlyphs if maxp else 0,
        serif_class=_classify_serif(tt),
    )
