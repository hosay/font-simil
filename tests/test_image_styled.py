"""Styled text: letter-spacing, per-letter colours, tight crops (logos).

Regression for the Google logo bug (2026-10-03): the matcher compared whole
lines pixel by pixel after stretching each candidate to the query's width, so
any spacing difference pushed glyphs out of phase and a serif (Lusitana) won.
"""

from pathlib import Path

import numpy as np
import pytest

from fontmatch.image.glyphs import AtlasSource, GlyphAtlas, build_atlas
from fontmatch.image.prep import ink_map
from fontmatch.image.rank import ImageMatcher
from fontmatch.image.synth import GOOGLE_COLORS, render_styled

FIXTURES = Path(__file__).parent / "fixtures"
SANS = [
    "Roboto-Regular.ttf",
    "Arimo-Regular.ttf",
    "Montserrat-Regular.ttf",
    "Lato-Regular.ttf",
    "Ubuntu-Regular.ttf",
    "OpenSans-Regular.ttf",
    "DejaVuSans.ttf",
    "PTSans-Regular.ttf",
]
SERIF = [
    "Tinos-Regular.ttf",
    "Lora-Regular.ttf",
    "Merriweather-Regular.ttf",
    "PTSerif-Regular.ttf",
    "DejaVuSerif.ttf",
    "NotoSerif-Regular.ttf",
]


PRODUCTION_ATLAS_FACES = 4622  # faces in the 2026-10-03 atlas (prefilter budget ratio)


def _family(name: str) -> str:
    return name.split("-")[0].replace(".ttf", "").lower()


@pytest.fixture(scope="module")
def atlas(tmp_path_factory):
    out = tmp_path_factory.mktemp("atlas")
    build_atlas([AtlasSource(FIXTURES / f, f) for f in SANS + SERIF], out, workers=1)
    return GlyphAtlas.load(out)


@pytest.fixture(scope="module")
def matcher(atlas):
    return ImageMatcher(atlas, family_of={f: _family(f) for f in SANS + SERIF})


def _face(atlas, key):
    return next(i for i, f in enumerate(atlas.faces) if f["key"] == key)


class TestComposeTracking:
    def test_zero_tracking_is_unchanged(self, atlas):
        face = _face(atlas, "Roboto-Regular.ttf")
        a, _ = atlas.compose(face, "Hamburg")
        b, _ = atlas.compose(face, "Hamburg", tracking=0.0)
        assert np.array_equal(a, b)

    @pytest.mark.parametrize("tracking", [-3.0, 6.0])
    def test_tracking_is_added_between_every_pair(self, atlas, tracking):
        face = _face(atlas, "Roboto-Regular.ttf")
        natural, _ = atlas.compose(face, "Hamburg")
        tracked, _ = atlas.compose(face, "Hamburg", tracking=tracking)
        assert tracked.shape[0] == natural.shape[0]
        assert abs(tracked.shape[1] - (natural.shape[1] + 6 * tracking)) <= 2

    def test_tracking_applies_to_spaces(self, atlas):
        face = _face(atlas, "Roboto-Regular.ttf")
        natural, _ = atlas.compose(face, "Ha mb")
        tracked, _ = atlas.compose(face, "Ha mb", tracking=4.0)
        assert abs(tracked.shape[1] - (natural.shape[1] + 4 * 4.0)) <= 2


