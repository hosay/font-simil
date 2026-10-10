"""Retention sweeps must be runnable without a deploy (privacy.html promises dates)."""

from fontmatch.index.store import FontStore
from fontmatch.maintenance import RETENTION, run_sweeps


def test_runs_every_sweep_the_privacy_policy_promises(tmp_path):
    store = FontStore(tmp_path / "t.db")
    store.build_index()
    result = run_sweeps(store)
    assert set(result) == {"usage", "image_cache", "rating_ips"}
    assert all(isinstance(v, int) for v in result.values())


def test_retention_windows_match_the_app_defaults(tmp_path):
    """If these drift from service/app.py, the policy and the sweeps disagree."""
    assert RETENTION == {"usage": 90, "image_cache": 30, "rating_ips": 365}


def test_entrypoint_is_runnable_as_a_module():
    import importlib.util

    assert importlib.util.find_spec("fontmatch.maintenance") is not None
