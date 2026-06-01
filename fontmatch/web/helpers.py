"""Utility functions for the Flask web layer."""

from __future__ import annotations

import math
import re

# Weight/style suffixes to strip when building a Google Fonts URL.
_WEIGHT_SUFFIXES = re.compile(
    r"\s+(Extra\s*Light|Ultra\s*Light|Thin|Light|Regular|Medium|"
    r"Semi\s*Bold|Demi\s*Bold|Bold|Extra\s*Bold|Ultra\s*Bold|"
    r"Black|Heavy|Italic|Oblique|Condensed|Expanded|Narrow|Wide|"
    r"Display|Text|Caption|SemiBold|ExtraBold|Book|Roman)\s*$",
    re.IGNORECASE,
)

# Proprietary font -> open-source equivalent family name in our corpus
PROPRIETARY_TO_OPEN_SOURCE = {
    "Times New Roman": "Tinos",
    "Arial": "Arimo",
    "Helvetica": "Liberation Sans",
    "Georgia": "Gelasio",
    "Courier New": "Cousine",
    "Verdana": "DejaVu Sans",
    "Garamond": "EB Garamond",
    "Futura": "Nunito ExtraLight",
    "Palatino": "Lora",
    "Trebuchet MS": "Fira Sans",
    "Calibri": "Carlito",
    "Cambria": "Caladea",
}

# Case-insensitive lookup for proprietary font names
_PROP_LOOKUP = {k.lower(): k for k in PROPRIETARY_TO_OPEN_SOURCE}

# Extra metadata for the /alternative-to/ pages
PROPRIETARY_FONTS = {
    "Times New Roman": {
        "css_family": "'Times New Roman', Times, serif",
        "category": "serif",
        "vendor": "Monotype",
        "description": "The classic serif typeface bundled with Windows and widely used in print and academic documents.",
    },
    "Arial": {
        "css_family": "Arial, Helvetica, sans-serif",
        "category": "sans-serif",
        "vendor": "Monotype",
        "description": "One of the most widely used sans-serif typefaces, bundled with Windows and macOS.",
    },
    "Helvetica": {
        "css_family": "'Helvetica Neue', Helvetica, Arial, sans-serif",
        "category": "sans-serif",
        "vendor": "Linotype",
        "description": "The iconic Swiss sans-serif typeface, a staple of modern graphic design. Bundled with macOS.",
    },
    "Georgia": {
        "css_family": "Georgia, 'Times New Roman', serif",
        "category": "serif",
        "vendor": "Microsoft",
        "description": "A serif typeface designed specifically for screen readability, bundled with Windows and macOS.",
    },
    "Courier New": {
        "css_family": "'Courier New', Courier, monospace",
        "category": "monospace",
        "vendor": "Monotype",
        "description": "The standard monospaced typeface bundled with most operating systems.",
    },
    "Verdana": {
        "css_family": "Verdana, Geneva, sans-serif",
        "category": "sans-serif",
        "vendor": "Microsoft",
        "description": "A humanist sans-serif designed for screen readability at small sizes.",
    },
    "Garamond": {
        "css_family": "Garamond, 'EB Garamond', serif",
        "category": "serif",
        "vendor": "Various",
        "description": "A family of old-style serif typefaces named after the 16th-century engraver Claude Garamond.",
    },
    "Futura": {
        "css_family": "Futura, 'Century Gothic', sans-serif",
        "category": "sans-serif",
        "vendor": "Bauer",
        "description": "An influential geometric sans-serif typeface designed in 1927.",
    },
    "Palatino": {
        "css_family": "'Palatino Linotype', 'Book Antiqua', Palatino, serif",
        "category": "serif",
        "vendor": "Linotype",
        "description": "An old-style serif typeface designed by Hermann Zapf, bundled with macOS and Windows.",
    },
    "Trebuchet MS": {
        "css_family": "'Trebuchet MS', 'Lucida Grande', sans-serif",
        "category": "sans-serif",
        "vendor": "Microsoft",
        "description": "A humanist sans-serif typeface designed by Vincent Connare for Microsoft.",
    },
    "Calibri": {
        "css_family": "Calibri, 'Gill Sans', sans-serif",
        "category": "sans-serif",
        "vendor": "Microsoft",
        "description": "The default font in Microsoft Office since 2007, a modern humanist sans-serif.",
    },
    "Cambria": {
        "css_family": "Cambria, Georgia, serif",
        "category": "serif",
        "vendor": "Microsoft",
        "description": "A transitional serif typeface designed for on-screen reading and body text in Microsoft Office.",
    },
}


