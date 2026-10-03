"""Image matcher: rank faces by how well they reproduce the query's text."""

import random
from pathlib import Path

import pytest

from fontmatch.image.glyphs import AtlasSource, GlyphAtlas, build_atlas
from fontmatch.image.prep import ink_map
from fontmatch.image.rank import ImageMatcher, line_features
from fontmatch.image.synth import degrade, render_text_image

FIXTURES = Path(__file__).parent / "fixtures"
FONTS = [
    "Roboto-Regular.ttf",
    "Roboto-Bold.ttf",
    "Tinos-Regular.ttf",
    "Cousine-Regular.ttf",
    "Arimo-Regular.ttf",
    "DejaVuSerif.ttf",
    "DejaVuSans.ttf",
    "Caladea-Regular.ttf",
    "Carlito-Regular.ttf",
]


@pytest.fixture(scope="module")
def matcher(tmp_path_factory):
    out = tmp_path_factory.mktemp("atlas")
    build_atlas([AtlasSource(FIXTURES / f, f) for f in FONTS], out, workers=1)
    families = {f: f.split("-")[0].replace(".ttf", "").lower() for f in FONTS}
    return ImageMatcher(GlyphAtlas.load(out), family_of=families)


def test_normalization_invariant(matcher):
    """A render of font F, run through the query pipeline, must look like the
    atlas's own typesetting of F (same features up to resampling noise)."""
    text = "Quick Hamburg"
    face = next(i for i, f in enumerate(matcher.atlas.faces) if f["key"] == "Tinos-Regular.ttf")
    composed, _ = matcher.atlas.compose(face, text)
    q = line_features(ink_map(render_text_image(FIXTURES / "Tinos-Regular.ttf", text, size_px=48)))
    c = line_features(composed / 255.0)
    assert abs(q.aspect - c.aspect) / c.aspect < 0.03
    assert q.correlate(c) > 0.9


@pytest.mark.parametrize(
    "font",
    ["Roboto-Regular.ttf", "Tinos-Regular.ttf", "Cousine-Regular.ttf", "Caladea-Regular.ttf"],
)
def test_clean_render_ranks_own_face_first(matcher, font):
    img = render_text_image(FIXTURES / font, "Sunrise Bakery", size_px=48)
    matches = matcher.rank(img, text="Sunrise Bakery", k=3)
    assert matches[0].key == font


def test_weight_is_distinguished(matcher):
    img = render_text_image(FIXTURES / "Roboto-Bold.ttf", "Sunrise Bakery", size_px=48)
    matches = matcher.rank(img, text="Sunrise Bakery", k=3, dedupe=False)
    assert matches[0].key == "Roboto-Bold.ttf"


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_screenshot_tier_keeps_own_family_in_top3(matcher, seed):
    img = degrade(
        render_text_image(FIXTURES / "Tinos-Regular.ttf", "Harbor View Hotel", 36),
        "screenshot",
        random.Random(seed),
    )
    fams = [m.family for m in matcher.rank(img, text="Harbor View Hotel", k=3)]
    assert "tinos" in fams


def test_dedupes_by_family(matcher):
    img = render_text_image(FIXTURES / "Roboto-Regular.ttf", "Sunrise Bakery", size_px=48)
    fams = [m.family for m in matcher.rank(img, text="Sunrise Bakery", k=5)]
    assert len(fams) == len(set(fams))


def test_blank_image_returns_nothing(matcher):
    from PIL import Image

    assert matcher.rank(Image.new("RGB", (100, 40), "white"), text="Hello", k=3) == []


def test_empty_text_returns_nothing(matcher):
    img = render_text_image(FIXTURES / "Roboto-Regular.ttf", "Sunrise", size_px=48)
    assert matcher.rank(img, text="   ", k=3) == []
