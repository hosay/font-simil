#!/usr/bin/env python3
"""CLI tool to identify the closest open-source font for a given font file.

Usage:
    python skill/identify_font.py <font_file> [--db <db_path>] [--top <k>]

Examples:
    python skill/identify_font.py my_font.ttf
    python skill/identify_font.py my_font.woff2 --top 5
    python skill/identify_font.py my_font.otf --db /path/to/fontmatch.db
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fontmatch.features.fingerprint import fingerprint
from fontmatch.fonts.loader import UnsupportedFontError, load
from fontmatch.index.store import FontStore

DEFAULT_DB = Path(__file__).resolve().parent.parent / "fontmatch.db"


def main():
    parser = argparse.ArgumentParser(
        description="Identify the closest open-source font for a given font file."
    )
    parser.add_argument("font_file", help="Path to the font file (TTF/OTF/WOFF/WOFF2)")
    parser.add_argument(
        "--db", default=str(DEFAULT_DB), help="Path to the fontmatch database"
    )
    parser.add_argument("--top", type=int, default=5, help="Number of matches to return")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    font_path = Path(args.font_file)
    if not font_path.exists():
        print(f"Error: file not found: {font_path}", file=sys.stderr)
        sys.exit(1)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Error: database not found: {db_path}", file=sys.stderr)
        print("Run corpus ingestion first to build the database.", file=sys.stderr)
        sys.exit(1)

    try:
        font = load(font_path)
    except UnsupportedFontError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    fp = fingerprint(font)

    store = FontStore(db_path)
    store.build_index()

    results = store.identify(fp, k=args.top)
    store.close()

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print(f"Query: {font.family} {font.subfamily} ({font_path.name})")
        print(f"Top {len(results)} matches:")
        print()
        for i, match in enumerate(results, 1):
            print(f"  {i}. {match['family']}")
            print(f"     Name:       {match['name']}")
            print(f"     Distance:   {match['distance']:.4f}")
            print(f"     Metric:     {match['metric_distance']:.4f}")
            print(f"     Perceptual: {match['perceptual_distance']:.4f}")
            print()


if __name__ == "__main__":
    main()
