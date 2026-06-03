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
    # Classic system / Office fonts
    "Times New Roman": "Tinos",
    "Arial": "Arimo",
    "Helvetica": "Liberation Sans",
    "Helvetica Neue": "Liberation Sans",
    "Georgia": "Gelasio",
    "Courier New": "Cousine",
    "Verdana": "DejaVu Sans",
    "Calibri": "Carlito",
    "Cambria": "Caladea",
    "Trebuchet MS": "Fira Sans",
    "Segoe UI": "Source Sans 3",
    "Aptos": "Inter",
    # Serif classics
    "Garamond": "EB Garamond",
    "Palatino": "Lora",
    "Bodoni": "Libre Bodoni",
    "Didot": "GFS Didot",
    "Trajan": "Cinzel",
    "Trajan Pro": "Cinzel",
    "Optima": "Lato",
    "Copperplate": "Cinzel",
    "Cooper Black": "Baloo 2",
    # Geometric / grotesque sans
    "Futura": "Nunito ExtraLight",
    "Gotham": "Montserrat",
    "Proxima Nova": "Nunito",
    "Avenir": "Nunito",
    "Century Gothic": "Poppins",
    "Avant Garde": "Poppins",
    "Brandon Grotesque": "Nunito",
    "Museo Sans": "Nunito",
    "Gilroy": "Poppins",
    "Garet": "Sora",
    # Humanist / neo-grotesque sans
    "Myriad Pro": "Source Sans 3",
    "Gill Sans": "Lato",
    "Frutiger": "Source Sans 3",
    "Univers": "Inter",
    "DIN": "Source Sans 3",
    # Tech / modern sans
    "San Francisco": "Inter",
    "SF Pro": "Inter",
    "Product Sans": "Poppins",
    "Google Sans": "Poppins",
    "Canva Sans": "DM Sans 9pt",
    "Satoshi": "DM Sans 9pt",
    "Sofia Pro": "Sofia Sans",
    "Spotify": "Montserrat",
    # Impact / display
    "Impact": "Anton",
    "Comic Sans": "Comic Neue",
    "Knockout": "Oswald",
    "Eurostile": "Orbitron",
    "Recoleta": "Fraunces",
    # Script
    "Monotype Corsiva": "Great Vibes",
}

# Case-insensitive lookup for proprietary font names
_PROP_LOOKUP = {k.lower(): k for k in PROPRIETARY_TO_OPEN_SOURCE}

# Aliases for corpus fonts whose DB name differs from the common search term.
# Maps the common name (as typed by users) to the actual DB family name.
CORPUS_ALIASES = {
    "DM Sans": "DM Sans 9pt",
    "Raleway": "Raleway Thin",
    "League Spartan": "League Spartan Thin",
    "Source Sans Pro": "Source Sans 3",
    "Cormorant Garamond": "Cormorant Garamond Light",
    "Old English": "UnifrakturMaguntia",
    "Sans Serif": "Inter",
}

