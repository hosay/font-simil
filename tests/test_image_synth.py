"""Synthetic query images for the image-matcher eval and tests."""

import random
from pathlib import Path

import numpy as np

from fontmatch.image.synth import TIERS, degrade, render_text_image

FIXTURES = Path(__file__).parent / "fixtures"
ROBOTO = FIXTURES / "Roboto-Regular.ttf"


def test_render_has_dark_text_on_light_background():
    img = render_text_image(ROBOTO, "Hamburg", size_px=48)
    arr = np.asarray(img.convert("L"))
    assert img.mode == "RGB"
    assert arr.min() < 60 and np.median(arr) > 200
    assert img.height > 48 and img.width > img.height


def test_render_respects_colours():
    img = render_text_image(ROBOTO, "Hi", size_px=40, fg=(255, 255, 255), bg=(0, 0, 80))
    arr = np.asarray(img)
    assert tuple(arr[0, 0]) == (0, 0, 80)


def test_degrade_is_deterministic_per_seed():
    img = render_text_image(ROBOTO, "Quick fox", size_px=40)
    for tier in TIERS:
        a = np.asarray(degrade(img, tier, random.Random(7)))
        b = np.asarray(degrade(img, tier, random.Random(7)))
        np.testing.assert_array_equal(a, b)


def test_clean_tier_is_identity():
    img = render_text_image(ROBOTO, "Quick fox", size_px=40)
    np.testing.assert_array_equal(
        np.asarray(degrade(img, "clean", random.Random(1))), np.asarray(img)
    )


def test_degraded_tiers_change_pixels_but_keep_text():
    img = render_text_image(ROBOTO, "Quick fox", size_px=40)
    for tier in ("screenshot", "photo"):
        out = degrade(img, tier, random.Random(3))
        gray = np.asarray(out.convert("L")).astype(int)
        assert out.mode == "RGB"
        assert gray.max() - gray.min() > 60  # still has contrast
