"""HTML page routes for the font matching website."""

from __future__ import annotations

import base64
import hashlib
import io
from pathlib import Path

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from fontmatch.features.fingerprint import fingerprint
from fontmatch.features.perceptual import FINGERPRINT_SCHEMA_VERSION
from fontmatch.fonts.loader import UnsupportedFontError, load
from fontmatch.web.helpers import (
    PROPRIETARY_CANONICAL,
    PROPRIETARY_FONTS,
    PROPRIETARY_TO_OPEN_SOURCE,
    deslugify,
    enrich_matches,
    google_fonts_license,
    google_fonts_css_name,
    google_fonts_name,
    google_fonts_source_for,
    google_fonts_url,
    license_label,
    lookup_proprietary,
    slugify,
)
from fontmatch.web.similar import find_similar

web_bp = Blueprint("web", __name__)


@web_bp.get("/robots.txt")
def robots_txt():
    base = request.host_url.rstrip("/")
    content = (
        f"User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /admin/\nSitemap: {base}/sitemap.txt\n"
    )
    return Response(content, mimetype="text/plain")


@web_bp.get("/sitemap.txt")
def sitemap_txt():
    """Text sitemap: static pages plus the /similar-to pages worth indexing
    (see indexable_similar_slugs)."""
    base = request.host_url.rstrip("/")
    lines = [
        f"{base}/",
        f"{base}/popular",
        f"{base}/identify",
        f"{base}/api/docs",
        f"{base}/privacy",
        f"{base}/terms",
        f"{base}/support",
    ]
    lines += [f"{base}/similar-to/{slug}" for slug in sorted(indexable_similar_slugs())]
    return Response("\n".join(lines), mimetype="text/plain")


# Popular fonts shown on the homepage — ordered by Google search volume for
# "similar font to X".  Only the top entries are displayed; the full list
# lives on the /popular page.
POPULAR_FONTS = [
    {"label": "Times New Roman", "target": "Tinos"},
    {"label": "Helvetica", "target": "Liberation Sans"},
    {"label": "Proxima Nova", "target": "Nunito"},
    {"label": "Futura", "target": "Nunito ExtraLight"},
    {"label": "Impact", "target": "Anton"},
    {"label": "Arial", "target": "Arimo"},
    {"label": "Gotham", "target": "Montserrat"},
    {"label": "Calibri", "target": "Carlito"},
    {"label": "Comic Sans", "target": "Comic Neue"},
    {"label": "Avenir", "target": "Nunito"},
    {"label": "Century Gothic", "target": "Poppins"},
    {"label": "Verdana", "target": "DejaVu Sans"},
    {"label": "Garamond", "target": "EB Garamond"},
    {"label": "Georgia", "target": "Gelasio"},
    {"label": "Bodoni", "target": "Libre Bodoni"},
]

