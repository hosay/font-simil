#!/usr/bin/env python3
"""Hammer test for FontMatch web app and API.

Phases:
  1. Rate limit verification (run first to avoid contamination)
  2. Concurrent read-only stress
  3. Concurrent write stress
  4. Mixed read/write under load
  5. Edge cases and error handling
  6. Database locking stress (RLock contention)
"""

import json
import random
import string
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

import requests

BASE = "http://localhost:8087"
FONT_FILE = str(Path(__file__).parent / "tests" / "fixtures" / "Cousine-Regular.ttf")

# Fonts known to be servable
FONT_FILES = [
    "Cousine-Regular.ttf",
    "Tinos-Regular.ttf",
    "Average-Regular.ttf",
    "Roboto-Regular.ttf",
    "DejaVuSans.ttf",
]

GET_ENDPOINTS = [
    "/api/health",
    "/similar-to/times-new-roman",
    "/similar-to/arial",
    "/similar-to/helvetica",
    "/similar-to/roboto",
    "/api/browse?q=rob",
    "/api/browse?q=open&category=sans-serif",
    "/",
    "/popular",
    "/api/docs",
    "/sitemap.txt",
    "/robots.txt",
]

SIMILAR_SLUGS = [
    "times-new-roman", "arial", "helvetica", "roboto", "cousine",
    "georgia", "verdana", "impact", "comic-sans-ms", "trebuchet-ms",
]


