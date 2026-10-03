"""Glyph atlas: build, load, compose lines."""

from pathlib import Path

import numpy as np
import pytest

from fontmatch.image.glyphs import AtlasSource, GlyphAtlas, build_atlas, faces_for
from fontmatch.image.prep import crop_to_ink, text_mask
from fontmatch.image.synth import render_text_image

FIXTURES = Path(__file__).parent / "fixtures"
VF = Path(__file__).parent / "fixtures_variable" / "JosefinSlab[wght].ttf"


@pytest.fixture(scope="module")
def atlas(tmp_path_factory):
    out = tmp_path_factory.mktemp("atlas")
    sources = [
        AtlasSource(path=FIXTURES / "Roboto-Regular.ttf", key="Roboto-Regular.ttf"),
        AtlasSource(path=FIXTURES / "Tinos-Regular.ttf", key="Tinos-Regular.ttf"),
        AtlasSource(path=VF, key="JosefinSlab[wght].ttf"),
    ]
    build_atlas(sources, out, workers=1)
    return GlyphAtlas.load(out)


def _iou(a, b):
    return (a & b).sum() / max(1, (a | b).sum())


def _same_height(a, b):
    from PIL import Image

    h = 40

    def resize(m, w):
        return (
            np.asarray(
                Image.fromarray(m.astype(np.uint8) * 255).resize((w, h), Image.Resampling.BILINEAR)
            )
            > 127
        )

    w = int(round(a.shape[1] * h / a.shape[0]))
    return resize(a, w), resize(b, w)


def test_variable_font_contributes_regular_and_bold(atlas):
    styles = [f["style"] for f in atlas.faces if f["key"] == "JosefinSlab[wght].ttf"]
    assert styles == ["Regular", "Bold"]
    assert [f["key"] for f in atlas.faces].count("Roboto-Regular.ttf") == 1


def test_faces_for_static_font():
    assert faces_for(FIXTURES / "Roboto-Regular.ttf") == [None]


def test_composed_line_matches_direct_render(atlas):
    face = next(i for i, f in enumerate(atlas.faces) if f["key"] == "Roboto-Regular.ttf")
    composed, coverage = atlas.compose(face, "Hamburg fox")
    assert coverage == 1.0
    direct = crop_to_ink(
        text_mask(render_text_image(FIXTURES / "Roboto-Regular.ttf", "Hamburg fox", size_px=48))
    )
    a, b = _same_height(crop_to_ink(composed > 127), direct)
    assert abs(a.shape[1] - b.shape[1]) <= 2
    assert _iou(a, b) > 0.8


def test_bold_instance_is_heavier(atlas):
    idx = {f["style"]: i for i, f in enumerate(atlas.faces) if f["key"] == "JosefinSlab[wght].ttf"}
    reg, _ = atlas.compose(idx["Regular"], "Hamburg")
    bold, _ = atlas.compose(idx["Bold"], "Hamburg")
    assert (bold > 127).mean() > (reg > 127).mean() * 1.15


def test_coverage_counts_only_chars_the_face_lacks(atlas):
    # A char outside the atlas charset (CJK) is skipped for every face: not counted.
    _, coverage = atlas.compose(0, "ab\u4e2d")
    assert coverage == 1.0
    # A charset char this particular face lacks does reduce coverage.
    missing = [c for i, c in enumerate(atlas.charset) if atlas.meta[0, i, 1] < 0]
    if missing:
        _, coverage = atlas.compose(0, "ab" + missing[0])
        assert coverage == pytest.approx(2 / 3)


def test_atlas_reload_is_memory_mapped(atlas):
    assert isinstance(atlas.pixels, np.memmap)
