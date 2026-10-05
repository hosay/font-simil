"""Social preview images (og:image) for /similar-to pages."""

import io
import re
from pathlib import Path

import pytest
from PIL import Image

FIXTURES = Path(__file__).parent / "fixtures"
OG = re.compile(r'<meta property="og:image" content="([^"]+)"')


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    from fontmatch.service.app import create_app

    db = tmp_path_factory.mktemp("db") / "test.db"
    return create_app(db_path=db, fixture_dir=FIXTURES, testing=True)


@pytest.fixture(scope="module")
def client(app):
    return app.test_client()


def test_card_renders_at_1200x630():
    from fontmatch.web.og import render_card

    data = render_card(FIXTURES / "Lato-Regular.ttf", "", "Free alternative to Optima", "Lato")
    img = Image.open(io.BytesIO(data))
    assert img.size == (1200, 630) and img.format == "PNG"
    assert img.convert("L").getextrema()[0] < 80  # there is dark ink on it


def test_long_names_are_shrunk_to_fit():
    from fontmatch.web.og import render_card

    data = render_card(FIXTURES / "Lato-Regular.ttf", "", "Fonts similar to X", "W" * 60)
    assert Image.open(io.BytesIO(data)).size == (1200, 630)


def test_page_points_to_its_card_and_card_is_cached(client, app):
    html = client.get("/similar-to/optima").get_data(as_text=True)
    url = OG.search(html).group(1)
    assert url.endswith("/og/similar-to/optima.png")
    assert 'name="twitter:card" content="summary_large_image"' in html
    path = "/" + url.split("://", 1)[1].split("/", 1)[1]
    resp = client.get(path)
    assert resp.status_code == 200 and resp.mimetype == "image/png"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert Image.open(io.BytesIO(resp.data)).size == (1200, 630)
    assert len(list(Path(app.config["OG_DIR"]).rglob("*.png"))) == 1
    client.get(path)
    assert len(list(Path(app.config["OG_DIR"]).rglob("*.png"))) == 1


def test_non_canonical_or_unknown_slug_is_404(client):
    assert client.get("/og/similar-to/Optima.png").status_code == 404
    assert client.get("/og/similar-to/no-such-font.png").status_code == 404
    assert client.get("/og/similar-to/..%2F..%2Fetc.png").status_code == 404


def test_other_pages_use_the_site_card(client):
    html = client.get("/").get_data(as_text=True)
    assert OG.search(html).group(1).endswith("/static/og-default.png")
    assert client.get("/static/og-default.png").status_code == 200