class TestColourInk:
    def test_every_letter_of_a_multicolour_word_is_full_ink(self):
        img = render_styled(FIXTURES / "Roboto-Regular.ttf", "Google", 96, colors=GOOGLE_COLORS)
        soft = ink_map(img)
        # Stroke cores of every letter (incl. the yellow 'o') must reach ~1.
        cols = np.flatnonzero((soft > 0.3).any(axis=0))
        gaps = np.flatnonzero(np.diff(cols) > 1)
        starts = np.r_[cols[0], cols[gaps + 1]]
        ends = np.r_[cols[gaps], cols[-1]]
        assert len(starts) >= 5  # letters separated
        for a, b in zip(starts, ends):
            letter = soft[:, a : b + 1]
            assert np.percentile(letter[letter > 0.3], 75) > 0.9

    def test_coloured_box_on_white_page_is_background_not_text(self):
        """Text on a coloured banner inside a white page: the ink map should
        mark the letters, not the banner."""
        from PIL import Image

        text = render_styled(
            FIXTURES / "Roboto-Regular.ttf", "Harbor", 64, colors=[(255, 255, 255)],
            bg=(26, 115, 232), pad=12,
        )  # fmt: skip
        page = Image.new("RGB", (text.width + 8, text.height + 8), (255, 255, 255))
        page.paste(text, (4, 4))
        soft = ink_map(page)
        inner = soft[10:-10, 10:-10]
        assert inner.mean() < 0.5  # letters are the minority of the banner

    def test_greyscale_unchanged_in_spirit(self):
        from fontmatch.image.synth import render_text_image

        dark = ink_map(render_text_image(FIXTURES / "Roboto-Regular.ttf", "Hamburg", 48))
        light = ink_map(
            render_text_image(
                FIXTURES / "Roboto-Regular.ttf", "Hamburg", 48, fg=(250, 250, 250), bg=(20, 20, 60)
            )
        )
        assert np.abs(dark - light).mean() < 0.02


@pytest.mark.parametrize("tracking_em", [-0.05, 0.12, 0.25])
@pytest.mark.parametrize(
    "font", ["Roboto-Regular.ttf", "Montserrat-Regular.ttf", "Lora-Regular.ttf"]
)
def test_letter_spaced_text_ranks_own_face_first(matcher, font, tracking_em):
    img = render_styled(FIXTURES / font, "Harbor View", 56, tracking_em=tracking_em)
    matches = matcher.rank(img, text="Harbor View", k=3)
    assert matches[0].key == font


def test_logo_style_word_ranks_own_face_first(matcher):
    img = render_styled(
        FIXTURES / "Montserrat-Regular.ttf", "Google", 110, tracking_em=-0.04,
        colors=GOOGLE_COLORS, pad=1,
    )  # fmt: skip
    assert matcher.rank(img, text="Google", k=3)[0].key == "Montserrat-Regular.ttf"


def test_logo_in_unknown_sans_gets_sans_alternatives(atlas):
    """The real task: the logo's font isn't in the corpus. Without Montserrat,
    the best alternative must be a sans, not a serif (the Lusitana bug). On
    this 14-font corpus DejaVu Serif (DejaVu Sans with slabs) is a fair
    runner-up; the strict top-5 check runs on the real corpus
    (test_image_styled_corpus.py)."""
    others = {f: _family(f) for f in SANS + SERIF if not f.startswith("Montserrat")}
    matcher = ImageMatcher(atlas, family_of=others)
    img = render_styled(
        FIXTURES / "Montserrat-Regular.ttf", "Google", 110, tracking_em=-0.04,
        colors=GOOGLE_COLORS, pad=1,
    )  # fmt: skip
    top = [m.key for m in matcher.rank(img, text="Google", k=3)]
    assert top[0] in SANS and sum(k in SERIF for k in top) <= 1, top