# Complete list of popular font searches, ordered by Google search volume.
# Includes both proprietary fonts (mapped to OSS equivalents) and fonts
# already in the corpus.  Used on the /popular index page.
ALL_POPULAR_FONTS = [
    # --- Proprietary / commercial fonts (mapped via PROPRIETARY_TO_OPEN_SOURCE) ---
    {"label": "Times New Roman", "slug": "times-new-roman", "category": "serif"},
    {"label": "Helvetica", "slug": "helvetica", "category": "sans-serif"},
    {"label": "Proxima Nova", "slug": "proxima-nova", "category": "sans-serif"},
    {"label": "Futura", "slug": "futura", "category": "sans-serif"},
    {"label": "Impact", "slug": "impact", "category": "sans-serif"},
    {"label": "Arial", "slug": "arial", "category": "sans-serif"},
    {"label": "Gotham", "slug": "gotham", "category": "sans-serif"},
    {"label": "Calibri", "slug": "calibri", "category": "sans-serif"},
    {"label": "Comic Sans", "slug": "comic-sans", "category": "sans-serif"},
    {"label": "Avenir", "slug": "avenir", "category": "sans-serif"},
    {"label": "Century Gothic", "slug": "century-gothic", "category": "sans-serif"},
    {"label": "Verdana", "slug": "verdana", "category": "sans-serif"},
    {"label": "Helvetica Neue", "slug": "helvetica-neue", "category": "sans-serif"},
    {"label": "Myriad Pro", "slug": "myriad-pro", "category": "sans-serif"},
    {"label": "Trajan", "slug": "trajan", "category": "serif"},
    {"label": "Optima", "slug": "optima", "category": "sans-serif"},
    {"label": "Eurostile", "slug": "eurostile", "category": "sans-serif"},
    {"label": "Trajan Pro", "slug": "trajan-pro", "category": "serif"},
    {"label": "Bodoni", "slug": "bodoni", "category": "serif"},
    {"label": "Didot", "slug": "didot", "category": "serif"},
    {"label": "Gill Sans", "slug": "gill-sans", "category": "sans-serif"},
    {"label": "San Francisco", "slug": "san-francisco", "category": "sans-serif"},
    {"label": "Avant Garde", "slug": "avant-garde", "category": "sans-serif"},
    {"label": "DIN", "slug": "din", "category": "sans-serif"},
    {"label": "Univers", "slug": "univers", "category": "sans-serif"},
    {"label": "Frutiger", "slug": "frutiger", "category": "sans-serif"},
    {"label": "Monotype Corsiva", "slug": "monotype-corsiva", "category": "script"},
    {"label": "Museo Sans", "slug": "museo-sans", "category": "sans-serif"},
    {"label": "Knockout", "slug": "knockout", "category": "sans-serif"},
    {"label": "SF Pro", "slug": "sf-pro", "category": "sans-serif"},
    {"label": "Aptos", "slug": "aptos", "category": "sans-serif"},
    {"label": "Georgia", "slug": "georgia", "category": "serif"},
    {"label": "Brandon Grotesque", "slug": "brandon-grotesque", "category": "sans-serif"},
    {"label": "Copperplate", "slug": "copperplate", "category": "serif"},
    {"label": "Cooper Black", "slug": "cooper-black", "category": "serif"},
    {"label": "Garamond", "slug": "garamond", "category": "serif"},
    {"label": "Segoe UI", "slug": "segoe-ui", "category": "sans-serif"},
    {"label": "Spotify", "slug": "spotify", "category": "sans-serif"},
    {"label": "Satoshi", "slug": "satoshi", "category": "sans-serif"},
    {"label": "Sofia Pro", "slug": "sofia-pro", "category": "sans-serif"},
    {"label": "Recoleta", "slug": "recoleta", "category": "serif"},
    {"label": "Product Sans", "slug": "product-sans", "category": "sans-serif"},
    {"label": "Google Sans", "slug": "google-sans", "category": "sans-serif"},
    {"label": "Canva Sans", "slug": "canva-sans", "category": "sans-serif"},
    {"label": "Gilroy", "slug": "gilroy", "category": "sans-serif"},
    {"label": "Garet", "slug": "garet", "category": "sans-serif"},
    # --- Fonts already in the corpus ---
    {"label": "Montserrat", "slug": "montserrat", "category": "sans-serif"},
    {"label": "Roboto", "slug": "roboto", "category": "sans-serif"},
    {"label": "Bebas Neue", "slug": "bebas-neue", "category": "sans-serif"},
    {"label": "Open Sans", "slug": "open-sans", "category": "sans-serif"},
    {"label": "DejaVu Sans", "slug": "dejavu-sans", "category": "sans-serif"},
    {"label": "DM Sans", "slug": "dm-sans", "category": "sans-serif"},
    {"label": "Cinzel", "slug": "cinzel", "category": "serif"},
    {"label": "Cormorant Garamond", "slug": "cormorant-garamond", "category": "serif"},
    {"label": "Great Vibes", "slug": "great-vibes", "category": "script"},
    {"label": "Source Sans Pro", "slug": "source-sans-pro", "category": "sans-serif"},
    {"label": "Lato", "slug": "lato", "category": "sans-serif"},
    {"label": "League Spartan", "slug": "league-spartan", "category": "sans-serif"},
    {"label": "Raleway", "slug": "raleway", "category": "sans-serif"},
    {"label": "Playfair Display", "slug": "playfair-display", "category": "serif"},
    {"label": "Poppins", "slug": "poppins", "category": "sans-serif"},
]

