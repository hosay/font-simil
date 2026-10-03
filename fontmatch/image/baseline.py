"""B0 baseline ranker: CLIP embedding of the whole query image compared with
the corpus glyph-sheet embeddings already in the DB. Zero new indexing.

Expected to be weak (CLIP is content-biased and the sheets show different
characters), it exists so the real engine has a number to beat.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
from PIL import Image

from fontmatch.features.perceptual import FINGERPRINT_SCHEMA_VERSION, perceptual
from fontmatch.image.catalog import CatalogEntry


def to_white_on_black(img: Image.Image) -> np.ndarray:
    gray = np.asarray(img.convert("L"), dtype=np.uint8)
    if np.median(gray) > 127:  # light background -> invert
        gray = 255 - gray
    return gray


class ClipSheetRanker:
    def __init__(self, db_path: Path, catalog: list[CatalogEntry]):
        by_name = {e.name: e for e in catalog}
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        rows = conn.execute(
            """SELECT f.name, fp.perceptual_vec FROM fonts f
               JOIN fingerprints fp ON fp.font_id = f.id WHERE fp.schema_version = ?""",
            (FINGERPRINT_SCHEMA_VERSION,),
        ).fetchall()
        conn.close()
        self.families = []
        vecs = []
        for name, blob in rows:
            if name in by_name:
                self.families.append(by_name[name].base_family)
                vecs.append(np.frombuffer(blob, dtype=np.float64))
        self.matrix = np.stack(vecs)

    def rank(self, img: Image.Image, text: str = "", k: int = 10) -> list[tuple[str, float]]:
        q = perceptual(to_white_on_black(img))
        sims = self.matrix @ q
        out, seen = [], set()
        for i in np.argsort(-sims):
            fam = self.families[i]
            if fam in seen:
                continue
            seen.add(fam)
            out.append((fam, float(sims[i])))
            if len(out) >= k:
                break
        return out
