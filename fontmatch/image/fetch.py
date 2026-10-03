"""SSRF-guarded download of user images (e.g. ChatGPT's ``download_url``).

We don't allowlist hosts (OpenAI may change its file CDN without notice).
Instead every hop is checked:

- https only, default port, no credentials in the URL
- the hostname is resolved once; if *any* address is not public
  (private, loopback, link-local, CGNAT/Tailscale, multicast, reserved)
  the request is refused
- the connection is pinned to the vetted IP (Host header + TLS SNI keep the
  original hostname), so a second DNS lookup can't rebind to an internal IP
- redirects are followed manually and each target is re-checked
- the body is streamed and aborted past ``max_bytes``
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urljoin

import httpx

logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_REDIRECTS = 3
TIMEOUT_SECONDS = 15.0

# 100.64.0.0/10 (CGNAT, used by Tailscale) is not flagged by is_private.
_EXTRA_BLOCKED = [ipaddress.ip_network("100.64.0.0/10")]


class FetchError(Exception):
    """The image could not be fetched safely. Message is user-presentable."""


@dataclass(frozen=True)
class FetchedImage:
    data: bytes
    content_type: str
    final_host: str


Resolver = Callable[[str], list[str]]


def is_public_address(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if isinstance(ip, ipaddress.IPv6Address) and (ip.sixtofour or ip.teredo or ip.is_site_local):
        return False  # tunnels can embed internal IPv4 addresses; fec0::/10 is site-local
    if not ip.is_global or ip.is_multicast or ip.is_reserved:
        return False
    return not any(ip in net for net in _EXTRA_BLOCKED)


def validate_url(url: str) -> httpx.URL:
    try:
        parsed = httpx.URL(url)
    except Exception as exc:
        raise FetchError("Invalid image URL") from exc
    if parsed.scheme != "https":
        raise FetchError("Only https image URLs are supported")
    if not parsed.host:
        raise FetchError("Invalid image URL: missing host")
    if parsed.userinfo:
        raise FetchError("Image URLs with credentials are not allowed")
    if parsed.port not in (None, 443):
        raise FetchError("Image URLs on non-standard ports are not allowed")
    return parsed


def _system_resolver(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise FetchError(f"Could not resolve image host {host}") from exc
    return sorted({info[4][0] for info in infos})


def _vetted_ip(host: str, resolver: Resolver) -> str:
    addresses = resolver(host)
    if not addresses:
        raise FetchError(f"Could not resolve image host {host}")
    parsed = [ipaddress.ip_address(addr) for addr in addresses]
    for ip in parsed:
        if not is_public_address(ip):
            logger.warning("Refused image fetch: %s resolves to %s", host, ip)
            raise FetchError(f"Image host {host} is not allowed")
    # Prefer IPv4: this host has no global IPv6 route.
    parsed.sort(key=lambda ip: ip.version)
    return str(parsed[0])


def fetch_image_bytes(
    url: str,
    *,
    resolver: Resolver = _system_resolver,
    transport: httpx.BaseTransport | None = None,
    max_bytes: int = MAX_IMAGE_BYTES,
    timeout: float = TIMEOUT_SECONDS,
) -> FetchedImage:
    """Download an image from a public https URL with SSRF protection.

    ``timeout`` is a total deadline across DNS, redirects and the body.
    """
    deadline = time.monotonic() + timeout

    def check_deadline():
        if time.monotonic() > deadline:
            raise FetchError("Image download took too long")

    try:
        return _fetch(url, resolver, transport, max_bytes, timeout, check_deadline)
    except FetchError:
        raise
    except httpx.HTTPError as exc:
        logger.warning("image fetch failed: %s", type(exc).__name__)
        raise FetchError("Could not download the image") from exc


def _fetch(url, resolver, transport, max_bytes, timeout, check_deadline) -> FetchedImage:
    with httpx.Client(
        transport=transport, timeout=timeout, follow_redirects=False, trust_env=False
    ) as client:
        current = url
        for _hop in range(MAX_REDIRECTS + 1):
            check_deadline()
            parsed = validate_url(current)
            host = parsed.host
            ip = _vetted_ip(host, resolver)
            pinned = parsed.copy_with(host=ip)
            request = client.build_request(
                "GET",
                pinned,
                headers={"Host": host, "Accept": "image/*"},
                extensions={"sni_hostname": host},
            )
            response = client.send(request, stream=True)
            try:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise FetchError("Image URL redirected without a location")
                    current = urljoin(str(parsed), location)
                    continue
                if response.status_code != 200:
                    raise FetchError(f"Image download failed (HTTP {response.status_code})")
                declared = response.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > max_bytes:
                    raise FetchError("Image is too large (max 10 MB)")
                chunks = []
                total = 0
                for chunk in response.iter_bytes():
                    check_deadline()
                    total += len(chunk)
                    if total > max_bytes:
                        raise FetchError("Image is too large (max 10 MB)")
                    chunks.append(chunk)
                return FetchedImage(
                    data=b"".join(chunks),
                    content_type=response.headers.get("content-type", ""),
                    final_host=host,
                )
            finally:
                response.close()
        raise FetchError("Image URL redirected too many times")
