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
    rows.npy      optional float16 (chars, faces, ROW_BINS): each glyph's ink
                  per ROW_BIN-px band relative to the baseline (from ROW_TOP).
                  Summed over a line's glyphs it gives the line's vertical
                  ink profile, which letter-spacing doesn't change. Computed
                  lazily per character when the file is missing.

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
# Row profiles: bands of ROW_BIN px from ROW_TOP (above the baseline) to
# ROW_BOTTOM (below it). Covers >99.9% of glyph ink at EM_PX = 48; ink
# outside is clipped into the edge bands.
ROW_TOP, ROW_BOTTOM, ROW_BIN = -72, 40, 2
ROW_BINS = (ROW_BOTTOM - ROW_TOP) // ROW_BIN


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
    pixels = np.memmap(out_dir / "pixels.bin", dtype=np.uint8, mode="r")
    _write_rows(np.stack(metas), pixels.view(np.ndarray), out_dir / "rows.npy")
    return len(faces)


def _char_row_profiles(meta: np.ndarray, buf: np.ndarray, ci: int) -> np.ndarray:
    """(faces, ROW_BINS) float16 ink profile of one character in every face."""
    out = np.zeros((meta.shape[0], ROW_BINS), dtype=np.float32)
    for face in range(meta.shape[0]):
        off, w, h, _, y = (int(v) for v in meta[face, ci])
        if w <= 0 or h <= 0:
            continue
        rows = buf[off : off + w * h].reshape(h, w).sum(axis=1, dtype=np.float32) / 255.0
        bins = np.clip((np.arange(y, y + h) - ROW_TOP) // ROW_BIN, 0, ROW_BINS - 1)
        np.add.at(out[face], bins, rows)
    return out.astype(np.float16)


def write_row_profiles(atlas: "GlyphAtlas", path: Path) -> None:
    """Write the rows.npy sidecar for an existing atlas (~1 min for ~5k faces)."""
    _write_rows(atlas.meta, atlas._buf, Path(path))


def _write_rows(meta: np.ndarray, buf: np.ndarray, path: Path) -> None:
    # Temp name + rename: readers never see a partial file.
    tmp = path.with_name(path.stem + ".tmp.npy")
    table = np.lib.format.open_memmap(
        tmp, mode="w+", dtype=np.float16, shape=(meta.shape[1], meta.shape[0], ROW_BINS)
    )  # one character's profiles for every face are contiguous (one read per char)
    for ci in range(meta.shape[1]):
        table[ci] = _char_row_profiles(meta, buf, ci)
    table.flush()
    del table
    tmp.replace(path)


class GlyphAtlas:
    def __init__(self, pixels, meta, advance, faces, charset, directory=None, rows=None):
        self.directory = directory
        self.rows = rows  # rows.npy sidecar (memory-mapped) or None
        self._lazy_rows: dict[int, np.ndarray] = {}
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
        faces = json.loads((directory / "faces.json").read_text())
        rows_path = directory / "rows.npy"
        rows = np.load(rows_path, mmap_mode="r") if rows_path.exists() else None
        if rows is not None and rows.shape != (len(charset), len(faces), ROW_BINS):
            logger.warning(
                "%s doesn't match the atlas (stale; rebuild it with "
                "scripts/build_glyph_index.py --rows-only): profiles computed on demand",
                rows_path,
            )
            rows = None
        elif rows is None:
            logger.warning(
                "%s missing: row profiles computed on demand (~0.1 s per new character "
                "per process); build it with scripts/build_glyph_index.py --rows-only",
                rows_path,
            )
        return cls(
            pixels=np.memmap(directory / "pixels.bin", dtype=np.uint8, mode="r"),
            meta=np.load(directory / "meta.npy"),
            advance=np.load(directory / "advance.npy"),
            faces=faces,
            charset=charset,
            directory=directory,
            rows=rows,
        )

    def row_profiles(self, ci: int) -> np.ndarray:
        """(faces, ROW_BINS) row-ink profile of atlas column ``ci``."""
        if self.rows is not None:
            return self.rows[ci]
        if ci not in self._lazy_rows:
            self._lazy_rows[ci] = _char_row_profiles(self.meta, self._buf, ci)
        return self._lazy_rows[ci]

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

    def compose(self, face: int, text: str, tracking: float = 0.0) -> tuple[np.ndarray, float]:
        """Typeset ``text`` in one face. Returns (uint8 image, coverage) where
        coverage is the fraction of non-space chars the face could draw.

        ``tracking`` (px, may be negative) is added to every advance, spaces
        included, like CSS letter-spacing."""
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
                pen += space + tracking
                continue
            wanted += 1
            if meta[ci, 1] < 0:
                pen += space + tracking  # this face lacks the glyph: stand-in width
                continue
            off, w, h, x, y = (int(v) for v in meta[ci])
            placements.append((off, w, h, int(round(pen)) + x, y))
            pen += float(adv[ci]) + tracking
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


__all__ = [
    "AtlasSource",
    "GlyphAtlas",
    "build_atlas",
    "faces_for",
    "write_row_profiles",
    "CHARSET",
]