# Every proprietary page is linked from /popular (internal links help
# search engines find them); hand-ordered entries above keep their place.
_listed = {entry["slug"] for entry in ALL_POPULAR_FONTS}
ALL_POPULAR_FONTS += [
    {"label": name, "slug": slugify(name), "category": PROPRIETARY_FONTS[name]["category"]}
    for name in PROPRIETARY_TO_OPEN_SOURCE
    if slugify(name) not in _listed
]
del _listed

DEFAULT_K = 10

# DB family names of variable fonts that differ from the name people search
# for ("DM Sans 9pt" is DM Sans). Their pages canonicalise to the common name.
# Not every CORPUS_ALIASES entry: "Sans Serif" -> Inter is a redirect of a
# concept, not another name for the same page.
VARIANT_CANONICAL = {
    "DM Sans 9pt": "DM Sans",
    "Raleway Thin": "Raleway",
    "League Spartan Thin": "League Spartan",
    "Cormorant Garamond Light": "Cormorant Garamond",
    "Montserrat Thin": "Montserrat",
    "Nunito ExtraLight": "Nunito",
    "Nunito Sans 12pt ExtraLight": "Nunito Sans",
    "Source Sans 3 ExtraLight": "Source Sans 3",
    "Figtree Light": "Figtree",
    "Libre Franklin Thin": "Libre Franklin",
    **PROPRIETARY_CANONICAL,
}


def indexable_similar_slugs() -> set[str]:
    """/similar-to pages offered to search engines: every proprietary font and
    the corpus fonts on /popular (pages with search demand). The other
    ~2,800 corpus pages are near-identical templates; indexing them on a
    young domain risks a site-wide thin-content rating, so they are noindex
    and left out of the sitemap until Search Console shows demand."""
    aliases = {slugify(name) for name in PROPRIETARY_CANONICAL}
    return (
        {slugify(name) for name in PROPRIETARY_TO_OPEN_SOURCE}
        | {entry["slug"] for entry in ALL_POPULAR_FONTS}
    ) - aliases


def similar_page_seo(slug: str, display_name: str) -> tuple[str, bool]:
    """(canonical slug, indexable) for a /similar-to page. A requested slug
    that is itself indexable is its own canonical (e.g. "source-sans-pro",
    the name people search for, though the font is now Source Sans 3)."""
    indexable = indexable_similar_slugs()
    requested = slugify(slug)
    if requested in indexable:
        return requested, True
    canonical = slugify(VARIANT_CANONICAL.get(display_name, display_name))
    return canonical, canonical in indexable


@web_bp.get("/")
def index():
    store = current_app.config["STORE"]
    families = store.list_font_families(clean_only=True, indexed_only=True)

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
        total_popular=len(ALL_POPULAR_FONTS),
    )


@web_bp.get("/popular")
def popular():
    """Full index of popular font searches, grouped by category."""
    groups = {}
    for entry in ALL_POPULAR_FONTS:
        cat = entry["category"]
        groups.setdefault(cat, []).append(entry)

    # Sort categories in a sensible display order
    category_order = ["sans-serif", "serif", "monospace", "script"]
    category_labels = {
        "sans-serif": "Sans-Serif",
        "serif": "Serif",
        "monospace": "Monospace",
        "script": "Script & Decorative",
    }
    ordered_groups = []
    for cat in category_order:
        if cat in groups:
            items = sorted(groups[cat], key=lambda e: e["label"].lower())
            ordered_groups.append({"label": category_labels.get(cat, cat.title()), "fonts": items})

    return render_template(
        "popular.html",
        groups=ordered_groups,
        total=len(ALL_POPULAR_FONTS),
    )


