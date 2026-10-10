"""Backfill ``fonts.license_id`` from the fonts' own name tables.

Rows ingested from directories without an OFL.txt/LICENSE (system packages
such as Liberation, DejaVu, Noto, FreeFont) were stored as 'unknown' even
though the font file declares its licence (OpenType name IDs 13/14). This
reads it back for every 'unknown' row whose file can be found under the
given directories, and drops cached match results that embed the old value.

    python -m fontmatch.index.relicense --db PATH --font-dir DIR [--font-dir DIR ...]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
from pathlib import Path

from fontmatch.features.perceptual import FINGERPRINT_SCHEMA_VERSION
from fontmatch.index.ingest import FONT_EXTENSIONS, license_from_name_table
from fontmatch.index.store import FontStore

log = logging.getLogger(__name__)


def _file_index(search_dirs: list[Path]) -> dict[str, list[Path]]:
    """Basename -> every font file with that name under the search dirs, in
    walk order. The same basename recurs (an older DejaVu under /usr/share,
    a newer one in the Google Fonts repo), so the caller picks by hash."""
    index: dict[str, list[Path]] = {}
    for root in search_dirs:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            for filename in sorted(filenames):
                if Path(filename).suffix.lower() in FONT_EXTENSIONS:
                    index.setdefault(filename, []).append(Path(dirpath) / filename)
    return index


def _candidates(
    source: str, name: str, search_dirs: list[Path], index: dict[str, list[Path]]
) -> list[Path]:
    """Files that may be the row's: its ``source`` path under a search dir
    first, then every file with the same basename."""
    found: list[Path] = []
    rel = Path(source) if source else None
    if rel is not None and not rel.is_absolute() and ".." not in rel.parts:
        for root in search_dirs:
            candidate = root / rel
            if candidate.is_file():
                found.append(candidate)
    for candidate in index.get(rel.name if rel is not None else name, []):
        if candidate not in found:
            found.append(candidate)
    return found


def _find_file(row, search_dirs: list[Path], index: dict[str, list[Path]]) -> tuple:
    """(path, outcome): the candidate whose sha256 is the row's file_hash,
    else (None, "hash_mismatch") when same-named files exist, else
    (None, "not_found"). Unreadable files are skipped."""
    outcome = "not_found"
    for candidate in _candidates(row["source"] or "", row["name"], search_dirs, index):
        try:
            digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
        except OSError as exc:
            log.warning("cannot read %s: %s", candidate, exc)
            continue
        if digest == row["file_hash"]:
            return candidate, "found"
        outcome = "hash_mismatch"
        log.warning("hash mismatch, skipped: %s vs %s", row["source"], candidate)
    return None, outcome


def _purge_cached_results(store: FontStore, names: set[str]) -> int:
    """Drop font-match cache rows that list any of the relicensed fonts;
    the cached dicts embed ``license_id`` (FontStore.identify)."""
    with store._lock:
        rows = store.conn.execute(
            "SELECT query_hash, result_json FROM match_cache WHERE schema_version = ?",
            (FINGERPRINT_SCHEMA_VERSION,),
        ).fetchall()
        stale = []
        for row in rows:
            try:
                result = json.loads(row["result_json"])
            except ValueError:
                continue
            if not isinstance(result, list):
                continue
            if any(isinstance(m, dict) and m.get("name") in names for m in result):
                stale.append((row["query_hash"],))
        if stale:
            store.conn.executemany(
                "DELETE FROM match_cache WHERE query_hash = ? AND schema_version = ?",
                [(h, FINGERPRINT_SCHEMA_VERSION) for (h,) in stale],
            )
            store.conn.commit()
    return len(stale)


def relicense_unknown(
    store_or_db_path: FontStore | str | Path, search_dirs: list[Path]
) -> dict[str, int]:
    """Relicense every ``license_id='unknown'`` row whose file declares a licence.

    A file is only trusted when its sha256 equals the row's ``file_hash``, so a
    same-named file from another version or directory never relicenses a row.
    Returns counts: updated, still_unknown (file found, nothing recognised),
    not_found (also unreadable), hash_mismatch, cache_purged.
    """
    store = (
        store_or_db_path
        if isinstance(store_or_db_path, FontStore)
        else FontStore(store_or_db_path)
    )
    search_dirs = [Path(d) for d in search_dirs]
    index = _file_index(search_dirs)
    counts = {"updated": 0, "still_unknown": 0, "not_found": 0, "hash_mismatch": 0}
    updated_names: set[str] = set()

    with store._lock:
        # The service's workers write this DB too: wait rather than fail.
        store.conn.execute("PRAGMA busy_timeout = 30000")
        rows = store.conn.execute(
            "SELECT id, name, source, file_hash FROM fonts WHERE license_id = 'unknown'"
        ).fetchall()
    for row in rows:
        path, outcome = _find_file(row, search_dirs, index)
        if path is None:
            counts[outcome] += 1
            log.debug("%s: %s (%s)", outcome, row["name"], row["source"])
            continue
        license_id = license_from_name_table(path)
        if license_id is None:
            counts["still_unknown"] += 1
            log.debug("no licence in name table: %s", path)
            continue
        with store._lock:
            store.conn.execute(
                "UPDATE fonts SET license_id = ? WHERE id = ?", (license_id, row["id"])
            )
            store.conn.commit()
        updated_names.add(row["name"])
        counts["updated"] += 1
        log.info("%s -> %s", row["source"] or row["name"], license_id)

    counts["cache_purged"] = _purge_cached_results(store, updated_names) if updated_names else 0
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(os.environ.get("FONTMATCH_DB", "fontmatch.db")),
        help="SQLite database (default: $FONTMATCH_DB or ./fontmatch.db)",
    )
    parser.add_argument(
        "--font-dir",
        type=Path,
        action="append",
        required=True,
        dest="font_dirs",
        help="Directory to search for the fonts' files (repeatable)",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if not args.db.exists():
        log.error("no database at %s", args.db)
        return 1
    for d in args.font_dirs:
        if not d.is_dir():
            log.warning("not a directory: %s", d)

    counts = relicense_unknown(args.db, args.font_dirs)
    log.info(
        "updated %d, still unknown %d, file not found %d, hash mismatch %d, "
        "cached results purged %d",
        counts["updated"],
        counts["still_unknown"],
        counts["not_found"],
        counts["hash_mismatch"],
        counts["cache_purged"],
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
