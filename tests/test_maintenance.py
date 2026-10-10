"""Retention sweeps must be runnable without a deploy (privacy.html promises dates)."""

from fontmatch.index.store import FontStore
from fontmatch.maintenance import main, run_sweeps


def test_runs_every_sweep_the_privacy_policy_promises(tmp_path):
    store = FontStore(tmp_path / "t.db")
    store.build_index()
    result = run_sweeps(store)
    assert set(result) == {"usage", "image_cache", "rating_ips"}
    assert all(isinstance(v, int) for v in result.values())


def test_cli_runs_the_sweeps_against_the_given_database(tmp_path):
    FontStore(tmp_path / "t.db").build_index()
    assert main(["--db", str(tmp_path / "t.db")]) == 0


def test_cli_fails_cleanly_when_the_database_is_missing(tmp_path):
    """The timer must not create an empty DB next to the real one."""
    missing = tmp_path / "missing.db"
    assert main(["--db", str(missing)]) == 1
    assert not missing.exists()
