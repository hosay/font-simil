"""Image-result feedback ("Was this right?") and public share links (/r/<id>).

Uploads are never stored by default. An image is kept only on request: when
the visitor creates a share link, or ticks "keep this image" with feedback.

The results page carries a signed token over the result's cache key and the
SHA-256 of the JPEG preview the server made of the upload. The client posts
that preview back (the exact bytes of the page's data URI) with the token, so
only the image that produced a result can be attached to it: the endpoints
can't be used to host arbitrary images. Results are read from the server's
cache, never from the client.

These routes are outside /api on purpose: /api/* allows any origin (CORS).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
import secrets
from pathlib import Path

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from itsdangerous import BadSignature, URLSafeTimedSerializer

user_content_bp = Blueprint("user_content", __name__)

FEEDBACK_IMAGE_DAYS = 730  # the privacy policy promises at most 2 years
TOKEN_SALT = "dupefont-image-result"
TOKEN_MAX_AGE = 24 * 3600  # a results page can be shared for a day
MAX_PREVIEW_BYTES = 2 * 1024 * 1024
MAX_FONT_NAME = 100
DEFAULT_SECRET = "dev-key-change-me"
SHARE_ID = re.compile(r"^[A-Za-z0-9_-]{10}$")
DATA_URI_PREFIX = "data:image/jpeg;base64,"


# --- Tokens ------------------------------------------------------------------


def can_sign(app) -> bool:
    """Tokens need a real secret: with the public default anyone could forge
    one (tests excepted)."""
    return bool(app.secret_key) and (app.secret_key != DEFAULT_SECRET or app.config.get("TESTING"))


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.secret_key, salt=TOKEN_SALT)


def make_token(result_key: str, preview: bytes) -> str | None:
    if not can_sign(current_app):
        return None
    return _serializer().dumps({"k": result_key, "p": hashlib.sha256(preview).hexdigest()})


def _read_token(token) -> dict | None:
    if not isinstance(token, str) or not can_sign(current_app):
        return None
    try:
        data = _serializer().loads(token, max_age=TOKEN_MAX_AGE)
    except BadSignature:  # includes SignatureExpired
        return None
    if not isinstance(data, dict) or not isinstance(data.get("k"), str):
        return None
    return data


def _preview_bytes(data_uri, token_data: dict) -> bytes | None:
    """The posted preview, if it is the one the token was issued for."""
    if not isinstance(data_uri, str) or not data_uri.startswith(DATA_URI_PREFIX):
        return None
    if len(data_uri) > MAX_PREVIEW_BYTES * 4 // 3 + len(DATA_URI_PREFIX) + 4:
        return None
    try:
        raw = base64.b64decode(data_uri[len(DATA_URI_PREFIX) :], validate=True)
    except (binascii.Error, ValueError):
        return None
    digest = hashlib.sha256(raw).hexdigest()
    if not hmac.compare_digest(digest, str(token_data.get("p", ""))):
        return None
    return raw


def _cached_result(result_key: str) -> dict | None:
    from fontmatch.image.service import IMAGE_SCHEMA_VERSION

    cached = current_app.config["STORE"].get_cached_result(result_key, IMAGE_SCHEMA_VERSION)
    return cached[0] if cached else None


def _content_dir(sub: str) -> Path:
    d = Path(current_app.config["USER_CONTENT_DIR"]) / sub
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save(path: Path, data: bytes) -> None:
    from fontmatch.samples import SampleStore

    SampleStore(path.parent, lambda name: None).save(path, data)


def _bad(message: str, status: int = 400):
    return jsonify({"error": message}), status


# --- Feedback ----------------------------------------------------------------


def expire_feedback_images(days: int = FEEDBACK_IMAGE_DAYS) -> int:
    """Delete feedback images older than the retention period. Run at startup."""
    names = current_app.config["STORE"].expire_feedback_images(days)
    folder = Path(current_app.config["USER_CONTENT_DIR"]) / "feedback"
    for name in names:
        if re.fullmatch(r"[A-Za-z0-9]{1,40}\.jpg", name or ""):
            (folder / name).unlink(missing_ok=True)
    return len(names)


@user_content_bp.post("/image-feedback")
def image_feedback():
    body = request.get_json(silent=True) or {}
    data = _read_token(body.get("token"))
    if data is None:
        return _bad("This result has expired. Upload the image again to send feedback.")
    verdict = body.get("verdict")
    if verdict not in ("yes", "no"):
        return _bad("verdict must be 'yes' or 'no'")
    correct = body.get("correct_font")
    correct = correct.strip()[:MAX_FONT_NAME] or None if isinstance(correct, str) else None

    image_file = None
    if body.get("keep_image") is True:
        raw = _preview_bytes(body.get("preview"), data)
        if raw is None:
            return _bad("The image doesn't match this result.")
        image_file = secrets.token_hex(12) + ".jpg"
        _save(_content_dir("feedback") / image_file, raw)

    result = _cached_result(data["k"])
    top = (result or {}).get("matches") or [{}]
    store = current_app.config["STORE"]
    replaced = store.save_image_feedback(
        data["k"], verdict, top[0].get("family"), correct, image_file,
        request.remote_addr or "unknown",
    )  # fmt: skip
    if replaced:
        (_content_dir("feedback") / replaced).unlink(missing_ok=True)
    return jsonify({"saved": True})


# --- Shares ------------------------------------------------------------------


def _share_or_404(share_id: str) -> dict:
    if not SHARE_ID.match(share_id):
        abort(404)
    share = current_app.config["STORE"].get_share(share_id)
    if share is None:
        abort(404)
    return share


@user_content_bp.post("/share")
def create_share():
    body = request.get_json(silent=True) or {}
    data = _read_token(body.get("token"))
    if data is None:
        return _bad("This result has expired. Upload the image again to share it.")
    raw = _preview_bytes(body.get("preview"), data)
    if raw is None:
        return _bad("The image doesn't match this result.")
    result = _cached_result(data["k"])
    if result is None:
        return _bad("This result has expired. Upload the image again to share it.", 410)
    from fontmatch.image.prep import ImageError, load_image

    try:
        load_image(raw)  # it's our own JPEG, but never serve what doesn't decode
    except ImageError:
        return _bad("The image can't be shared.")

    delete_token = secrets.token_urlsafe(16)
    token_hash = hashlib.sha256(delete_token.encode()).hexdigest()
    share_id = current_app.config["STORE"].create_share(
        secrets.token_urlsafe(8)[:10], data["k"], data["p"], result, token_hash
    )
    image = _content_dir("shares") / f"{share_id}.jpg"
    if not image.is_file():
        _save(image, raw)
    return jsonify(
        {
            "url": url_for("user_content.share_page", share_id=share_id, _external=True),
            "delete_url": url_for(
                "user_content.delete_share", share_id=share_id, token=delete_token, _external=True
            ),
        }
    )


def _noindex(resp: Response) -> Response:
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


@user_content_bp.get("/r/<share_id>")
def share_page(share_id: str):
    from fontmatch.samples import sample_url
    from fontmatch.web.api import _add_image_urls
    from fontmatch.web.helpers import license_label

    share = _share_or_404(share_id)
    result = _add_image_urls(share["result"], current_app.config["STORE"])
    for m in result["matches"]:
        m["sample_url"] = sample_url(m["name"], m.get("style") or "")
        m["license_label"] = license_label(m.get("license_id") or "unknown")
    image_url = url_for("user_content.share_image", share_id=share_id)
    html = render_template(
        "share.html",
        image_result=result,
        image_url=image_url,
        og_image=url_for("user_content.share_image", share_id=share_id, _external=True),
        robots="noindex, nofollow",
        no_analytics=True,  # user content: never in session recordings
    )
    return _noindex(Response(html))


@user_content_bp.get("/r/<share_id>.jpg")
def share_image(share_id: str):
    _share_or_404(share_id)
    path = Path(current_app.config["USER_CONTENT_DIR"]) / "shares" / f"{share_id}.jpg"
    if not path.is_file():
        abort(404)
    resp = send_file(path, mimetype="image/jpeg", max_age=86400)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return _noindex(resp)


def remove_share(share_id: str) -> bool:
    """Delete a share and its image (also used by the admin page)."""
    deleted = current_app.config["STORE"].delete_share(share_id)
    path = Path(current_app.config["USER_CONTENT_DIR"]) / "shares" / f"{share_id}.jpg"
    path.unlink(missing_ok=True)
    return deleted


@user_content_bp.route("/r/<share_id>/delete", methods=["GET", "POST"])
def delete_share(share_id: str):
    share = _share_or_404(share_id)
    token = request.args.get("token") or request.form.get("token") or ""
    expected = share["delete_token_hash"]
    if not hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), expected):
        abort(403)
    if request.method == "GET":
        html = render_template(
            "share_delete.html", share_id=share_id, token=token, robots="noindex, nofollow",
            no_analytics=True,
        )  # fmt: skip
        return _noindex(Response(html))
    remove_share(share_id)
    flash("The share link and its image were deleted.", "success")
    return redirect(url_for("web.identify_form"))


def preview_token(result_key: str, preview_data_uri: str | None) -> str | None:
    """Token for a results page (None when its preview couldn't be made)."""
    if not preview_data_uri or not preview_data_uri.startswith(DATA_URI_PREFIX):
        return None
    raw = base64.b64decode(preview_data_uri[len(DATA_URI_PREFIX) :])
    return make_token(result_key, raw)
