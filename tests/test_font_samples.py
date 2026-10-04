"""Pregenerated "quick brown fox" sample images (shown in the ChatGPT widget)."""

import io
from pathlib import Path

import pytest
from PIL import Image

FIXTURES = Path(__file__).parent / "fixtures"
VARIABLE = Path(__file__).parent / "fixtures_variable"


def _png(data: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    img.load()
    return img


class TestRenderSample:
    def test_renders_a_wide_png_with_ink(self):
        from fontmatch.samples import SAMPLE_TEXT, render_sample

        assert SAMPLE_TEXT.startswith("The quick brown fox")
        img = _png(render_sample(FIXTURES / "DejaVuSans.ttf"))
        assert img.format == "PNG"
        assert img.width > 4 * img.height
        lo, hi = img.convert("L").getextrema()
        assert lo < 80 and hi > 200  # dark text on a light background

    def test_different_fonts_render_differently(self):
        from fontmatch.samples import render_sample

        a = render_sample(FIXTURES / "DejaVuSans.ttf")
        b = render_sample(FIXTURES / "DejaVuSerif.ttf")
        assert a != b

    def test_variable_font_named_instance_is_applied(self):
        from fontmatch.samples import render_sample

        variable = VARIABLE / "JosefinSlab[wght].ttf"
        regular = render_sample(variable, "Regular")
        bold = render_sample(variable, "Bold")
        ink = lambda d: sum(255 - p for p in _png(d).convert("L").getdata())  # noqa: E731
        assert ink(bold) > ink(regular) * 1.1

    def test_unknown_style_falls_back_without_error(self):
        from fontmatch.samples import render_sample

        assert render_sample(FIXTURES / "DejaVuSans.ttf", "Ultra Wide Nonsense")


class TestSampleStore:
    def test_filename_is_stable_and_safe(self):
        from fontmatch.samples import sample_filename

        a = sample_filename("../../etc/passwd", "Bold")
        assert a == sample_filename("../../etc/passwd", "Bold")
        assert a != sample_filename("../../etc/passwd", "Regular")
        assert "/" not in a and a.endswith(".png")

    def test_get_renders_once_then_serves_from_disk(self, tmp_path):
        from fontmatch.samples import SampleStore

        calls = []

        def resolve(name):
            calls.append(name)
            return FIXTURES / "DejaVuSans.ttf" if name == "DejaVuSans.ttf" else None

        store = SampleStore(tmp_path, resolve)
        first = store.get("DejaVuSans.ttf", "")
        assert first is not None and first.is_file()
        assert store.get("DejaVuSans.ttf", "") == first
        assert calls == ["DejaVuSans.ttf"]  # second call never resolved/rendered

    def test_unknown_font_returns_none(self, tmp_path):
        from fontmatch.samples import SampleStore

        store = SampleStore(tmp_path, lambda name: None)
        assert store.get("Nope.ttf", "") is None
        assert list(tmp_path.iterdir()) == []

    def test_broken_font_returns_none(self, tmp_path):
        from fontmatch.samples import SampleStore

        store = SampleStore(tmp_path, lambda name: FIXTURES / "corrupt.ttf")
        assert store.get("corrupt.ttf", "") is None


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    from fontmatch.service.app import create_app

    tmp = tmp_path_factory.mktemp("samples")
    application = create_app(db_path=tmp / "test.db", fixture_dir=FIXTURES, testing=True)
    application.config["SAMPLES_DIR"] = tmp / "samples"
    return application


class TestSampleRoute:
    def test_serves_png_for_corpus_font(self, app):
        resp = app.test_client().get("/font-sample/DejaVuSans.ttf.png")
        assert resp.status_code == 200
        assert resp.mimetype == "image/png"
        assert "max-age" in resp.headers.get("Cache-Control", "")
        assert _png(resp.data).width > 100

    def test_style_param_is_accepted(self, app):
        resp = app.test_client().get("/font-sample/DejaVuSans.ttf.png?style=Regular")
        assert resp.status_code == 200

    @pytest.mark.parametrize(
        "name", ["Nope-Regular.ttf", "..%2F..%2Fetc%2Fpasswd", "x" * 300]
    )
    def test_unknown_or_hostile_names_404(self, app, name):
        assert app.test_client().get(f"/font-sample/{name}.png").status_code == 404

    def test_sample_url_helper(self):
        from fontmatch.samples import sample_url

        url = sample_url("Inter[opsz,wght].ttf", "Bold")
        assert url.startswith("/font-sample/Inter%5Bopsz%2Cwght%5D.ttf.png")
        assert url.endswith("?style=Bold")
        assert sample_url("A.ttf", "") == "/font-sample/A.ttf.png"


def test_arbitrary_styles_share_one_cached_file(tmp_path):
    from fontmatch.samples import SampleStore

    store = SampleStore(tmp_path, lambda name: FIXTURES / "DejaVuSans.ttf")
    paths = {store.get("DejaVuSans.ttf", f"Style {i}") for i in range(5)}
    assert len(paths) == 1
    assert len(list(tmp_path.glob("*.png"))) == 1


def test_safe_resolve_rejects_sibling_prefix(tmp_path):
    from fontmatch.web.api import _safe_resolve

    (tmp_path / "corpus").mkdir()
    (tmp_path / "corpus2").mkdir()
    assert _safe_resolve(str(tmp_path / "corpus"), "../corpus2/x.ttf") is None
    assert _safe_resolve(str(tmp_path / "corpus"), "a/x.ttf") == (tmp_path / "corpus/a/x.ttf")


def test_font_without_latin_letters_has_no_sample(tmp_path):
    """A subset font missing the sample letters must not render tofu boxes."""
    from fontTools import subset
    from fontTools.ttLib import TTFont

    from fontmatch.samples import render_sample

    font = TTFont(FIXTURES / "DejaVuSans.ttf")
    sub = subset.Subsetter()
    sub.populate(text="0123456789")
    sub.subset(font)
    path = tmp_path / "digits.ttf"
    font.save(path)
    with pytest.raises(ValueError):
        render_sample(path)


def test_unrenderable_font_is_not_retried(tmp_path):
    from fontmatch.samples import SampleStore

    calls = []

    def resolve(name):
        calls.append(name)
        return FIXTURES / "corrupt.ttf"

    store = SampleStore(tmp_path, resolve)
    assert store.get("corrupt.ttf", "") is None
    assert store.get("corrupt.ttf", "") is None
    assert calls == ["corrupt.ttf"]


def test_disk_failure_returns_none_not_500(tmp_path, monkeypatch):
    from fontmatch.samples import SampleStore

    store = SampleStore(tmp_path / "s", lambda name: FIXTURES / "DejaVuSans.ttf")

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(store, "save", boom)
    assert store.get("DejaVuSans.ttf", "") is None
    assert not list((tmp_path / "s").glob("*.tmp"))


def test_relative_samples_dir_is_resolved(tmp_path, monkeypatch):
    """send_file() resolves relative paths against the package dir, not the CWD."""
    from fontmatch.service.app import create_app

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FONTMATCH_SAMPLES_DIR", "rel_samples")
    app = create_app(db_path=tmp_path / "t.db", fixture_dir=FIXTURES, testing=True)
    assert app.config["SAMPLES_DIR"] == tmp_path / "rel_samples"
    assert app.test_client().get("/font-sample/DejaVuSans.ttf.png").status_code == 200
