"""Flask application for the font matching service."""

from __future__ import annotations

import os
from pathlib import Path

from flask import Flask, jsonify
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

    store = FontStore(db_path or DEFAULT_DB_PATH)

    if fixture_dir is not None:
        ingest_corpus(fixture_dir, store)

    store.build_index()
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
    for sys_dir in ["/usr/share/fonts/truetype/liberation", "/usr/share/fonts/truetype/dejavu"]:
        if Path(sys_dir).is_dir():
            corpus_dirs.append(sys_dir)
    app.config["CORPUS_DIRS"] = corpus_dirs

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