@web_bp.get("/font-sample/<path:filename>")
def font_sample(filename: str):
    """Pregenerated "quick brown fox" PNG for a corpus font (``?style=`` picks a
    variable-font instance). Rendered and saved on a miss."""
    from fontmatch.samples import SampleStore
    from fontmatch.web.api import resolve_font_file

    if not filename.endswith(".png"):
        abort(404)
    store = current_app.config["STORE"]
    corpus_dirs = current_app.config.get("CORPUS_DIRS", [])

    def resolve(name: str):
        if not store._get_font_license(name):
            return None  # only open-source corpus fonts get samples
        return resolve_font_file(store, corpus_dirs, name, walk=False)

    samples = SampleStore(current_app.config["SAMPLES_DIR"], resolve)
    path = samples.get(filename[: -len(".png")], request.args.get("style", "")[:64])
    if path is None:
        abort(404)
    return send_file(path, mimetype="image/png", max_age=7 * 86400)


@web_bp.get("/privacy")
def privacy():
    return render_template("privacy.html")


@web_bp.get("/terms")
def terms():
    return render_template("terms.html")


@web_bp.get("/support")
def support():
    return render_template("support.html")


@web_bp.get("/.well-known/openai-apps-challenge")
def openai_apps_challenge():
    """Domain verification for the ChatGPT plugin directory: the token, verbatim."""
    token = current_app.config.get("APPS_CHALLENGE", "")
    if not token:
        abort(404)
    return Response(token, mimetype="text/plain")


@web_bp.get("/identify")
def identify_form():
    # Upload pages never load analytics: nothing a visitor uploads may reach Clarity.
    return render_template("identify.html", matches=None, no_analytics=True)


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
            no_analytics=True,
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
        no_analytics=True,
    )


def _preview_data_uri(raw: bytes) -> str | None:
    """Small JPEG of the upload to show beside the results. Not stored: it is
    what a share link or feedback with "keep this image" sends back."""
    from fontmatch.image.prep import ImageError, load_image

    try:
        img = load_image(raw)
    except ImageError:
        return None
    img.thumbnail((1000, 1000))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=80)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


@web_bp.post("/identify-image")
def identify_image_submit():
    from fontmatch.image.prep import ImageError
    from fontmatch.image.service import EngineUnavailable, NoTextFound
    from fontmatch.samples import sample_url
    from fontmatch.web.api import identify_image_cached, image_result_key
    from fontmatch.web.user_content import preview_token

    f = request.files.get("image")
    if f is None or not f.filename:
        flash("Please choose an image to upload.", "error")
        return redirect(url_for("web.identify_form") + "#image")
    raw = f.read()
    hint = (request.form.get("text_hint") or "").strip()
    current_app.config["STORE"].log_request("/identify-image")
    try:
        result = identify_image_cached(raw, hint)
    except (ImageError, NoTextFound) as exc:
        flash(f"{exc} Try a tighter crop, or type the text in the box below.", "error")
        return redirect(url_for("web.identify_form") + "#image")
    except EngineUnavailable:
        flash("Image matching is temporarily unavailable, please try again.", "error")
        return redirect(url_for("web.identify_form") + "#image")

    for m in result["matches"]:
        m["sample_url"] = sample_url(m["name"], m.get("style") or "")
        m["license_label"] = license_label(m.get("license_id") or "unknown")
    preview = _preview_data_uri(raw)
    return render_template(
        "identify.html",
        matches=None,
        image_result=result,
        image_preview=preview,
        result_token=preview_token(image_result_key(raw, hint), preview),
        text_hint=hint,
        no_analytics=True,
    )


RELATED_LIMIT = 8