class TestRowProfiles:
    """Per-glyph row-ink profiles: a letter-spacing-invariant line signature
    used to choose which faces get the (expensive) rendered comparison."""

    def test_profile_mass_matches_glyph_ink(self, atlas):
        from fontmatch.image.glyphs import ROW_BIN, ROW_TOP

        face = _face(atlas, "Roboto-Regular.ttf")
        ci = atlas.char_indices("H")[0]
        off, w, h, x, y = (int(v) for v in atlas.meta[face, ci])
        glyph = atlas._buf[off : off + w * h].reshape(h, w) / 255.0
        prof = atlas.row_profiles(ci)[face].astype(np.float32)
        assert prof.sum() == pytest.approx(glyph.sum(), rel=0.01)
        # bins are rows relative to the baseline, ROW_BIN px each
        nonzero = np.flatnonzero(prof > 0.01)
        assert nonzero[0] == (y - ROW_TOP) // ROW_BIN
        assert nonzero[-1] == (y + h - 1 - ROW_TOP) // ROW_BIN

    def test_sidecar_and_lazy_profiles_agree(self, atlas):
        from fontmatch.image.glyphs import GlyphAtlas

        assert atlas.rows is not None  # build_atlas writes rows.npy
        lazy = GlyphAtlas(
            atlas.pixels, atlas.meta, atlas.advance, atlas.faces, atlas.charset, rows=None
        )
        for ch in "gH":
            ci = atlas.char_indices(ch)[0]
            assert np.array_equal(lazy.row_profiles(ci), atlas.row_profiles(ci))

    def test_profile_ignores_faint_edge_rows(self):
        """A one-pixel anti-aliasing row at the top/bottom (renderer
        differences) must not move the profile much."""
        from fontmatch.image.rank import PROFILE_BINS, query_profile

        rng = np.random.default_rng(0)
        line = rng.uniform(0.5, 1.0, size=(36, 200)).astype(np.float32)
        line[:, ::3] = 0
        padded = np.pad(line, ((1, 1), (0, 0)))
        padded[0, :] = 0.05
        padded[-1, :] = 0.05
        a, b = query_profile(line, PROFILE_BINS), query_profile(padded, PROFILE_BINS)
        assert np.abs(a - b).sum() < 0.03

    def test_own_face_profile_is_closest_for_another_renderer(self, atlas):
        """Pillow at 56 px vs the 48 px atlas: the true face's profile must be
        among the closest (it is the prefilter's main signal)."""
        from fontmatch.image.rank import PROFILE_BINS, query_profile

        m = ImageMatcher(atlas, family_of={f: _family(f) for f in SANS + SERIF})
        text = "HARBOR VIEW"
        prof = m.line_profiles(text)
        for font in ("Lora-Regular.ttf", "Merriweather-Regular.ttf", "Roboto-Regular.ttf"):
            q = m._query(ink_map(render_styled(FIXTURES / font, text, 56, tracking_em=0.3)))
            dist = np.abs(prof - query_profile(q.soft, PROFILE_BINS)).sum(axis=1)
            own = min(dist[j] for j, f in enumerate(m.faces) if atlas.faces[f]["key"] == font)
            assert (dist < own).sum() <= 3, (font, own, np.sort(dist)[:5])

    def test_prefilter_keeps_true_face_for_tracked_text(self, atlas):
        """A heavily tracked render must survive the prefilter at the service's
        budget ratio (PREFILTER_FACES of ~4.6k atlas faces, i.e. ~6 of 15 here).
        The old natural-aspect prefilter dropped exactly these."""
        from fontmatch.image.rank import PREFILTER_FACES

        m = ImageMatcher(atlas, family_of={f: _family(f) for f in SANS + SERIF})
        budget = round(len(m.faces) * PREFILTER_FACES / PRODUCTION_ATLAS_FACES)
        for font in ("Lora-Regular.ttf", "Montserrat-Regular.ttf", "Roboto-Regular.ttf"):
            img = render_styled(FIXTURES / font, "HARBOR VIEW", 56, tracking_em=0.3)
            sig = m.components(ink_map(img), "HARBOR VIEW", prefilter=budget)
            keys = [atlas.faces[f]["key"] for f in sig.faces]
            assert font in keys, (font, keys)


class TestConfidence:
    """ "Likely the same font" comes from how far the top family stands out
    (ranking margin), not from the raw correlation: after the spacing fit,
    wrong fonts correlate highly too."""

    def test_margin_is_positive_and_ordered(self, matcher):
        img = render_styled(FIXTURES / "Lora-Regular.ttf", "Harbor View", 56)
        matches = matcher.rank(img, text="Harbor View", k=3)
        assert matches[0].key == "Lora-Regular.ttf"
        assert matches[0].margin > 0.06
        assert all(m.margin <= matches[0].margin for m in matches[1:])

    def test_font_outside_the_candidates_has_a_small_margin(self, atlas):
        # Arimo vs Roboto vs DejaVu Sans... no clear winner for Montserrat text
        others = {f: _family(f) for f in SANS + SERIF if not f.startswith("Montserrat")}
        m = ImageMatcher(atlas, family_of=others)
        own = ImageMatcher(atlas, family_of={f: _family(f) for f in SANS + SERIF})
        img = render_styled(FIXTURES / "Montserrat-Regular.ttf", "Harbor View", 56)
        assert (
            m.rank(img, text="Harbor View", k=2)[0].margin
            < own.rank(img, text="Harbor View", k=2)[0].margin
        )


