"""Request counter tests."""


class TestRequestCounter:
    def test_counter_starts_at_zero(self, tmp_path):
        from fontmatch.index.store import FontStore

        store = FontStore(tmp_path / "test.db")
        assert store.request_count() == 0

    def test_log_request_increments(self, tmp_path):
        from fontmatch.index.store import FontStore

        store = FontStore(tmp_path / "test.db")
        store.log_request("/api/identify")
        store.log_request("/api/identify")
        assert store.request_count("/api/identify") == 2

    def test_counter_per_endpoint(self, tmp_path):
        from fontmatch.index.store import FontStore

        store = FontStore(tmp_path / "test.db")
        store.log_request("/api/identify")
        store.log_request("/identify")
        store.log_request("/api/identify")
        assert store.request_count("/api/identify") == 2
        assert store.request_count("/identify") == 1
        assert store.request_count() == 3
