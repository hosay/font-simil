"""User scoring tests."""

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def app():
    import tempfile

    from fontmatch.service.app import create_app

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        application = create_app(db_path=db_path, fixture_dir=FIXTURES, testing=True)
        yield application


@pytest.fixture
def client(app):
    return app.test_client()


class TestUserScoring:
    def test_save_score_returns_200(self, client):
        resp = client.post(
            "/api/scores",
            json={"query_font": "test.ttf", "match_font": "Roboto-Regular.ttf", "score": 4},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "average_score" in data
        assert "vote_count" in data

    def test_save_score_persists_to_db(self, app):
        store = app.config["STORE"]
        saved = store.save_user_score("q.ttf", "m.ttf", 5, ip_address="10.0.0.1")
        assert saved is True
        avg, count = store.get_average_score("q.ttf", "m.ttf")
        assert count == 1
        assert avg == 5.0

    def test_duplicate_ip_silently_rejected(self, app):
        store = app.config["STORE"]
        saved1 = store.save_user_score("dup.ttf", "match.ttf", 3, ip_address="10.0.0.2")
        saved2 = store.save_user_score("dup.ttf", "match.ttf", 5, ip_address="10.0.0.2")
        assert saved1 is True
        assert saved2 is False  # silently rejected — same IP, same pair
        avg, count = store.get_average_score("dup.ttf", "match.ttf")
        assert count == 1
        assert avg == 3.0  # original score kept

    def test_different_ips_both_accepted(self, app):
        store = app.config["STORE"]
        store.save_user_score("avg.ttf", "m.ttf", 2, ip_address="10.0.0.3")
        store.save_user_score("avg.ttf", "m.ttf", 4, ip_address="10.0.0.4")
        avg, count = store.get_average_score("avg.ttf", "m.ttf")
        assert count == 2
        assert avg == 3.0

    def test_score_out_of_range_returns_400(self, client):
        resp = client.post(
            "/api/scores",
            json={"query_font": "t.ttf", "match_font": "m.ttf", "score": 6},
        )
        assert resp.status_code == 400

    def test_score_missing_fields_returns_400(self, client):
        resp = client.post("/api/scores", json={"query_font": "t.ttf"})
        assert resp.status_code == 400

    def test_score_no_json_returns_400(self, client):
        resp = client.post("/api/scores")
        assert resp.status_code == 400
