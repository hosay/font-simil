"""HTML page routes for the font matching website."""

from __future__ import annotations

import base64
import hashlib

from flask import (
    Blueprint,
    Response,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from fontmatch.features.fingerprint import fingerprint
from fontmatch.features.perceptual import FINGERPRINT_SCHEMA_VERSION
from fontmatch.fonts.loader import UnsupportedFontError, load
from fontmatch.web.helpers import (
    PROPRIETARY_FONTS,
    PROPRIETARY_TO_OPEN_SOURCE,
    deslugify,
    enrich_matches,
    lookup_proprietary,
    slugify,
)

web_bp = Blueprint("web", __name__)


@web_bp.get("/robots.txt")
def robots_txt():
    content = "User-agent: *\nAllow: /\nDisallow: /api/\nSitemap: /sitemap.txt\n"
    return Response(content, mimetype="text/plain")


@web_bp.get("/sitemap.txt")
def sitemap_txt():
    """Simple text sitemap listing all browsable pages."""
    store = current_app.config["STORE"]
    families = store.list_font_families(clean_only=True)
    lines = ["/", "/identify", "/api/docs"]
    for prop_name in PROPRIETARY_TO_OPEN_SOURCE:
        lines.append(f"/similar-to/{slugify(prop_name)}")
    for fam in families:
        lines.append(f"/similar-to/{slugify(fam)}")
    return Response("\n".join(lines), mimetype="text/plain")


# Popular fonts shown on the homepage.
# Each maps a well-known proprietary name to its open-source equivalent in our
# corpus, so the link actually works.
POPULAR_FONTS = [
    {"label": "Times New Roman", "target": "Tinos"},
    {"label": "Arial", "target": "Arimo"},
    {"label": "Helvetica", "target": "Liberation Sans"},
    {"label": "Courier New", "target": "Cousine"},
    {"label": "Calibri", "target": "Carlito"},
    {"label": "Cambria", "target": "Caladea"},
    {"label": "Georgia", "target": "Gelasio"},
    {"label": "Garamond", "target": "EB Garamond"},
    {"label": "Palatino", "target": "Lora"},
    {"label": "Verdana", "target": "DejaVu Sans"},
]

DEFAULT_K = 10


@web_bp.get("/")
def index():
    store = current_app.config["STORE"]
    families = store.list_font_families(clean_only=True)

    # Only include popular links whose target family actually exists in the corpus
    popular_links = []
    for entry in POPULAR_FONTS:
        row = store.get_font_by_family(entry["target"])
        if row:
            popular_links.append(
                {
                    "label": entry["label"],
                    "target_family": row["family"],
                    "slug": slugify(entry["label"]),
                }
            )

    return render_template(
        "index.html",
        font_count=store.font_count(),
        request_count=store.request_count(),
        popular_links=popular_links,
        families=families[:40],
        slugify=slugify,
    )


@web_bp.get("/identify")
def identify_form():
    return render_template("identify.html", matches=None)


@web_bp.post("/identify")
def identify_submit():
    store = current_app.config["STORE"]

    if "file" not in request.files or request.files["file"].filename == "":
        flash("Please select a font file to upload.", "error")
        return redirect(url_for("web.identify_form"))

    f = request.files["file"]
    raw = f.read()
    file_hash = hashlib.sha256(raw).hexdigest()
    filename = f.filename or "unknown"

    store.log_request("/identify")

    # Build a data URI so the browser can render the uploaded font
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "ttf"
    mime_map = {
        "ttf": "font/ttf",
        "otf": "font/otf",
        "woff": "font/woff",
        "woff2": "font/woff2",
    }
    mime = mime_map.get(ext, "font/ttf")
    query_font_data_uri = f"data:{mime};base64,{base64.b64encode(raw).decode()}"

    try:
        font = load(raw)
    except UnsupportedFontError:
        flash(
            "Unsupported font format. Please upload a TTF, OTF, WOFF, or WOFF2 file.",
            "error",
        )
        return redirect(url_for("web.identify_form"))

    query_family = font.family

    # Check cache
    cached = store.get_cached_result(file_hash, FINGERPRINT_SCHEMA_VERSION)
    if cached is not None:
        return render_template(
            "identify.html",
            matches=enrich_matches(cached, store=store),
            query_name=filename,
            query_family=query_family,
            query_font_data_uri=query_font_data_uri,
        )

    try:
        fp = fingerprint(font)
    except Exception:
        flash("Could not process this font file.", "error")
        return redirect(url_for("web.identify_form"))

    results = store.identify(fp, k=DEFAULT_K)
    store.cache_result(file_hash, fp.schema_version, results)
    return render_template(
        "identify.html",
        matches=enrich_matches(results, store=store),
        query_name=filename,
        query_family=query_family,
        query_font_data_uri=query_font_data_uri,
    )


@web_bp.get("/similar-to/<slug>")
def similar_to(slug: str):
    store = current_app.config["STORE"]
    family_name = deslugify(slug)

    # If user searched for a proprietary font name, look up the open-source
    # equivalent but keep the original name as the display title.
    display_name = family_name
    canonical_prop = lookup_proprietary(family_name)
    oss_name = PROPRIETARY_TO_OPEN_SOURCE.get(family_name)
    lookup_name = oss_name or family_name

    # Proprietary font metadata (None for open-source fonts)
    prop_meta = None
    if canonical_prop:
        display_name = canonical_prop
        meta = PROPRIETARY_FONTS.get(canonical_prop, {})
        prop_meta = {
            "name": canonical_prop,
            "css_family": meta.get("css_family", f"'{canonical_prop}', serif"),
            "category": meta.get("category", "sans-serif"),
            "vendor": meta.get("vendor", ""),
            "description": meta.get("description", ""),
        }

    font_row = store.get_font_by_family(lookup_name, licensed_only=True)
    if font_row is None:
        font_row = store.get_font_by_family(slug.replace("-", " "), licensed_only=True)

    if font_row is None:
        return render_template("similar.html", family_name=display_name, matches=None, found=False, prop=None)

    fp = store.get_fingerprint(font_row["file_hash"], FINGERPRINT_SCHEMA_VERSION)
    if fp is None:
        return render_template("similar.html", family_name=display_name, matches=None, found=False, prop=None)

    results = store.identify(fp, k=DEFAULT_K)

    # Check if the corpus font file is available for download
    from fontmatch.web.helpers import _is_crawled_source

    corpus_source = store.get_font_source(font_row["name"])
    corpus_has_file = corpus_source is not None and not _is_crawled_source(corpus_source)

    return render_template(
        "similar.html",
        family_name=display_name,
        corpus_family=font_row["family"],
        query_font_name=font_row["name"],
        corpus_has_file=corpus_has_file,
        matches=enrich_matches(results, store=store),
        found=True,
        slugify=slugify,
        prop=prop_meta,
    )
