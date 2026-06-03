"""Phase 4 — Glyph rendering & perceptual features tests."""

from pathlib import Path

import numpy as np

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str):
    from fontmatch.fonts import load

    return load(FIXTURES / name)


class TestRenderGlyphs:
    """Test multi-glyph rendering pipeline."""

    def test_render_glyphs_returns_dict(self):
        """render_glyphs returns a dict mapping characters to grayscale images."""
        from fontmatch.features.perceptual import render_glyphs

        font = _load("Roboto-Regular.ttf")
        glyphs = render_glyphs(font)
        assert isinstance(glyphs, dict)
        assert len(glyphs) > 0

    def test_render_glyphs_has_diagnostic_chars(self):
        """Renders the key diagnostic characters."""
        from fontmatch.features.perceptual import DIAGNOSTIC_CHARS, render_glyphs

        font = _load("Roboto-Regular.ttf")
        glyphs = render_glyphs(font)
        # Should have most of the diagnostic chars
        assert len(glyphs) >= len(DIAGNOSTIC_CHARS) * 0.8

    def test_glyph_images_are_square_grayscale(self):
        """Each glyph image is a square grayscale ndarray."""
        from fontmatch.features.perceptual import GLYPH_SIZE, render_glyphs

        font = _load("Roboto-Regular.ttf")
        glyphs = render_glyphs(font)
        for char, img in glyphs.items():
            assert isinstance(img, np.ndarray), f"Glyph '{char}' not ndarray"
            assert img.ndim == 2, f"Glyph '{char}' not 2D"
            assert img.shape == (GLYPH_SIZE, GLYPH_SIZE), (
                f"Glyph '{char}' shape {img.shape} != ({GLYPH_SIZE}, {GLYPH_SIZE})"
            )
            assert img.dtype == np.uint8

    def test_glyph_images_have_content(self):
        """Rendered glyphs are not blank."""
        from fontmatch.features.perceptual import render_glyphs

        font = _load("Roboto-Regular.ttf")
        glyphs = render_glyphs(font)
        blank_count = sum(1 for img in glyphs.values() if img.max() == 0)
        assert blank_count < len(glyphs) * 0.2, (
            f"Too many blank glyphs: {blank_count}/{len(glyphs)}"
        )


class TestComposeSheet:
    """Test glyph sheet composition."""

    def test_compose_sheet_returns_image(self):
        """compose_sheet returns a PIL-compatible grayscale image as ndarray."""
        from fontmatch.features.perceptual import compose_sheet, render_glyphs

        font = _load("Roboto-Regular.ttf")
        glyphs = render_glyphs(font)
        sheet = compose_sheet(glyphs)
        assert isinstance(sheet, np.ndarray)
        assert sheet.ndim == 2
        assert sheet.dtype == np.uint8
        assert sheet.max() > 0

    def test_compose_sheet_deterministic(self):
        """Same font produces identical sheets."""
        from fontmatch.features.perceptual import compose_sheet, render_glyphs

        font = _load("Roboto-Regular.ttf")
        sheet1 = compose_sheet(render_glyphs(font))
        sheet2 = compose_sheet(render_glyphs(font))
        np.testing.assert_array_equal(sheet1, sheet2)


class TestRenderDeterminism:
    def test_same_font_identical_vector(self):
        """Rendering the same font twice yields identical perceptual vector."""
        from fontmatch.features.perceptual import perceptual, render_glyphs, compose_sheet

        font = _load("Roboto-Regular.ttf")
        sheet1 = compose_sheet(render_glyphs(font))
        sheet2 = compose_sheet(render_glyphs(font))
        np.testing.assert_array_equal(sheet1, sheet2)

        vec1 = perceptual(sheet1)
        vec2 = perceptual(sheet2)
        np.testing.assert_array_equal(vec1, vec2)


class TestRelativeOrdering:
    def test_bold_regular_closer_than_unrelated(self):
        """Bold and Regular of the same family are closer than two unrelated families."""
        from fontmatch.features.perceptual import perceptual, render_glyphs, compose_sheet
        from fontmatch.match.scorer import _cosine_distance

        reg = perceptual(compose_sheet(render_glyphs(_load("Roboto-Regular.ttf"))))
        bold = perceptual(compose_sheet(render_glyphs(_load("Roboto-Bold.ttf"))))
        serif = perceptual(compose_sheet(render_glyphs(_load("Tinos-Regular.ttf"))))

        d_same_family = _cosine_distance(reg, bold)
        d_different = _cosine_distance(reg, serif)
        assert d_same_family < d_different, (
            f"Same-family distance {d_same_family:.4f} should be < "
            f"cross-family distance {d_different:.4f}"
        )


class TestRenderOutput:
    def test_render_returns_2d_grayscale(self):
        from fontmatch.features.perceptual import render_glyphs, compose_sheet

        sheet = compose_sheet(render_glyphs(_load("Roboto-Regular.ttf")))
        assert isinstance(sheet, np.ndarray)
        assert sheet.ndim == 2  # grayscale
        assert sheet.dtype == np.uint8

    def test_render_has_content(self):
        """The rendered image isn't blank."""
        from fontmatch.features.perceptual import render_glyphs, compose_sheet

        sheet = compose_sheet(render_glyphs(_load("Roboto-Regular.ttf")))
        assert sheet.max() > 0

    def test_perceptual_vector_fixed_length(self):
        from fontmatch.features.perceptual import perceptual, render_glyphs, compose_sheet

        v1 = perceptual(compose_sheet(render_glyphs(_load("Roboto-Regular.ttf"))))
        v2 = perceptual(compose_sheet(render_glyphs(_load("Tinos-Regular.ttf"))))
        assert v1.shape == v2.shape
        assert v1.dtype == np.float64
        assert len(v1) > 0

    def test_perceptual_vector_is_clip_sized(self):
        """CLIP ViT-B/32 produces 512-dim embeddings."""
        from fontmatch.features.perceptual import perceptual, render_glyphs, compose_sheet

        v = perceptual(compose_sheet(render_glyphs(_load("Roboto-Regular.ttf"))))
        assert v.shape == (512,)


class TestMissingGlyph:
    def test_graceful_with_limited_glyph_set(self):
        """Font with limited glyphs still produces a valid render and vector."""
        from fontmatch.features.perceptual import perceptual, render_glyphs, compose_sheet

        font = _load("Cousine-Regular.ttf")
        glyphs = render_glyphs(font)
        sheet = compose_sheet(glyphs)
        vec = perceptual(sheet)
        assert sheet.max() > 0
        assert len(vec) > 0


# Legacy compatibility: keep the render() function working for backward compat
class TestLegacyRender:
    def test_render_still_works(self):
        """The old render() function still returns a 2D grayscale image."""
        from fontmatch.features.perceptual import render

        font = _load("Roboto-Regular.ttf")
        img = render(font)
        assert isinstance(img, np.ndarray)
        assert img.ndim == 2
        assert img.dtype == np.uint8
        assert img.max() > 0
