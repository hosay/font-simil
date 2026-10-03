"""Regression tests for the Phase 1 review findings."""

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from fontmatch.image.glyphs import AtlasSource, GlyphAtlas, build_atlas
from fontmatch.image.prep import ink_map
from fontmatch.image.rank import ImageMatcher
from fontmatch.image.synth import render_text_image

FIXTURES = Path(__file__).parent / "fixtures"
FONTS = ["Roboto-Regular.ttf", "Tinos-Regular.ttf", "Cousine-Regular.ttf"]


@pytest.fixture(scope="module")
def matcher(tmp_path_factory):
    out = tmp_path_factory.mktemp("atlas")
    build_atlas([AtlasSource(FIXTURES / f, f) for f in FONTS], out, workers=1)
    return ImageMatcher(GlyphAtlas.load(out), {f: f.split("-")[0].lower() for f in FONTS})


@pytest.mark.parametrize("text", ["I", "!", "1", "Il"])
def test_narrow_text_does_not_crash(matcher, text):
    img = render_text_image(FIXTURES / "Roboto-Regular.ttf", text, 48)
    matcher.rank(img, text=text, k=3)  # must not raise (skimage hog needs >= 16 px)


def test_unsupported_characters_fall_back(matcher):
    """'Škoda →' : Š -> S via NFKD; the arrow is missing everywhere but must
    not filter out every face."""
    img = render_text_image(FIXTURES / "Tinos-Regular.ttf", "Skoda", 48)
    matches = matcher.rank(img, text="Škoda →", k=3)
    assert matches and matches[0].key == "Tinos-Regular.ttf"


def test_missing_glyph_still_advances_pen(matcher):
    face = 0
    with_gap, _ = matcher.atlas.compose(face, "ab→cd")
    without, _ = matcher.atlas.compose(face, "abcd")
    assert with_gap.shape[1] > without.shape[1]


def test_deskew_leaves_short_straight_words_alone():
    from fontmatch.image.prep import estimate_skew

    for word in ("Hi", "OK", "Go"):
        m = ink_map(render_text_image(FIXTURES / "Roboto-Regular.ttf", word, 48)) > 0.5
        assert estimate_skew(m) == 0.0


def test_display_scores_follow_ranking(tmp_path):
    from fontmatch.image.catalog import CatalogEntry
    from fontmatch.image.service import ImageIdentifier

    build_atlas([AtlasSource(FIXTURES / f, f) for f in FONTS], tmp_path, workers=1)
    entries = [
        CatalogEntry(f, f.split("-")[0], f.split("-")[0].lower(), "Regular", FIXTURES / f,
                     "OFL-1.1", "sans", False)
        for f in FONTS
    ]  # fmt: skip
    ident = ImageIdentifier(GlyphAtlas.load(tmp_path), entries)
    import io

    buf = io.BytesIO()
    render_text_image(FIXTURES / "Tinos-Regular.ttf", "Harbor View Hotel", 48).save(buf, "PNG")
    out = ident.identify(buf.getvalue(), hint="Harbor View Hotel", k=3)
    scores = [m["score"] for m in out["matches"]]
    assert scores == sorted(scores, reverse=True)
    assert out["matches"][0]["match_label"] in ("likely the same font", "similar alternative")


# --- locate-level fixes -----------------------------------------------------------


def test_long_line_cropped_to_word_window(monkeypatch):
    import fontmatch.image.locate as loc
    from fontmatch.image.locate import Line

    words = "The quick brown fox jumps over the lazy dog again and again today".split()
    boxes = []
    x = 10
    for w in words:
        boxes.append(((x, 10, x + 20 * len(w), 40), w))
        x += 20 * len(w) + 10
    line = Line(box=(10, 10, x, 40), text=" ".join(words), conf=90.0, words=tuple(boxes))
    monkeypatch.setattr(loc, "find_lines", lambda im: [line])
    img = Image.new("RGB", (x + 20, 60), "white")
    result = loc.locate(img, hint=" ".join(words))
    assert len(result.transcript) <= loc.MAX_CHARS
    assert words[0] in result.transcript and result.transcript.startswith("The quick")
    n = len(result.transcript.split())
    assert result.box[2] == boxes[n - 1][0][2]  # crop ends at the last kept word


def test_hint_quotes_are_stripped(monkeypatch):
    import fontmatch.image.locate as loc

    monkeypatch.setattr(loc, "find_lines", lambda im: [])
    img = render_text_image(FIXTURES / "Roboto-Regular.ttf", "Hello", 48)
    assert loc.locate(img, hint="“Hello”").transcript == "Hello"


def test_partial_match_selects_line():
    from fontmatch.image.locate import Line, choose_line

    lines = [
        Line(box=(0, 0, 100, 30), text="Welcome to the Grand Hotel Budapest", conf=90.0),
        Line(box=(0, 40, 100, 200), text="Book now", conf=90.0),  # most prominent
    ]
    assert choose_line(lines, hint="Grand Hotel").text.startswith("Welcome")


def test_neighbouring_ink_outside_ocr_box_is_ignored():
    from fontmatch.image.prep import keep_components_touching

    soft = np.zeros((60, 200), dtype=np.float32)
    soft[20:40, 20:180] = 1.0  # the line
    soft[0:5, 50:60] = 1.0  # descender of the line above
    soft[55:60, 0:200] = 1.0  # underline / rule below
    kept = keep_components_touching(soft, (20, 20, 180, 40))
    assert kept[0:5].sum() == 0 and kept[55:60].sum() == 0
    assert kept[20:40, 20:180].min() == 1.0


def test_case_mismatch_between_hint_and_image(matcher):
    img = render_text_image(FIXTURES / "Tinos-Regular.ttf", "WELCOME HOME", 48)
    text = matcher.best_casing(ink_map(img), ["Welcome Home"])
    assert text == "WELCOME HOME"
    text = matcher.best_casing(ink_map(img.copy()), ["WELCOME HOME"])
    assert text == "WELCOME HOME"
    mixed = render_text_image(FIXTURES / "Tinos-Regular.ttf", "Welcome Home", 48)
    assert matcher.best_casing(ink_map(mixed), ["Welcome Home"]) == "Welcome Home"


def test_mcp_result_carries_match_label():
    from fontmatch.mcp_server.server import _font_match

    m = _font_match({"family": "Tinos", "score": 90, "match_label": "likely the same font"})
    assert m.match == "likely the same font"