# Case-insensitive lookup for corpus aliases
_ALIAS_LOOKUP = {k.lower(): k for k in CORPUS_ALIASES}

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
    "Helvetica Neue": {
        "css_family": "'Helvetica Neue', Helvetica, Arial, sans-serif",
        "category": "sans-serif",
        "vendor": "Linotype",
        "description": "The refined successor to Helvetica with improved legibility and a wider range of weights.",
    },
    "Segoe UI": {
        "css_family": "'Segoe UI', Tahoma, Geneva, sans-serif",
        "category": "sans-serif",
        "vendor": "Microsoft",
        "description": "The default UI typeface for Windows and Microsoft products since Windows Vista.",
    },
    "Aptos": {
        "css_family": "Aptos, Calibri, sans-serif",
        "category": "sans-serif",
        "vendor": "Microsoft",
        "description": "Microsoft's new default font for Office apps, replacing Calibri in 2023.",
    },
    "Bodoni": {
        "css_family": "Bodoni, 'Bodoni MT', 'Libre Bodoni', serif",
        "category": "serif",
        "vendor": "Various",
        "description": "A high-contrast modern serif typeface designed by Giambattista Bodoni in the late 18th century.",
    },
    "Didot": {
        "css_family": "Didot, 'Bodoni MT', serif",
        "category": "serif",
        "vendor": "Various",
        "description": "A French modern serif known for extreme contrast between thick and thin strokes, widely used in fashion magazines.",
    },
    "Trajan": {
        "css_family": "Trajan, 'Trajan Pro', serif",
        "category": "serif",
        "vendor": "Adobe",
        "description": "An all-caps serif typeface inspired by the inscriptions on Trajan's Column in Rome, popular in movie posters.",
    },
    "Trajan Pro": {
        "css_family": "'Trajan Pro', Trajan, serif",
        "category": "serif",
        "vendor": "Adobe",
        "description": "The professional version of Trajan with expanded OpenType features, inspired by Roman inscriptional lettering.",
    },
    "Optima": {
        "css_family": "Optima, 'Palatino Linotype', sans-serif",
        "category": "sans-serif",
        "vendor": "Linotype",
        "description": "A humanist sans-serif with subtle flared stroke endings, designed by Hermann Zapf in 1958.",
    },
    "Copperplate": {
        "css_family": "'Copperplate Gothic', Copperplate, serif",
        "category": "serif",
        "vendor": "Various",
        "description": "A distinctive all-caps typeface with small serifs, inspired by copperplate engraving.",
    },
    "Cooper Black": {
        "css_family": "'Cooper Black', serif",
        "category": "serif",
        "vendor": "Various",
        "description": "A heavy, rounded serif typeface popular in 1970s advertising and retro designs.",
    },
    "Gotham": {
        "css_family": "Gotham, Montserrat, sans-serif",
        "category": "sans-serif",
        "vendor": "Hoefler & Co.",
        "description": "A geometric sans-serif inspired by architectural signage in New York City, made famous by the Obama 2008 campaign.",
    },
    "Proxima Nova": {
        "css_family": "'Proxima Nova', Montserrat, sans-serif",
        "category": "sans-serif",
        "vendor": "Mark Simonson Studio",
        "description": "A geometric sans-serif that bridges the gap between Futura and Akzidenz-Grotesk, one of the most popular web fonts.",
    },
    "Avenir": {
        "css_family": "Avenir, 'Avenir Next', Nunito, sans-serif",
        "category": "sans-serif",
        "vendor": "Linotype",
        "description": "A geometric sans-serif designed by Adrian Frutiger in 1988, meaning 'future' in French.",
    },
    "Century Gothic": {
        "css_family": "'Century Gothic', 'Apple Gothic', sans-serif",
        "category": "sans-serif",
        "vendor": "Monotype",
        "description": "A geometric sans-serif typeface inspired by Futura, bundled with Windows.",
    },
    "Avant Garde": {
        "css_family": "'ITC Avant Garde Gothic', 'Century Gothic', sans-serif",
        "category": "sans-serif",
        "vendor": "ITC",
        "description": "A geometric sans-serif originally designed for Avant Garde magazine, known for tight letter spacing and geometric forms.",
    },
    "Brandon Grotesque": {
        "css_family": "'Brandon Grotesque', Nunito, sans-serif",
        "category": "sans-serif",
        "vendor": "HVD Fonts",
        "description": "A geometric sans-serif with a warm, friendly character, popular in branding and editorial design.",
    },
    "Museo Sans": {
        "css_family": "'Museo Sans', Nunito, sans-serif",
        "category": "sans-serif",
        "vendor": "exljbris",
        "description": "A clean, geometric sans-serif companion to Museo, popular in web and print design.",
    },
    "Gilroy": {
        "css_family": "Gilroy, Poppins, sans-serif",
        "category": "sans-serif",
        "vendor": "Radomir Tinkov",
        "description": "A modern geometric sans-serif with a clean, professional look, popular in tech and startup branding.",
    },
    "Garet": {
        "css_family": "Garet, Sora, sans-serif",
        "category": "sans-serif",
        "vendor": "Colophon Foundry",
        "description": "A clean geometric sans-serif with distinctive rounded terminals.",
    },
    "Myriad Pro": {
        "css_family": "'Myriad Pro', 'Myriad', sans-serif",
        "category": "sans-serif",
        "vendor": "Adobe",
        "description": "Adobe's signature humanist sans-serif, used in Apple branding from 2002 to 2015.",
    },
    "Gill Sans": {
        "css_family": "'Gill Sans', 'Gill Sans MT', sans-serif",
        "category": "sans-serif",
        "vendor": "Monotype",
        "description": "A British humanist sans-serif designed by Eric Gill in 1928, inspired by Edward Johnston's typeface for the London Underground.",
    },
    "Frutiger": {
        "css_family": "Frutiger, 'Frutiger Neue', sans-serif",
        "category": "sans-serif",
        "vendor": "Linotype",
        "description": "A humanist sans-serif originally designed for airport signage, prized for legibility at all sizes.",
    },
    "Univers": {
        "css_family": "Univers, 'Helvetica Neue', sans-serif",
        "category": "sans-serif",
        "vendor": "Linotype",
        "description": "A neo-grotesque sans-serif designed by Adrian Frutiger in 1957, known for its systematic weight and width numbering.",
    },
    "DIN": {
        "css_family": "'DIN Pro', 'DIN Next', sans-serif",
        "category": "sans-serif",
        "vendor": "Various",
        "description": "A sans-serif originally designed for German industrial standards (Deutsches Institut für Normung), widely used in signage and tech.",
    },
    "San Francisco": {
        "css_family": "-apple-system, BlinkMacSystemFont, sans-serif",
        "category": "sans-serif",
        "vendor": "Apple",
        "description": "Apple's system font used across iOS, macOS, and watchOS since 2015.",
    },
    "SF Pro": {
        "css_family": "-apple-system, BlinkMacSystemFont, sans-serif",
        "category": "sans-serif",
        "vendor": "Apple",
        "description": "The professional variant of San Francisco, Apple's system typeface for macOS and iOS.",
    },
    "Product Sans": {
        "css_family": "'Product Sans', 'Google Sans', Poppins, sans-serif",
        "category": "sans-serif",
        "vendor": "Google",
        "description": "Google's custom geometric sans-serif used in the Google logo and product branding.",
    },
    "Google Sans": {
        "css_family": "'Google Sans', 'Product Sans', Poppins, sans-serif",
        "category": "sans-serif",
        "vendor": "Google",
        "description": "Google's proprietary text typeface used across Google products and Android UI.",
    },
    "Canva Sans": {
        "css_family": "'Canva Sans', 'DM Sans', sans-serif",
        "category": "sans-serif",
        "vendor": "Canva",
        "description": "Canva's custom sans-serif typeface used as the default in Canva designs.",
    },
    "Satoshi": {
        "css_family": "Satoshi, 'DM Sans', sans-serif",
        "category": "sans-serif",
        "vendor": "Indian Type Foundry",
        "description": "A modern, clean sans-serif with geometric shapes and friendly personality, popular in UI design.",
    },
    "Sofia Pro": {
        "css_family": "'Sofia Pro', 'Sofia Sans', sans-serif",
        "category": "sans-serif",
        "vendor": "Mostardesign",
        "description": "A geometric sans-serif with soft, rounded forms and a contemporary feel.",
    },
    "Spotify": {
        "css_family": "'Circular Std', Montserrat, sans-serif",
        "category": "sans-serif",
        "vendor": "Spotify / Lineto",
        "description": "Spotify uses Circular, a geometric sans-serif by Lineto. Find similar open-source alternatives.",
    },
    "Impact": {
        "css_family": "Impact, 'Arial Black', sans-serif",
        "category": "sans-serif",
        "vendor": "Monotype",
        "description": "A bold condensed sans-serif designed for headlines and impact, widely used in memes and web graphics.",
    },
    "Comic Sans": {
        "css_family": "'Comic Sans MS', 'Comic Sans', cursive",
        "category": "sans-serif",
        "vendor": "Microsoft",
        "description": "A casual sans-serif typeface designed to mimic comic book lettering, bundled with Windows.",
    },
    "Knockout": {
        "css_family": "Knockout, Oswald, sans-serif",
        "category": "sans-serif",
        "vendor": "Hoefler & Co.",
        "description": "A wide family of condensed sans-serifs inspired by 19th-century wood type, popular in sports and editorial design.",
    },
    "Eurostile": {
        "css_family": "Eurostile, 'Eurostile Extended', sans-serif",
        "category": "sans-serif",
        "vendor": "URW",
        "description": "A geometric sans-serif with distinctive squared letterforms, widely used in sci-fi and tech design.",
    },
    "Recoleta": {
        "css_family": "Recoleta, Georgia, serif",
        "category": "serif",
        "vendor": "Latinotype",
        "description": "A soft, friendly serif with rounded terminals inspired by the Cooper Black and Windsor typefaces.",
    },
    "Monotype Corsiva": {
        "css_family": "'Monotype Corsiva', cursive",
        "category": "script",
        "vendor": "Monotype",
        "description": "An italic script typeface bundled with Windows, popular for invitations and formal documents.",
    },
}


def lookup_proprietary(name: str) -> str | None:
    """Case-insensitive lookup of a proprietary font name.

    Returns the canonical (correctly cased) name, or None.
    """
    return _PROP_LOOKUP.get(name.lower())


def lookup_corpus_alias(name: str) -> str | None:
    """Case-insensitive lookup of a corpus alias.

    Returns the actual DB family name, or None if not an alias.
    """
    canonical = _ALIAS_LOOKUP.get(name.lower())
    if canonical:
        return CORPUS_ALIASES[canonical]
    return None


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

    return matches
