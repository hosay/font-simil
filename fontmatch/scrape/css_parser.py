"""Parse CSS @font-face declarations and extract font URLs."""

from __future__ import annotations

import re
from urllib.parse import urljoin

# Match @font-face blocks
_FONT_FACE_RE = re.compile(r"@font-face\s*\{([^}]+)\}", re.DOTALL)

# Match src entries: url('...') format('...')
_SRC_ENTRY_RE = re.compile(
    r"""url\(\s*['"]?([^'")\s]+)['"]?\s*\)\s*(?:format\(\s*['"]([^'"]+)['"]?\s*\))?""",
    re.IGNORECASE,
)

# Format preference order (lower = better)
_FORMAT_PRIORITY = {
    "woff2": 0,
    "woff": 1,
    "truetype": 2,
    "opentype": 3,
    "embedded-opentype": 4,
    "svg": 5,
}


def _resolve_url(url: str, base_url: str) -> str:
    """Resolve a relative URL against a base URL."""
    return urljoin(base_url, url)


def extract_font_urls(
    css: str,
    base_url: str,
) -> list[dict]:
    """Extract font file URLs from CSS text.

    Returns a list of dicts with keys: url, format, family (if available).
    Prefers woff2 when multiple formats are offered for the same face.
    """
    results = []

    for face_match in _FONT_FACE_RE.finditer(css):
        block = face_match.group(1)

        # Extract family name if present
        family_match = re.search(r"font-family\s*:\s*['\"]?([^'\";\n]+)", block)
        family = family_match.group(1).strip() if family_match else None

        # Collect all src entries for this face
        src_entries = []
        for src_match in _SRC_ENTRY_RE.finditer(block):
            url = src_match.group(1)
            fmt = src_match.group(2) or ""
            # Skip data URIs
            if url.startswith("data:"):
                continue
            src_entries.append(
                {
                    "url": _resolve_url(url, base_url),
                    "format": fmt.lower(),
                    "family": family,
                }
            )

        if not src_entries:
            continue

        # Pick the best format for this face
        src_entries.sort(key=lambda e: _FORMAT_PRIORITY.get(e["format"], 99))
        results.append(src_entries[0])

    return results
