"""JSON API blueprint for the font matching service."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request, send_file

from fontmatch.features.fingerprint import fingerprint
from fontmatch.features.perceptual import FINGERPRINT_SCHEMA_VERSION
from fontmatch.fonts.loader import UnsupportedFontError, load
from fontmatch.web.helpers import (
    CORPUS_ALIASES,
    PROPRIETARY_TO_OPEN_SOURCE,
    enrich_matches,
    slugify,
)

_MIME_TYPES = {
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}

API_VERSION = "1"

api_bp = Blueprint("api", __name__)


@api_bp.after_request
def add_api_version_header(response):
    response.headers["X-API-Version"] = API_VERSION
    return response


DEFAULT_K = 10


def _add_urls(matches: list[dict], store) -> list[dict]:
    """Add download_url and google_fonts_url to each match for the API response."""
    # enrich_matches computes google_fonts_url, has_file, score breakdown, license
    enrich_matches(matches, store=store)
    for m in matches:
        m["download_url"] = f"/api/font-file/{m['name']}" if m.get("has_file") else None
        m["score_breakdown"] = {
            "metric": m.get("metric_score"),
            "perceptual": m.get("perceptual_score"),
        }
    return matches


@api_bp.get("/health")
def health():
    store = current_app.config["STORE"]
    return jsonify({"status": "ok", "font_count": store.font_count()})


@api_bp.post("/identify")
def identify():
    store = current_app.config["STORE"]

    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    f = request.files["file"]
    raw = f.read()
    file_hash = hashlib.sha256(raw).hexdigest()

    store.log_request("/api/identify")

    # Check cache
    cached = store.get_cached_result(file_hash, FINGERPRINT_SCHEMA_VERSION)
    if cached is not None:
        return jsonify({"matches": _add_urls(cached, store), "cached": True})

    # Load and fingerprint
    try:
        font = load(raw)
    except UnsupportedFontError as exc:
        return jsonify({"error": str(exc), "type": "UnsupportedFontError"}), 400

    try:
        fp = fingerprint(font)
    except Exception as exc:
        return jsonify({"error": f"Fingerprinting failed: {exc}"}), 400

    results = store.identify(fp, k=DEFAULT_K)
    store.cache_result(file_hash, fp.schema_version, results)
    return jsonify({"matches": _add_urls(results, store), "cached": False})


@api_bp.get("/fonts/<int:font_id>")
def get_font(font_id: int):
    store = current_app.config["STORE"]
    row = store.conn.execute(
        """SELECT id, file_hash, name, family, subfamily,
                  units_per_em, license_id, source
           FROM fonts WHERE id = ?""",
        (font_id,),
    ).fetchone()
    if row is None:
        return jsonify({"error": "Font not found"}), 404
    return jsonify(dict(row))


@api_bp.post("/scores")
def save_score():
    store = current_app.config["STORE"]
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    query_font = data.get("query_font", "").strip()
    match_font = data.get("match_font", "").strip()
    score = data.get("score")

    if not query_font or not match_font:
        return jsonify({"error": "query_font and match_font required"}), 400
    if not isinstance(score, int) or not (1 <= score <= 5):
        return jsonify({"error": "score must be integer 1-5"}), 400

    ip_address = request.remote_addr or "unknown"

    saved = store.save_user_score(query_font, match_font, score, ip_address)
    # Always return success — silent fail if already rated from this IP
    avg, count = store.get_average_score(query_font, match_font)
    return jsonify({"average_score": avg, "vote_count": count, "saved": saved})


def _proprietary_matches(query: str) -> list[dict]:
    """Return proprietary/alias font entries that match the search query.

    Each result has ``family``, ``category``, and ``slug`` keys so the
    client can build a link to ``/similar-to/<slug>``.
    """
    if not query:
        return []
    q = query.lower()
    hits: list[dict] = []
    # Search proprietary names
    for prop_name, oss_name in PROPRIETARY_TO_OPEN_SOURCE.items():
        if q in prop_name.lower():
            hits.append(
                {
                    "family": prop_name,
                    "category": "proprietary",
                    "slug": slugify(prop_name),
                    "oss_equivalent": oss_name,
                }
            )
    # Search corpus aliases (e.g. "DM Sans" -> "DM Sans 9pt")
    for alias_name in CORPUS_ALIASES:
        if q in alias_name.lower():
            hits.append(
                {
                    "family": alias_name,
                    "category": "alias",
                    "slug": slugify(alias_name),
                }
            )
    return hits


@api_bp.get("/browse")
def browse_fonts():
    """Search and filter fonts in the corpus, including proprietary names."""
    store = current_app.config["STORE"]
    query = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()
    page = max(1, request.args.get("page", 1, type=int))
    per_page = min(100, max(1, request.args.get("per_page", 40, type=int)))
    offset = (page - 1) * per_page

    results, total = store.search_font_families(
        query=query,
        category=category,
        offset=offset,
        limit=per_page,
    )

    # On the first page, prepend proprietary / alias matches so that
    # typing "Helvetica" shows a result even though it's not in the corpus.
    if query and page == 1 and not category:
        prop_hits = _proprietary_matches(query)
        # Deduplicate: remove proprietary hits whose slug already
        # appears in the corpus results.
        corpus_slugs = {slugify(r["family"]) for r in results}
        new_hits = [h for h in prop_hits if h["slug"] not in corpus_slugs]
        if new_hits:
            results = new_hits[:per_page] + results
            total += len(new_hits[:per_page])

    return jsonify(
        {
            "fonts": results,
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": (total + per_page - 1) // per_page if per_page else 1,
        }
    )


@api_bp.post("/report")
def report_match():
    """Report a bad match. Stored alongside user scores."""
    store = current_app.config["STORE"]
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    query_font = data.get("query_font", "").strip()
    match_font = data.get("match_font", "").strip()
    if not query_font or not match_font:
        return jsonify({"error": "query_font and match_font required"}), 400

    ip_address = request.remote_addr or "unknown"
    # Record as a score of 0 (special flag value below the 1-5 range)
    saved = store.save_report(query_font, match_font, ip_address)
    return jsonify({"reported": saved})


@api_bp.get("/font-file/<path:name>")
def font_file(name: str):
    """Serve a font file from the corpus for @font-face rendering."""
    store = current_app.config["STORE"]
    source = store.get_font_source(name)
    if source is None:
        return jsonify({"error": "Font not found in DB"}), 404

    corpus_dirs = current_app.config.get("CORPUS_DIRS", [])

    # Try the source path directly in each corpus dir
    for corpus_dir in corpus_dirs:
        font_path = _safe_resolve(corpus_dir, source)
        if font_path and font_path.is_file():
            return _serve_font(font_path)

    # Google Fonts sources may be stored without the license-category prefix
    # (e.g. "familyslug/Font.ttf" instead of "ofl/familyslug/Font.ttf").
    # Try common prefixes.
    for prefix in ("ofl", "apache", "ufl"):
        prefixed = f"{prefix}/{source}"
        for corpus_dir in corpus_dirs:
            font_path = _safe_resolve(corpus_dir, prefixed)
            if font_path and font_path.is_file():
                return _serve_font(font_path)

    # If source is a bare filename, search for it by name in corpus dirs.
    # Use os.walk instead of rglob because rglob treats [] as glob patterns
    # and variable fonts use [axis].ttf naming.
    bare_name = Path(source).name
    for corpus_dir in corpus_dirs:
        direct = _safe_resolve(corpus_dir, bare_name)
        if direct and direct.is_file():
            return _serve_font(direct)
        for dirpath, _dirnames, filenames in os.walk(corpus_dir):
            if bare_name in filenames:
                candidate = Path(dirpath) / bare_name
                if _is_within(candidate, corpus_dir):
                    return _serve_font(candidate)

    return jsonify({"error": "Font file not on disk"}), 404


def _is_within(path: Path, directory: str) -> bool:
    """Check that a resolved path stays within the given directory."""
    try:
        return str(path.resolve()).startswith(str(Path(directory).resolve()))
    except (OSError, ValueError):
        return False


def _safe_resolve(corpus_dir: str, relative: str) -> Path | None:
    """Resolve a path within a corpus dir, returning None if it escapes."""
    candidate = (Path(corpus_dir) / relative).resolve()
    if str(candidate).startswith(str(Path(corpus_dir).resolve())):
        return candidate
    return None


def _serve_font(font_path: Path):
    """Send a font file with the correct MIME type."""
    mime = _MIME_TYPES.get(font_path.suffix.lower(), "application/octet-stream")
    return send_file(font_path, mimetype=mime, max_age=86400)


@api_bp.get("/docs")
def api_docs():
    from flask import render_template

    store = current_app.config["STORE"]
    return render_template("api_docs.html", font_count=store.font_count())
