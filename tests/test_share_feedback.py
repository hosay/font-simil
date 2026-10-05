"""Image results: "Was this right?" feedback and public share links.

Uploads are still never stored by default. An image is kept only when the
visitor asks: a share link, or ticking "keep this image" with feedback. The
image sent back must be the exact preview the server produced for that
result (signed token over the result key and the preview's hash), so the
endpoints can't be used to host arbitrary images.
"""

import base64
import io
import re
from pathlib import Path

import pytest
from PIL import Image

from tests.test_web_image import FIXTURES, FakeIdentifier

TOKEN = re.compile(r'data-token="([^"]+)"')
PREVIEW = re.compile(r'<img src="(data:image/jpeg;base64,[^"]+)"')


def _png(text_color="black"):
    img = Image.new("RGB", (160, 50), "white")
    from PIL import ImageDraw

    ImageDraw.Draw(img).text((10, 15), "Hello", fill=text_color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture(scope="module")
def base_app(tmp_path_factory):
    from fontmatch.service.app import create_app

    tmp = tmp_path_factory.mktemp("share")
    return create_app(db_path=tmp / "test.db", fixture_dir=FIXTURES, testing=True)


@pytest.fixture
def app(base_app, monkeypatch):
    monkeypatch.setitem(base_app.config, "IMAGE_IDENTIFIER", FakeIdentifier())
    base_app.config["LIMITER"].reset()
    return base_app


def _result_page(client, image=None):
    resp = client.post(
        "/identify-image",
        data={"image": (io.BytesIO(image or _png()), "shot.png"), "text_hint": "Hello"},
        content_type="multipart/form-data",
    )
    html = resp.get_data(as_text=True)
    return TOKEN.search(html).group(1), PREVIEW.search(html).group(1)


def _files(app, sub):
    d = Path(app.config["USER_CONTENT_DIR"]) / sub
    return sorted(d.glob("*.jpg")) if d.is_dir() else []


class TestFeedback:
    def test_yes_is_recorded_without_storing_the_image(self, app):
        c = app.test_client()
        token, _ = _result_page(c)
        before = len(_files(app, "feedback"))
        resp = c.post("/image-feedback", json={"token": token, "verdict": "yes"})
        assert resp.status_code == 200 and resp.get_json()["saved"]
        assert len(_files(app, "feedback")) == before
        stats = app.config["STORE"].image_feedback_stats(days=30)
        assert stats["yes"] >= 1

    def test_no_with_font_and_consented_image(self, app):
        c = app.test_client()
        token, preview = _result_page(c, _png("navy"))
        c.post("/image-feedback", json={"token": token, "verdict": "no"})
        resp = c.post(
            "/image-feedback",
            json={"token": token, "verdict": "no", "correct_font": "Futura", "keep_image": True,
                  "preview": preview},
        )  # fmt: skip
        assert resp.status_code == 200
        rows = app.config["STORE"].recent_image_feedback(limit=5)
        row = next(r for r in rows if r["correct_font"] == "Futura")
        assert row["verdict"] == "no" and row["top_font"] == "DejaVu Sans"
        assert (Path(app.config["USER_CONTENT_DIR"]) / "feedback" / row["image_file"]).is_file()
        # One verdict per visitor and result: the second post updated the first.
        stats = app.config["STORE"].image_feedback_stats(days=30)
        assert stats["no"] == 1

    def test_image_without_consent_is_not_kept(self, app):
        c = app.test_client()
        token, preview = _result_page(c, _png("green"))
        before = len(_files(app, "feedback"))
        c.post("/image-feedback", json={"token": token, "verdict": "no", "preview": preview})
        assert len(_files(app, "feedback")) == before

    @pytest.mark.parametrize("verdict", ["", "maybe", None])
    def test_bad_verdict(self, app, verdict):
        token, _ = _result_page(app.test_client())
        resp = app.test_client().post("/image-feedback", json={"token": token, "verdict": verdict})
        assert resp.status_code == 400


class TestTokens:
    def test_tampered_token_rejected(self, app):
        token, preview = _result_page(app.test_client())
        bad = token[:-2] + ("AA" if token[-2:] != "AA" else "BB")
        for path, body in [
            ("/image-feedback", {"token": bad, "verdict": "yes"}),
            ("/share", {"token": bad, "preview": preview}),
        ]:
            assert app.test_client().post(path, json=body).status_code == 400

    def test_other_image_rejected(self, app):
        token, _ = _result_page(app.test_client())
        _, other = _result_page(app.test_client(), _png("red"))
        resp = app.test_client().post("/share", json={"token": token, "preview": other})
        assert resp.status_code == 400

    def test_expired_token_rejected(self, app, monkeypatch):
        from fontmatch.web import user_content

        token, preview = _result_page(app.test_client())
        monkeypatch.setattr(user_content, "TOKEN_MAX_AGE", -1)
        resp = app.test_client().post("/share", json={"token": token, "preview": preview})
        assert resp.status_code == 400

    def test_oversized_preview_rejected(self, app):
        token, _ = _result_page(app.test_client())
        huge = "data:image/jpeg;base64," + "A" * (3 * 1024 * 1024)
        resp = app.test_client().post("/share", json={"token": token, "preview": huge})
        assert resp.status_code in (400, 413)

    def test_not_a_cross_origin_api(self, app):
        token, preview = _result_page(app.test_client())
        resp = app.test_client().post(
            "/share",
            json={"token": token, "preview": preview},
            headers={"Origin": "https://evil.example"},
        )
        assert "Access-Control-Allow-Origin" not in resp.headers


class TestShare:
    def _share(self, app, image=None):
        c = app.test_client()
        token, preview = _result_page(c, image)
        resp = c.post("/share", json={"token": token, "preview": preview})
        assert resp.status_code == 200
        return resp.get_json(), preview

    def test_share_page_shows_image_and_results(self, app):
        body, preview = self._share(app, _png("purple"))
        assert re.fullmatch(r"/r/[A-Za-z0-9_-]{10}", body["url"].split("localhost", 1)[-1])
        path = "/" + body["url"].split("://", 1)[1].split("/", 1)[1]
        resp = app.test_client().get(path)
        html = resp.get_data(as_text=True)
        assert resp.status_code == 200
        assert "Hello World" in html and "DejaVu Sans" in html and "Arimo" in html
        assert 'content="noindex' in html and resp.headers["X-Robots-Tag"].startswith("noindex")
        assert "clarity" not in html.lower() or "data-clarity" not in html
        img = app.test_client().get(path + ".jpg")
        assert img.status_code == 200 and img.mimetype == "image/jpeg"
        assert img.headers["X-Content-Type-Options"] == "nosniff"
        assert img.data == base64.b64decode(preview.split(",", 1)[1])
        assert path + ".jpg" in html  # og:image

    def test_each_share_has_its_own_delete_link(self, app):
        # Anyone uploading the same viral logo gets a valid token; sharing it
        # again must not take over (or invalidate) the first sharer's link.
        c = app.test_client()
        token, preview = _result_page(c, _png("teal"))
        a = c.post("/share", json={"token": token, "preview": preview}).get_json()
        b = c.post("/share", json={"token": token, "preview": preview}).get_json()
        assert a["url"] != b["url"]
        assert app.test_client().get(a["delete_url"].split("localhost", 1)[-1]).status_code == 200
        delete_b = b["delete_url"].split("localhost", 1)[-1]
        assert app.test_client().post(delete_b).status_code in (200, 302)
        assert app.test_client().get(a["url"].split("localhost", 1)[-1]).status_code == 200

    def test_delete_link(self, app):
        body, _ = self._share(app, _png("orange"))
        path = "/" + body["url"].split("://", 1)[1].split("/", 1)[1]
        delete = "/" + body["delete_url"].split("://", 1)[1].split("/", 1)[1]
        assert app.test_client().get(delete).status_code == 200  # confirmation page
        wrong = re.sub(r"token=[^&]+", "token=nope", delete)
        assert app.test_client().post(wrong).status_code == 403
        assert app.test_client().post(delete).status_code in (200, 302)
        assert app.test_client().get(path).status_code == 404
        assert app.test_client().get(path + ".jpg").status_code == 404
        assert app.test_client().post(delete).status_code == 404  # already gone

    @pytest.mark.parametrize("bad", ["../etc/x", "short", "a" * 11, "abc%2F..%2Fxx"])
    def test_invalid_ids_are_404(self, app, bad):
        assert app.test_client().get(f"/r/{bad}").status_code == 404
        assert app.test_client().get(f"/r/{bad}.jpg").status_code == 404

    def test_expired_result_cannot_be_shared(self, app):
        c = app.test_client()
        token, preview = _result_page(c, _png("brown"))
        with app.config["STORE"]._lock:
            app.config["STORE"].conn.execute("DELETE FROM match_cache")
            app.config["STORE"].conn.commit()
        resp = c.post("/share", json={"token": token, "preview": preview})
        assert resp.status_code == 410

    def test_results_page_explains_what_sharing_stores(self, app):
        resp = app.test_client().post(
            "/identify-image",
            data={"image": (io.BytesIO(_png()), "shot.png")},
            content_type="multipart/form-data",
        )
        html = resp.get_data(as_text=True)
        assert "Was this right?" in html
        assert "public link" in html.lower()


def test_no_tokens_with_the_default_secret_key(tmp_path, monkeypatch):
    from fontmatch.web import user_content

    class App:
        secret_key = "dev-key-change-me"
        config = {"TESTING": False}

    assert user_content.can_sign(App()) is False


def test_updating_feedback_keeps_its_original_time(base_app):
    store = base_app.config["STORE"]
    store.save_image_feedback("img:t:time", "no", None, None, None, "7.7.7.7")
    with store._lock:
        store.conn.execute(
            "UPDATE image_feedback SET created_at = '2020-01-01 00:00:00' "
            "WHERE result_key = 'img:t:time'"
        )
        store.conn.commit()
    store.save_image_feedback("img:t:time", "no", None, "Futura", None, "7.7.7.7")
    with store._lock:
        row = store.conn.execute(
            "SELECT created_at, correct_font FROM image_feedback WHERE result_key = 'img:t:time'"
        ).fetchone()
    assert row["created_at"] == "2020-01-01 00:00:00" and row["correct_font"] == "Futura"


def test_forget_old_ips_covers_feedback(base_app):
    store = base_app.config["STORE"]
    store.save_image_feedback("img:test:x", "yes", "Arimo", None, None, "1.2.3.4")
    with store._lock:
        store.conn.execute("UPDATE image_feedback SET created_at = datetime('now', '-400 days')")
        store.conn.commit()
    store.forget_old_rating_ips(days=365)
    with store._lock:
        ips = [r[0] for r in store.conn.execute("SELECT ip_address FROM image_feedback")]
    assert all(ip is None for ip in ips)


def test_old_feedback_images_expire(base_app):
    from fontmatch.web.user_content import expire_feedback_images

    store = base_app.config["STORE"]
    d = Path(base_app.config["USER_CONTENT_DIR"]) / "feedback"
    d.mkdir(parents=True, exist_ok=True)
    (d / "old0000000000000000000000.jpg").write_bytes(b"x")
    store.save_image_feedback(
        "img:t:old", "no", None, None, "old0000000000000000000000.jpg", "9.9.9.9"
    )
    with store._lock:
        store.conn.execute(
            "UPDATE image_feedback SET created_at = datetime('now', '-800 days') "
            "WHERE result_key = 'img:t:old'"
        )
        store.conn.commit()
    with base_app.app_context():
        assert expire_feedback_images(days=730) == 1
    assert not (d / "old0000000000000000000000.jpg").exists()
    row = [
        r
        for r in store.recent_image_feedback(limit=100)
        if r["top_font"] is None and r["verdict"] == "no"
    ]
    assert all(r["image_file"] is None for r in row)


def test_admin_post_from_another_site_is_refused(base_app, monkeypatch):
    from werkzeug.security import generate_password_hash

    monkeypatch.setitem(base_app.config, "ADMIN_PASSWORD_HASH", generate_password_hash("pw"))
    auth = {"Authorization": "Basic " + base64.b64encode(b"dfadmin:pw").decode()}
    resp = base_app.test_client().post(
        "/admin/shares/AbCdEfGhIj/delete", headers={**auth, "Origin": "https://evil.example"}
    )
    assert resp.status_code == 403