class TestReview2:
    def test_heavy_tightly_cropped_text_is_not_inverted(self):
        """Ink covering > 50% of a tight crop: the background is still the
        border colour (review 2: the whole-image mode picked the text)."""
        from PIL import Image

        arr = np.full((60, 200, 3), 255, dtype=np.uint8)
        for x in range(4, 196, 20):
            arr[3:57, x : x + 13] = 0  # 13 of every 20 columns are ink: ~60%
        soft = ink_map(Image.fromarray(arr))
        assert soft[30, 8] > 0.9  # inside a bar
        assert soft[30, 18] < 0.1  # gap between bars
        assert soft[0, 0] < 0.1  # border

    def test_single_family_has_no_margin(self, atlas):
        m = ImageMatcher(atlas, family_of={"Lora-Regular.ttf": "lora"})  # Regular + Bold faces
        img = render_styled(FIXTURES / "Lora-Regular.ttf", "Harbor", 56)
        assert m.rank(img, text="Harbor", k=2)[0].margin == 0.0

    def test_font_outside_the_candidates_is_not_labelled_same(self, atlas):
        from fontmatch.image.service import SAME_FONT_MARGIN

        others = {f: _family(f) for f in SANS + SERIF if not f.startswith("Roboto")}
        m = ImageMatcher(atlas, family_of=others)
        img = render_styled(FIXTURES / "Roboto-Regular.ttf", "Harbor View", 56)
        assert m.rank(img, text="Harbor View", k=2)[0].margin < SAME_FONT_MARGIN

    @pytest.mark.parametrize("text", ["H", "Ω Harbor", "Harbor ✓"])
    def test_odd_texts_do_not_crash(self, matcher, text):
        img = render_styled(
            FIXTURES / "Roboto-Regular.ttf", "Harbor" if len(text) > 1 else text, 56
        )
        matcher.rank(img, text=text, k=3)

    def test_prefilter_default_follows_the_module_constant(self, matcher, monkeypatch):
        import fontmatch.image.rank as R

        monkeypatch.setattr(R, "PREFILTER_FACES", 2)
        img = render_styled(FIXTURES / "Roboto-Regular.ttf", "Harbor", 56)
        assert len(matcher.components(ink_map(img), "Harbor").faces) == 2

    def test_huge_crop_is_downscaled_before_matching(self, matcher):
        import time

        from fontmatch.image.locate import Located
        from fontmatch.image.rank import rank_located

        img = render_styled(FIXTURES / "Lora-Regular.ttf", "Harbor", 900, pad=40)
        located = Located(crop=img, transcript="Harbor", source="hint", box=(0, 0, *img.size))
        t0 = time.perf_counter()
        _, matches = rank_located(matcher, located, k=1)
        assert time.perf_counter() - t0 < 3.0
        assert matches[0].key == "Lora-Regular.ttf"


class TestCategoryVote:
    """Alternatives that disagree with the top matches' style category are
    demoted (a serif among a geometric logo's sans alternatives was the
    visible symptom of the Google logo bug)."""

    def _matcher(self, atlas, with_categories):
        others = {f: _family(f) for f in SANS + SERIF if not f.startswith("Montserrat")}
        cats = {f: ("sans" if f in SANS else "serif") for f in others} if with_categories else None
        return ImageMatcher(atlas, family_of=others, category_of=cats)

    def _logo(self):
        return render_styled(
            FIXTURES / "Montserrat-Regular.ttf", "Google", 110, tracking_em=-0.04,
            colors=GOOGLE_COLORS, pad=1,
        )  # fmt: skip

    def test_serif_runner_up_is_demoted(self, atlas):
        """Mechanism only: CATEGORY_VOTE is tuned on the real corpus, where
        close sans alternatives are plentiful (the strict "no serif in the top
        5" check is in test_image_styled_corpus.py)."""
        without = [m.key for m in self._matcher(atlas, False).rank(self._logo(), "Google", k=5)]
        with_vote = [m.key for m in self._matcher(atlas, True).rank(self._logo(), "Google", k=5)]
        assert without[1] == "DejaVuSerif.ttf"  # the case this guards against
        assert with_vote.index("DejaVuSerif.ttf") > without.index("DejaVuSerif.ttf")
        assert with_vote[0] in SANS

    def test_own_font_still_wins_against_the_vote(self, atlas):
        cats = {f: ("sans" if f in SANS else "serif") for f in SANS + SERIF}
        m = ImageMatcher(atlas, family_of={f: _family(f) for f in SANS + SERIF}, category_of=cats)
        img = render_styled(FIXTURES / "Lora-Regular.ttf", "Harbor View", 56)
        assert m.rank(img, text="Harbor View", k=1)[0].key == "Lora-Regular.ttf"


