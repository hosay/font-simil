"""Phase 5 — Similarity scoring & matcher tests."""

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str):
    from fontmatch.fonts import load

    return load(FIXTURES / name)


def _fp(name: str):
    from fontmatch.features.fingerprint import fingerprint

    return fingerprint(_load(name))


# Build a small corpus of all our fixture fonts
CORPUS_FONTS = [
    "Roboto-Regular.ttf",
    "Roboto-Bold.ttf",
    "Arimo-Regular.ttf",
    "Tinos-Regular.ttf",
    "Cousine-Regular.ttf",
    "Carlito-Regular.ttf",
    "Caladea-Regular.ttf",
]


@pytest.fixture(scope="module")
def corpus():
    """Pre-compute fingerprints for the test corpus."""
    from fontmatch.features.fingerprint import fingerprint

    return {name: fingerprint(_load(name)) for name in CORPUS_FONTS}


class TestDistance:
    def test_distance_self_is_zero(self, corpus):
        from fontmatch.match.scorer import distance

        fp = corpus["Roboto-Regular.ttf"]
        assert distance(fp, fp) == pytest.approx(0.0)

    def test_distance_is_symmetric(self, corpus):
        from fontmatch.match.scorer import distance

        a = corpus["Roboto-Regular.ttf"]
        b = corpus["Tinos-Regular.ttf"]
        assert distance(a, b) == pytest.approx(distance(b, a))

    def test_distance_positive(self, corpus):
        from fontmatch.match.scorer import distance

        a = corpus["Roboto-Regular.ttf"]
        b = corpus["Tinos-Regular.ttf"]
        assert distance(a, b) > 0


class TestRanking:
    def test_arimo_closer_to_roboto_than_tinos(self, corpus):
        """Sans-serif Arimo should rank closer to Roboto than serif Tinos."""
        from fontmatch.match.scorer import distance

        q = corpus["Arimo-Regular.ttf"]
        d_roboto = distance(q, corpus["Roboto-Regular.ttf"])
        d_tinos = distance(q, corpus["Tinos-Regular.ttf"])
        assert d_roboto < d_tinos

    def test_caladea_closer_to_tinos_than_roboto(self, corpus):
        """Serif Caladea should rank closer to serif Tinos than sans Roboto."""
        from fontmatch.match.scorer import distance

        q = corpus["Caladea-Regular.ttf"]
        d_tinos = distance(q, corpus["Tinos-Regular.ttf"])
        d_roboto = distance(q, corpus["Roboto-Regular.ttf"])
        assert d_tinos < d_roboto

    def test_rank_returns_ordered_matches(self, corpus):
        from fontmatch.match.scorer import rank

        q = corpus["Arimo-Regular.ttf"]
        candidates = {n: fp for n, fp in corpus.items() if n != "Arimo-Regular.ttf"}
        results = rank(q, candidates, k=3)
        assert len(results) == 3
        # Distances should be ascending
        for i in range(len(results) - 1):
            assert results[i].distance <= results[i + 1].distance

    def test_rank_top1_for_sans_query_is_sans(self, corpus):
        """Querying with a sans font should return another sans at rank 1."""
        from fontmatch.match.scorer import rank

        q = corpus["Arimo-Regular.ttf"]
        candidates = {n: fp for n, fp in corpus.items() if n != "Arimo-Regular.ttf"}
        results = rank(q, candidates, k=1)
        top = results[0]
        # Top match should be a sans font (Roboto or Carlito)
        assert top.name in ("Roboto-Regular.ttf", "Carlito-Regular.ttf")


class TestEvaluationHarness:
    def test_recall_at_5_on_category_groups(self, corpus):
        """Sans query fonts should find sans matches in top-5; same for serif."""
        from fontmatch.match.scorer import rank

        sans_fonts = ["Roboto-Regular.ttf", "Arimo-Regular.ttf", "Carlito-Regular.ttf"]
        serif_fonts = ["Tinos-Regular.ttf", "Caladea-Regular.ttf"]

        # For each sans font, check that another sans is in top-3
        for name in sans_fonts:
            q = corpus[name]
            candidates = {n: fp for n, fp in corpus.items() if n != name}
            results = rank(q, candidates, k=5)
            top5_names = {r.name for r in results}
            other_sans = set(sans_fonts) - {name}
            assert top5_names & other_sans, (
                f"Query {name}: no sans font in top-5: {top5_names}"
            )

        # For each serif font, check that another serif is in top-3
        for name in serif_fonts:
            q = corpus[name]
            candidates = {n: fp for n, fp in corpus.items() if n != name}
            results = rank(q, candidates, k=5)
            top5_names = {r.name for r in results}
            other_serif = set(serif_fonts) - {name}
            assert top5_names & other_serif, (
                f"Query {name}: no serif font in top-5: {top5_names}"
            )

    def test_evaluate_mrr(self, corpus):
        from fontmatch.match.scorer import evaluate

        # Define ground-truth: each font's expected category peers
        ground_truth = {
            "Arimo-Regular.ttf": {"Roboto-Regular.ttf", "Carlito-Regular.ttf"},
            "Roboto-Regular.ttf": {"Arimo-Regular.ttf", "Carlito-Regular.ttf"},
            "Tinos-Regular.ttf": {"Caladea-Regular.ttf"},
            "Caladea-Regular.ttf": {"Tinos-Regular.ttf"},
        }
        result = evaluate(ground_truth, corpus)
        assert result["mrr"] > 0.5
        assert result["recall@5"] >= 0.8
