"""Image loading guards and text-mask extraction."""

import io
import random
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from fontmatch.image.prep import ImageError, crop_to_ink, load_image, text_mask
from fontmatch.image.synth import degrade, render_text_image

ROBOTO = Path(__file__).parent / "fixtures" / "Roboto-Regular.ttf"


def _encode(img, fmt, **kw):
    buf = io.BytesIO()
    img.save(buf, format=fmt, **kw)
    return buf.getvalue()


class TestLoadImage:
    @pytest.mark.parametrize("fmt", ["PNG", "JPEG", "WEBP"])
    def test_accepts_common_formats(self, fmt):
        img = load_image(_encode(Image.new("RGB", (30, 20), "white"), fmt))
        assert img.mode == "RGB" and img.size == (30, 20)

    def test_rejects_other_formats(self):
        with pytest.raises(ImageError, match="PNG, JPEG or WebP"):
            load_image(_encode(Image.new("RGB", (30, 20)), "BMP"))

    def test_rejects_garbage(self):
        with pytest.raises(ImageError):
            load_image(b"definitely not an image")

    def test_rejects_decompression_bomb(self):
        # Tiny file, huge declared canvas.
        data = _encode(Image.new("1", (12000, 12000)), "PNG")
        with pytest.raises(ImageError, match="too large"):
            load_image(data)

    def test_downscales_long_edge(self):
        img = load_image(_encode(Image.new("RGB", (4000, 1000), "white"), "PNG"))
        assert max(img.size) == 2000 and img.size[1] == 500

    def test_applies_exif_rotation(self):
        img = Image.new("RGB", (40, 20), "white")
        exif = Image.Exif()
        exif[0x0112] = 6  # rotate 90 CW on display
        out = load_image(_encode(img, "JPEG", exif=exif.tobytes()))
        assert out.size == (20, 40)

    def test_flattens_transparency_onto_white(self):
        img = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
        out = load_image(_encode(img, "PNG"))
        assert out.getpixel((5, 5)) == (255, 255, 255)


def _transparent_text(fg, size_px=48, text="Hamburg"):
    """Text in colour ``fg`` on a fully transparent canvas (anti-aliased alpha)."""
    from PIL import ImageDraw, ImageFont

    face = ImageFont.truetype(str(ROBOTO), size_px)
    left, top, right, bottom = face.getbbox(text)
    alpha = Image.new("L", (right - left + 40, bottom - top + 40), 0)
    ImageDraw.Draw(alpha).text((20 - left, 20 - top), text, font=face, fill=255)
    img = Image.new("RGBA", alpha.size, fg + (0,))
    img.putalpha(alpha)
    return img


class TestTransparentBackground:
    """Transparent PNGs (logo files) are flattened onto whichever of white or
    black contrasts with the text, so white text isn't lost on white."""

    @pytest.mark.parametrize(
        "fg", [(255, 255, 255), (250, 230, 60), (0, 0, 0), (66, 133, 244), (128, 128, 128)]
    )
    def test_text_survives_flattening(self, fg):
        out = load_image(_encode(_transparent_text(fg), "PNG"))
        mask = text_mask(out)
        assert 0.03 < mask.mean() < 0.5  # letters, not nothing and not everything
        assert mask[0].sum() == 0  # the transparent margin is background

    def test_white_text_goes_on_black(self):
        out = load_image(_encode(_transparent_text((255, 255, 255)), "PNG"))
        assert out.getpixel((0, 0)) == (0, 0, 0)

    def test_dark_text_stays_on_white(self):
        out = load_image(_encode(_transparent_text((20, 20, 20)), "PNG"))
        assert out.getpixel((0, 0)) == (255, 255, 255)

    def test_la_and_palette_modes(self):
        white = _transparent_text((255, 255, 255))
        la = white.convert("LA")
        assert load_image(_encode(la, "PNG")).getpixel((0, 0)) == (0, 0, 0)
        pal = white.convert("RGBA").quantize(colors=16, method=Image.Quantize.FASTOCTREE)
        out = load_image(_encode(pal, "PNG"))
        assert text_mask(out).mean() > 0.03

    def test_dark_text_on_white_badge_stays_on_white(self):
        # A white pill with black text on a transparent canvas: the plate must
        # merge into the background, not become one big "letter" on black.
        text = _transparent_text((0, 0, 0))
        w, h = text.size
        canvas = Image.new("RGBA", (w + 200, h + 120), (0, 0, 0, 0))
        plate = Image.new("RGBA", (w + 20, h + 10), (255, 255, 255, 255))
        plate.alpha_composite(text, (10, 5))
        canvas.alpha_composite(plate, (90, 55))
        out = load_image(_encode(canvas, "PNG"))
        assert out.getpixel((0, 0)) == (255, 255, 255)

    def test_opaque_rgba_unchanged(self):
        img = Image.new("RGBA", (20, 10), (255, 255, 255, 255))
        img.putpixel((5, 5), (0, 0, 0, 255))
        out = load_image(_encode(img, "PNG"))
        assert out.getpixel((0, 0)) == (255, 255, 255) and out.getpixel((5, 5)) == (0, 0, 0)


