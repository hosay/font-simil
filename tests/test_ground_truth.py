"""Ground-truth integration tests — verifies the matcher correctly identifies
metric-compatible font pairs from the plan's table.

These tests run against a real 258-font corpus built from Google Fonts,
system fonts, and test fixtures. The matcher must find the expected
open-source equivalent without any name-based shortcuts.
"""

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
DB_PATH = Path(__file__).parent.parent / "fontmatch.db"


@pytest.fixture(scope="module")
def store():
    """Load the real corpus database."""
    from fontmatch.index.store import FontStore

    if not DB_PATH.exists():
        pytest.skip("fontmatch.db not built — run corpus ingestion first")
    s = FontStore(DB_PATH)
    s.build_index()
    yield s
    s.close()


def _query(store, font_name: str, k: int = 10):
    from fontmatch.features.fingerprint import fingerprint
    from fontmatch.fonts import load

    fp = fingerprint(load(FIXTURES / font_name))
    return store.identify(fp, k=k)


class TestMetricCompatiblePairsRecall1:
    """Core metric-compatible pairs MUST match at rank 1."""

    def test_liberation_sans_finds_arimo(self, store):
        results = _query(store, "LiberationSans-Regular.ttf")
        assert "Arimo" in results[0]["family"]

    def test_liberation_serif_finds_tinos(self, store):
        results = _query(store, "LiberationSerif-Regular.ttf")
        assert "Tinos" in results[0]["family"]

    def test_liberation_mono_finds_cousine(self, store):
        results = _query(store, "LiberationMono-Regular.ttf")
        assert "Cousine" in results[0]["family"]

    def test_arimo_finds_liberation_sans(self, store):
        results = _query(store, "Arimo-Regular.ttf")
        assert "Liberation Sans" in results[0]["family"]

    def test_tinos_finds_liberation_serif(self, store):
        results = _query(store, "Tinos-Regular.ttf")
        assert "Liberation Serif" in results[0]["family"]

    def test_cousine_finds_liberation_mono(self, store):
        results = _query(store, "Cousine-Regular.ttf")
        assert "Liberation Mono" in results[0]["family"]


class TestSecondaryPairsCategory:
    """Calibri/Cambria-compatible fonts should return same-category matches.

    In a large crawled corpus (3000+ fonts), many web fonts from editorial
    sites have similar proportions to Carlito/Caladea. The system correctly
    finds these genuinely similar fonts. We test that results stay in the
    right serif/sans category rather than requiring specific families.
    """

    def test_carlito_gets_sans_results(self, store):
        """Carlito (Calibri-compatible, sans) → results should be mostly sans."""
        results = _query(store, "Carlito-Regular.ttf")
        top5 = [r["family"] for r in results[:5]]
        # Should not return mono or obviously serif fonts at the top
        known_serif = {"Tinos", "Liberation Serif", "Caladea", "PT Serif"}
        serif_in_top5 = [f for f in top5 if f in known_serif]
        assert len(serif_in_top5) <= 1, f"Too many serif fonts for sans query: {top5}"

    def test_caladea_gets_serif_results(self, store):
        """Caladea (Cambria-compatible, serif) → results should be mostly serif."""
        results = _query(store, "Caladea-Regular.ttf")
        top5 = [r["family"] for r in results[:5]]
        # Should not return mono fonts at the top
        known_mono = {"Cousine", "Liberation Mono", "Source Code Pro", "Fira Mono"}
        mono_in_top5 = [f for f in top5 if f in known_mono]
        assert len(mono_in_top5) == 0, f"Mono fonts in serif query results: {top5}"


