"""Corpus ingestion: walk a font directory, fingerprint, and store."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from fontmatch.features.fingerprint import fingerprint
from fontmatch.features.perceptual import FINGERPRINT_SCHEMA_VERSION
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


# Name ID 13 (licence description) / 14 (licence URL) substrings, checked in
# order. OFL comes before GPL so a dual "GPL AND OFL" font (Linux Libertine)
# is recorded as OFL-1.1, the licence a user would actually pick.
_GPL_FONT_EXCEPTION = "GPL-3.0-or-later WITH Font-exception-2.0"
_NAME_TABLE_LICENSES: list[tuple[str, str, str]] = [
    # (what to search: "text", "url" or "any"), lower-cased needle, SPDX id
    ("any", "scripts.sil.org/ofl", "OFL-1.1"),
    ("any", "open font license", "OFL-1.1"),
    ("any", "apache license", "Apache-2.0"),
    ("any", "ubuntu font licen", "UFL-1.0"),  # "Licence" in the font, "License" elsewhere
    ("url", "dejavu", "Bitstream-Vera"),
    ("text", "gnu freefont", _GPL_FONT_EXCEPTION),
]


def license_from_name_table(path: Path) -> str | None:
    """Licence declared inside the font file (OpenType name IDs 13 and 14).

    Fallback for fonts shipped without an OFL.txt/LICENSE next to them
    (system packages such as Liberation, DejaVu, Noto, FreeFont). Returns
    an SPDX id, or None when the strings are missing or unrecognised; it
    never raises (unreadable file, woff2 without brotli, no name table).
    """
    try:
        from fontTools.ttLib import TTFont

        font = TTFont(str(path), lazy=True)
        try:
            name_table = font["name"]
            text = (name_table.getDebugName(13) or "").lower()
            url = (name_table.getDebugName(14) or "").lower()
        finally:
            font.close()
    except Exception:
        return None

    for where, needle, spdx in _NAME_TABLE_LICENSES:
        haystack = {"text": text, "url": url}.get(where, text + "\n" + url)
        if needle in haystack:
            return spdx
    if re.search(r"\bmit license\b", text + "\n" + url):  # not "permit licensees"
        return "MIT"
    # "Bitstream" alone also appears in Bitstream Charter's (differently
    # licensed) notice; DejaVu's reads "Fonts are (c) Bitstream ... DejaVu".
    if "bitstream" in text and ("dejavu" in text or "vera" in text):
        return "Bitstream-Vera"
    if "gnu.org" in url and "gpl" in url and "lgpl" not in url:
        return _GPL_FONT_EXCEPTION
    return None


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
        existing = store.get_fingerprint(loaded.file_hash, FINGERPRINT_SCHEMA_VERSION)
        if existing is not None:
            count += 1
            continue

        license_id = _detect_license(font_file.parent)
        if license_id == "unknown":
            license_id = license_from_name_table(font_file) or "unknown"

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
