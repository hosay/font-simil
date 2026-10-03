#!/usr/bin/env python3
"""Build the glyph atlas used by the image matcher.

Renders every catalog font (licensed, file on disk) into glyph_atlas/.
Re-run after the corpus changes. Takes a few minutes.

Usage:
    python scripts/build_glyph_index.py [--db fontmatch.db] [--out glyph_atlas] [--workers 4]
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fontmatch.image.catalog import build_catalog, save_catalog_json  # noqa: E402
from fontmatch.image.glyphs import AtlasSource, build_atlas  # noqa: E402
from fontmatch.image.paths import SEARCH_DIRS  # noqa: E402


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--db", default=str(ROOT / "fontmatch.db"))
    ap.add_argument("--out", default=str(ROOT / "glyph_atlas"))
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--catalog-schema", type=int, default=None)
    args = ap.parse_args()

    start = time.time()
    catalog = build_catalog(Path(args.db), SEARCH_DIRS, args.catalog_schema)
    print(f"catalog: {len(catalog)} fonts", flush=True)

    # Build into a temp dir, then swap, so a running server never sees a half-written atlas.
    out = Path(args.out)
    tmp = out.with_name(out.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    n = build_atlas([AtlasSource(e.path, e.name) for e in catalog], tmp, workers=args.workers)
    save_catalog_json(catalog, tmp / "catalog.json")
    old = out.with_name(out.name + ".old")
    shutil.rmtree(old, ignore_errors=True)
    if out.exists():
        out.rename(old)
    tmp.rename(out)
    shutil.rmtree(old, ignore_errors=True)
    size = sum(f.stat().st_size for f in out.iterdir()) / 1e6
    print(f"atlas: {n} faces, {size:.0f} MB in {time.time() - start:.0f}s -> {out}")


if __name__ == "__main__":
    main()
