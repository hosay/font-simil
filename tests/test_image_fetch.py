"""Tests for the SSRF-guarded image fetcher used by the ChatGPT (MCP) tool."""

import ipaddress

import httpx
import pytest

from fontmatch.image.fetch import (
    FetchError,
    fetch_image_bytes,
    is_public_address,
    validate_url,
)

PUBLIC_IP = "93.184.216.34"


def _resolver(mapping):
    def resolve(host):
        if host not in mapping:
            raise FetchError(f"Could not resolve {host}")
        return mapping[host]

    return resolve


def _transport(handler):
    return httpx.MockTransport(handler)


class TestIsPublicAddress:
    @pytest.mark.parametrize(
        "ip",
        [
            "127.0.0.1",
            "10.1.2.3",
            "172.16.0.1",
            "192.168.1.1",
            "169.254.169.254",  # cloud metadata
            "100.67.193.2",  # tailscale CGNAT
            "0.0.0.0",
            "::1",
            "fe80::1",
            "fd00::1",
            "::ffff:127.0.0.1",
            "224.0.0.1",
        ],
    )
    def test_rejects_non_public(self, ip):
        assert not is_public_address(ipaddress.ip_address(ip))

    @pytest.mark.parametrize("ip", [PUBLIC_IP, "2606:4700::6810:85e5"])
    def test_accepts_public(self, ip):
        assert is_public_address(ipaddress.ip_address(ip))


class TestValidateUrl:
    def test_accepts_https(self):
        assert validate_url("https://files.example.com/a.png?sig=1").host == "files.example.com"

    @pytest.mark.parametrize(
        "url",
        [
            "http://files.example.com/a.png",
            "ftp://files.example.com/a.png",
            "file:///etc/passwd",
            "https://user:pw@files.example.com/a.png",
            "https://files.example.com:8443/a.png",
            "https:///nohost",
            "not a url",
        ],
    )
    def test_rejects(self, url):
        with pytest.raises(FetchError):
            validate_url(url)


