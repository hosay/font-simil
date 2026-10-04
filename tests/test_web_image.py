"""Image -> font matching on the website (/identify, image mode)."""

import io
from pathlib import Path

import pytest
from PIL import Image

FIXTURES = Path(__file__).parent / "fixtures"

RESULT = {
    "transcript": "Hello World",
    "transcript_source": "hint",
    "text_box": [0, 0, 10, 10],
    "matches": [
        {"name": "DejaVuSans.ttf", "family": "DejaVu Sans", "style": "Regular",
         "license_id": "OFL-1.1", "category": "sans", "score": 97,
         "match_label": "likely the same font"},
        {"name": "Arimo-Regular.ttf", "family": "Arimo", "style": "Bold",
         "license_id": "Apache-2.0", "category": "sans", "score": 80,
         "match_label": "similar alternative"},
    ],
}  # fmt: skip


def _png(size=(120, 40), color="white"):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


class FakeIdentifier:
    version = "test"

    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def identify(self, data, hint="", k=5):
        self.calls.append(hint)
        if self.error:
            raise self.error
        import copy

        return copy.deepcopy(RESULT)


@pytest.fixture(scope="module")
def base_app(tmp_path_factory):
    from fontmatch.service.app import create_app

    tmp = tmp_path_factory.mktemp("webimg")
    return create_app(db_path=tmp / "test.db", fixture_dir=FIXTURES, testing=True)


@pytest.fixture
def app(base_app, monkeypatch):
    fake = FakeIdentifier()
    monkeypatch.setitem(base_app.config, "IMAGE_IDENTIFIER", fake)
    base_app.config["LIMITER"].reset()
    return base_app


def _post(app, image=None, hint="", follow=False):
    data = {"text_hint": hint}
    if image is not None:
        data["image"] = (io.BytesIO(image), "shot.png")
    return app.test_client().post(
        "/identify-image", data=data, content_type="multipart/form-data", follow_redirects=follow
    )


class TestForm:
    def test_identify_page_offers_image_and_font_upload(self, app):
        html = app.test_client().get("/identify").get_data(as_text=True)
        assert 'action="/identify-image"' in html
        assert 'name="image"' in html and 'name="text_hint"' in html
        assert 'name="file"' in html  # font-file mode still there
        assert "image/png" in html

    def test_homepage_mentions_images(self, app):
        html = app.test_client().get("/").get_data(as_text=True)
        assert "/identify#image" in html or 'href="/identify"' in html
        assert "image" in html.lower()


class TestSubmit:
    def test_results_page(self, app):
        resp = _post(app, _png(), hint="Hello World")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "DejaVu Sans" in html and "Arimo" in html
        assert "Hello World" in html
        assert "likely the same font" in html.lower()
        assert "/font-sample/DejaVuSans.ttf.png" in html
        assert "/similar-to/dejavu-sans" in html
        assert 'src="data:image/jpeg;base64,' in html  # preview of the upload
        assert app.config["IMAGE_IDENTIFIER"].calls == ["Hello World"]

    def test_preview_is_downscaled(self, app):
        import base64
        import re

        html = _post(app, _png(size=(3000, 600))).get_data(as_text=True)
        b64 = re.search(r'src="data:image/jpeg;base64,([^"]+)"', html).group(1)
        preview = Image.open(io.BytesIO(base64.b64decode(b64)))
        assert max(preview.size) <= 1000

    def test_repeat_upload_uses_cache(self, app):
        image = _png(size=(77, 33))
        _post(app, image, hint="cache me")
        _post(app, image, hint="cache me")
        assert app.config["IMAGE_IDENTIFIER"].calls == ["cache me"]

    def test_missing_image_redirects_with_message(self, app):
        resp = _post(app, None, follow=True)
        assert resp.status_code == 200
        assert "choose an image" in resp.get_data(as_text=True).lower()

    @pytest.mark.parametrize(
        "error,message",
        [
            ("NoTextFound", "No readable text"),
            ("ImageError", "Unsupported image"),
            ("EngineUnavailable", "temporarily unavailable"),
        ],
    )
    def test_errors_are_flashed(self, app, monkeypatch, error, message):
        from fontmatch.image import prep, service

        exc = {
            "NoTextFound": service.NoTextFound("No readable text found in the image."),
            "ImageError": prep.ImageError("Unsupported image format"),
            "EngineUnavailable": service.EngineUnavailable("tesseract exploded"),
        }[error]
        monkeypatch.setitem(app.config, "IMAGE_IDENTIFIER", FakeIdentifier(error=exc))
        resp = _post(app, _png(size=(50, 51)), follow=True)
        html = resp.get_data(as_text=True)
        assert message in html
        assert "tesseract" not in html

    def test_logged_separately_from_api(self, app):
        store = app.config["STORE"]
        before = store.request_count("/identify-image")
        _post(app, _png(size=(61, 20)))
        assert store.request_count("/identify-image") == before + 1

    def test_rate_limited(self, app):
        codes = [_post(app, _png(size=(30 + i, 20))).status_code for i in range(12)]
        assert codes[-1] == 429
