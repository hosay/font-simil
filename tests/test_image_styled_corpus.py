"""Logo-style queries against the real atlas and catalog (integration).

The Google logo (2026-10-03) matched Lusitana, a serif, at 82%. Its font
(Product Sans) is not in the corpus, so the right answer is "a geometric
sans". These renders reproduce that setup with corpus fonts whose own family
is removed from the candidates.
"""

from pathlib import Path

import pytest

from fontmatch.image.catalog import load_catalog_json
from fontmatch.image.rank import DEFAULT_ATLAS_DIR, ImageMatcher, load_atlas, rank_located
from fontmatch.image.service import SAME_FONT_MARGIN
from fontmatch.image.synth import GOOGLE_COLORS, render_styled

pytestmark = [pytest.mark.integration, pytest.mark.xdist_group("integration")]

GF = Path(__file__).resolve().parent.parent / "google-fonts-repo" / "ofl"
LOGO_FONTS = {
    "poppins": GF / "poppins" / "Poppins-Medium.ttf",
    "questrial": GF / "questrial" / "Questrial-Regular.ttf",
    "outfit": GF / "outfit" / "Outfit[wght].ttf",
    "urbanist": GF / "urbanist" / "Urbanist[wght].ttf",
}


@pytest.fixture(scope="module")
def corpus():
    if not (DEFAULT_ATLAS_DIR / "catalog.json").exists():
        pytest.skip("glyph atlas not built")
    catalog = load_catalog_json(DEFAULT_ATLAS_DIR / "catalog.json")
    return load_atlas(), catalog


def _located(img, text):
    from fontmatch.image.locate import locate

    return locate(img, hint=text)


@pytest.mark.parametrize("family", sorted(LOGO_FONTS))
def test_logo_font_not_in_corpus_gets_sans_alternatives(corpus, family):
    atlas, catalog = corpus
    if not LOGO_FONTS[family].exists():
        pytest.skip(f"{LOGO_FONTS[family]} missing")
    category = {e.base_family: e.category for e in catalog}
    kept = [e for e in catalog if e.base_family != family]
    matcher = ImageMatcher(
        atlas, {e.name: e.base_family for e in kept}, {e.name: e.category for e in kept}
    )
    img = render_styled(
        LOGO_FONTS[family], "Google", 120, tracking_em=-0.03, colors=GOOGLE_COLORS, pad=1
    )
    _, matches = rank_located(matcher, _located(img, "Google"), k=5)
    cats = [category[m.family] for m in matches]
    # Google files some geometric sans under "display" (Funnel Display);
    # the bug is a serif or script among the alternatives.
    ok = cats and cats[0] == "sans" and not {"serif", "handwriting"} & set(cats)
    assert ok, list(zip([m.family for m in matches], cats))
    # Not in the catalog, so never "likely the same font" (the Lusitana bug
    # showed a confident-looking 82%).
    assert matches[0].margin < SAME_FONT_MARGIN, matches[0]


@pytest.mark.parametrize("family", sorted(LOGO_FONTS))
@pytest.mark.parametrize("tracking_em", [-0.03, 0.2])
def test_styled_text_finds_its_own_family(corpus, family, tracking_em):
    atlas, catalog = corpus
    if not LOGO_FONTS[family].exists():
        pytest.skip(f"{LOGO_FONTS[family]} missing")
    matcher = ImageMatcher(
        atlas, {e.name: e.base_family for e in catalog}, {e.name: e.category for e in catalog}
    )
    text = "Google" if tracking_em < 0 else "HARBOR VIEW"
    img = render_styled(
        LOGO_FONTS[family], text, 96, tracking_em=tracking_em, colors=GOOGLE_COLORS
    )
    _, matches = rank_located(matcher, _located(img, text), k=5)
    assert family in [m.family for m in matches]