@dataclass
class Stats:
    total: int = 0
    success: int = 0
    errors: int = 0
    status_counts: dict = field(default_factory=lambda: defaultdict(int))
    latencies: list = field(default_factory=list)
    failures: list = field(default_factory=list)

    def record(self, status: int, latency: float, url: str, body: str = ""):
        self.total += 1
        self.latencies.append(latency)
        self.status_counts[status] += 1
        if 200 <= status < 300:
            self.success += 1
        else:
            self.errors += 1
            if status >= 500:
                self.failures.append((url, status, body[:200]))

    def summary(self) -> str:
        if not self.latencies:
            return "  No requests recorded"
        s = sorted(self.latencies)
        p50 = s[len(s) // 2]
        p95 = s[int(len(s) * 0.95)]
        p99 = s[int(len(s) * 0.99)]
        lines = [
            f"  Total: {self.total} | Success: {self.success} | Errors: {self.errors}",
            f"  Latency: min={min(s):.3f}s avg={sum(s)/len(s):.3f}s p50={p50:.3f}s p95={p95:.3f}s p99={p99:.3f}s max={max(s):.3f}s",
            f"  Status codes: {dict(self.status_counts)}",
        ]
        if self.failures:
            lines.append(f"  *** {len(self.failures)} CRITICAL 5xx FAILURES ***")
            for url, code, body in self.failures[:5]:
                lines.append(f"    {code} {url}: {body}")
        return "\n".join(lines)


def _visitor_ip(visitor_id: int) -> str:
    """Generate a unique fake IP for a simulated visitor."""
    return f"10.0.{visitor_id // 256}.{visitor_id % 256}"


def _headers(visitor_id: int | None = None) -> dict:
    """Build request headers, optionally with a spoofed visitor IP."""
    if visitor_id is None:
        return {}
    return {"X-Forwarded-For": _visitor_ip(visitor_id)}


def timed_get(url: str, timeout: float = 30,
              visitor_id: int | None = None) -> tuple[int, float, str]:
    t0 = time.monotonic()
    try:
        r = requests.get(url, timeout=timeout, headers=_headers(visitor_id))
        return r.status_code, time.monotonic() - t0, r.text[:200]
    except Exception as e:
        return 0, time.monotonic() - t0, str(e)


def timed_post(url: str, json_data: dict = None, files: dict = None,
               timeout: float = 30,
               visitor_id: int | None = None) -> tuple[int, float, str]:
    t0 = time.monotonic()
    try:
        r = requests.post(url, json=json_data, files=files, timeout=timeout,
                          headers=_headers(visitor_id))
        return r.status_code, time.monotonic() - t0, r.text[:200]
    except Exception as e:
        return 0, time.monotonic() - t0, str(e)


def run_phase(name: str, fn, duration: float, threads: int,
              multi_visitor: bool = True) -> Stats:
    """Run a phase with concurrent threads.

    If multi_visitor is True, each thread gets its own simulated visitor IP
    via X-Forwarded-For so requests are rate-limited independently (simulating
    real concurrent users, not one user hammering).
    """
    print(f"\n{'='*60}")
    print(f"PHASE: {name}")
    visitor_label = f"{threads} visitors" if multi_visitor else "single IP"
    print(f"  Threads: {threads} ({visitor_label}) | Duration: {duration}s")
    print(f"{'='*60}")

    stats = Stats()
    stop_time = time.monotonic() + duration

    def worker(thread_id: int):
        vid = thread_id if multi_visitor else None
        while time.monotonic() < stop_time:
            url, status, latency, body = fn(vid)
            stats.record(status, latency, url, body)

    with ThreadPoolExecutor(max_workers=threads) as pool:
        futures = [pool.submit(worker, i) for i in range(threads)]
        for f in as_completed(futures):
            f.result()

    print(stats.summary())
    return stats


# ---------------------------------------------------------------------------
# Phase 1: Rate limit verification
# ---------------------------------------------------------------------------
def phase_rate_limits() -> Stats:
    print(f"\n{'='*60}")
    print("PHASE 1: Rate Limit Verification")
    print(f"{'='*60}")
    stats = Stats()

    # 1a. Test 60/min global limit - fire 70 rapid requests
    print("\n  1a. Global 60/min limit (70 rapid GETs to /api/health)...")
    got_429 = False
    for i in range(70):
        status, lat, body = timed_get(f"{BASE}/api/health")
        stats.record(status, lat, "/api/health", body)
        if status == 429:
            got_429 = True
    if got_429:
        print(f"    PASS: Got 429 after rapid-fire (expected)")
    else:
        print(f"    NOTE: No 429 received — /api/health may be exempt from Flask-Limiter")

    # Wait for rate limit window to reset
    print("  Waiting 5s for rate limit window cooldown...")
    time.sleep(5)

    # 1b. Test 10/min fingerprint limit
    print("\n  1b. Fingerprint 10/min limit (12 rapid POSTs to /api/identify)...")
    identify_codes = []
    for i in range(12):
        with open(FONT_FILE, "rb") as f:
            status, lat, body = timed_post(
                f"{BASE}/api/identify",
                files={"file": ("Cousine-Regular.ttf", f, "font/ttf")},
            )
        stats.record(status, lat, "/api/identify", body)
        identify_codes.append(status)

    ok_count = sum(1 for c in identify_codes if c == 200)
    rate_limited = sum(1 for c in identify_codes if c == 429)
    print(f"    Results: {ok_count} succeeded, {rate_limited} rate-limited (429)")
    if rate_limited > 0:
        print(f"    PASS: Fingerprint rate limit enforced")
    else:
        print(f"    WARN: No 429s — rate limit may not be working or window is wider")

    print("\n  Waiting 5s for cooldown...")
    time.sleep(5)

    print(stats.summary())
    return stats


# ---------------------------------------------------------------------------
# Phase 2: Concurrent read-only stress
# ---------------------------------------------------------------------------
def phase_read_stress() -> Stats:
    def read_fn(visitor_id):
        url = BASE + random.choice(GET_ENDPOINTS)
        status, lat, body = timed_get(url, visitor_id=visitor_id)
        return url, status, lat, body

    return run_phase("2: Concurrent Read-Only Stress (50 visitors)", read_fn,
                     duration=10, threads=50)


# ---------------------------------------------------------------------------
# Phase 3: Concurrent write stress
# ---------------------------------------------------------------------------
def phase_write_stress() -> Stats:
    fonts = ["Cousine-Regular.ttf", "Tinos-Regular.ttf", "Roboto-Regular.ttf",
             "Average-Regular.ttf", "DejaVuSans.ttf"]

    def write_fn(visitor_id):
        action = random.random()
        if action < 0.6:
            # Score submission
            url = f"{BASE}/api/scores"
            data = {
                "query_font": random.choice(fonts),
                "match_font": random.choice(fonts),
                "score": random.randint(1, 5),
            }
            status, lat, body = timed_post(url, json_data=data, visitor_id=visitor_id)
        else:
            # Report submission
            url = f"{BASE}/api/report"
            data = {
                "query_font": random.choice(fonts),
                "match_font": random.choice(fonts),
            }
            status, lat, body = timed_post(url, json_data=data, visitor_id=visitor_id)
        return url, status, lat, body

    return run_phase("3: Concurrent Write Stress (20 visitors)", write_fn,
                     duration=10, threads=20)


# ---------------------------------------------------------------------------
# Phase 4: Mixed read/write under load
# ---------------------------------------------------------------------------
def phase_mixed() -> Stats:
    fonts = ["Cousine-Regular.ttf", "Tinos-Regular.ttf", "Roboto-Regular.ttf"]

    def mixed_fn(visitor_id):
        r = random.random()
        if r < 0.60:
            # Read - GET endpoint
            url = BASE + random.choice(GET_ENDPOINTS)
            status, lat, body = timed_get(url, visitor_id=visitor_id)
        elif r < 0.75:
            # Read - font file
            url = f"{BASE}/api/font-file/{random.choice(FONT_FILES)}"
            status, lat, body = timed_get(url, visitor_id=visitor_id)
        elif r < 0.90:
            # Write - score
            url = f"{BASE}/api/scores"
            data = {
                "query_font": random.choice(fonts),
                "match_font": random.choice(fonts),
                "score": random.randint(1, 5),
            }
            status, lat, body = timed_post(url, json_data=data, visitor_id=visitor_id)
        else:
            # Write - report
            url = f"{BASE}/api/report"
            data = {
                "query_font": random.choice(fonts),
                "match_font": random.choice(fonts),
            }
            status, lat, body = timed_post(url, json_data=data, visitor_id=visitor_id)
        return url, status, lat, body

    return run_phase("4: Mixed Read/Write Under Load (30 visitors)", mixed_fn,
                     duration=10, threads=30)


# ---------------------------------------------------------------------------
# Phase 5: Edge cases
# ---------------------------------------------------------------------------
def phase_edge_cases() -> Stats:
    print(f"\n{'='*60}")
    print("PHASE 5: Edge Cases & Error Handling")
    print(f"{'='*60}")
    stats = Stats()

    cases = [
        # Non-existent resources
        ("GET", "/similar-to/definitely-not-a-font-xyz", None, "non-existent font slug"),
        ("GET", "/similar-to/unknown-proprietary-font", None, "unmapped proprietary slug"),
        ("GET", "/api/font-file/nope.ttf", None, "non-existent font file"),
        ("GET", "/api/fonts/999999", None, "non-existent font ID"),

        # Path traversal attempts
        ("GET", "/api/font-file/../../etc/passwd", None, "path traversal in font-file"),
        ("GET", "/similar-to/../../../etc/passwd", None, "path traversal in slug"),

        # Special characters in slugs
        ("GET", "/similar-to/font%00name", None, "null byte in slug"),
        ("GET", "/similar-to/font<script>alert(1)</script>", None, "XSS in slug"),
        ("GET", "/similar-to/" + "a" * 500, None, "very long slug"),

        # Long query strings
        ("GET", f"/api/browse?q={'a' * 1000}", None, "very long search query"),
        ("GET", "/api/browse?q=&category=invalid-cat", None, "invalid category filter"),
        ("GET", "/api/browse?page=-1", None, "negative page number"),
        ("GET", "/api/browse?per_page=99999", None, "huge per_page"),

        # Malformed POST bodies
        ("POST_JSON", "/api/scores", {}, "empty JSON body for scores"),
        ("POST_JSON", "/api/scores", {"query_font": "Arial"}, "missing match_font and score"),
        ("POST_JSON", "/api/scores", {"query_font": "Arial", "match_font": "Roboto"}, "missing score"),
        ("POST_JSON", "/api/scores",
         {"query_font": "Arial", "match_font": "Roboto", "score": 99}, "score out of range"),
        ("POST_JSON", "/api/scores",
         {"query_font": "Arial", "match_font": "Roboto", "score": "not-a-number"}, "non-numeric score"),
        ("POST_JSON", "/api/report", {}, "empty JSON body for report"),
        ("POST_JSON", "/api/report", {"query_font": "Arial"}, "missing match_font for report"),

        # Non-JSON POST bodies
        ("POST_RAW", "/api/scores", "not json at all", "plain text body for scores"),
        ("POST_RAW", "/api/report", "not json at all", "plain text body for report"),

        # Rapid-fire single endpoint
        ("RAPID", "/api/browse?q=a", None, "100 rapid requests to browse"),
    ]

    # Each edge case gets its own visitor IP to avoid rate-limit interference
    for case_idx, (method, url, payload, desc) in enumerate(cases):
        vid = 200 + case_idx  # unique visitor per case
        full_url = BASE + url if not url.startswith("http") else url
        print(f"\n  Testing: {desc}")

        if method == "GET":
            status, lat, body = timed_get(full_url, visitor_id=vid)
            stats.record(status, lat, full_url, body)
            print(f"    -> {status} ({lat:.3f}s)")

        elif method == "POST_JSON":
            status, lat, body = timed_post(full_url, json_data=payload,
                                           visitor_id=vid)
            stats.record(status, lat, full_url, body)
            print(f"    -> {status} ({lat:.3f}s) {body[:100]}")

        elif method == "POST_RAW":
            t0 = time.monotonic()
            try:
                hdrs = _headers(vid)
                hdrs["Content-Type"] = "text/plain"
                r = requests.post(full_url, data=payload,
                                  headers=hdrs, timeout=10)
                lat = time.monotonic() - t0
                stats.record(r.status_code, lat, full_url, r.text[:200])
                print(f"    -> {r.status_code} ({lat:.3f}s)")
            except Exception as e:
                lat = time.monotonic() - t0
                stats.record(0, lat, full_url, str(e))
                print(f"    -> ERROR ({lat:.3f}s): {e}")

        elif method == "RAPID":
            # Use many unique visitors to avoid per-IP rate limits
            print(f"    Firing 100 rapid requests (100 unique visitors)...")
            rapid_stats = Stats()
            for i in range(100):
                status, lat, body = timed_get(full_url, visitor_id=300 + i)
                rapid_stats.record(status, lat, full_url, body)
                stats.record(status, lat, full_url, body)
            print(f"    -> {rapid_stats.success} ok, {rapid_stats.errors} errors")
            if rapid_stats.failures:
                print(f"    *** 5xx errors in rapid fire! ***")

    print(f"\n{stats.summary()}")
    return stats


# ---------------------------------------------------------------------------
# Phase 6: Database locking stress (RLock contention)
# ---------------------------------------------------------------------------
def phase_db_stress() -> Stats:
    """Hit endpoints that all require DB access to stress the RLock."""
    db_endpoints = [
        "/similar-to/times-new-roman",
        "/similar-to/arial",
        "/similar-to/helvetica",
        "/api/browse?q=rob",
        "/api/browse?q=open",
        "/api/browse?q=liberation",
    ]

    def db_fn(visitor_id):
        url = BASE + random.choice(db_endpoints)
        status, lat, body = timed_get(url, visitor_id=visitor_id)
        return url, status, lat, body

    return run_phase("6: DB Locking Stress (50 visitors, RLock contention)", db_fn,
                     duration=10, threads=50)


# ---------------------------------------------------------------------------
# Phase 7: Concurrent font-file + similar-to (reproduces original bug)
# ---------------------------------------------------------------------------
def phase_font_file_storm() -> Stats:
    """Reproduce the original bug scenario: browser loading a similar-to page
    which triggers many concurrent font-file requests."""

    font_files = FONT_FILES + [
        "Mate-Regular.ttf",
        "Arimo-Regular.ttf",
        "Carlito-Regular.ttf",
    ]

    def storm_fn(visitor_id):
        r = random.random()
        if r < 0.3:
            # Load similar-to page (triggers template + DB)
            slug = random.choice(SIMILAR_SLUGS)
            url = f"{BASE}/similar-to/{slug}"
        else:
            # Load font file (triggered by @font-face CSS)
            name = random.choice(font_files)
            url = f"{BASE}/api/font-file/{name}"
        status, lat, body = timed_get(url, visitor_id=visitor_id)
        return url, status, lat, body

    return run_phase("7: Font-File Storm (50 visitors, original bug scenario)",
                     storm_fn, duration=10, threads=50)


# ---------------------------------------------------------------------------
# Phase 8: Cache effectiveness
# ---------------------------------------------------------------------------
def phase_cache_effectiveness() -> Stats:
    """Test that fingerprint caching works correctly.

    1. Upload the same font twice via API — second should be cached
    2. Hit the same similar-to page twice — second should be faster (cached)
    3. Upload same font concurrently from 5 threads — all should succeed
    """
    print(f"\n{'='*60}")
    print("PHASE 8: Cache Effectiveness")
    print(f"{'='*60}")
    stats = Stats()

    # Use unique visitor IPs to avoid rate-limit interference
    cache_vid = 500

    # 8a. API identify cache: first call computes, second returns cached
    print("\n  8a. API identify cache (same font uploaded twice)...")
    t0 = time.monotonic()
    with open(FONT_FILE, "rb") as f:
        r1 = requests.post(f"{BASE}/api/identify",
                           files={"file": ("Cousine-Regular.ttf", f, "font/ttf")},
                           headers=_headers(cache_vid), timeout=30)
    lat1 = time.monotonic() - t0
    status1 = r1.status_code
    stats.record(status1, lat1, "/api/identify (uncached)")
    try:
        cached1 = r1.json().get("cached", "N/A")
    except Exception:
        cached1 = "parse_error"
    print(f"    First:  {status1} ({lat1:.3f}s) cached={cached1}")

    t0 = time.monotonic()
    with open(FONT_FILE, "rb") as f:
        r2 = requests.post(f"{BASE}/api/identify",
                           files={"file": ("Cousine-Regular.ttf", f, "font/ttf")},
                           headers=_headers(cache_vid), timeout=30)
    lat2 = time.monotonic() - t0
    status2 = r2.status_code
    stats.record(status2, lat2, "/api/identify (cached)")
    try:
        cached2 = r2.json().get("cached", "N/A")
    except Exception:
        cached2 = "parse_error"
    print(f"    Second: {status2} ({lat2:.3f}s) cached={cached2}")

    if cached2 is True:
        print(f"    PASS: Second call returned cached=True")
    elif status2 == 429:
        print(f"    SKIP: Rate limited (429) — cannot verify cache")
    else:
        print(f"    WARN: Second call cached={cached2} — expected True")

    speedup = lat1 / lat2 if lat2 > 0 else 0
    print(f"    Speedup: {speedup:.1f}x")

    # 8b. Similar-to page cache: first loads and caches, second is faster
    print("\n  8b. Similar-to page cache (same page loaded twice)...")
    # Pick a font that's unlikely to be cached from prior phases
    test_slug = "eb-garamond"
    status1, lat1, body1 = timed_get(f"{BASE}/similar-to/{test_slug}", visitor_id=501)
    stats.record(status1, lat1, f"/similar-to/{test_slug} (first load)")
    print(f"    First:  {status1} ({lat1:.3f}s)")

    status2, lat2, body2 = timed_get(f"{BASE}/similar-to/{test_slug}", visitor_id=501)
    stats.record(status2, lat2, f"/similar-to/{test_slug} (cached load)")
    print(f"    Second: {status2} ({lat2:.3f}s)")

    speedup = lat1 / lat2 if lat2 > 0 else 0
    print(f"    Speedup: {speedup:.1f}x")
    if speedup > 1.5:
        print(f"    PASS: Cached load was {speedup:.1f}x faster")
    elif status1 != 200:
        print(f"    SKIP: Page returned {status1}")
    else:
        print(f"    INFO: Speedup only {speedup:.1f}x (may already have been cached)")

    # 8c. Concurrent uploads of same font
    print("\n  8c. Concurrent identify uploads (5 threads, same font)...")
    concurrent_results = []

    def upload_font(vid):
        with open(FONT_FILE, "rb") as f:
            return timed_post(
                f"{BASE}/api/identify",
                files={"file": ("Cousine-Regular.ttf", f, "font/ttf")},
                visitor_id=vid,
            )

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(upload_font, 510 + i) for i in range(5)]
        for fut in as_completed(futures):
            status, lat, body = fut.result()
            stats.record(status, lat, "/api/identify (concurrent)")
            concurrent_results.append(status)

    ok = sum(1 for c in concurrent_results if c == 200)
    rate_limited = sum(1 for c in concurrent_results if c == 429)
    errors = sum(1 for c in concurrent_results if c >= 500)
    print(f"    Results: {ok} ok, {rate_limited} rate-limited, {errors} errors")
    if errors > 0:
        print(f"    *** FAIL: {errors} server errors during concurrent upload ***")
    else:
        print(f"    PASS: No server errors")

    print(f"\n{stats.summary()}")
    return stats


