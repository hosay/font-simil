"""Corpus ingestion: walk a font directory, fingerprint, and store."""

from __future__ import annotations

import functools
import logging
import re
from pathlib import Path

from fontmatch import licenses
from fontmatch.features.fingerprint import fingerprint
from fontmatch.features.perceptual import FINGERPRINT_SCHEMA_VERSION
from fontmatch.fonts.loader import UnsupportedFontError, load
from fontmatch.index.store import FontStore

logger = logging.getLogger(__name__)

FONT_EXTENSIONS = {".ttf", ".otf", ".woff", ".woff2"}
# Licence files beside a font, tried in this order; a file that matches no
# keyword does not end the search (a README-ish LICENSE.txt next to OFL.txt).
LICENSE_FILENAMES = ("OFL.txt", "UFL.txt", "LICENSE.txt", "LICENCE.txt", "LICENSE", "LICENSE.md")
_LICENSE_FILE_READ = 20_000  # OFL headers with many Reserved Font Names run past 2 KB

_LICENSE_KEYWORDS = {
    "SIL Open Font License": licenses.OFL,
    "Open Font License": licenses.OFL,
    "Apache License": licenses.APACHE,
    "MIT License": licenses.MIT,
    "Ubuntu Font Licence": licenses.UFL,  # the file spells it "LICENCE"
}

# Debian: package file lists and documentation roots; tests point these at temp trees.
DPKG_INFO_DIR = Path("/var/lib/dpkg/info")
DEBIAN_DOC_ROOT = Path("/usr/share/doc")

_METADATA_LICENSE = re.compile(r'^license:\s*"([A-Za-z0-9_-]+)"\s*$', re.MULTILINE)


def _license_file_in(directory: Path) -> str | None:
    """Licence named by a licence file in ``directory`` (first keyword hit)."""
    for name in LICENSE_FILENAMES:
        license_file = directory / name
        if not license_file.is_file():
            continue
        content = license_file.read_text(errors="ignore")[:_LICENSE_FILE_READ].lower()
        for keyword, spdx in _LICENSE_KEYWORDS.items():
            if keyword.lower() in content:
                return spdx
    return None


def _beside_license(font_file: Path) -> str | None:
    return _license_file_in(font_file.parent)


def _metadata_pb_license(font_file: Path) -> str | None:
    """google/fonts ``METADATA.pb`` ``license: "OFL"`` line (whole line only)."""
    meta = font_file.parent / "METADATA.pb"
    if not meta.is_file():
        return None
    match = _METADATA_LICENSE.search(meta.read_text(errors="ignore")[:4000])
    return licenses.GOOGLE_FONTS_LICENSES.get(match.group(1)) if match else None


def _parent_license_dir(font_file: Path) -> str | None:
    """A licence file one level up, but only when that directory is a recognised
    licence folder (google/fonts ``ofl/``, ``apache/``, ``ufl/``); a repository
    root LICENSE usually covers code, not the fonts in a subfolder."""
    parent = font_file.parent.parent
    if parent.name.lower() not in licenses.GOOGLE_FONTS_DIRS:
        return None
    return _license_file_in(parent)


@functools.lru_cache(maxsize=4)
def _dpkg_font_files(info_dir: Path) -> dict[str, str]:
    """Resolved file path -> owning Debian package, from ``fonts-*.list``."""
    owners: dict[str, str] = {}
    for listing in sorted(info_dir.glob("fonts-*.list")):
        package = listing.name[: -len(".list")].split(":")[0]  # strip :arch
        try:
            lines = listing.read_text(errors="ignore").splitlines()
        except OSError:
            continue
        for line in lines:
            if line:
                owners[line.strip()] = package
    return owners


def _debian_copyright_license(font_file: Path) -> str | None:
    """Licence from the owning Debian package's DEP-5 copyright file. The owner
    comes from dpkg's file lists (a directory such as
    /usr/share/fonts/truetype/liberation is shared by two packages with
    different licences, so the directory name is not evidence). Only the
    ``Files: *`` stanza counts; packaging stanzas (``debian/*``) are ignored."""
    try:
        resolved = str(font_file.resolve())
    except OSError:
        return None
    package = _dpkg_font_files(DPKG_INFO_DIR).get(resolved)
    if package is None:
        return None
    copyright_file = DEBIAN_DOC_ROOT / package / "copyright"
    if not copyright_file.is_file():
        return None
    text = copyright_file.read_text(errors="ignore")
    for stanza in re.split(r"\n\s*\n", text):
        if re.search(r"^Files:\s*\*\s*$", stanza, re.MULTILINE):
            match = re.search(r"^License:\s*(\S.*?)\s*$", stanza, re.MULTILINE)
            if not match:
                return None
            return licenses.DEBIAN_COPYRIGHT_LICENSES.get(match.group(1).lower())
    return None


# Name ID 13 (licence description) / 14 (licence URL) substrings, checked in
# order. OFL comes before GPL so a dual "GPL AND OFL" font (Linux Libertine)
# is recorded as OFL-1.1, the licence a user would actually pick.
_GPL_FONT_EXCEPTION = licenses.GPL_FONT_EXCEPTION
_NAME_TABLE_LICENSES: list[tuple[str, str, str]] = [
    # (what to search: "text", "url" or "any"), lower-cased needle, SPDX id
    ("any", "scripts.sil.org/ofl", licenses.OFL),
    ("any", "open font license", licenses.OFL),
    ("any", "apache license", licenses.APACHE),
    ("any", "ubuntu font licen", licenses.UFL),  # "Licence" in the font, "License" elsewhere
    ("text", "liberation fonts license", licenses.LIBERATION),  # Liberation 1.x (Narrow)
    ("url", "dejavu", licenses.BITSTREAM_VERA),
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
        return licenses.MIT
    # "Bitstream" alone also appears in Bitstream Charter's (differently
    # licensed) notice; DejaVu's reads "Fonts are (c) Bitstream ... DejaVu".
    if "bitstream" in text and ("dejavu" in text or "vera" in text):
        return licenses.BITSTREAM_VERA
    if "gnu.org" in url and "gpl" in url and "lgpl" not in url:
        return _GPL_FONT_EXCEPTION
    return None


# Evidence tiers, strongest first. A licence file in the font's own directory
# outranks the name table on purpose: google/fonts relicensed families (Open
# Sans, Apache -> OFL) whose name tables lagged, and the directory is what the
# distributor asserts. The corollary is that a flat mixed folder must not hold
# a licence file; the corpus layout is one family per directory.
LICENSE_TIERS = (
    _beside_license,
    _metadata_pb_license,
    license_from_name_table,
    _parent_license_dir,
    _debian_copyright_license,
)


def resolve_license_across(paths: list[Path]) -> str:
    """Licence for identical copies of one font: each tier is tried on every
    copy before a weaker tier is consulted, so a copy in a bare folder cannot
    out-vote a copy sitting next to its OFL.txt."""
    paths = [Path(p) for p in paths]
    for tier in LICENSE_TIERS:
        for path in paths:
            found = tier(path)
            if found:
                return found
    return licenses.UNKNOWN


def resolve_license(font_file: Path) -> str:
    """The licence to record for ``font_file``: licence file beside it, its
    directory's METADATA.pb, its own name table, a licence file in a recognised
    licence folder one level up, then the owning Debian package's copyright
    file. ``"unknown"`` when nothing is recognised."""
    return resolve_license_across([font_file])


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

        license_id = resolve_license(font_file)

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
