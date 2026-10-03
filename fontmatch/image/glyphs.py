"""Glyph atlas: pre-rendered glyph bitmaps + advances for every candidate face.

Lets the matcher typeset the query's text in thousands of fonts without
keeping thousands of FreeType faces open. Built offline by
``scripts/build_glyph_index.py``; loaded memory-mapped.

Layout of an atlas directory:
    pixels.bin    uint8, all glyph bitmaps concatenated (anti-aliased, 0..255)
    meta.npy      int32 (faces, chars, 5): offset, width, height, x, y
                  (x, y = bitmap top-left relative to pen position on the
                  baseline; width = -1 when the face lacks the char)
    advance.npy   float32 (faces, chars): advance width in px
    faces.json    [{key, style}] per face (key = catalog font name)
    charset.json  the characters, in column order

Simplifications: no kerning or shaping (fine for Latin text at this
resolution), one fixed render size.
"""

from __future__ import annotations

import json
import logging
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import ImageFont

from fontmatch.fonts.variable import apply_instance, choose_instance, named_instances

logger = logging.getLogger(__name__)

EM_PX = 48
CHARSET = (
    "".join(chr(c) for c in range(33, 127))
    + "ÀÁÂÄÅÇÈÉÊËÌÍÎÏÑÒÓÔÖØÙÚÛÜàáâäåçèéêëìíîïñòóôöøùúûüß"
    + "‘’“”–—…€£¥©®™•"
)
assert len(set(CHARSET)) == len(CHARSET)
_CHAR_INDEX = {c: i for i, c in enumerate(CHARSET)}
_SPACE_FALLBACK_EM = 0.25


@dataclass(frozen=True)
class AtlasSource:
    path: Path
    key: str  # catalog font name


def faces_for(path: Path) -> list[str | None]:
    """Named instances to render for a font file. ``None`` = the file as-is.

    Variable fonts contribute Regular + Bold (Italic + Bold Italic for italic
    files) when those instances exist.
    """
    from fontTools.ttLib import TTFont

    with TTFont(str(path), lazy=True) as tt:
        names = {name for name, _ in named_instances(tt)}
    if not names:
        return [None]
    italic = "italic" in path.name.lower()
    wanted = ["Italic", "Bold Italic"] if italic else ["Regular", "Bold"]
    chosen = [n for n in wanted if n in names]
    return chosen or [None]


def _render_face(path: Path, instance: str | None):
    from fontTools.ttLib import TTFont

    face = ImageFont.truetype(str(path), EM_PX)
    with TTFont(str(path), lazy=True) as tt:
        if instance is not None:
            _, coords = choose_instance(tt, [instance])
            apply_instance(face, tt, coords)
        cmap = tt.getBestCmap() or {}
    meta = np.full((len(CHARSET), 5), -1, dtype=np.int32)
    advance = np.zeros(len(CHARSET), dtype=np.float32)
    chunks = []
    offset = 0
    for i, ch in enumerate(CHARSET):
        if ord(ch) not in cmap:
            continue
        img, (x, y) = face.getmask2(ch, mode="L", anchor="ls")
        w, h = img.size
        data = bytes(img)
        meta[i] = (offset, w, h, x, y)
        advance[i] = face.getlength(ch)
        chunks.append(data)
        offset += len(data)
    space = face.getlength(" ") if ord(" ") in cmap else EM_PX * _SPACE_FALLBACK_EM
    return meta, advance, b"".join(chunks), float(space)


def _render_source(src: AtlasSource):
    out = []
    try:
        for instance in faces_for(src.path):
            meta, advance, pixels, space = _render_face(src.path, instance)
            style = instance or "default"
            out.append((src.key, style, meta, advance, pixels, space))
    except Exception as exc:  # broken font files shouldn't kill the build
        logger.warning("atlas: skipping %s: %s", src.path, exc)
    return out


