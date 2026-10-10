"""MCP server for the DupeFont ChatGPT app.

Runs as its own small ASGI process (uvicorn) and imports no ML code: it
downloads the user's image (SSRF-guarded) and forwards it to the Flask app's
JSON API on localhost, which owns the matching engine. See
docs/image-matching.md.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import httpx
from mcp.server.apps import APP_MIME_TYPE, Apps
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.resources import TextResource
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import CallToolResult, Icon, TextContent, ToolAnnotations
from pydantic import BaseModel, Field

from fontmatch.image.fetch import FetchedImage, FetchError, fetch_image_bytes
from fontmatch.mcp_server.usage import DEFAULT_PATH as USAGE_DB_PATH
from fontmatch.mcp_server.usage import NullUsageLog, UsageLog
from fontmatch.samples import sample_url

# Leave headroom for multipart overhead under Flask's 10 MB MAX_CONTENT_LENGTH.
MAX_IMAGE_BYTES = 9_500_000

logger = logging.getLogger(__name__)

SITE_URL = os.environ.get("DUPEFONT_SITE_URL", "https://dupefont.com")
PUBLIC_HOSTS = ["dupefont.com", "www.dupefont.com"]
PER_SUBJECT_PER_MINUTE = 20
GLOBAL_PER_MINUTE = 300
# The image backend does ~1 req/s per gunicorn worker (2 workers): give the
# image tool its own budget and fail fast instead of queueing behind it.
IMAGE_PER_MINUTE = 90
_IMAGE_SLOTS = threading.BoundedSemaphore(4)
BACKEND_TIMEOUT = 35.0  # + 15 s image fetch, well under nginx's 130 s

# Results UI shown in ChatGPT (font samples can't be sent as MCP image
# content: ChatGPT doesn't render it). Bump the version when the HTML changes
# in a breaking way: hosts cache by URI.
WIDGET_URI = "ui://widget/dupefont-results-v3.html"  # bump on change: ChatGPT caches templates
# ChatGPT keeps asking for the URI it cached per tool for a while after a bump ("Couldn't
# open ..." if the server no longer serves it), so the previous URI stays readable.
LEGACY_WIDGET_URIS = ("ui://widget/dupefont-results-v2.html",)
WIDGET_HTML = (Path(__file__).parent / "widget.html").read_text()

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)


# --- Models -------------------------------------------------------------------


class OpenAIFile(BaseModel):
    """A file the user attached in ChatGPT (shape required by the Apps SDK)."""

    download_url: str = Field(description="Temporary URL to download the file")
    file_id: str = Field(description="ChatGPT file identifier")
    mime_type: str = Field(default="", description="MIME type, e.g. image/png")
    file_name: str = Field(default="", description="Original file name")


class FontResult(BaseModel):
    family: str
    style: str
    license: str = Field(description="SPDX license id, e.g. OFL-1.1")
    similarity: int = Field(description="0-100 visual similarity, higher is closer")
    match: str = Field(
        default="similar alternative",
        description="'likely the same font' (high confidence) or 'similar alternative'",
    )
    google_fonts_url: str | None
    dupefont_url: str = Field(description="Page with previews and more alternatives")
    css: str = Field(description="CSS font-family declaration with a generic fallback")


class ImageMatchResult(BaseModel):
    transcript: str = Field(description="The text that was matched")
    transcript_source: str = Field(description="'hint' (your reading) or 'ocr'")
    matches: list[FontResult]
    note: str


class AlternativesResult(BaseModel):
    query: str
    matched_font: str = Field(description="Corpus font the query resolved to")
    is_proprietary: bool
    matches: list[FontResult]
    note: str


NOTE = (
    "These are free, open-source fonts that look closest to the original; "
    "they are alternatives, not necessarily the exact font used."
)

IMAGE_TOOL_DESCRIPTION = (
    "Find the closest FREE (open-source) fonts to the text shown in an image. "
    "Use this when the user shares a screenshot, photo, logo or design and asks what font it is, "
    "wants a free or Google Fonts alternative, or wants a font that looks like it. "
    "Pass the image, and in text_hint the exact text you can read in the image (the most "
    "prominent line, with its exact capitalisation). Returns ranked open-source fonts with "
    "license, similarity score and links. It does not name commercial fonts; it finds free "
    "look-alikes."
)

ALTERNATIVES_TOOL_DESCRIPTION = (
    "Find free (open-source) alternatives to a font by name, e.g. 'Helvetica', 'Gotham', "
    "'Futura' or any Google Font. Use this when the user names a font and wants a free, "
    "license-safe replacement or similar fonts. Returns ranked open-source fonts with license, "
    "similarity score and links."
)


# --- Backend (Flask API on localhost) ------------------------------------------


class BackendError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class HttpBackend:
    """Calls the Flask JSON API. The shared token exempts these calls from the
    per-IP limits (all MCP traffic arrives from 127.0.0.1); this process
    applies its own per-user limit instead."""

    def __init__(
        self, base_url: str, token: str = "", transport: httpx.BaseTransport | None = None
    ):
        headers = {"X-Internal-Token": token} if token else {}
        self.client = httpx.Client(
            base_url=base_url, headers=headers, timeout=60.0, transport=transport
        )

    def _json(self, response: httpx.Response) -> dict:
        if response.status_code == 200:
            return response.json()
        if response.status_code == 429:
            logger.error("backend rate-limited the MCP service (is FONTMATCH_INTERNAL_TOKEN set?)")
            raise BackendError(429, "The font service is busy, please try again in a minute.")
        if response.status_code in (400, 404, 413, 422):
            try:
                message = response.json().get("error") or "Request rejected"
            except ValueError:
                message = "Request rejected"
            raise BackendError(response.status_code, message)
        logger.error("backend %s -> HTTP %s", response.request.url.path, response.status_code)
        raise BackendError(
            response.status_code, "Font matching is temporarily unavailable, please try again."
        )

    def identify_image(self, data: bytes, hint: str) -> dict:
        try:
            response = self.client.post(
                "/api/identify-image",
                files={"image": ("image", data, "application/octet-stream")},
                data={"text_hint": hint},
            )
        except httpx.HTTPError as exc:
            logger.error("backend unreachable: %s", exc)
            raise BackendError(
                503, "Font matching is temporarily unavailable, please try again."
            ) from exc
        return self._json(response)

    def similar(self, name: str) -> dict:
        try:
            response = self.client.get("/api/similar-to", params={"font": name})
        except httpx.HTTPError as exc:
            logger.error("backend unreachable: %s", exc)
            raise BackendError(
                503, "Font matching is temporarily unavailable, please try again."
            ) from exc
        return self._json(response)


# --- Rate limiting ---------------------------------------------------------------


class RateLimiter:
    """Sliding one-minute window per ChatGPT user (``openai/subject``) plus a
    global cap. In-memory: the MCP service runs as a single process."""

    def __init__(self, per_subject: int, global_limit: int):
        self.per_subject = per_subject
        self.global_limit = global_limit
        self._hits: dict[str, deque] = {}
        self._all: deque = deque()
        self._lock = threading.Lock()

    def allow(self, subject: str) -> bool:
        now = time.monotonic()
        with self._lock:
            self._expire(self._all, now)
            if len(self._all) >= self.global_limit:
                return False  # checked first: denied subjects are never stored
            hits = self._hits.get(subject)
            if hits is not None:
                self._expire(hits, now)
                if len(hits) >= self.per_subject:
                    return False
            else:
                hits = self._hits[subject] = deque()
            hits.append(now)
            self._all.append(now)
            if len(self._hits) > 10_000:  # drop idle subjects
                for key in [k for k, q in self._hits.items() if not q or now - q[-1] > 60]:
                    del self._hits[key]
            return True

    @staticmethod
    def _expire(q: deque, now: float) -> None:
        while q and now - q[0] > 60:
            q.popleft()


def _meta(ctx: Context | None) -> dict:
    meta: Any = getattr(getattr(ctx, "request_context", None), "meta", None) if ctx else None
    if meta is None:
        return {}
    data = meta.model_dump() if hasattr(meta, "model_dump") else dict(meta)
    return {**(getattr(meta, "model_extra", None) or {}), **data}


def _subject(ctx: Context | None) -> str:
    subject = _meta(ctx).get("openai/subject")
    return str(subject) if subject else "anonymous"


# --- Usage tracking ----------------------------------------------------------------


_BACKEND_STATUS = {
    400: "bad_input",
    404: "not_found",
    413: "bad_input",
    422: "no_text",
    429: "busy",
}


def _fail(message: str, status: str) -> ToolError:
    """A ToolError tagged with the usage-log status it should be recorded as."""
    err = ToolError(message)
    err.usage_status = status
    return err


class _Call:
    """One usage-log row, recorded when the block exits however it exits."""

    def __init__(self, usage, tool: str, ctx: Context | None):
        self.usage = usage
        self.started = time.perf_counter()
        self.subject = "anonymous"
        self.fields: dict[str, Any] = {"tool": tool}
        try:
            meta = _meta(ctx)
            location = meta.get("openai/userLocation")
            self.subject = _subject(ctx)
            self.fields["locale"] = meta.get("openai/locale")
            if isinstance(location, dict):
                self.fields["country"] = location.get("country")
        except Exception:  # odd request metadata must not fail the call
            logger.exception("could not read request metadata")

    def __enter__(self) -> "_Call":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        status = "ok" if exc is None else getattr(exc, "usage_status", "error")
        try:
            self.usage.record(
                subject=self.subject,
                status=status,
                latency_ms=int((time.perf_counter() - self.started) * 1000),
                **self.fields,
            )
        except Exception:  # tracking must never break a call
            logger.exception("usage record failed")
        return False

    def result(self, matches: list["FontResult"]) -> None:
        if matches:
            self.fields.update(
                top_family=matches[0].family,
                top_similarity=matches[0].similarity,
                match_label=matches[0].match,
            )


# --- Formatting ------------------------------------------------------------------


_GENERIC = {
    "serif": "serif",
    "mono": "monospace",
    "handwriting": "cursive",
    "display": "sans-serif",
}


def _font_match(m: dict) -> FontResult:
    family = m["family"]
    generic = _GENERIC.get(m.get("category", ""), "sans-serif")
    style = m.get("style") or "Regular"
    return FontResult(
        family=family,
        style=style,
        license=m.get("license_id") or "unknown",
        similarity=int(m.get("score") or 0),
        match=m.get("match_label") or "similar alternative",
        google_fonts_url=m.get("google_fonts_url"),
        dupefont_url=SITE_URL + (m.get("similar_url") or ""),
        css=f"font-family: '{family}', {generic};",
    )


def _sample_image(m: dict) -> str | None:
    """Absolute URL of the 'quick brown fox' PNG for a backend match, if any."""
    style = m.get("style") or "Regular"
    return SITE_URL + sample_url(m["name"], style) if m.get("name") else None


SAMPLES_META_KEY = "dupefont/sampleImages"


def _tool_result(payload: BaseModel, raw_matches: list[dict]) -> CallToolResult:
    """Wire result: ``payload`` is what the assistant reads (structuredContent and
    text); the type samples are widget-only, so they travel in ``_meta`` (the
    widget gets it as window.openai.toolResponseMetadata). ChatGPT otherwise
    pastes image URLs from structuredContent into its prose as markdown images,
    which it then renders as broken placeholders."""
    return CallToolResult(
        content=[TextContent(type="text", text=payload.model_dump_json())],
        structured_content=payload.model_dump(mode="json"),
        meta={SAMPLES_META_KEY: [_sample_image(m) for m in raw_matches]},
    )


# --- Server ----------------------------------------------------------------------


def _fetch_capped(url: str) -> FetchedImage:
    return fetch_image_bytes(url, max_bytes=MAX_IMAGE_BYTES)


def _widget_resource(uri: str = WIDGET_URI) -> TextResource:
    csp = {"resourceDomains": [SITE_URL], "connectDomains": []}
    return TextResource(
        uri=uri,
        name="dupefont-results",
        title="DupeFont results",
        mime_type=APP_MIME_TYPE,
        text=WIDGET_HTML,
        meta={
            "ui": {"csp": csp, "prefersBorder": True, "domain": SITE_URL},
            # ChatGPT's legacy keys, for hosts that predate the MCP Apps fields.
            "openai/widgetCSP": {"resource_domains": [SITE_URL], "connect_domains": []},
            "openai/widgetPrefersBorder": True,
            "openai/widgetDomain": SITE_URL,
            "openai/widgetDescription": (
                "Shows the matched free fonts with a type sample of each, similarity, "
                "license and links. No need to repeat the full list in text."
            ),
        },
    )


def _ui_meta(invoking: str, invoked: str) -> dict:
    return {
        "openai/outputTemplate": WIDGET_URI,
        "openai/toolInvocation/invoking": invoking,
        "openai/toolInvocation/invoked": invoked,
    }


def create_server(
    fetch: Callable[[str], FetchedImage] = _fetch_capped,
    backend: Any = None,
    usage: Any = None,
) -> MCPServer:
    if backend is None:
        backend = HttpBackend(
            os.environ.get("FONTMATCH_API_URL", "http://127.0.0.1:8087"),
            token=os.environ.get("FONTMATCH_INTERNAL_TOKEN", ""),
        )
    usage = usage or NullUsageLog()
    limiter = RateLimiter(PER_SUBJECT_PER_MINUTE, GLOBAL_PER_MINUTE)
    image_limiter = RateLimiter(PER_SUBJECT_PER_MINUTE, IMAGE_PER_MINUTE)
    apps = Apps()

    def _check_rate(call: _Call, bucket: RateLimiter) -> None:
        if not bucket.allow(call.subject):
            raise _fail(
                "Too many requests right now. Please wait a minute and try again.", "rate_limited"
            )

    def _backend_failed(exc: BackendError) -> ToolError:
        return _fail(str(exc), _BACKEND_STATUS.get(exc.status, "backend_error"))

    @apps.tool(
        resource_uri=WIDGET_URI,
        name="find_free_font_from_image",
        title="Find a free font from an image",
        description=IMAGE_TOOL_DESCRIPTION,
        annotations=READ_ONLY,
        meta={"openai/fileParams": ["image"], **_ui_meta("Reading the type…", "Matched fonts")},
    )
    def find_free_font_from_image(
        image: OpenAIFile,
        text_hint: Annotated[
            str,
            Field(description="The exact text visible in the image (main line), as you read it"),
        ] = "",
        ctx: Context | None = None,
    ) -> ImageMatchResult:
        with _Call(usage, "find_free_font_from_image", ctx) as call:
            _check_rate(call, image_limiter)
            if not _IMAGE_SLOTS.acquire(blocking=False):
                raise _fail(
                    "The font service is busy right now, please try again shortly.", "busy"
                )
            try:
                try:
                    fetched = fetch(image.download_url)
                except FetchError as exc:
                    raise _fail(str(exc), "fetch_error") from exc
                call.fields.update(image_bytes=len(fetched.data), image_host=fetched.final_host)
                try:
                    result = backend.identify_image(fetched.data, text_hint)
                except BackendError as exc:
                    raise _backend_failed(exc) from exc
            finally:
                _IMAGE_SLOTS.release()
            matches = [_font_match(m) for m in result.get("matches", [])]
            call.fields["transcript_source"] = result.get("transcript_source")
            call.result(matches)
            logger.info(
                "image tool: subject=%s host=%s bytes=%d source=%s top=%s %.2fs",
                hashlib.sha256(call.subject.encode()).hexdigest()[:10],
                fetched.final_host,
                len(fetched.data),
                result.get("transcript_source"),
                matches[0].family if matches else None,
                time.perf_counter() - call.started,
            )
            payload = ImageMatchResult(
                transcript=result.get("transcript", ""),
                transcript_source=result.get("transcript_source", ""),
                matches=matches,
                note=NOTE,
            )
            return _tool_result(payload, result.get("matches", []))

    @apps.tool(
        resource_uri=WIDGET_URI,
        name="find_free_alternatives",
        title="Find free alternatives to a font",
        description=ALTERNATIVES_TOOL_DESCRIPTION,
        annotations=READ_ONLY,
        meta=_ui_meta("Finding free alternatives…", "Found free alternatives"),
    )
    def find_free_alternatives(
        font_name: Annotated[
            str, Field(description="Font family name, e.g. 'Helvetica Neue'", max_length=100)
        ],
        ctx: Context | None = None,
    ) -> AlternativesResult:
        with _Call(usage, "find_free_alternatives", ctx) as call:
            call.fields["query"] = font_name.strip()[:100]
            _check_rate(call, limiter)
            try:
                result = backend.similar(font_name.strip())
            except BackendError as exc:
                raise _backend_failed(exc) from exc
            raw = result.get("matches", [])[:5]
            matches = [_font_match(m) for m in raw]
            call.result(matches)
            payload = AlternativesResult(
                query=result.get("query", font_name),
                matched_font=result.get("matched_font", ""),
                is_proprietary=result.get("proprietary") is not None,
                matches=matches,
                note=NOTE,
            )
            return _tool_result(payload, raw)

    apps.add_resource(_widget_resource())
    for legacy in LEGACY_WIDGET_URIS:
        apps.add_resource(_widget_resource(legacy))
    server = MCPServer(
        "dupefont",
        title="DupeFont",
        description="Find free look-alike fonts for any font or image of text.",
        website_url=SITE_URL,
        version="1.1.0",
        icons=[
            Icon(
                src=f"{SITE_URL}/static/favicon.svg",
                mime_type="image/svg+xml",
                sizes=["any"],
            )
        ],
        extensions=[apps],
    )
    _inline_schema_refs(server, "find_free_font_from_image")
    return server


def _inline_schema_refs(server: MCPServer, tool_name: str) -> None:
    """Replace ``$ref``s with their ``$defs`` in a tool's input schema: the Apps
    SDK file-param scanner expects the OpenAIFile object inline."""
    tool = server._tool_manager.get_tool(tool_name)
    schema = tool.parameters
    defs = schema.pop("$defs", {})

    def resolve(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(dict(defs[node["$ref"].split("/")[-1]]))
            return {k: resolve(v) for k, v in node.items()}
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    tool.parameters = resolve(schema)


def create_app():
    """ASGI app for uvicorn: ``uvicorn --factory fontmatch.mcp_server.server:create_app``."""
    if not os.environ.get("FONTMATCH_INTERNAL_TOKEN"):
        # Without it every ChatGPT user shares 127.0.0.1's per-IP limits on the
        # Flask side and calls fail as "busy". Refuse to start instead.
        raise RuntimeError(
            "FONTMATCH_INTERNAL_TOKEN must be set (same value as fontmatch.service)"
        )
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    # httpx logs full request URLs at INFO; ChatGPT's download URLs are signed.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    server = create_server(
        usage=UsageLog(os.environ.get("DUPEFONT_MCP_USAGE_DB") or USAGE_DB_PATH)
    )
    return server.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=PUBLIC_HOSTS + ["127.0.0.1:*", "localhost:*"],
            allowed_origins=["https://chatgpt.com", "https://chat.openai.com"],
        ),
    )
