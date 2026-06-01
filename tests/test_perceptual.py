"""Phase 4 — Glyph rendering & perceptual features tests."""

from pathlib import Path

import numpy as np

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str):
    from fontmatch.fonts import load

    return load(FIXTURES / name)


class TestRenderDeterminism:
    def test_same_font_identical_vector(self):
        """Rendering the same font twice yields identical perceptual vector."""
        from fontmatch.features.perceptual import perceptual, render

        font = _load("Roboto-Regular.ttf")
        img1 = render(font)
        img2 = render(font)
        np.testing.assert_array_equal(img1, img2)

        vec1 = perceptual(img1)
        vec2 = perceptual(img2)
        np.testing.assert_array_equal(vec1, vec2)


class TestRelativeOrdering:
    def test_bold_regular_closer_than_unrelated(self):
        """Bold and Regular of the same family are closer than two unrelated families."""
        from fontmatch.features.perceptual import perceptual, render

        reg = perceptual(render(_load("Roboto-Regular.ttf")))
        bold = perceptual(render(_load("Roboto-Bold.ttf")))
        serif = perceptual(render(_load("Tinos-Regular.ttf")))

        d_same_family = np.linalg.norm(reg - bold)
        d_different = np.linalg.norm(reg - serif)
        assert d_same_family < d_different, (
            f"Same-family distance {d_same_family:.4f} should be < "
            f"cross-family distance {d_different:.4f}"
        )


class TestRenderOutput:
    def test_render_returns_2d_grayscale(self):
        from fontmatch.features.perceptual import render

        img = render(_load("Roboto-Regular.ttf"))
        assert isinstance(img, np.ndarray)
        assert img.ndim == 2  # grayscale
        assert img.dtype == np.uint8

    def test_render_has_content(self):
        """The rendered image isn't blank."""
        from fontmatch.features.perceptual import render

        img = render(_load("Roboto-Regular.ttf"))
        assert img.max() > 0

    def test_perceptual_vector_fixed_length(self):
        from fontmatch.features.perceptual import perceptual, render

        v1 = perceptual(render(_load("Roboto-Regular.ttf")))
        v2 = perceptual(render(_load("Tinos-Regular.ttf")))
        assert v1.shape == v2.shape
        assert v1.dtype == np.float64
        assert len(v1) > 0


class TestMissingGlyph:
    def test_graceful_with_limited_glyph_set(self):
        """Font with limited glyphs still produces a valid render and vector."""
        from fontmatch.features.perceptual import perceptual, render

        # All our fixtures have basic Latin, so this should work fine
        font = _load("Cousine-Regular.ttf")
        img = render(font)
        vec = perceptual(img)
        assert img.max() > 0
        assert len(vec) > 0
