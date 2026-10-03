"""The candidate catalog: every font the image matcher may recommend.

A candidate is a licensed font (license_id set in the DB) whose file we can
find on disk, so we can render it. Each entry carries its base family (for
one-result-per-family dedupe) and a Google Fonts style category.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# Google Fonts METADATA.pb categories, normalised.
CATEGORIES = ("sans", "serif", "mono", "display", "handwriting")
_GF_CATEGORY = {
    "SANS_SERIF": "sans",
    "SERIF": "serif",
    "MONOSPACE": "mono",
    "DISPLAY": "display",
    "HANDWRITING": "handwriting",
}
_CATEGORY_RE = re.compile(r'^category:\s*"([A-Z_]+)"', re.MULTILINE)

FONT_SUFFIXES = {".ttf", ".otf"}


@dataclass(frozen=True)
class CatalogEntry:
    name: str  # DB fonts.name (file name)
    family: str
    base_family: str
    subfamily: str
    path: Path
    license_id: str
    category: str
    is_italic: bool


def base_family(family: str) -> str:
    from fontmatch.index.store import FontStore

    return FontStore._base_family(family)


_SIBLING_TOKENS = re.compile(
    r"\b(sc|display|text|caption|subhead|titling|deck|poster|banner|micro|"
    r"condensed|semi ?condensed|extra ?condensed|ultra ?condensed|expanded|semi ?expanded|"
    r"narrow|wide|flex|pro|variable|vf|\d+pt)\b"
)


def family_group(family: str) -> str:
    """Family with sibling-design suffixes removed ("Alegreya Sans SC",
    "Playfair Display", "DM Sans 9pt" -> their base). Used by the eval so a
    query's sibling families count as "the same family" for the dev/test
    split and leave-one-family-out exclusion."""
    fam = base_family(family)
    fam = _SIBLING_TOKENS.sub(" ", fam)
    return re.sub(r"\s+", " ", fam).strip() or base_family(family)


def gf_category(font_dir: Path) -> str | None:
    meta = font_dir / "METADATA.pb"
    if not meta.is_file():
        return None
    match = _CATEGORY_RE.search(meta.read_text(errors="ignore"))
    return _GF_CATEGORY.get(match.group(1)) if match else None


def _serif_class_category(serif_score: float) -> str:
    if serif_score > 0.7:
        return "serif"
    if serif_score > 0.3:
        return "mono"
    return "sans"


def _file_index(search_dirs: list[Path]) -> dict[str, Path]:
    index: dict[str, Path] = {}
    for root in search_dirs:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.suffix.lower() in FONT_SUFFIXES:
                index.setdefault(path.name, path)
    return index


def resolve_source(
    source: str, search_dirs: list[Path], file_index: dict[str, Path]
) -> Path | None:
    for root in search_dirs:
        candidate = root / source
        if candidate.is_file():
            return candidate
    return file_index.get(Path(source).name)


def build_catalog(
    db_path: Path, search_dirs: list[Path], schema_version: int | None = None
) -> list[CatalogEntry]:
    """Read licensed fonts from the DB and resolve their files.

    Only fonts with a fingerprint at ``schema_version`` (default: current)
    are included, i.e. exactly the fonts the file matcher can return.
    """
    import sqlite3

    import numpy as np

    from fontmatch.features.perceptual import FINGERPRINT_SCHEMA_VERSION

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    rows = conn.execute(
        """SELECT f.name, f.family, f.subfamily, f.source, f.license_id, fp.metric_vec
           FROM fonts f JOIN fingerprints fp ON fp.font_id = f.id
           WHERE f.license_id != '' AND fp.schema_version = ?""",
        (schema_version or FINGERPRINT_SCHEMA_VERSION,),
    ).fetchall()
    conn.close()

    file_index = _file_index(search_dirs)
    entries = []
    for name, family, subfamily, source, license_id, metric_blob in rows:
        path = resolve_source(source or name, search_dirs, file_index)
        if path is None:
            continue
        metric = np.frombuffer(metric_blob, dtype=np.float64)
        category = gf_category(path.parent) or _serif_class_category(float(metric[-1]))
        is_italic = (
            bool(metric[2]) or "italic" in (subfamily or "").lower() or "Italic" in path.name
        )
        entries.append(
            CatalogEntry(
                name=name,
                family=family,
                base_family=base_family(family),
                subfamily=subfamily or "",
                path=path,
                license_id=license_id,
                category=category,
                is_italic=is_italic,
            )
        )
    entries.sort(key=lambda e: (e.base_family, e.name))
    return entries


def save_catalog_json(entries: list[CatalogEntry], path: Path) -> None:
    """Persist the catalog next to the glyph atlas, so the service uses exactly
    the fonts the atlas was built from (independent of DB schema rows)."""
    import json

    rows = [{**e.__dict__, "path": str(e.path)} for e in entries]
    Path(path).write_text(json.dumps(rows))


def load_catalog_json(path: Path) -> list[CatalogEntry]:
    import json

    rows = json.loads(Path(path).read_text())
    return [CatalogEntry(**{**r, "path": Path(r["path"])}) for r in rows]
