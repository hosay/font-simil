"""Private usage dashboard (/admin/stats), behind HTTP Basic auth.

Credentials come from the environment (/etc/fontmatch/env on prod):
``DUPEFONT_ADMIN_USER`` (default dfadmin) and ``DUPEFONT_ADMIN_PASSWORD_HASH``
(a werkzeug hash; generate with ``python -c "from werkzeug.security import
generate_password_hash as g; print(g('...'))"``). No hash = dashboard off (404).

ChatGPT usage is read from the MCP service's own usage DB (read-only);
website usage from fontmatch.db's request_log.
"""

from __future__ import annotations

import hmac
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from werkzeug.security import check_password_hash

admin_bp = Blueprint("admin", __name__)

DAYS = 30


def _authorized() -> bool:
    auth = request.authorization
    if auth is None or auth.type != "basic":
        return False
    user_ok = hmac.compare_digest(
        (auth.username or "").encode(), current_app.config["ADMIN_USER"].encode()
    )
    try:
        password_ok = check_password_hash(
            current_app.config["ADMIN_PASSWORD_HASH"], auth.password or ""
        )
    except ValueError:  # malformed hash in the environment
        current_app.logger.error("admin: DUPEFONT_ADMIN_PASSWORD_HASH is not a valid hash")
        return False
    return user_ok and password_ok


def _percentile(values: list[int], q: float) -> int | None:
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))]


def _open_ro(path: Path) -> sqlite3.Connection | None:
    if not Path(path).is_file():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def usage_stats(path: Path) -> dict | None:
    """Aggregates for the dashboard; None when the MCP usage DB doesn't exist yet,
    ``{"error": ...}`` when it exists but can't be read."""
    conn = _open_ro(path)
    if conn is None:
        return None
    try:
        q = lambda sql, *args: [dict(r) for r in conn.execute(sql, args)]  # noqa: E731
        since = f"-{DAYS} days"

        totals = {}
        for label, window in [("today", "start of day"), ("7d", "-7 days"), ("30d", since)]:
            row = q(
                "SELECT COUNT(*) AS calls, COUNT(DISTINCT subject_hash) AS users, "
                "SUM(status = 'ok') AS ok FROM mcp_calls WHERE ts >= datetime('now', ?)",
                window,
            )[0]
            totals[label] = {k: row[k] or 0 for k in row}
        row = q(
            "SELECT COUNT(*) AS calls, COUNT(DISTINCT subject_hash) AS users, "
            "SUM(status = 'ok') AS ok FROM mcp_calls"
        )[0]
        totals["all"] = {k: row[k] or 0 for k in row}

        tools = q(
            "SELECT tool, COUNT(*) AS calls, COUNT(DISTINCT subject_hash) AS users, "
            "SUM(status = 'ok') AS ok FROM mcp_calls WHERE ts >= datetime('now', ?) "
            "GROUP BY tool ORDER BY calls DESC",
            since,
        )
        for t in tools:
            latencies = [
                r["latency_ms"]
                for r in q(
                    "SELECT latency_ms FROM mcp_calls WHERE tool = ? AND status = 'ok' "
                    "AND latency_ms IS NOT NULL AND ts >= datetime('now', ?)",
                    t["tool"],
                    since,
                )
            ]
            t["p50"] = _percentile(latencies, 0.5)
            t["p95"] = _percentile(latencies, 0.95)

        per_day = {
            r["day"]: r
            for r in q(
                "SELECT date(ts) AS day, COUNT(*) AS calls, "
                "SUM(tool = 'find_free_font_from_image') AS image, "
                "COUNT(DISTINCT subject_hash) AS users, SUM(status != 'ok') AS failed "
                "FROM mcp_calls WHERE ts >= datetime('now', ?) GROUP BY day",
                since,
            )
        }
        today = datetime.now(timezone.utc).date()
        daily = []
        for offset in range(DAYS - 1, -1, -1):
            day = (today - timedelta(days=offset)).isoformat()
            r = per_day.get(day) or {}
            daily.append(
                {
                    "day": day,
                    "calls": r.get("calls") or 0,
                    "image": r.get("image") or 0,
                    "users": r.get("users") or 0,
                    "failed": r.get("failed") or 0,
                }
            )
        peak = max((d["calls"] for d in daily), default=0) or 1
        for d in daily:
            d["pct"] = round(100 * d["calls"] / peak)
            d["image_pct"] = round(100 * d["image"] / peak)

        def top(column: str, where: str = "") -> list[dict]:
            return q(
                f"SELECT {column} AS label, COUNT(*) AS n FROM mcp_calls "
                f"WHERE {column} IS NOT NULL AND {column} != '' AND ts >= datetime('now', ?) "
                f"{where} GROUP BY {column} ORDER BY n DESC LIMIT 15",
                since,
            )

        return {
            "totals": totals,
            "tools": tools,
            "daily": daily,
            "statuses": q(
                "SELECT status AS label, COUNT(*) AS n FROM mcp_calls "
                "WHERE ts >= datetime('now', ?) GROUP BY status ORDER BY n DESC",
                since,
            ),
            "top_image_fonts": top("top_family", "AND tool = 'find_free_font_from_image'"),
            "top_queries": top("query"),
            "countries": top("country"),
            "locales": top("locale"),
            "recent": q(
                "SELECT ts, tool, subject_hash, status, latency_ms, query, top_family, "
                "top_similarity, country FROM mcp_calls ORDER BY id DESC LIMIT 50"
            ),
        }
    except sqlite3.Error as exc:
        current_app.logger.error("admin: usage DB unreadable: %s", exc)
        return {"error": str(exc)}
    finally:
        conn.close()