class TestFetchImageBytes:
    def test_returns_body_and_pins_resolved_ip(self):
        seen = {}

        def handler(request):
            seen["host_header"] = request.headers["host"]
            seen["url_host"] = request.url.host
            seen["sni"] = request.extensions.get("sni_hostname")
            return httpx.Response(200, content=b"PNGDATA", headers={"content-type": "image/png"})

        result = fetch_image_bytes(
            "https://files.example.com/a.png",
            resolver=_resolver({"files.example.com": [PUBLIC_IP]}),
            transport=_transport(handler),
        )
        assert result.data == b"PNGDATA"
        assert result.content_type == "image/png"
        # Connection goes to the IP we vetted, not a second DNS lookup.
        assert seen["url_host"] == PUBLIC_IP
        assert seen["host_header"] == "files.example.com"
        assert seen["sni"] == "files.example.com"

    def test_rejects_private_resolution(self):
        def handler(request):  # pragma: no cover - must not be reached
            raise AssertionError("request should not be sent")

        with pytest.raises(FetchError, match="not allowed"):
            fetch_image_bytes(
                "https://evil.example.com/a.png",
                resolver=_resolver({"evil.example.com": ["169.254.169.254"]}),
                transport=_transport(handler),
            )

    def test_rejects_if_any_resolved_address_is_private(self):
        with pytest.raises(FetchError, match="not allowed"):
            fetch_image_bytes(
                "https://mixed.example.com/a.png",
                resolver=_resolver({"mixed.example.com": [PUBLIC_IP, "10.0.0.5"]}),
                transport=_transport(lambda r: httpx.Response(200, content=b"x")),
            )

    def test_follows_redirect_and_rechecks_target(self):
        def handler(request):
            if request.headers["host"] == "a.example.com":
                return httpx.Response(302, headers={"location": "https://b.example.com/img.png"})
            return httpx.Response(200, content=b"OK")

        result = fetch_image_bytes(
            "https://a.example.com/x",
            resolver=_resolver({"a.example.com": [PUBLIC_IP], "b.example.com": ["8.8.8.8"]}),
            transport=_transport(handler),
        )
        assert result.data == b"OK"

    def test_redirect_to_private_is_rejected(self):
        def handler(request):
            return httpx.Response(302, headers={"location": "https://internal.example.com/"})

        with pytest.raises(FetchError, match="not allowed"):
            fetch_image_bytes(
                "https://a.example.com/x",
                resolver=_resolver(
                    {"a.example.com": [PUBLIC_IP], "internal.example.com": ["127.0.0.1"]}
                ),
                transport=_transport(handler),
            )

    def test_redirect_to_http_is_rejected(self):
        def handler(request):
            return httpx.Response(302, headers={"location": "http://b.example.com/"})

        with pytest.raises(FetchError, match="https"):
            fetch_image_bytes(
                "https://a.example.com/x",
                resolver=_resolver({"a.example.com": [PUBLIC_IP]}),
                transport=_transport(handler),
            )

    def test_too_many_redirects(self):
        def handler(request):
            return httpx.Response(302, headers={"location": "https://a.example.com/again"})

        with pytest.raises(FetchError, match="redirect"):
            fetch_image_bytes(
                "https://a.example.com/x",
                resolver=_resolver({"a.example.com": [PUBLIC_IP]}),
                transport=_transport(handler),
            )

    def test_size_limit_enforced_while_streaming(self):
        def handler(request):
            return httpx.Response(200, content=b"x" * 2048)

        with pytest.raises(FetchError, match="too large"):
            fetch_image_bytes(
                "https://a.example.com/x",
                resolver=_resolver({"a.example.com": [PUBLIC_IP]}),
                transport=_transport(handler),
                max_bytes=1024,
            )

    def test_http_error_status(self):
        with pytest.raises(FetchError, match="404"):
            fetch_image_bytes(
                "https://a.example.com/x",
                resolver=_resolver({"a.example.com": [PUBLIC_IP]}),
                transport=_transport(lambda r: httpx.Response(404)),
            )


class TestReviewFixes:
    @pytest.mark.parametrize("ip", ["fec0::1", "2001:db8::1", "64:ff9b::7f00:1", "2002:7f00:1::1"])
    def test_rejects_more_ipv6_forms(self, ip):
        assert not is_public_address(ipaddress.ip_address(ip))

    def test_prefers_ipv4_when_both_resolve(self):
        seen = {}

        def handler(request):
            seen["host"] = request.url.host
            return httpx.Response(200, content=b"ok")

        fetch_image_bytes(
            "https://dual.example.com/a.png",
            resolver=_resolver({"dual.example.com": ["2606:4700::6810:85e5", PUBLIC_IP]}),
            transport=_transport(handler),
        )
        assert seen["host"] == PUBLIC_IP

    def test_network_errors_become_fetch_errors(self):
        def handler(request):
            raise httpx.ConnectError("boom at 10.1.2.3 with ssl internals")

        with pytest.raises(FetchError) as exc:
            fetch_image_bytes(
                "https://a.example.com/x",
                resolver=_resolver({"a.example.com": [PUBLIC_IP]}),
                transport=_transport(handler),
            )
        assert "10.1.2.3" not in str(exc.value)

    def test_total_deadline(self, monkeypatch):
        import fontmatch.image.fetch as f

        clock = iter([0.0, 0.0, 100.0, 100.0, 100.0])
        monkeypatch.setattr(f.time, "monotonic", lambda: next(clock, 100.0))

        def handler(request):
            return httpx.Response(200, content=b"x" * 10)

        with pytest.raises(FetchError, match="too long"):
            fetch_image_bytes(
                "https://a.example.com/x",
                resolver=_resolver({"a.example.com": [PUBLIC_IP]}),
                transport=_transport(handler),
                timeout=15.0,
            )
