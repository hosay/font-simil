#!/usr/bin/env python3
"""Build the font corpus database from Google Fonts and system fonts.

Usage:
    python scripts/build_corpus.py
    python scripts/build_corpus.py --google-fonts /path/to/google/fonts
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fontmatch.index.ingest import ingest_corpus
from fontmatch.index.store import FontStore


def main():
    parser = argparse.ArgumentParser(description="Build the font corpus database")
    parser.add_argument("--db", default="fontmatch.db", help="Database path")
    parser.add_argument(
        "--google-fonts",
        default="google-fonts-repo",
        help="Path to google/fonts repo clone",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    store = FontStore(db_path)

    sources = [
        ("Test fixtures", Path("tests/fixtures")),
        ("System Liberation", Path("/usr/share/fonts/truetype/liberation")),
        ("System DejaVu", Path("/usr/share/fonts/truetype/dejavu")),
        ("System Ubuntu", Path("/usr/share/fonts/truetype/ubuntu")),
        ("System FreeFonts", Path("/usr/share/fonts/truetype/freefont")),
        ("Linux Libertine", Path("/usr/share/fonts/opentype/linux-libertine")),
    ]

    gf_path = Path(args.google_fonts)
    if gf_path.exists():
        sources.append(("Google Fonts", gf_path))

    total = 0
    start = time.time()

    for label, path in sources:
        if path.exists():
            print(f"Ingesting {label} from {path}...")
            count = ingest_corpus(path, store)
            total += count
            print(f"  -> {count} fonts")
        else:
            print(f"SKIP {label}: {path} not found")

    elapsed = time.time() - start
    print(f"\nTotal: {store.font_count()} fonts in {elapsed:.1f}s")

    # Build the index
    store.build_index()
    print(f"Index built with {len(store._index)} entries")
    store.close()


if __name__ == "__main__":
    main()