# ---------------------------------------------------------------------------
# Phase 9: Uncached search/browse performance
# ---------------------------------------------------------------------------
def phase_uncached_search() -> Stats:
    """Test browse/search without cache benefits.

    Uses unique random queries to force fresh DB queries every time,
    ensuring we're testing actual query performance, not cache hits.
    """
    print(f"\n{'='*60}")
    print("PHASE 9: Uncached Search/Browse Performance")
    print(f"{'='*60}")
    stats = Stats()

    # 9a. Unique search queries (each query is different, no cache hit)
    print("\n  9a. 100 unique search queries (no cache possible)...")
    # Use 2-char combos that will match various font families
    prefixes = list("abcdefghijklmnopqrstuvwxyz")
    queries = [f"{a}{b}" for a in prefixes[:10] for b in prefixes[:10]]
    random.shuffle(queries)

    for i, q in enumerate(queries[:100]):
        url = f"{BASE}/api/browse?q={q}"
        status, lat, body = timed_get(url, visitor_id=600 + i)
        stats.record(status, lat, url, body)

    if stats.latencies:
        s = sorted(stats.latencies)
        print(f"    Completed {stats.total} queries")
        print(f"    Latency: avg={sum(s)/len(s):.3f}s p95={s[int(len(s)*0.95)]:.3f}s max={max(s):.3f}s")
        if any(c >= 500 for c in stats.status_counts):
            print(f"    *** FAIL: 5xx errors in search queries ***")
        else:
            print(f"    PASS: All searches completed without server errors")

    # 9b. Concurrent unique searches (stress the DB under parallel unique queries)
    print("\n  9b. Concurrent unique searches (20 threads, 5 seconds)...")
    concurrent_stats = Stats()
    query_pool = [f"{a}{b}{c}" for a in "abcde" for b in "fghij" for c in "klmno"]
    random.shuffle(query_pool)
    idx = [0]  # mutable counter for thread-safe unique queries

    stop_time = time.monotonic() + 5

    def unique_search(thread_id):
        vid = 700 + thread_id
        while time.monotonic() < stop_time:
            # Each thread gets its own query
            i = idx[0]
            idx[0] += 1
            q = query_pool[i % len(query_pool)]
            url = f"{BASE}/api/browse?q={q}"
            status, lat, body = timed_get(url, visitor_id=vid)
            concurrent_stats.record(status, lat, url, body)

    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = [pool.submit(unique_search, i) for i in range(20)]
        for f in as_completed(futures):
            f.result()

    # Merge into main stats
    for s_code, count in concurrent_stats.status_counts.items():
        stats.status_counts[s_code] += count
    stats.total += concurrent_stats.total
    stats.success += concurrent_stats.success
    stats.errors += concurrent_stats.errors
    stats.latencies.extend(concurrent_stats.latencies)
    stats.failures.extend(concurrent_stats.failures)

    if concurrent_stats.latencies:
        s = sorted(concurrent_stats.latencies)
        print(f"    Completed {concurrent_stats.total} queries")
        print(f"    Latency: avg={sum(s)/len(s):.3f}s p95={s[int(len(s)*0.95)]:.3f}s max={max(s):.3f}s")
        if concurrent_stats.failures:
            print(f"    *** FAIL: {len(concurrent_stats.failures)} 5xx errors ***")
        else:
            print(f"    PASS: All concurrent searches completed")

    # 9c. Category-filtered searches (different code path in DB)
    print("\n  9c. Category-filtered searches...")
    categories = ["sans-serif", "serif", "mono"]
    for i, cat in enumerate(categories):
        url = f"{BASE}/api/browse?category={cat}&per_page=40"
        vid = 720 + i
        t0 = time.monotonic()
        try:
            r = requests.get(url, timeout=30, headers=_headers(vid))
            lat = time.monotonic() - t0
            stats.record(r.status_code, lat, url)
            try:
                count = r.json().get("total", "?")
            except Exception:
                count = f"status={r.status_code}"
            print(f"    {cat}: {r.status_code} ({lat:.3f}s) total={count}")
        except Exception as e:
            lat = time.monotonic() - t0
            stats.record(0, lat, url, str(e))
            print(f"    {cat}: ERROR ({lat:.3f}s): {e}")

    # 9d. Pagination stress — walk through many pages
    print("\n  9d. Pagination walk (pages 1-10)...")
    for page in range(1, 11):
        url = f"{BASE}/api/browse?page={page}&per_page=40"
        status, lat, body = timed_get(url, visitor_id=730)
        stats.record(status, lat, url, body)
    print(f"    10 pages fetched, all {'OK' if not stats.failures else 'ERRORS'}")

    print(f"\n{stats.summary()}")
    return stats


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("=" * 60)
    print("FONTMATCH HAMMER TEST")
    print(f"Target: {BASE}")
    print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # Verify server is up
    try:
        r = requests.get(f"{BASE}/api/health", timeout=5)
        data = r.json()
        print(f"Server OK: {data['font_count']} fonts indexed")
    except Exception as e:
        print(f"FATAL: Server not reachable: {e}")
        sys.exit(1)

    all_stats: dict[str, Stats] = {}

    # Run edge cases and cache first (before rate limits accumulate)
    all_stats["1_edge_cases"] = phase_edge_cases()
    all_stats["2_cache"] = phase_cache_effectiveness()
    all_stats["3_uncached_search"] = phase_uncached_search()

    # Now stress tests (these will hit rate limits — that's expected)
    all_stats["4_read_stress"] = phase_read_stress()
    all_stats["5_write_stress"] = phase_write_stress()
    all_stats["6_mixed"] = phase_mixed()
    all_stats["7_db_stress"] = phase_db_stress()
    all_stats["8_font_storm"] = phase_font_file_storm()

    # Rate limit verification last (so earlier phases don't poison it)
    all_stats["9_rate_limits"] = phase_rate_limits()

    # Final summary
    print(f"\n{'='*60}")
    print("FINAL SUMMARY")
    print(f"{'='*60}")

    total_requests = 0
    total_errors = 0
    critical_failures = []

    for name, st in all_stats.items():
        total_requests += st.total
        total_errors += st.errors
        critical_failures.extend(st.failures)
        has_5xx = " *** 5xx FAILURES ***" if st.failures else ""
        print(f"  {name}: {st.total} reqs, {st.success} ok, {st.errors} err{has_5xx}")

    print(f"\n  TOTAL: {total_requests} requests, {total_errors} errors")

    if critical_failures:
        print(f"\n  *** {len(critical_failures)} CRITICAL 5xx FAILURES ***")
        # Deduplicate by URL pattern
        seen = defaultdict(int)
        for url, code, body in critical_failures:
            # Strip query params for grouping
            key = url.split("?")[0]
            seen[(key, code)] += 1
        for (url, code), count in sorted(seen.items(), key=lambda x: -x[1]):
            print(f"    {code} x{count}: {url}")
    else:
        print("\n  NO CRITICAL 5xx FAILURES")

    print(f"\n{'='*60}")
    return 1 if critical_failures else 0


if __name__ == "__main__":
    sys.exit(main())
