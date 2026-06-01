"""Phase 6 — Persistence & nearest-neighbor index tests."""

from pathlib import Path

import numpy as np

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str):
    from fontmatch.fonts import load

    return load(FIXTURES / name)


def _fp(name: str):
    from fontmatch.features.fingerprint import fingerprint

    return fingerprint(_load(name))


class TestRoundTrip:
    def test_store_and_reload_fingerprint(self, tmp_path):
        from fontmatch.index.store import FontStore

        db_path = tmp_path / "test.db"
        store = FontStore(db_path)

        fp = _fp("Roboto-Regular.ttf")
        store.store_fingerprint("Roboto-Regular.ttf", fp, license_id="Apache-2.0")

        loaded = store.get_fingerprint(fp.file_hash, fp.schema_version)
        assert loaded is not None
        np.testing.assert_array_equal(loaded.metric_array(), fp.metric_array())
        np.testing.assert_array_equal(loaded.perceptual_vec, fp.perceptual_vec)
        assert loaded.family == fp.family

    def test_store_idempotent(self, tmp_path):
        """Storing the same fingerprint twice doesn't create duplicates."""
        from fontmatch.index.store import FontStore

        db_path = tmp_path / "test.db"
        store = FontStore(db_path)

        fp = _fp("Roboto-Regular.ttf")
        store.store_fingerprint("Roboto-Regular.ttf", fp, license_id="Apache-2.0")
        store.store_fingerprint("Roboto-Regular.ttf", fp, license_id="Apache-2.0")

        count = store.font_count()
        assert count == 1


class TestCacheBehavior:
    def test_cached_identify_skips_recompute(self, tmp_path):
        """A second identify of the same file_hash returns cached result."""
        from fontmatch.index.store import FontStore

        db_path = tmp_path / "test.db"
        store = FontStore(db_path)

        # Store a few fonts
        for name in ["Roboto-Regular.ttf", "Arimo-Regular.ttf", "Tinos-Regular.ttf"]:
            fp = _fp(name)
            store.store_fingerprint(name, fp, license_id="Apache-2.0")

        # Build index
        store.build_index()

        # First query
        query_fp = _fp("Cousine-Regular.ttf")
        result1 = store.identify(query_fp, k=2)
        assert len(result1) > 0

        # Cache the result
        store.cache_result(query_fp.file_hash, query_fp.schema_version, result1)

        # Second query should hit cache
        cached = store.get_cached_result(query_fp.file_hash, query_fp.schema_version)
        assert cached is not None
        assert len(cached) == len(result1)

    def test_schema_version_mismatch_misses_cache(self, tmp_path):
        from fontmatch.index.store import FontStore

        db_path = tmp_path / "test.db"
        store = FontStore(db_path)

        fp = _fp("Roboto-Regular.ttf")
        store.store_fingerprint("Roboto-Regular.ttf", fp, license_id="Apache-2.0")
        store.build_index()

        query_fp = _fp("Arimo-Regular.ttf")
        result = store.identify(query_fp, k=1)
        store.cache_result(query_fp.file_hash, query_fp.schema_version, result)

        # Different schema version should miss
        cached = store.get_cached_result(query_fp.file_hash, 999)
        assert cached is None


class TestNearestNeighbor:
    def test_index_matches_brute_force(self, tmp_path):
        """Nearest-neighbor via index returns correct top families."""
        from fontmatch.index.store import FontStore

        db_path = tmp_path / "test.db"
        store = FontStore(db_path)

        corpus_names = [
            "Roboto-Regular.ttf", "Roboto-Bold.ttf", "Arimo-Regular.ttf",
            "Tinos-Regular.ttf", "Cousine-Regular.ttf",
        ]
        for name in corpus_names:
            fp = _fp(name)
            store.store_fingerprint(name, fp, license_id="Apache-2.0")

        store.build_index()

        query = _fp("Carlito-Regular.ttf")

        # Index result — deduplicates by family and excludes self-family
        index_result = store.identify(query, k=3)
        index_families = [r["family"] for r in index_result]

        # Should return 3 distinct families, all from the corpus
        assert len(index_result) == 3
        assert len(set(index_families)) == 3  # all different families
        # Top matches for a sans-serif query should include sans fonts
        assert any("Arimo" in f or "Roboto" in f for f in index_families)
