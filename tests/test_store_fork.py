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
