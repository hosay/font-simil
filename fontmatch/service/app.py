"""Flask application for the font matching service."""

from __future__ import annotations

import calendar
import hmac
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, g, jsonify, render_template, request
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.middleware.proxy_fix import ProxyFix

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
    # Trust one level of X-Forwarded-For so rate limiting works behind a
    # reverse proxy (nginx, caddy, etc.).
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)
    app.secret_key = os.environ.get("SECRET_KEY", "dev-key-change-me")
    app.config["TESTING"] = testing
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_SIZE

    # CORS — allow API access from any origin
    CORS(app, resources={r"/api/*": {"origins": "*"}})

    # Calls from the local MCP service (ChatGPT app) carry a shared token and
    # skip the per-IP limits: every MCP request arrives from 127.0.0.1, so
    # per-IP limits would throttle all ChatGPT users together. The MCP
    # service applies its own per-user limit instead.
    internal_token = os.environ.get("FONTMATCH_INTERNAL_TOKEN", "")

    def is_internal_request() -> bool:
        if not internal_token:
            return False
        if request.headers.get("X-Forwarded-For"):
            return False  # came through nginx, not from the local MCP service
        if request.remote_addr not in ("127.0.0.1", "::1"):
            return False
        supplied = request.headers.get("X-Internal-Token", "").encode("utf-8", "surrogateescape")
        return hmac.compare_digest(supplied, internal_token.encode())

    app.config["IS_INTERNAL_REQUEST"] = is_internal_request

    # Rate limiting
    limiter = Limiter(
        get_remote_address,
        app=app,
        default_limits=["60 per minute"],
        storage_uri="memory://",
    )
    app.config["LIMITER"] = limiter
    limiter.request_filter(is_internal_request)

    @app.errorhandler(413)
    def too_large(e):
        if request.path.startswith("/api/"):
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
    store.cleanup_image_cache(days=30)
    store.forget_old_rating_ips(days=365)
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
    # Absolute: send_file() resolves relative paths against the package, not the CWD.
    app.config["SAMPLES_DIR"] = Path(
        os.environ.get("FONTMATCH_SAMPLES_DIR") or "font_samples"
    ).resolve()
    # Generated social preview cards. Tests get a throwaway directory: this
    # machine is prod and tests must never write next to its data.
    og_dir = os.environ.get("DUPEFONT_OG_DIR") or (
        tempfile.mkdtemp(prefix="og-") if testing else app.config["SAMPLES_DIR"].parent / "og_images"
    )
    app.config["OG_DIR"] = Path(og_dir).resolve()
    # Images visitors chose to keep (share links, feedback): shares/, feedback/.
    user_dir = os.environ.get("DUPEFONT_USER_CONTENT_DIR") or (
        tempfile.mkdtemp(prefix="uc-") if testing else app.config["SAMPLES_DIR"].parent / "user_content"
    )
    app.config["USER_CONTENT_DIR"] = Path(user_dir).resolve()

    # Microsoft Clarity (consent-gated in static/analytics.js); "" disables it.
    app.config["CLARITY_PROJECT_ID"] = os.environ.get("DUPEFONT_CLARITY_ID", "ys6l88q2n9")

    # Named in the privacy policy as the operator (legal entity or person).
    app.config["OPERATOR"] = (
        os.environ.get("DUPEFONT_OPERATOR") or "Datacleave Ltd, a Canadian federal corporation, Victoria, British Columbia, Canada"
    )

    # Private usage dashboard (/admin/stats); off unless a password hash is set.
    from fontmatch.mcp_server.usage import DEFAULT_PATH as MCP_USAGE_DB

    app.config["ADMIN_USER"] = os.environ.get("DUPEFONT_ADMIN_USER", "dfadmin")
    app.config["ADMIN_PASSWORD_HASH"] = os.environ.get("DUPEFONT_ADMIN_PASSWORD_HASH", "")
    app.config["MCP_USAGE_DB"] = Path(os.environ.get("DUPEFONT_MCP_USAGE_DB") or MCP_USAGE_DB)

    # Image -> font matcher (glyph atlas + catalog load lazily on first use)
    from fontmatch.image.service import LazyImageIdentifier

    app.config["IMAGE_IDENTIFIER"] = LazyImageIdentifier(
        db_path or DEFAULT_DB_PATH,
        os.environ.get("FONTMATCH_GLYPH_ATLAS") or None,
    )

    # Paths exempt from daily rate limiting
    _EXEMPT_PREFIXES = ("/static/", "/api/health", "/font-sample/")
    _EXEMPT_PATHS = {"/robots.txt", "/sitemap.txt", "/favicon.ico"}

    @app.before_request
    def check_daily_rate_limit():
        path = request.path
        if path.startswith(_EXEMPT_PREFIXES) or path in _EXEMPT_PATHS:
            return None
        if is_internal_request():
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
    from fontmatch.web.admin import admin_bp
    from fontmatch.web.api import api_bp
    from fontmatch.web.routes import web_bp

    app.register_blueprint(web_bp)
    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(admin_bp, url_prefix="/admin")
    from fontmatch.web.user_content import expire_feedback_images, user_content_bp

    app.register_blueprint(user_content_bp)
    with app.app_context():
        try:
            expire_feedback_images()
        except Exception as exc:  # never block startup on housekeeping
            app.logger.error("feedback image cleanup failed: %s", exc)
    # gunicorn --preload forks after this: leave no connection to inherit.
    store.close()

    # Apply stricter rate limits to CPU-intensive identify endpoints
    # The wrapped function must replace the registered view, or the limit is
    # silently ignored (it was, before image matching was added).
    for endpoint in (
        "api.identify", "api.identify_image", "web.identify_submit", "web.identify_image_submit"
    ):
        app.view_functions[endpoint] = limiter.limit("10 per minute")(app.view_functions[endpoint])
    # A results page or the ChatGPT widget loads 5-10 sample images at once.
    app.view_functions["web.font_sample"] = limiter.limit("300 per minute")(
        app.view_functions["web.font_sample"]
    )
    # Each share stores an image: keep the volume small.
    app.view_functions["user_content.create_share"] = limiter.limit("5 per minute;50 per day")(
        app.view_functions["user_content.create_share"]
    )
    app.view_functions["user_content.image_feedback"] = limiter.limit("10 per minute")(
        app.view_functions["user_content.image_feedback"]
    )
    # Basic-auth password checks are deliberately slow (scrypt): cap guessing.
    app.view_functions["admin.stats"] = limiter.limit("20 per minute")(app.view_functions["admin.stats"])

    # Make slugify available in all templates
    app.jinja_env.globals["slugify"] = slugify

    return app


def get_app() -> Flask:
    """App factory for Flask CLI: flask --app fontmatch.service.app:get_app run."""
    return create_app()