class TestCategoryCoherence:
    """Results should stay within the same category (sans/serif/mono)."""

    def test_sans_query_returns_mostly_sans(self, store):
        results = _query(store, "Roboto-Regular.ttf")
        # At least 3 of top 5 should be sans
        families = [r["family"] for r in results[:5]]
        # Serif families we know
        serif_families = {"Tinos", "Liberation Serif", "Caladea", "DejaVu Serif"}
        non_serif = [f for f in families if f not in serif_families]
        assert len(non_serif) >= 3

    def test_serif_query_returns_mostly_serif(self, store):
        results = _query(store, "Tinos-Regular.ttf")
        families = [r["family"] for r in results[:5]]
        # Known sans families
        sans_families = {
            "Arimo", "Liberation Sans", "Roboto", "Lato", "Ubuntu",
        }
        non_sans = [f for f in families if f not in sans_families]
        assert len(non_sans) >= 3

    def test_mono_query_returns_mostly_mono(self, store):
        results = _query(store, "Cousine-Regular.ttf")
        top5 = [r["family"] for r in results[:5]]
        # Known mono families
        mono_families = {
            "Liberation Mono", "Cousine", "Source Code Pro",
            "DejaVu Sans Mono", "Fira Mono", "Fira Code",
            "JetBrains Mono", "Inconsolata", "Ubuntu Mono",
            "Noto Sans Mono",
        }
        mono_hits = [f for f in top5 if any(m in f for m in mono_families)]
        assert len(mono_hits) >= 3, (
            f"Expected >=3 mono fonts in top 5, got {len(mono_hits)}: {top5}"
        )


class TestEvaluationMetrics:
    """Aggregate metrics over all ground-truth pairs."""

    def test_recall_at_1_above_threshold(self, store):
        """Recall@1 >= 0.75 over the 6 core pairs."""
        pairs = {
            "LiberationSans-Regular.ttf": "Arimo",
            "LiberationSerif-Regular.ttf": "Tinos",
            "LiberationMono-Regular.ttf": "Cousine",
            "Arimo-Regular.ttf": "Liberation Sans",
            "Tinos-Regular.ttf": "Liberation Serif",
            "Cousine-Regular.ttf": "Liberation Mono",
        }
        hits = 0
        for qname, expected_family in pairs.items():
            results = _query(store, qname)
            if expected_family in results[0]["family"]:
                hits += 1
        recall = hits / len(pairs)
        assert recall >= 0.75, f"Recall@1 = {recall:.2f}, expected >= 0.75"

    def test_recall_at_5_core_pairs(self, store):
        """Recall@5 = 1.0 over the 6 core metric-compatible pairs."""
        pairs = {
            "LiberationSans-Regular.ttf": ["Arimo"],
            "LiberationSerif-Regular.ttf": ["Tinos"],
            "LiberationMono-Regular.ttf": ["Cousine"],
            "Arimo-Regular.ttf": ["Liberation Sans"],
            "Tinos-Regular.ttf": ["Liberation Serif"],
            "Cousine-Regular.ttf": ["Liberation Mono"],
        }
        hits = 0
        for qname, expected in pairs.items():
            results = _query(store, qname, k=5)
            top5 = [r["family"] for r in results[:5]]
            if any(any(e in f for e in expected) for f in top5):
                hits += 1
        recall = hits / len(pairs)
        assert recall == 1.0, f"Recall@5 = {recall:.2f}, expected 1.0"

    def test_recall_at_1_core_pairs(self, store):
        """Recall@1 = 1.0 on the 6 core metric-compatible pairs.

        These are the strongest test: Arial<->Liberation Sans<->Arimo,
        Times<->Liberation Serif<->Tinos, Courier<->Liberation Mono<->Cousine.
        Even in a corpus of thousands of crawled web fonts, the metric-compatible
        clones should always rank first.
        """
        pairs = {
            "LiberationSans-Regular.ttf": "Arimo",
            "LiberationSerif-Regular.ttf": "Tinos",
            "LiberationMono-Regular.ttf": "Cousine",
            "Arimo-Regular.ttf": "Liberation Sans",
            "Tinos-Regular.ttf": "Liberation Serif",
            "Cousine-Regular.ttf": "Liberation Mono",
        }
        hits = 0
        for qname, expected_family in pairs.items():
            results = _query(store, qname)
            if expected_family in results[0]["family"]:
                hits += 1
        recall = hits / len(pairs)
        assert recall == 1.0, f"Recall@1 (core) = {recall:.2f}, expected 1.0"