class TestReview3:
    def test_margin_uses_the_best_rival_even_when_the_vote_reorders(self):
        """A face that leads only because the vote demoted a better-keyed
        rival must get a negative margin (no "likely the same font")."""
        from fontmatch.image.rank import family_margins

        families = np.array(["a", "b", "c"])
        key = np.array([1.00, 1.05, 0.80])  # b has the best key...
        order = np.array([0, 1, 2])  # ...but the vote put a first
        reranked = np.ones(3, dtype=bool)
        margins = family_margins(families, key, reranked)
        assert margins[order[0]] == pytest.approx(-0.05)
        assert margins[1] == pytest.approx(0.05)

    def test_margin_is_zero_without_comparable_rival(self):
        from fontmatch.image.rank import family_margins

        margins = family_margins(np.array(["a", "a"]), np.array([1.0, 0.9]), np.ones(2, bool))
        assert list(margins) == [0.0, 0.0]

    def test_vote_counts_families_not_negative_keys(self, atlas):
        """One vote per family (top 3); keys can be negative, so they don't
        weight the vote. Without a majority the top family's category wins."""
        from fontmatch.image.rank import majority_category

        assert majority_category(["serif", "sans", "sans"]) == "sans"
        assert majority_category(["serif", "sans", "mono"]) == "serif"  # no majority: the top's

    def test_stale_rows_sidecar_is_ignored_and_recomputed(self, atlas, tmp_path, caplog):
        import shutil

        from fontmatch.image.glyphs import GlyphAtlas

        for f in atlas.directory.iterdir():
            shutil.copy(f, tmp_path / f.name)
        np.save(tmp_path / "rows.npy", np.zeros((3, 3, 3), dtype=np.float16))
        with caplog.at_level("WARNING"):
            stale = GlyphAtlas.load(tmp_path)
        assert stale.rows is None
        assert "doesn't match" in caplog.text and "missing" not in caplog.text
        ci = atlas.char_indices("g")[0]
        assert np.array_equal(stale.row_profiles(ci), atlas.row_profiles(ci))

    def test_logo_through_the_service_gets_a_sans(self, atlas):
        """End to end (locate, crop, ink, rank, vote, label) on a Google-style
        logo whose font isn't a candidate: a sans comes first. (The label
        threshold is calibrated on the real corpus, where it's checked:
        test_image_styled_corpus.py; on 13 candidates DejaVu Sans really does
        stand out.)"""
        import io

        from fontmatch.image.catalog import CatalogEntry
        from fontmatch.image.service import ImageIdentifier

        entries = [
            CatalogEntry(name=f, family=_family(f).title(), base_family=_family(f),
                         subfamily="Regular", path=FIXTURES / f, license_id="OFL-1.1",
                         category="sans" if f in SANS else "serif", is_italic=False)  # fmt: skip
            for f in SANS + SERIF
            if not f.startswith("Montserrat")
        ]
        ident = ImageIdentifier(atlas, entries)
        buf = io.BytesIO()
        render_styled(
            FIXTURES / "Montserrat-Regular.ttf", "Google", 110, tracking_em=-0.04,
            colors=GOOGLE_COLORS, pad=1,
        ).save(buf, "PNG")  # fmt: skip
        out = ident.identify(buf.getvalue(), hint="Google", k=3)
        assert out["matches"][0]["category"] == "sans"
        assert out["matches"][0]["match_label"] in ("similar alternative", "likely the same font")