def lookup_proprietary(name: str) -> str | None:
    """Case-insensitive lookup of a proprietary font name.

    Returns the canonical (correctly cased) name, or None.
    """
    return _PROP_LOOKUP.get(name.lower())


def slugify(family: str) -> str:
    """Convert 'Open Sans' -> 'open-sans'."""
    return re.sub(r"[^a-z0-9]+", "-", family.lower()).strip("-")


def deslugify(slug: str) -> str:
    """Convert 'open-sans' -> 'Open Sans' (title case)."""
    return slug.replace("-", " ").title()


def base_family_name(family: str) -> str:
    """Strip weight/style suffixes: 'Alegreya Sans ExtraBold' -> 'Alegreya Sans'."""
    return _WEIGHT_SUFFIXES.sub("", family).strip()


def distance_to_score(distance: float) -> int:
    """Convert a raw distance to a 0-100 match score for display."""
    d = max(0.0, min(distance, 1.0))
    score = 100 * math.exp(-6.0 * d)
    return max(0, min(100, round(score)))


def google_fonts_url(family: str) -> str:
    """Build a Google Fonts specimen URL for a base family name."""
    base = base_family_name(family)
    return "https://fonts.google.com/specimen/" + base.replace(" ", "+")


_KNOWN_GOOGLE_FONTS = {
    "Roboto",
    "Roboto Mono",
    "Arimo",
    "Tinos",
    "Cousine",
    "Carlito",
    "Caladea",
    "Gelasio",
    "Lato",
    "Open Sans",
    "Noto Sans",
    "Noto Serif",
    "Noto Sans Mono",
    "Fira Sans",
    "Fira Mono",
    "Fira Code",
    "Source Code Pro",
    "Source Sans 3",
    "PT Sans",
    "PT Serif",
    "Ubuntu",
    "Ubuntu Mono",
    "Montserrat",
    "Lora",
    "Merriweather",
    "EB Garamond",
    "Alegreya",
    "Alegreya Sans",
    "Barlow",
    "Inconsolata",
    "Work Sans",
    "Nunito",
    "Mulish",
    "Bitter",
    "DM Sans",
    "DM Serif Display",
    "Spectral",
    "Vollkorn",
    "Inter",
    "Poppins",
    "Oswald",
    "Raleway",
    "Cormorant Garamond",
    "Space Mono",
    "JetBrains Mono",
}


def _is_crawled_source(source: str) -> bool:
    """Check if a source looks like a crawled web font (domain name, not a file path)."""
    if not source:
        return True
    # Crawled sources look like "zoho.com" or "siemens.com/SiemensSerif"
    # Local sources look like "Tinos-Regular.ttf" or "ofl/tinos/Tinos-Regular.ttf"
    return "." in source.split("/")[0] and not source.endswith((".ttf", ".otf", ".woff", ".woff2"))


def distance_to_score_component(distance: float, scale: float = 6.0) -> int:
    """Convert a raw sub-distance to a 0-100 score."""
    d = max(0.0, min(distance, 1.0))
    return max(0, min(100, round(100 * math.exp(-scale * d))))


_LICENSE_LABELS = {
    "OFL-1.1": "SIL Open Font License",
    "Apache-2.0": "Apache 2.0",
    "MIT": "MIT License",
    "unknown": "Unknown",
}


def license_label(license_id: str) -> str:
    """Return a human-friendly license label."""
    return _LICENSE_LABELS.get(license_id, license_id or "Unknown")


def enrich_matches(matches: list[dict], store=None) -> list[dict]:
    """Add human-friendly fields to each match dict for template rendering."""
    for m in matches:
        m["score"] = distance_to_score(m["distance"])
        m["google_fonts_url"] = None

        # Score breakdown
        m["metric_score"] = distance_to_score_component(m.get("metric_distance", 0), scale=6.0)
        m["perceptual_score"] = distance_to_score_component(
            m.get("perceptual_distance", 0), scale=6.0
        )

        # License
        m.setdefault("license_id", "unknown")
        m["license_label"] = license_label(m["license_id"])

        # Check if font file is available on disk (not a crawled web font)
        source = None
        if store is not None:
            source = store.get_font_source(m["name"])
        m["has_file"] = source is not None and not _is_crawled_source(source)

        # Google Fonts link
        if store is not None and store.has_google_fonts_source(m["family"]):
            m["google_fonts_url"] = google_fonts_url(m["family"])
        elif base_family_name(m["family"]) in _KNOWN_GOOGLE_FONTS:
            m["google_fonts_url"] = google_fonts_url(m["family"])

    return matches
