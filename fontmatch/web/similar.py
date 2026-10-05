"""Free alternatives to a font given by name (shared by /similar-to, the API and MCP)."""

from __future__ import annotations

from dataclasses import dataclass

from fontmatch.features.perceptual import FINGERPRINT_SCHEMA_VERSION
from fontmatch.web.helpers import (
    METRIC_COMPATIBLE,
    PROPRIETARY_FONTS,
    PROPRIETARY_NOTES,
    PROPRIETARY_TO_OPEN_SOURCE,
    lookup_corpus_alias,
    lookup_proprietary,
)

DEFAULT_K = 10


@dataclass
class SimilarResult:
    display_name: str  # what the user asked for, canonicalised
    font_row: dict  # corpus font the lookup resolved to
    prop: dict | None  # proprietary font metadata, if the query was one
    matches: list[dict]  # raw store.identify() results (cached)


def find_similar(
    store, family_name: str, *, slug: str | None = None, k: int = DEFAULT_K
) -> SimilarResult | None:
    """Resolve a family name (proprietary names and aliases included) to a
    corpus font and return its closest licensed matches, or None."""
    display_name = family_name
    canonical_prop = lookup_proprietary(family_name)
    oss_name = PROPRIETARY_TO_OPEN_SOURCE.get(canonical_prop) if canonical_prop else None
    # Also check corpus aliases (e.g. "DM Sans" -> "DM Sans 9pt")
    alias_target = lookup_corpus_alias(family_name)
    lookup_name = oss_name or alias_target or family_name

    prop_meta = None
    if canonical_prop:
        display_name = canonical_prop
        meta = PROPRIETARY_FONTS.get(canonical_prop, {})
        prop_meta = {
            "name": canonical_prop,
            "css_family": meta.get("css_family", f"'{canonical_prop}', serif"),
            "category": meta.get("category", "sans-serif"),
            "vendor": meta.get("vendor", ""),
            "description": meta.get("description", ""),
            "notes": PROPRIETARY_NOTES.get(canonical_prop, ""),
            "metric_compatible": canonical_prop in METRIC_COMPATIBLE,
        }

    font_row = store.get_font_by_family(lookup_name, licensed_only=True)
    if font_row is None and slug is not None:
        font_row = store.get_font_by_family(slug.replace("-", " "), licensed_only=True)
    if font_row is None:
        return None

    if not canonical_prop:
        display_name = font_row["family"]

    fp = store.get_fingerprint(font_row["file_hash"], FINGERPRINT_SCHEMA_VERSION)
    if fp is None:
        return None

    cached = store.get_cached_result(font_row["file_hash"], FINGERPRINT_SCHEMA_VERSION)
    if cached is not None:
        results = cached
    else:
        results = store.identify(fp, k=k)
        store.cache_result(font_row["file_hash"], FINGERPRINT_SCHEMA_VERSION, results)

    return SimilarResult(
        display_name=display_name, font_row=dict(font_row), prop=prop_meta, matches=results
    )
