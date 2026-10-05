"""Search indexing: sitemap, noindex and canonical URLs for /similar-to pages.

Only pages with search demand are offered to search engines (proprietary
fonts and the corpus fonts on /popular); the ~2,800 other corpus pages are
near-identical templates and stay reachable but noindex.
"""

import re
from pathlib import Path

import pytest

from fontmatch.web.helpers import (
    PROPRIETARY_TO_OPEN_SOURCE,
    deslugify,
    lookup_proprietary,
    slugify,
)

FIXTURES = Path(__file__).parent / "fixtures"
ROBOTS = re.compile(r'<meta name="robots" content="([^"]+)"')
CANONICAL = re.compile(r'<link rel="canonical" href="([^"]+)"')


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from fontmatch.service.app import create_app

    db = tmp_path_factory.mktemp("db") / "test.db"
    return create_app(db_path=db, fixture_dir=FIXTURES, testing=True).test_client()


def _page(client, path):
    resp = client.get(path)
    html = resp.get_data(as_text=True)
    robots = ROBOTS.search(html)
    canonical = CANONICAL.search(html)
    return resp.status_code, robots and robots.group(1), canonical and canonical.group(1)


def test_sitemap_lists_only_pages_with_search_demand(client):
    lines = client.get("/sitemap.txt").get_data(as_text=True).split()
    paths = {line.split("://", 1)[1].split("/", 1)[1] for line in lines}
    assert "similar-to/helvetica" in paths  # proprietary
    assert "similar-to/montserrat" in paths  # corpus font on /popular
    assert "similar-to/jetbrains-mono" not in paths  # other corpus font
    assert len(lines) == len(set(lines))
    for prop in PROPRIETARY_TO_OPEN_SOURCE:
        assert f"similar-to/{slugify(prop)}" in paths


def test_proprietary_page_is_indexable_with_canonical(client):
    status, robots, canonical = _page(client, "/similar-to/helvetica")
    assert status == 200 and robots is None
    assert canonical.endswith("/similar-to/helvetica")


def test_case_variant_canonicalises(client):
    status, robots, canonical = _page(client, "/similar-to/Helvetica")
    assert status == 200 and canonical.endswith("/similar-to/helvetica")


def test_other_corpus_page_is_noindex(client):
    status, robots, canonical = _page(client, "/similar-to/jetbrains-mono")
    assert status == 200
    assert robots == "noindex, follow"
    assert canonical.endswith("/similar-to/jetbrains-mono")


def test_popular_corpus_page_is_indexable(client):
    status, robots, _ = _page(client, "/similar-to/montserrat")
    assert status == 200 and robots is None


def test_not_found_is_noindex_without_canonical(client):
    status, robots, canonical = _page(client, "/similar-to/no-such-font-xyz")
    assert status == 404 and robots == "noindex" and canonical is None


@pytest.mark.parametrize(
    "slug, display, expected",
    [
        ("dm-sans-9pt", "DM Sans 9pt", ("dm-sans", True)),  # DB variant name -> common name
        ("dm-sans", "DM Sans 9pt", ("dm-sans", True)),
        ("source-sans-pro", "Source Sans 3", ("source-sans-pro", True)),  # popular slug wins
        ("source-sans-3", "Source Sans 3", ("source-sans-3", False)),
        ("sans-serif", "Inter", ("inter", False)),  # semantic alias: not a variant
        ("helvetica", "Helvetica", ("helvetica", True)),
    ],
)
def test_similar_page_seo(slug, display, expected):
    from fontmatch.web.routes import similar_page_seo

    assert similar_page_seo(slug, display) == expected


def test_every_proprietary_name_survives_the_slug_round_trip():
    for name in PROPRIETARY_TO_OPEN_SOURCE:
        assert lookup_proprietary(deslugify(slugify(name))) == name, name
