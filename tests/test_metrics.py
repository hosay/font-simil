"""Phase 3 — Metric feature extraction tests."""

from pathlib import Path

import numpy as np

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str):
    from fontmatch.fonts import load

    return load(FIXTURES / name)


class TestMetricExtraction:
    def test_units_per_em(self):
        from fontmatch.features.metrics import metrics

        vec = metrics(_load("Roboto-Regular.ttf"))
        assert vec.units_per_em == 2048

    def test_weight_class(self):
        from fontmatch.features.metrics import metrics

        regular = metrics(_load("Roboto-Regular.ttf"))
        bold = metrics(_load("Roboto-Bold.ttf"))
        assert regular.weight_class == 400
        assert bold.weight_class == 700

    def test_width_class(self):
        from fontmatch.features.metrics import metrics

        vec = metrics(_load("Roboto-Regular.ttf"))
        assert vec.width_class == 5

    def test_italic_flag(self):
        from fontmatch.features.metrics import metrics

        vec = metrics(_load("Roboto-Regular.ttf"))
        assert vec.is_italic is False

    def test_italic_angle(self):
        from fontmatch.features.metrics import metrics

        vec = metrics(_load("Roboto-Regular.ttf"))
        assert vec.italic_angle == 0.0

    def test_cap_height_normalized(self):
        from fontmatch.features.metrics import metrics

        vec = metrics(_load("Roboto-Regular.ttf"))
        # 1456 / 2048 = 0.7109375
        assert abs(vec.cap_height - 1456 / 2048) < 0.001

    def test_x_height_normalized(self):
        from fontmatch.features.metrics import metrics

        vec = metrics(_load("Roboto-Regular.ttf"))
        # 1082 / 2048 = 0.52832...
        assert abs(vec.x_height - 1082 / 2048) < 0.001

    def test_ascender_normalized(self):
        from fontmatch.features.metrics import metrics

        vec = metrics(_load("Roboto-Regular.ttf"))
        assert abs(vec.ascender - 2146 / 2048) < 0.001

    def test_descender_normalized(self):
        from fontmatch.features.metrics import metrics

        vec = metrics(_load("Roboto-Regular.ttf"))
        assert abs(vec.descender - (-555 / 2048)) < 0.001

    def test_avg_width_normalized(self):
        from fontmatch.features.metrics import metrics

        vec = metrics(_load("Roboto-Regular.ttf"))
        assert abs(vec.avg_width - 1158 / 2048) < 0.001

    def test_glyph_count(self):
        from fontmatch.features.metrics import metrics

        vec = metrics(_load("Roboto-Regular.ttf"))
        assert vec.glyph_count == 3387


class TestSerifClassification:
    def test_tinos_is_serif(self):
        from fontmatch.features.metrics import metrics

        vec = metrics(_load("Tinos-Regular.ttf"))
        assert vec.serif_class == "serif"

    def test_roboto_is_sans(self):
        from fontmatch.features.metrics import metrics

        vec = metrics(_load("Roboto-Regular.ttf"))
        assert vec.serif_class == "sans"

    def test_arimo_is_sans(self):
        from fontmatch.features.metrics import metrics

        vec = metrics(_load("Arimo-Regular.ttf"))
        assert vec.serif_class == "sans"

    def test_cousine_is_mono(self):
        from fontmatch.features.metrics import metrics

        vec = metrics(_load("Cousine-Regular.ttf"))
        assert vec.serif_class == "mono"


class TestMetricVector:
    def test_to_array_is_numpy(self):
        from fontmatch.features.metrics import metrics

        vec = metrics(_load("Roboto-Regular.ttf"))
        arr = vec.to_array()
        assert isinstance(arr, np.ndarray)
        assert arr.dtype == np.float64

    def test_to_array_fixed_length(self):
        from fontmatch.features.metrics import metrics

        v1 = metrics(_load("Roboto-Regular.ttf")).to_array()
        v2 = metrics(_load("Tinos-Regular.ttf")).to_array()
        assert len(v1) == len(v2)
        assert len(v1) > 0

    def test_reproducible(self):
        from fontmatch.features.metrics import metrics

        v1 = metrics(_load("Roboto-Regular.ttf")).to_array()
        v2 = metrics(_load("Roboto-Regular.ttf")).to_array()
        np.testing.assert_array_equal(v1, v2)