def site_stats(store) -> list[dict]:
    with store._lock:
        rows = store.conn.execute(
            "SELECT endpoint, "
            "SUM(created_at >= datetime('now', 'start of day')) AS today, "
            "SUM(created_at >= datetime('now', '-7 days')) AS d7, "
            "SUM(created_at >= datetime('now', ?)) AS d30, COUNT(*) AS total "
            "FROM request_log GROUP BY endpoint ORDER BY d30 DESC",
            (f"-{DAYS} days",),
        ).fetchall()
    return [dict(r) for r in rows]


def _challenge() -> Response | None:
    """None when the request may proceed; otherwise the 404/401 to return."""
    if not current_app.config.get("ADMIN_PASSWORD_HASH"):
        abort(404)
    if not _authorized():
        return Response(
            "Authentication required", 401, {"WWW-Authenticate": 'Basic realm="DupeFont admin"'}
        )
    # Browsers resend Basic credentials automatically, so a form on another
    # site could post here: changes must come from our own pages.
    if (
        request.method == "POST"
        and request.origin
        and (request.origin.rstrip("/") != request.host_url.rstrip("/"))
    ):
        abort(403)
    return None


FEEDBACK_FILE = re.compile(r"^[0-9a-f]{24}\.jpg$|^[A-Za-z0-9]{1,40}\.jpg$")


@admin_bp.get("/feedback-image/<name>")
def feedback_image(name: str):
    if (denied := _challenge()) is not None:
        return denied
    path = Path(current_app.config["USER_CONTENT_DIR"]) / "feedback" / name
    if not FEEDBACK_FILE.match(name) or not path.is_file():
        abort(404)
    resp = send_file(path, mimetype="image/jpeg", max_age=0)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


@admin_bp.post("/shares/<share_id>/delete")
def delete_share(share_id: str):
    from fontmatch.web.user_content import SHARE_ID, remove_share

    if (denied := _challenge()) is not None:
        return denied
    if not SHARE_ID.match(share_id):
        abort(404)
    remove_share(share_id)
    return redirect(url_for("admin.stats") + "#shares", code=303)


@admin_bp.get("/stats")
def stats():
    if (denied := _challenge()) is not None:
        return denied
    store = current_app.config["STORE"]
    html = render_template(
        "admin_stats.html",
        usage=usage_stats(current_app.config["MCP_USAGE_DB"]),
        site=site_stats(store),
        feedback=store.image_feedback_stats(days=DAYS),
        feedback_no=store.recent_image_feedback(limit=20, verdict="no"),
        share_count=store.share_count(),
        shares=store.recent_shares(limit=20),
        generated=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        no_analytics=True,
    )
    return Response(
        html,
        headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow"},
    )
