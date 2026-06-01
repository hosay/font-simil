"""Web font crawler: fetch pages, parse CSS, download fonts, fingerprint."""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

import httpx

from fontmatch.features.fingerprint import fingerprint
from fontmatch.fonts.loader import UnsupportedFontError, load
from fontmatch.index.store import FontStore
from fontmatch.scrape.css_parser import extract_font_urls
from fontmatch.scrape.sites import parse_site_list

logger = logging.getLogger(__name__)

DEFAULT_RATE_LIMIT = 1.0  # seconds between requests to same domain
DEFAULT_TIMEOUT = 15.0


def _fetch_page_fonts(
    domain: str,
    client: httpx.Client,
    seen_hashes: set[str],
) -> list[dict]:
    """Fetch a domain's homepage and extract font files."""
    url = f"https://{domain}"
    fonts_found = []

    try:
        # Fetch the homepage
        resp = client.get(url, follow_redirects=True, timeout=DEFAULT_TIMEOUT)
        resp.raise_for_status()
        html = resp.text
    except Exception as exc:
        logger.debug("Failed to fetch %s: %s", url, exc)
        return []

    # Find linked CSS files
    import re

    css_urls = set()

    # Inline styles with @font-face
    inline_css_blocks = re.findall(r"<style[^>]*>(.*?)</style>", html, re.DOTALL | re.IGNORECASE)
    for block in inline_css_blocks:
        if "@font-face" in block:
            for entry in extract_font_urls(block, base_url=str(resp.url)):
                fonts_found.append(entry)

    # External CSS links
    for match in re.finditer(
        r'<link[^>]+href=["\']([^"\']+\.css[^"\']*)["\']', html, re.IGNORECASE
    ):
        css_url = match.group(1)
        if css_url.startswith("//"):
            css_url = "https:" + css_url
        elif css_url.startswith("/"):
            from urllib.parse import urljoin

            css_url = urljoin(str(resp.url), css_url)
        elif not css_url.startswith("http"):
            from urllib.parse import urljoin

            css_url = urljoin(str(resp.url), css_url)
        css_urls.add(css_url)

    # Fetch each CSS and extract fonts
    for css_url in list(css_urls)[:20]:  # cap at 20 CSS files per site
        try:
            css_resp = client.get(css_url, follow_redirects=True, timeout=DEFAULT_TIMEOUT)
            css_resp.raise_for_status()
            for entry in extract_font_urls(css_resp.text, base_url=css_url):
                fonts_found.append(entry)
        except Exception:
            continue

    # Download font files
    downloaded = []
    for entry in fonts_found:
        font_url = entry["url"]
        try:
            font_resp = client.get(font_url, follow_redirects=True, timeout=DEFAULT_TIMEOUT)
            font_resp.raise_for_status()
            data = font_resp.content
            if len(data) < 100:  # too small to be a real font
                continue
            file_hash = hashlib.sha256(data).hexdigest()
            if file_hash in seen_hashes:
                continue
            seen_hashes.add(file_hash)
            entry["data"] = data
            entry["file_hash"] = file_hash
            downloaded.append(entry)
        except Exception:
            continue

    return downloaded


def crawl(
    site_list_csv: str,
    store: FontStore,
    *,
    limit: int = 10000,
    rate_limit: float = DEFAULT_RATE_LIMIT,
    checkpoint_path: Path | None = None,
) -> dict:
    """Crawl top sites for web fonts.

    Returns stats dict with counts of sites processed and fonts found.
    """
    domains = parse_site_list(site_list_csv, limit=limit)
    seen_hashes: set[str] = set()
    stats = {"sites_processed": 0, "sites_failed": 0, "fonts_found": 0}

    # Load checkpoint if available
    completed_domains: set[str] = set()
    if checkpoint_path and checkpoint_path.exists():
        completed_domains = set(checkpoint_path.read_text().splitlines())

    client = httpx.Client(
        headers={
            "User-Agent": (
                "Mozilla/5.0 (compatible; FontMatcher/1.0; +https://github.com/fontmatch)"
            ),
        },
        follow_redirects=True,
        timeout=DEFAULT_TIMEOUT,
    )

    try:
        for domain in domains:
            if domain in completed_domains:
                stats["sites_processed"] += 1
                continue

            logger.info("Crawling %s (%d/%d)", domain, stats["sites_processed"] + 1, len(domains))

            fonts = _fetch_page_fonts(domain, client, seen_hashes)

            for font_entry in fonts:
                try:
                    loaded = load(font_entry["data"])
                    fp = fingerprint(loaded)
                    store.store_fingerprint(
                        f"{domain}/{font_entry.get('family', 'unknown')}",
                        fp,
                        source=domain,
                    )

                    # Store site-font relationship
                    store.conn.execute(
                        """INSERT OR IGNORE INTO sites (domain, rank)
                           VALUES (?, ?)""",
                        (domain, stats["sites_processed"] + 1),
                    )
                    store.conn.commit()

                    stats["fonts_found"] += 1
                except (UnsupportedFontError, Exception) as exc:
                    logger.debug("Could not process font from %s: %s", domain, exc)

            stats["sites_processed"] += 1

            # Checkpoint
            if checkpoint_path:
                with open(checkpoint_path, "a") as f:
                    f.write(domain + "\n")

            time.sleep(rate_limit)
    finally:
        client.close()

    return stats
