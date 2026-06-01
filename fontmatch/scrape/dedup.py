"""Font deduplication by content hash."""

from __future__ import annotations

import hashlib


def dedup_fonts(entries: list[dict]) -> list[dict]:
    """Deduplicate font entries by SHA-256 of their content.

    Each entry must have a 'data' key with raw bytes.
    Returns entries with unique content, keeping the first occurrence.
    """
    seen: set[str] = set()
    result = []
    for entry in entries:
        h = hashlib.sha256(entry["data"]).hexdigest()
        if h not in seen:
            seen.add(h)
            entry["file_hash"] = h
            result.append(entry)
    return result
