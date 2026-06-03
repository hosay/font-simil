"""Flask application for the font matching service."""

from __future__ import annotations

import calendar
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, g, jsonify, render_template, request
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from fontmatch.index.ingest import ingest_corpus
from fontmatch.index.store import FontStore
from fontmatch.web.helpers import slugify

DEFAULT_DB_PATH = Path("fontmatch.db")

# 10 MB upload limit
MAX_UPLOAD_SIZE = 10 * 1024 * 1024


def create_app(
    db_path: Path | None = None,
    fixture_dir: Path | None = None,
    testing: bool = False,
) -> Flask:
    """Create and configure the Flask app.

    If fixture_dir is provided, ingest those fonts into the corpus on startup.
    """
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent.parent / "templates"),
        static_folder=str(Path(__file__).parent.parent / "static"),
    )
    app.secret_key = os.environ.get("SECRET_KEY", "dev-key-change-me")
    app.config["TESTING"] = testing
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_SIZE

    # CORS — allow API access from any origin
    CORS(app, resources={r"/api/*": {"origins": "*"}})

    # Rate limiting
    limiter = Limiter(
        get_remote_address,
        app=app,
        default_limits=["60 per minute"],
        storage_uri="memory://",
    )
    app.config["LIMITER"] = limiter

    @app.errorhandler(413)
    def too_large(e):
        if "api" in (getattr(e, "description", "") or ""):
            return jsonify({"error": "File too large. Maximum size is 10 MB."}), 413
        from flask import flash, redirect, url_for

        flash("File too large. Maximum size is 10 MB.", "error")
        return redirect(url_for("web.identify_form"))

    # Daily rate limit configuration
    app.config["DAILY_RATE_LIMIT"] = int(
        os.environ.get("DAILY_RATE_LIMIT", 1000)
    )
    app.config["DAILY_RATE_TRACK_THRESHOLD"] = int(
        os.environ.get("DAILY_RATE_TRACK_THRESHOLD", 200)
    )

    store = FontStore(db_path or DEFAULT_DB_PATH)

    if fixture_dir is not None:
        ingest_corpus(fixture_dir, store)

    store.build_index()
    store.cleanup_old_usage(days=90)
    app.config["STORE"] = store

    # Directories to search for font files (for @font-face serving)
    corpus_dirs = []
    if fixture_dir is not None:
        corpus_dirs.append(str(fixture_dir))
    project_root = Path(__file__).parent.parent.parent
    # Test fixtures (always available)
    fixtures_dir = project_root / "tests" / "fixtures"
    if fixtures_dir.is_dir() and str(fixtures_dir) not in corpus_dirs:
        corpus_dirs.append(str(fixtures_dir))
    # Google Fonts repo
    gf_repo = project_root / "google-fonts-repo"
    if gf_repo.is_dir():
        corpus_dirs.append(str(gf_repo))
    db_dir = (db_path or DEFAULT_DB_PATH).parent
    gf_repo_db = db_dir / "google-fonts-repo"
    if gf_repo_db.is_dir() and str(gf_repo_db) not in corpus_dirs:
        corpus_dirs.append(str(gf_repo_db))
    # System font directories
    for sys_dir in [
        "/usr/share/fonts/truetype/liberation",
        "/usr/share/fonts/truetype/dejavu",
        "/usr/share/fonts/truetype/ubuntu",
        "/usr/share/fonts/truetype/freefont",
        "/usr/share/fonts/opentype/linux-libertine",
    ]:
        if Path(sys_dir).is_dir():
            corpus_dirs.append(sys_dir)
    app.config["CORPUS_DIRS"] = corpus_dirs

    # Paths exempt from daily rate limiting
    _EXEMPT_PREFIXES = ("/static/", "/api/health")
    _EXEMPT_PATHS = {"/robots.txt", "/sitemap.txt", "/favicon.ico"}

    @app.before_request
    def check_daily_rate_limit():
        path = request.path
        if path.startswith(_EXEMPT_PREFIXES) or path in _EXEMPT_PATHS:
            return None

        ip = get_remote_address() or "unknown"
        count = store.increment_daily_usage(ip)
        g.daily_usage_count = count
        g.daily_rate_limit = app.config["DAILY_RATE_LIMIT"]

        if count > g.daily_rate_limit:
            tomorrow = datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            ) + timedelta(days=1)
            reset_at = tomorrow.strftime("%Y-%m-%dT%H:%M:%SZ")
            reset_unix = str(calendar.timegm(tomorrow.timetuple()))

            if path.startswith("/api"):
                resp = jsonify({
                    "error": "Daily rate limit exceeded",
                    "limit": g.daily_rate_limit,
                    "reset_at": reset_at,
                })
                resp.status_code = 429
                resp.headers["X-RateLimit-Limit"] = str(g.daily_rate_limit)
                resp.headers["X-RateLimit-Remaining"] = "0"
                resp.headers["X-RateLimit-Reset"] = reset_unix
                return resp

            return render_template(
                "rate_limited.html",
                limit=g.daily_rate_limit,
                reset_at=reset_at,
            ), 429

        return None

    @app.after_request
    def add_rate_limit_headers(response):
        count = getattr(g, "daily_usage_count", None)
        if count is None:
            return response
        limit = getattr(g, "daily_rate_limit", app.config["DAILY_RATE_LIMIT"])
        remaining = max(0, limit - count)
        tomorrow = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ) + timedelta(days=1)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(
            calendar.timegm(tomorrow.timetuple())
        )
        return response

    # Register blueprints
    from fontmatch.web.api import api_bp
    from fontmatch.web.routes import web_bp

    app.register_blueprint(web_bp)
    app.register_blueprint(api_bp, url_prefix="/api")

    # Apply stricter rate limits to CPU-intensive identify endpoints
    limiter.limit("10 per minute")(app.view_functions["api.identify"])
    limiter.limit("10 per minute")(app.view_functions["web.identify_submit"])

    # Make slugify available in all templates
    app.jinja_env.globals["slugify"] = slugify

    return app


def get_app() -> Flask:
    """App factory for Flask CLI: flask --app fontmatch.service.app:get_app run."""
    return create_app()