class TestTextMask:
    def test_dark_on_light(self):
        img = render_text_image(ROBOTO, "Hamburg", size_px=48)
        mask = text_mask(img)
        assert mask.dtype == bool
        assert 0.03 < mask.mean() < 0.4  # text is the minority
        assert not mask[0, :].any()  # border is background

    def test_light_on_dark_gives_same_mask(self):
        dark = render_text_image(ROBOTO, "Hamburg", size_px=48)
        light = render_text_image(ROBOTO, "Hamburg", size_px=48, fg=(255, 255, 255), bg=(0, 0, 0))
        a, b = text_mask(dark), text_mask(light)
        assert (a == b).mean() > 0.99

    def test_coloured_jpeg_still_recovers_text(self):
        clean = text_mask(render_text_image(ROBOTO, "Hamburg", size_px=48))
        noisy_img = degrade(
            render_text_image(ROBOTO, "Hamburg", size_px=48), "screenshot", random.Random(2)
        )
        noisy = text_mask(noisy_img)
        assert abs(noisy.mean() - clean.mean()) < 0.05

    def test_crop_to_ink(self):
        mask = np.zeros((50, 80), dtype=bool)
        mask[10:20, 30:45] = True
        cropped = crop_to_ink(mask)
        assert cropped.shape == (10, 15) and cropped.all()

    def test_crop_to_ink_empty(self):
        assert crop_to_ink(np.zeros((5, 5), dtype=bool)).size == 0


class TestDeskew:
    @pytest.mark.parametrize("angle", [-4.0, -2.0, 3.0, 5.0])
    def test_rotated_line_is_straightened(self, angle):
        from fontmatch.image.prep import deskew, estimate_skew

        img = render_text_image(ROBOTO, "Harbor View Hotel", size_px=48)
        rotated = img.rotate(
            angle, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=(255, 255, 255)
        )
        mask = text_mask(rotated)
        assert estimate_skew(mask) == pytest.approx(angle, abs=0.75)
        straight = crop_to_ink(deskew(mask))
        reference = crop_to_ink(text_mask(img))
        assert straight.shape[0] == pytest.approx(reference.shape[0], rel=0.12)

    def test_straight_line_untouched(self):
        from fontmatch.image.prep import estimate_skew

        mask = text_mask(render_text_image(ROBOTO, "Harbor View Hotel", size_px=48))
        assert abs(estimate_skew(mask)) <= 0.5


class TestInkMap:
    def test_range_and_polarity(self):
        from fontmatch.image.prep import ink_map

        dark = ink_map(render_text_image(ROBOTO, "Hamburg", size_px=48))
        light = ink_map(
            render_text_image(ROBOTO, "Hamburg", size_px=48, fg=(250, 250, 250), bg=(20, 20, 60))
        )
        for m in (dark, light):
            assert m.dtype == np.float32
            assert m.min() >= 0.0 and m.max() <= 1.0
            assert m[0, 0] < 0.05  # background ~0
        assert np.abs(dark - light).mean() < 0.02

    def test_keeps_antialiasing(self):
        from fontmatch.image.prep import ink_map

        m = ink_map(render_text_image(ROBOTO, "Hamburg", size_px=48))
        partial = ((m > 0.1) & (m < 0.9)).sum()
        assert partial > 0.1 * (m > 0.5).sum()


class TestPrepReviewFixes:
    def test_corrupt_exif_is_an_image_error_not_a_crash(self, monkeypatch):
        import fontmatch.image.prep as prep

        def boom(img):
            raise ValueError("bad exif")

        monkeypatch.setattr(prep.ImageOps, "exif_transpose", boom)
        out = prep.load_image(_encode(Image.new("RGB", (30, 20), "white"), "JPEG"))
        assert out.size == (30, 20)  # orientation skipped, image still usable

    def test_large_rgba_downscaled(self):
        img = Image.new("RGBA", (5000, 2000), (0, 0, 0, 0))
        out = load_image(_encode(img, "PNG"))
        assert max(out.size) == 2000 and out.mode == "RGB"


def test_next_line_letters_overlapping_the_ocr_box_are_dropped():
    """Tesseract's line box reaches into the tops of the next line's capitals
    (r/identifythisfont jq81wk: Palatino scored as display fonts)."""
    from fontmatch.image.prep import keep_components_touching

    soft = np.zeros((100, 200), dtype=np.float32)
    soft[20:40, 20:60] = 1.0  # a letter of the line
    soft[20:52, 80:90] = 1.0  # a descender ("y") of the line, below the box
    soft[45:80, 120:140] = 1.0  # next line's capital, its top inside the box
    soft[5:24, 150:160] = 1.0  # line above's descender, its tail inside the box
    kept = keep_components_touching(soft, (10, 18, 190, 48))
    assert kept[20:40, 20:60].min() == 1.0
    assert kept[20:52, 80:90].min() == 1.0
    assert kept[45:80, 120:140].sum() == 0
    assert kept[5:24, 150:160].sum() == 0



def test_component_rule_falls_back_to_overlap_when_nothing_is_centred():
    """A script word joined to a long swash below the box must not vanish."""
    from fontmatch.image.prep import keep_components_touching

    soft = np.zeros((100, 200), dtype=np.float32)
    soft[30:45, 20:180] = 1.0  # the word
    soft[45:95, 170:180] = 1.0  # its swash, same component, far below the box
    kept = keep_components_touching(soft, (10, 28, 190, 46))
    assert kept[30:45, 20:180].min() == 1.0