def build_atlas(sources: list[AtlasSource], out_dir: Path, workers: int = 4) -> int:
    """Render every source and write the atlas. Returns the number of faces."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if workers > 1:
        from multiprocessing import get_context

        with get_context("fork").Pool(workers) as pool:
            results = pool.map(_render_source, sources, chunksize=8)
    else:
        results = [_render_source(s) for s in sources]

    faces, metas, advances, spaces = [], [], [], []
    with open(out_dir / "pixels.bin", "wb") as fh:
        base = 0
        for per_source in results:
            for key, style, meta, advance, pixels, space in per_source:
                meta = meta.copy()
                present = meta[:, 1] >= 0
                meta[present, 0] += base
                fh.write(pixels)
                base += len(pixels)
                faces.append({"key": key, "style": style, "space": space})
                metas.append(meta)
                advances.append(advance)
                spaces.append(space)
    np.save(out_dir / "meta.npy", np.stack(metas))
    np.save(out_dir / "advance.npy", np.stack(advances))
    (out_dir / "faces.json").write_text(json.dumps(faces))
    (out_dir / "charset.json").write_text(json.dumps(CHARSET))
    return len(faces)


class GlyphAtlas:
    def __init__(self, pixels, meta, advance, faces, charset, directory=None):
        self.directory = directory
        self.pixels = pixels
        self.meta = meta
        self.advance = advance
        self.faces = faces
        self.charset = charset
        self._index = {c: i for i, c in enumerate(charset)}
        # Plain ndarray view of the (memory-mapped) buffer: avoids np.memmap's
        # per-slice overhead in the hot loop; still backed by the mapping.
        self._buf = pixels.view(np.ndarray)

    @classmethod
    def load(cls, directory: Path) -> "GlyphAtlas":
        directory = Path(directory)
        charset = json.loads((directory / "charset.json").read_text())
        return cls(
            pixels=np.memmap(directory / "pixels.bin", dtype=np.uint8, mode="r"),
            meta=np.load(directory / "meta.npy"),
            advance=np.load(directory / "advance.npy"),
            faces=json.loads((directory / "faces.json").read_text()),
            charset=charset,
            directory=directory,
        )

    def char_indices(self, text: str) -> list[int | None]:
        """Atlas column per char: None for whitespace, -1 if not in the atlas.
        Unsupported accented letters fall back to their base letter (Š -> S)."""
        out = []
        for c in text:
            if c.isspace():
                out.append(None)
                continue
            idx = self._index.get(c)
            if idx is None:
                base = unicodedata.normalize("NFKD", c)[:1]
                idx = self._index.get(base, -1)
            out.append(idx)
        return out

    def compose(self, face: int, text: str) -> tuple[np.ndarray, float]:
        """Typeset ``text`` in one face. Returns (uint8 image, coverage) where
        coverage is the fraction of non-space chars the face could draw."""
        meta = self.meta[face]
        adv = self.advance[face]
        space = self.faces[face]["space"]
        placements = []
        pen = 0.0
        wanted = drawn = 0
        for ci in self.char_indices(text):
            if ci is None or ci < 0:
                # whitespace, or a char no face has (arrow, emoji): advance by a
                # space so later glyphs keep their positions; not counted.
                pen += space
                continue
            wanted += 1
            if meta[ci, 1] < 0:
                pen += space  # this face lacks the glyph: stand-in width
                continue
            off, w, h, x, y = (int(v) for v in meta[ci])
            placements.append((off, w, h, int(round(pen)) + x, y))
            pen += float(adv[ci])
            drawn += 1
        coverage = drawn / wanted if wanted else 0.0
        if not placements:
            return np.zeros((0, 0), dtype=np.uint8), coverage
        x0 = min(p[3] for p in placements)
        x1 = max(p[3] + p[1] for p in placements)
        y0 = min(p[4] for p in placements)
        y1 = max(p[4] + p[2] for p in placements)
        canvas = np.zeros((y1 - y0, x1 - x0), dtype=np.uint8)
        for off, w, h, x, y in placements:
            if w == 0 or h == 0:
                continue
            glyph = self._buf[off : off + w * h].reshape(h, w)
            region = canvas[y - y0 : y - y0 + h, x - x0 : x - x0 + w]
            np.maximum(region, glyph, out=region)
        return canvas, coverage


__all__ = ["AtlasSource", "GlyphAtlas", "build_atlas", "faces_for", "CHARSET"]
