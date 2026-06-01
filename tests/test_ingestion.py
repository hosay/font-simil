"""Phase 7 — Reference corpus ingestion tests."""

import shutil
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


class TestCorpusIngestion:
    def _make_corpus_dir(self, tmp_path):
        """Create a mini corpus directory mimicking Google Fonts layout."""
        corpus = tmp_path / "corpus"
        # Family 1: Roboto
        roboto_dir = corpus / "apache" / "roboto"
        roboto_dir.mkdir(parents=True)
        shutil.copy(FIXTURES / "Roboto-Regular.ttf", roboto_dir)
        shutil.copy(FIXTURES / "Roboto-Bold.ttf", roboto_dir)
        (roboto_dir / "LICENSE.txt").write_text("Apache License 2.0")

        # Family 2: Tinos
        tinos_dir = corpus / "ofl" / "tinos"
        tinos_dir.mkdir(parents=True)
        shutil.copy(FIXTURES / "Tinos-Regular.ttf", tinos_dir)
        (tinos_dir / "OFL.txt").write_text("SIL Open Font License 1.1")

        # Family 3: Arimo
        arimo_dir = corpus / "apache" / "arimo"
        arimo_dir.mkdir(parents=True)
        shutil.copy(FIXTURES / "Arimo-Regular.ttf", arimo_dir)
        (arimo_dir / "LICENSE.txt").write_text("Apache License 2.0")

        return corpus

    def test_ingest_produces_fingerprints(self, tmp_path):
        from fontmatch.index.ingest import ingest_corpus
        from fontmatch.index.store import FontStore

        corpus = self._make_corpus_dir(tmp_path)
        db_path = tmp_path / "test.db"
        store = FontStore(db_path)

        count = ingest_corpus(corpus, store)
        assert count == 4  # 2 Roboto + 1 Tinos + 1 Arimo
        assert store.font_count() == 4

    def test_ingest_is_idempotent(self, tmp_path):
        from fontmatch.index.ingest import ingest_corpus
        from fontmatch.index.store import FontStore

        corpus = self._make_corpus_dir(tmp_path)
        db_path = tmp_path / "test.db"
        store = FontStore(db_path)

        ingest_corpus(corpus, store)
        ingest_corpus(corpus, store)  # second run
        assert store.font_count() == 4  # no duplicates

    def test_ingest_captures_license(self, tmp_path):
        from fontmatch.index.ingest import ingest_corpus
        from fontmatch.index.store import FontStore

        corpus = self._make_corpus_dir(tmp_path)
        db_path = tmp_path / "test.db"
        store = FontStore(db_path)

        ingest_corpus(corpus, store)

        # Check license was stored
        row = store.conn.execute(
            "SELECT license_id FROM fonts WHERE family = 'Tinos'"
        ).fetchone()
        assert row is not None
        assert "OFL" in row["license_id"]

    def test_end_to_end_query_after_ingest(self, tmp_path):
        from fontmatch.features.fingerprint import fingerprint
        from fontmatch.fonts import load
        from fontmatch.index.ingest import ingest_corpus
        from fontmatch.index.store import FontStore

        corpus = self._make_corpus_dir(tmp_path)
        db_path = tmp_path / "test.db"
        store = FontStore(db_path)

        ingest_corpus(corpus, store)
        store.build_index()

        # Query with a font not in the corpus
        query_font = load(FIXTURES / "Cousine-Regular.ttf")
        query_fp = fingerprint(query_font)
        results = store.identify(query_fp, k=3)

        assert len(results) == 3
        assert all("family" in r for r in results)
