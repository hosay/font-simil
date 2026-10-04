"""Usage log for the MCP server: one row per tool call, for /admin/stats.

Its own SQLite file (written only by the MCP process, read by the Flask
dashboard), so the MCP service never needs write access to fontmatch.db.
Recording is best-effort: a broken log must never fail a ChatGPT call.

Privacy: the ChatGPT user id is stored only as a truncated SHA-256 and the
image text hint is never stored (see the privacy policy).
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_PATH = Path("/var/lib/fontmatch/mcp_usage.db")
RETENTION_DAYS = 180
PRUNE_EVERY = 500  # inserts

SCHEMA = """
CREATE TABLE IF NOT EXISTS mcp_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    tool TEXT NOT NULL,
    subject_hash TEXT,
    status TEXT NOT NULL,
    latency_ms INTEGER,
    image_bytes INTEGER,
    image_host TEXT,
    transcript_source TEXT,
    query TEXT,
    top_family TEXT,
    top_similarity INTEGER,
    match_label TEXT,
    locale TEXT,
    country TEXT
);
CREATE INDEX IF NOT EXISTS mcp_calls_ts ON mcp_calls (ts);
"""

COLUMNS = (
    "tool",
    "subject_hash",
    "status",
    "latency_ms",
    "image_bytes",
    "image_host",
    "transcript_source",
    "query",
    "top_family",
    "top_similarity",
    "match_label",
    "locale",
    "country",
)


def hash_subject(subject: str) -> str:
    return hashlib.sha256(subject.encode("utf-8", "replace")).hexdigest()[:16]


class UsageLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        self._inserts = 0
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=5)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            self.conn = conn
            self.prune()
        except (OSError, sqlite3.Error) as exc:
            logger.error("usage log disabled (%s): %s", self.path, exc)

    def record(self, *, subject: str = "", **fields) -> None:
        if self.conn is None:
            return
        fields["subject_hash"] = hash_subject(subject) if subject else None
        values = [fields.get(c) for c in COLUMNS]
        try:
            with self._lock:
                self.conn.execute(
                    f"INSERT INTO mcp_calls ({', '.join(COLUMNS)}) "
                    f"VALUES ({', '.join('?' for _ in COLUMNS)})",
                    values,
                )
                self.conn.commit()
                self._inserts += 1
            if self._inserts % PRUNE_EVERY == 0:
                self.prune()
        except Exception as exc:  # never fail the tool call
            logger.error("usage log write failed: %s", exc)

    def prune(self, days: int = RETENTION_DAYS) -> int:
        if self.conn is None:
            return 0
        try:
            with self._lock:
                cur = self.conn.execute(
                    "DELETE FROM mcp_calls WHERE ts < datetime('now', ?)", (f"-{int(days)} days",)
                )
                self.conn.commit()
            return cur.rowcount
        except Exception as exc:
            logger.error("usage log prune failed: %s", exc)
            return 0


class NullUsageLog:
    """Used when tracking is off (tests, local runs without a usage DB)."""

    def record(self, **fields) -> None:
        pass
