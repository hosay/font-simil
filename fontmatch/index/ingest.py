"""Corpus ingestion: walk a font directory, fingerprint, and store."""

from __future__ import annotations

import logging
from pathlib import Path

from fontmatch.features.fingerprint import fingerprint
from fontmatch.fonts.loader import UnsupportedFontError, load
from fontmatch.index.store import FontStore

logger = logging.getLogger(__name__)

FONT_EXTENSIONS = {".ttf", ".otf", ".woff", ".woff2"}
LICENSE_FILENAMES = {"OFL.txt", "LICENSE.txt", "LICENSE", "LICENSE.md", "LICENCE.txt"}

_LICENSE_KEYWORDS = {
    "SIL Open Font License": "OFL-1.1",
    "Open Font License": "OFL-1.1",
    "Apache License": "Apache-2.0",
    "MIT License": "MIT",
}


def _detect_license(family_dir: Path) -> str:
    """Detect license from license files in the font family directory."""
    for name in LICENSE_FILENAMES:
        license_file = family_dir / name
        if license_file.exists():
            content = license_file.read_text(errors="ignore")[:2000]
            for keyword, spdx in _LICENSE_KEYWORDS.items():
                if keyword.lower() in content.lower():
                    return spdx
            return "unknown"
    # Check parent directory too (some repos have license one level up)
    parent = family_dir.parent
    for name in LICENSE_FILENAMES:
        license_file = parent / name
        if license_file.exists():
            content = license_file.read_text(errors="ignore")[:2000]
            for keyword, spdx in _LICENSE_KEYWORDS.items():
                if keyword.lower() in content.lower():
                    return spdx
    return "unknown"


def ingest_corpus(corpus_path: Path, store: FontStore) -> int:
    """Walk a corpus directory, fingerprint all fonts, and store them.

    Expected layout (Google Fonts style):
        corpus_path/
            ofl/family_name/*.ttf + OFL.txt
            apache/family_name/*.ttf + LICENSE.txt

    Returns the number of fonts ingested.
    """
    corpus_path = Path(corpus_path)
    count = 0

    for font_file in sorted(corpus_path.rglob("*")):
        if font_file.suffix.lower() not in FONT_EXTENSIONS:
            continue

        try:
            loaded = load(font_file)
        except (UnsupportedFontError, Exception) as exc:
            logger.warning("Skipping %s: %s", font_file, exc)
            continue

        # Check if already stored
        existing = store.get_fingerprint(loaded.file_hash, 1)
        if existing is not None:
            count += 1
            continue

        license_id = _detect_license(font_file.parent)

        try:
            fp = fingerprint(loaded)
        except Exception as exc:
            logger.warning("Failed to fingerprint %s: %s", font_file, exc)
            continue

        store.store_fingerprint(
            font_file.name,
            fp,
            license_id=license_id,
            source=str(font_file.relative_to(corpus_path)),
        )
        count += 1

    return count
