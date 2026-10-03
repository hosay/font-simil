"""Regression tests for the Phase 2 review findings."""

import io
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

FIXTURES = Path(__file__).parent / "fixtures"


def _png(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# --- 1. memory: absurd query aspect ------------------------------------------


def test_extreme_aspect_query_is_rejected_without_big_allocation(tmp_path):
    from fontmatch.image.glyphs import AtlasSource, GlyphAtlas, build_atlas
    from fontmatch.image.rank import ImageMatcher

    build_atlas([AtlasSource(FIXTURES / "Roboto-Regular.ttf", "R.ttf")], tmp_path, workers=1)
    matcher = ImageMatcher(GlyphAtlas.load(tmp_path), {"R.ttf": "roboto"})
    strip = np.zeros((6, 2000), dtype=np.float32)
    strip[1:5, :] = 1.0  # a 2000x4 "line"
    assert matcher.rank_mask(strip, "Hello", k=3) == []


def test_candidates_scored_in_chunks(tmp_path, monkeypatch):
    import fontmatch.image.rank as rank
    from fontmatch.image.glyphs import AtlasSource, GlyphAtlas, build_atlas
    from fontmatch.image.synth import render_text_image
    from fontmatch.image.prep import ink_map

    fonts = ["Roboto-Regular.ttf", "Tinos-Regular.ttf", "Cousine-Regular.ttf"]
    build_atlas([AtlasSource(FIXTURES / f, f) for f in fonts], tmp_path, workers=1)
    matcher = rank.ImageMatcher(GlyphAtlas.load(tmp_path), {f: f.lower() for f in fonts})
    monkeypatch.setattr(rank, "SCORE_CHUNK", 1)
    img = render_text_image(FIXTURES / "Tinos-Regular.ttf", "Sunrise Bakery", 48)
    assert matcher.rank_mask(ink_map(img), "Sunrise Bakery", k=1)[0].key == "Tinos-Regular.ttf"


# --- app-level -----------------------------------------------------------------


@pytest.fixture
def app(tmp_path, monkeypatch):
    from fontmatch.service.app import create_app

    monkeypatch.setenv("FONTMATCH_INTERNAL_TOKEN", "tok")
    return create_app(db_path=tmp_path / "t.db", testing=True)


def test_413_on_api_is_json(app):
    app.config["MAX_CONTENT_LENGTH"] = 100
    resp = app.test_client().post(
        "/api/identify-image",
        data={"image": (io.BytesIO(b"x" * 1000), "a.png")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 413
    assert resp.is_json and "10 MB" in resp.get_json()["error"]


def test_identify_image_rate_limited(app):
    class Fake:
        def identify(self, data, hint="", k=5):
            from fontmatch.image.service import NoTextFound

            raise NoTextFound("none")

    app.config["IMAGE_IDENTIFIER"] = Fake()
    client = app.test_client()
    codes = [
        client.post(
            "/api/identify-image",
            data={"image": (io.BytesIO(_png(Image.new("RGB", (10 + i, 10)))), "a.png")},
            content_type="multipart/form-data",
        ).status_code
        for i in range(12)
    ]
    assert codes[-1] == 429


def test_engine_failure_is_503(app):
    from fontmatch.image.service import EngineUnavailable

    class Broken:
        def identify(self, data, hint="", k=5):
            raise EngineUnavailable("tesseract timed out")

    app.config["IMAGE_IDENTIFIER"] = Broken()
    resp = app.test_client().post(
        "/api/identify-image",
        data={"image": (io.BytesIO(_png(Image.new("RGB", (10, 10)))), "a.png")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 503 and "tesseract" not in resp.get_json()["error"].lower()


def test_non_ascii_token_header_is_not_a_500(app):
    resp = app.test_client().get(
        "/api/similar-to?font=Nope", headers={"X-Internal-Token": "tök".encode().decode("latin-1")}
    )
    assert resp.status_code == 404


def test_cache_key_includes_engine_version_and_urls_added_after_read(app):
    calls = []

    class Fake:
        version = "v1"

        def identify(self, data, hint="", k=5):
            calls.append(1)
            return {
                "transcript": "x",
                "transcript_source": "hint",
                "text_box": [0, 0, 1, 1],
                "matches": [{"name": "Nope.ttf", "family": "Nope", "style": "Regular",
                             "license_id": "OFL-1.1", "category": "sans", "score": 50}],
            }  # fmt: skip

    fake = Fake()
    app.config["IMAGE_IDENTIFIER"] = fake
    client = app.test_client()
    data = _png(Image.new("RGB", (20, 20)))

    def post():
        return client.post(
            "/api/identify-image",
            data={"image": (io.BytesIO(data), "a.png"), "text_hint": "x"},
            content_type="multipart/form-data",
        ).get_json()

    first = post()
    second = post()
    assert len(calls) == 1 and second["cached"] is True
    assert second["matches"][0]["similar_url"] == first["matches"][0]["similar_url"]
    stored = app.config["STORE"].conn.execute("SELECT result_json FROM match_cache").fetchone()[0]
    assert "similar_url" not in stored  # URLs are derived on read, never stored
    fake.version = "v2"  # atlas rebuilt -> cache miss
    post()
    assert len(calls) == 2


def test_old_image_cache_rows_pruned_at_startup(tmp_path):
    from fontmatch.index.store import FontStore
    from fontmatch.service.app import create_app

    store = FontStore(tmp_path / "t.db")
    store.conn.execute(
        "INSERT INTO match_cache (query_hash, schema_version, result_json, created_at) "
        "VALUES ('img:old', 1, '[]', datetime('now', '-60 days')), "
        "('img:new', 1, '[]', datetime('now')), ('fonthash', 6, '[]', datetime('now', '-60 days'))"
    )
    store.conn.commit()
    store.close()
    app = create_app(db_path=tmp_path / "t.db", testing=True)
    keys = {r[0] for r in app.config["STORE"].conn.execute("SELECT query_hash FROM match_cache")}
    assert keys == {"img:new", "fonthash"}


# --- 4. tesseract ----------------------------------------------------------------


def test_tesseract_errors_become_engine_unavailable(monkeypatch):
    import pytesseract

    import fontmatch.image.locate as loc
    from fontmatch.image.service import EngineUnavailable

    def boom(*a, **k):
        raise pytesseract.TesseractError(1, "bad")

    monkeypatch.setattr(pytesseract, "image_to_data", boom)
    with pytest.raises(EngineUnavailable):
        loc.find_lines(Image.new("RGB", (50, 20), "white"))


# --- 7. lazy load failure backoff --------------------------------------------------


def test_lazy_identifier_remembers_failure(monkeypatch, tmp_path):
    from fontmatch.image import service

    attempts = []

    def failing(cls, db_path, atlas_dir=None):
        attempts.append(1)
        raise FileNotFoundError("no atlas")

    monkeypatch.setattr(service.ImageIdentifier, "from_paths", classmethod(failing))
    lazy = service.LazyImageIdentifier(tmp_path / "x.db")
    for _ in range(3):
        with pytest.raises(service.EngineUnavailable):
            lazy.identify(b"x")
    assert len(attempts) == 1


@pytest.mark.parametrize(
    "path,field", [("/api/identify", "file"), ("/api/identify-image", "image")]
)
def test_upload_endpoints_limited_to_10_per_minute(app, path, field):
    client = app.test_client()
    codes = [
        client.post(
            path, data={field: (io.BytesIO(b"xx"), "a.bin")}, content_type="multipart/form-data"
        ).status_code
        for _ in range(12)
    ]
    assert codes[:10].count(429) == 0 and codes[-1] == 429
