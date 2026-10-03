"""ImageIdentifier service + POST /api/identify-image."""

import io
from pathlib import Path

import pytest
from PIL import Image

from fontmatch.image.catalog import CatalogEntry
from fontmatch.image.glyphs import AtlasSource, GlyphAtlas, build_atlas
from fontmatch.image.prep import ImageError
from fontmatch.image.service import ImageIdentifier, NoTextFound
from fontmatch.image.synth import render_text_image

FIXTURES = Path(__file__).parent / "fixtures"
FONTS = {
    "Roboto-Regular.ttf": ("Roboto", "sans"),
    "Tinos-Regular.ttf": ("Tinos", "serif"),
    "Cousine-Regular.ttf": ("Cousine", "mono"),
    "Caladea-Regular.ttf": ("Caladea", "serif"),
}


def _png(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture(scope="module")
def identifier(tmp_path_factory):
    out = tmp_path_factory.mktemp("atlas")
    build_atlas([AtlasSource(FIXTURES / f, f) for f in FONTS], out, workers=1)
    entries = [
        CatalogEntry(
            name=f,
            family=fam,
            base_family=fam.lower(),
            subfamily="Regular",
            path=FIXTURES / f,
            license_id="OFL-1.1",
            category=cat,
            is_italic=False,
        )
        for f, (fam, cat) in FONTS.items()
    ]
    return ImageIdentifier(GlyphAtlas.load(out), entries)


class TestService:
    def test_identifies_with_hint(self, identifier):
        data = _png(render_text_image(FIXTURES / "Tinos-Regular.ttf", "Harbor View Hotel", 48))
        result = identifier.identify(data, hint="Harbor View Hotel", k=3)
        assert result["transcript"] == "Harbor View Hotel"
        assert result["transcript_source"] == "hint"
        top = result["matches"][0]
        assert top["family"] == "Tinos"
        assert top["name"] == "Tinos-Regular.ttf"
        assert top["license_id"] == "OFL-1.1"
        assert 0 <= top["score"] <= 100
        assert top["category"] == "serif"

    def test_identifies_with_ocr_only(self, identifier):
        data = _png(render_text_image(FIXTURES / "Cousine-Regular.ttf", "Harbor View Hotel", 48))
        result = identifier.identify(data, hint="", k=3)
        assert result["transcript_source"] == "ocr"
        assert result["matches"][0]["family"] == "Cousine"

    def test_no_text(self, identifier):
        with pytest.raises(NoTextFound):
            identifier.identify(_png(Image.new("RGB", (200, 80), "white")), hint="")

    def test_bad_image(self, identifier):
        with pytest.raises(ImageError):
            identifier.identify(b"nope", hint="x")


@pytest.fixture(scope="module")
def client(tmp_path_factory, identifier):
    from fontmatch.service.app import create_app

    db = tmp_path_factory.mktemp("db") / "test.db"
    app = create_app(db_path=db, fixture_dir=FIXTURES, testing=True)
    app.config["IMAGE_IDENTIFIER"] = identifier
    return app.test_client()


class TestApi:
    def _post(self, client, data, hint=None):
        form = {"image": (io.BytesIO(data), "shot.png")}
        if hint is not None:
            form["text_hint"] = hint
        return client.post("/api/identify-image", data=form, content_type="multipart/form-data")

    def test_success(self, client):
        data = _png(render_text_image(FIXTURES / "Roboto-Regular.ttf", "Sunrise Bakery", 48))
        resp = self._post(client, data, hint="Sunrise Bakery")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["matches"][0]["family"] == "Roboto"
        assert body["matches"][0]["similar_url"].startswith("/similar-to/")
        assert body["transcript"] == "Sunrise Bakery"
        assert "google_fonts_url" in body["matches"][0]

    def test_missing_file(self, client):
        resp = client.post("/api/identify-image", data={}, content_type="multipart/form-data")
        assert resp.status_code == 400

    def test_bad_image(self, client):
        resp = self._post(client, b"garbage")
        assert resp.status_code == 400
        assert "PNG, JPEG or WebP" in resp.get_json()["error"]

    def test_no_text(self, client):
        resp = self._post(client, _png(Image.new("RGB", (100, 40), "white")))
        assert resp.status_code == 422
        assert resp.get_json()["type"] == "NoTextFound"

    def test_result_is_cached(self, client, identifier, monkeypatch):
        data = _png(render_text_image(FIXTURES / "Tinos-Regular.ttf", "Cached Query", 40))
        first = self._post(client, data, hint="Cached Query").get_json()
        monkeypatch.setattr(
            identifier, "identify", lambda *a, **k: pytest.fail("should hit cache")
        )
        second = self._post(client, data, hint="Cached Query").get_json()
        assert second["cached"] is True
        assert second["matches"] == first["matches"]


def test_scores_are_percentages_that_vary(identifier):
    data = _png(render_text_image(FIXTURES / "Tinos-Regular.ttf", "Harbor View Hotel", 48))
    scores = [
        m["score"] for m in identifier.identify(data, hint="Harbor View Hotel", k=4)["matches"]
    ]
    assert all(0 <= s <= 100 for s in scores)
    assert scores[0] < 100 or len(set(scores)) > 1  # not all clamped to 100
    assert scores == sorted(scores, reverse=True) or scores[0] >= max(scores[1:])
