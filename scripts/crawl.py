#!/usr/bin/env python3
"""Crawl top sites from Majestic Million for web fonts.

Usage:
    python scripts/crawl.py --limit 10000 --rate 0.5
    python scripts/crawl.py --limit 100 --rate 0.3  # test run
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fontmatch.index.store import FontStore
from fontmatch.scrape.crawler import crawl


def main():
    parser = argparse.ArgumentParser(description="Crawl top sites for web fonts")
    parser.add_argument("--limit", type=int, default=10000, help="Number of sites")
    parser.add_argument("--rate", type=float, default=0.5, help="Rate limit (seconds)")
    parser.add_argument("--db", default="fontmatch.db", help="Database path")
    parser.add_argument(
        "--csv", default="majestic_million.csv", help="Majestic Million CSV"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    db_path = Path(args.db)
    csv_path = Path(args.csv)

    if not csv_path.exists():
        print(f"Error: CSV not found at {csv_path}")
        sys.exit(1)

    store = FontStore(db_path)
    csv_content = csv_path.read_text()

    print(f"DB has {store.font_count()} fonts before crawl")
    print(f"Crawling top {args.limit} sites (rate: {args.rate}s)...\n")

    start = time.time()
    stats = crawl(
        csv_content,
        store,
        limit=args.limit,
        rate_limit=args.rate,
        checkpoint_path=Path("crawl_checkpoint.txt"),
    )
    elapsed = time.time() - start

    print("\n=== Crawl Complete ===")
    print(f"Sites processed: {stats['sites_processed']}")
    print(f"Fonts found:     {stats['fonts_found']}")
    print(f"Time:            {elapsed:.0f}s ({elapsed/60:.1f}m)")
    print(f"DB total fonts:  {store.font_count()}")

    # Rebuild index
    print("Rebuilding index...")
    store.build_index()
    print(f"Index built with {len(store._index)} entries")

    store.close()


if __name__ == "__main__":
    main()
