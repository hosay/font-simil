"""FontStore reopens its SQLite connection in a forked worker.

gunicorn --preload builds the app (and the store) in the master process; a
SQLite connection must not be used across fork(), so each worker gets its own.
"""

import os

from fontmatch.index.store import FontStore


def test_connection_reopened_after_fork(tmp_path, monkeypatch):
    store = FontStore(tmp_path / "f.db")
    parent_conn = store.conn
    assert store.conn is parent_conn  # same process: same connection

    monkeypatch.setattr(os, "getpid", lambda: -1)  # pretend we're a forked child
    child_conn = store.conn
    assert child_conn is not parent_conn
    store.log_request("/x")  # the new connection works
    assert store.conn is child_conn


def test_real_fork_child_uses_its_own_connection(tmp_path):
    """The app factory must leave no open connection behind for workers to
    inherit (gunicorn --preload forks after create_app)."""
    from fontmatch.service.app import create_app

    app = create_app(db_path=tmp_path / "f.db", testing=True)
    store = app.config["STORE"]
    assert store._conn is None  # closed after setup
    pid = os.fork()
    if pid == 0:  # child: write through a fresh connection, then exit
        try:
            store.log_request("/child")
            os._exit(0)
        except Exception:
            os._exit(1)
    _, status = os.waitpid(pid, 0)
    assert os.WEXITSTATUS(status) == 0
    assert store.request_count() >= 1