@web_bp.get("/og/similar-to/<slug>.png")
def og_similar(slug: str):
    """Preview card for a /similar-to page; only canonical slugs (bounded
    disk use: one card per page)."""
    from fontmatch.samples import SampleStore
    from fontmatch.web.api import resolve_font_file
    from fontmatch.web.og import card_filename, render_card

    if len(slug) > 100 or slug != slugify(slug):
        abort(404)
    store = current_app.config["STORE"]
    result = find_similar(store, deslugify(slug), slug=slug, k=DEFAULT_K)
    if result is None:
        abort(404)
    font_row = result.font_row
    if similar_page_seo(slug, result.display_name)[0] != slug:
        abort(404)
    gf_source = google_fonts_source_for(store, font_row["family"])
    shown = (google_fonts_name(gf_source) if gf_source else None) or VARIANT_CANONICAL.get(
        font_row["family"], font_row["family"]
    )
    if result.prop:
        kicker, title = f"Free alternative to {result.display_name}", shown
    else:
        kicker, title = "Free fonts similar to", shown
    target = Path(current_app.config["OG_DIR"]) / card_filename(
        slug, font_row["name"], kicker, title
    )
    if target.with_suffix(".none").exists():
        return redirect(url_for("static", filename="og-default.png"))
    if not target.is_file():
        font_path = resolve_font_file(
            store, current_app.config.get("CORPUS_DIRS", []), font_row["name"], walk=False
        )
        try:
            if font_path is None:
                raise ValueError("font file not found")
            data = render_card(font_path, "", kicker, title)
        except Exception as exc:  # glyph-less or broken font: use the site card
            current_app.logger.warning("og: cannot render %s: %s", slug, exc)
            SampleStore(target.parent, lambda name: None)._mark_failed(target)
            return redirect(url_for("static", filename="og-default.png"))
        SampleStore(target.parent, lambda name: None).save(target, data)
    resp = send_file(target, mimetype="image/png", max_age=7 * 86400)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


def _related_proprietary(name: str, prop: dict | None) -> list[dict]:
    """Other proprietary fonts of the same category, for internal links."""
    if not prop:
        return []
    out = []
    for other in PROPRIETARY_TO_OPEN_SOURCE:
        meta = PROPRIETARY_FONTS.get(other, {})
        if other != name and meta.get("category") == prop["category"]:
            out.append({"label": other, "slug": slugify(other)})
    return out[:RELATED_LIMIT]


@web_bp.get("/similar-to/<slug>")
def similar_to(slug: str):
    # Validate slug: reject overlong or null-byte-containing slugs
    if len(slug) > 100 or "\x00" in slug:
        abort(404)

    store = current_app.config["STORE"]
    family_name = deslugify(slug)

    result = find_similar(store, family_name, slug=slug, k=DEFAULT_K)
    if result is None:
        display_name = lookup_proprietary(family_name) or family_name
        return (
            render_template(
                "similar.html",
                family_name=display_name,
                matches=None,
                found=False,
                prop=None,
                robots="noindex",
                canonical_url=None,
            ),
            404,
        )
    display_name, font_row = result.display_name, result.font_row
    prop_meta, results = result.prop, result.matches

    # Check if the corpus font file is available for download
    from fontmatch.web.helpers import _is_crawled_source

    corpus_source = store.get_font_source(font_row["name"])
    corpus_has_file = corpus_source is not None and not _is_crawled_source(corpus_source)
    gf_source = google_fonts_source_for(store, font_row["family"])
    corpus_gf_family = google_fonts_name(gf_source) if gf_source else None
    corpus_gf_url = google_fonts_url(font_row["family"], gf_source) if gf_source else None
    corpus_display = corpus_gf_family or VARIANT_CANONICAL.get(
        font_row["family"], font_row["family"]
    )
    license_id = font_row.get("license_id") or "unknown"
    if license_id == "unknown" and gf_source:
        license_id = google_fonts_license(gf_source) or license_id
    corpus_license = license_label(license_id)
    if corpus_license in ("Unknown", "unknown"):
        corpus_license = None

    canonical_slug, indexable = similar_page_seo(slug, display_name)
    if not prop_meta:  # a corpus font's page is about the font: one name for it
        display_name = corpus_display
    return render_template(
        "similar.html",
        og_image=url_for("web.og_similar", slug=canonical_slug, _external=True),
        canonical_url=url_for("web.similar_to", slug=canonical_slug, _external=True),
        robots=None if indexable else "noindex, follow",
        family_name=display_name,
        corpus_family=corpus_display,
        corpus_slug=similar_page_seo(slugify(font_row["family"]), font_row["family"])[0],
        corpus_gf_css=google_fonts_css_name(corpus_gf_family),
        corpus_gf_url=corpus_gf_url,
        corpus_license=corpus_license,
        related=_related_proprietary(display_name, prop_meta),
        query_font_name=font_row["name"],
        corpus_has_file=corpus_has_file,
        matches=enrich_matches(results, store=store),
        found=True,
        slugify=slugify,
        prop=prop_meta,
    )
