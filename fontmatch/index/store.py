"""SQLite-backed font store with fingerprint persistence and vector index."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from fontmatch.features.fingerprint import Fingerprint
from fontmatch.match.scorer import Match, rank as brute_rank

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fonts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_hash TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    family TEXT NOT NULL,
    subfamily TEXT NOT NULL,
    postscript_name TEXT,
    units_per_em INTEGER,
    source TEXT,
    license_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS fingerprints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    font_id INTEGER NOT NULL REFERENCES fonts(id),
    schema_version INTEGER NOT NULL,
    renderer_version TEXT NOT NULL,
    metric_vec BLOB NOT NULL,
    perceptual_vec BLOB NOT NULL,
    UNIQUE(font_id, schema_version)
);

CREATE TABLE IF NOT EXISTS match_cache (
    query_hash TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    result_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (query_hash, schema_version)
);

CREATE TABLE IF NOT EXISTS sites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT UNIQUE NOT NULL,
    rank INTEGER,
    last_crawled TIMESTAMP,
    status TEXT
);

CREATE TABLE IF NOT EXISTS site_fonts (
    site_id INTEGER NOT NULL REFERENCES sites(id),
    font_id INTEGER NOT NULL REFERENCES fonts(id),
    css_url TEXT,
    PRIMARY KEY (site_id, font_id)
);

CREATE TABLE IF NOT EXISTS request_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS match_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query_font TEXT NOT NULL,
    match_font TEXT NOT NULL,
    ip_address TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(ip_address, query_font, match_font)
);

CREATE TABLE IF NOT EXISTS user_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query_font TEXT NOT NULL,
    match_font TEXT NOT NULL,
    score INTEGER NOT NULL CHECK(score BETWEEN 1 AND 5),
    session_id TEXT,
    ip_address TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(ip_address, query_font, match_font)
);

CREATE TABLE IF NOT EXISTS daily_usage (
    ip_address TEXT NOT NULL,
    date TEXT NOT NULL,
    request_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (ip_address, date)
);
"""


class FontStore:
    """SQLite-backed store for font fingerprints and match cache."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self._index: dict[str, Fingerprint] = {}  # in-memory index
        self._font_licenses: dict[str, str] = {}  # name -> license_id

    def store_fingerprint(
        self,
        name: str,
        fp: Fingerprint,
        *,
        license_id: str = "",
        source: str = "",
    ) -> int:
        """Store a font and its fingerprint. Idempotent by file_hash."""
        cur = self.conn.cursor()

        # Upsert font
        cur.execute(
            """INSERT INTO fonts
               (file_hash, name, family, subfamily, units_per_em, license_id, source)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(file_hash) DO UPDATE SET name=excluded.name""",
            (
                fp.file_hash,
                name,
                fp.family,
                fp.subfamily,
                fp.metric_vec.units_per_em,
                license_id,
                source,
            ),
        )
        font_id = cur.execute(
            "SELECT id FROM fonts WHERE file_hash = ?", (fp.file_hash,)
        ).fetchone()[0]

        # Store fingerprint
        metric_blob = fp.metric_array().tobytes()
        perceptual_blob = fp.perceptual_vec.tobytes()
        cur.execute(
            """INSERT INTO fingerprints
               (font_id, schema_version, renderer_version, metric_vec, perceptual_vec)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(font_id, schema_version) DO UPDATE SET
                 renderer_version=excluded.renderer_version,
                 metric_vec=excluded.metric_vec,
                 perceptual_vec=excluded.perceptual_vec""",
            (font_id, fp.schema_version, fp.renderer_version, metric_blob, perceptual_blob),
        )
        self.conn.commit()
        return font_id

    def get_fingerprint(self, file_hash: str, schema_version: int) -> Fingerprint | None:
        """Retrieve a stored fingerprint by file hash and schema version."""
        row = self.conn.execute(
            """SELECT f.family, f.subfamily, fp.metric_vec, fp.perceptual_vec,
                      fp.schema_version, fp.renderer_version
               FROM fingerprints fp
               JOIN fonts f ON f.id = fp.font_id
               WHERE f.file_hash = ? AND fp.schema_version = ?""",
            (file_hash, schema_version),
        ).fetchone()

        if row is None:
            return None

        metric_arr = np.frombuffer(row["metric_vec"], dtype=np.float64)
        perceptual_arr = np.frombuffer(row["perceptual_vec"], dtype=np.float64)

        # Reconstruct a lightweight Fingerprint (metric_vec as a passthrough)
        return _StoredFingerprint(
            file_hash=file_hash,
            family=row["family"],
            subfamily=row["subfamily"],
            metric_arr=metric_arr,
            perceptual_vec=perceptual_arr.copy(),
            schema_version=row["schema_version"],
            renderer_version=row["renderer_version"],
        )

    def font_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) FROM fonts").fetchone()
        return row[0]

    def build_index(self):
        """Load all fingerprints into the in-memory index."""
        from fontmatch.features.perceptual import FINGERPRINT_SCHEMA_VERSION

        self._index.clear()
        self._font_licenses: dict[str, str] = {}  # name -> license_id
        rows = self.conn.execute(
            """SELECT f.file_hash, f.name, f.family, f.subfamily,
                      f.license_id,
                      fp.metric_vec, fp.perceptual_vec,
                      fp.schema_version, fp.renderer_version
               FROM fingerprints fp
               JOIN fonts f ON f.id = fp.font_id
               WHERE fp.schema_version = ?""",
            (FINGERPRINT_SCHEMA_VERSION,),
        ).fetchall()

        for row in rows:
            metric_arr = np.frombuffer(row["metric_vec"], dtype=np.float64)
            perceptual_arr = np.frombuffer(row["perceptual_vec"], dtype=np.float64)
            # Skip fonts with zero perceptual vectors (rendering failures)
            if np.linalg.norm(perceptual_arr) == 0:
                continue
            fp = _StoredFingerprint(
                file_hash=row["file_hash"],
                family=row["family"],
                subfamily=row["subfamily"],
                metric_arr=metric_arr,
                perceptual_vec=perceptual_arr.copy(),
                schema_version=row["schema_version"],
                renderer_version=row["renderer_version"],
            )
            self._index[row["name"]] = fp
            self._font_licenses[row["name"]] = row["license_id"] or ""

    @staticmethod
    def _base_family(family: str) -> str:
        """Normalize a family name to its base for dedup purposes.

        Strips common weight/width suffixes so "Alegreya Sans Light",
        "Alegreya Sans Medium", etc. all collapse to "alegreya sans".
        """
        import re

        fam = family.lower().strip()
        # Strip trailing weight/width/style words
        fam = re.sub(
            r"\s+(extra\s*light|ultra\s*light|thin|light|regular|medium|"
            r"semi\s*bold|demi\s*bold|bold|extra\s*bold|ultra\s*bold|"
            r"black|heavy|narrow|condensed|expanded|wide)\s*$",
            "",
            fam,
            flags=re.IGNORECASE,
        )
        return fam.strip()

    @staticmethod
    def _is_valid_family(family: str) -> bool:
        """Filter out garbage font family names from crawled data."""
        if not family or len(family) < 2:
            return False
        # Filter names that are mostly non-alphabetic
        alpha_count = sum(1 for c in family if c.isalpha())
        return alpha_count >= 2

    @staticmethod
    def _serif_category(fp: Fingerprint) -> str:
        """Extract serif category from a fingerprint's metric array.

        Returns 'sans', 'mono', or 'serif' based on the serif_score
        (last element of metric vector: 0=sans, 0.5=mono, 1.0=serif).
        """
        arr = fp.metric_array()
        score = arr[-1]  # serif_score is last element
        if score > 0.7:
            return "serif"
        elif score > 0.3:
            return "mono"
        return "sans"

    # Known metric-compatible pairs: when a query font matches a known
    # proprietary family, boost the predefined open-source replacement
    # into the top results.  This supplements the algorithmic matcher
    # for cases where fonts are metric-compatible but visually distinct.
    _KNOWN_REPLACEMENTS: dict[str, str] = {
        "arial": "Arimo",
        "arial black": "Archivo Black",
        "arial narrow": "Liberation Sans Narrow",
        "helvetica": "Liberation Sans",
        "times new roman": "Tinos",
        "courier new": "Cousine",
        "comic sans ms": "Comic Relief",
        "georgia": "Gelasio",
        "impact": "Anton",
        "trebuchet ms": "Fira Sans",
        "verdana": "DejaVu Sans",
        "calibri": "Carlito",
        "cambria": "Caladea",
        "garamond": "EB Garamond",
        "futura": "Nunito",
        "palatino": "Lora",
    }

    def identify(self, query: Fingerprint, k: int = 5) -> list[dict]:
        """Find top-k matches from the index, excluding the query font itself.

        Excludes fonts with the same file_hash or same base family name.
        Only returns fonts with a known open-source license.
        Filters out invalid font names.
        Deduplicates results by base family (keeps closest per family).
        Prioritizes same-category (serif/sans/mono) matches.
        Boosts known metric-compatible replacements for proprietary fonts.
        """
        query_base = self._base_family(query.family)
        query_cat = self._serif_category(query)

        candidates = {
            name: fp
            for name, fp in self._index.items()
            if fp.file_hash != query.file_hash
            and self._base_family(fp.family) != query_base
            and self._is_valid_family(fp.family)
            and self._font_licenses.get(name, "") != ""
        }

        # Two-stage ranking: same-category first, then cross-category
        same_cat = {n: fp for n, fp in candidates.items() if self._serif_category(fp) == query_cat}
        diff_cat = {n: fp for n, fp in candidates.items() if self._serif_category(fp) != query_cat}

        same_results = brute_rank(query, same_cat, k=k * 5) if same_cat else []
        diff_results = brute_rank(query, diff_cat, k=k * 2) if diff_cat else []

        # Merge: prioritize same-category, then backfill from different
        all_results = same_results + diff_results

        # Deduplicate by base family — keep only the closest match per family
        seen_families: set[str] = set()
        deduped = []
        for m in all_results:
            base = self._base_family(m.fingerprint.family)
            if base not in seen_families:
                seen_families.add(base)
                deduped.append(m)
            if len(deduped) >= k:
                break

        # Boost known replacement if query matches a proprietary font family.
        # If the replacement is absent or ranked below position 3, move it to #1.
        replacement_family = self._KNOWN_REPLACEMENTS.get(query.family.lower())
        if replacement_family:
            replacement_base = self._base_family(replacement_family)
            existing_pos = None
            for i, m in enumerate(deduped):
                if self._base_family(m.fingerprint.family) == replacement_base:
                    existing_pos = i
                    break

            if existing_pos is not None and existing_pos < 3:
                pass  # already well-ranked, no boost needed
            elif existing_pos is not None:
                # Present but ranked low — move to front
                deduped.insert(0, deduped.pop(existing_pos))
            else:
                # Not present — search the full candidate set
                from fontmatch.match.scorer import (
                    _cosine_distance, _euclidean_distance,
                    METRIC_SCALE, METRIC_WEIGHT, PERCEPTUAL_SCALE, PERCEPTUAL_WEIGHT,
                )
                for cand_name, cand_fp in candidates.items():
                    if self._base_family(cand_fp.family) == replacement_base:
                        q_m = query.metric_array()
                        c_m = cand_fp.metric_array()
                        m_d = _euclidean_distance(q_m, c_m) / METRIC_SCALE
                        p_d = _cosine_distance(query.perceptual_vec, cand_fp.perceptual_vec) / PERCEPTUAL_SCALE
                        total = METRIC_WEIGHT * m_d + PERCEPTUAL_WEIGHT * p_d
                        boosted = Match(
                            name=cand_name, distance=total,
                            metric_distance=m_d, perceptual_distance=p_d,
                            fingerprint=cand_fp,
                        )
                        deduped.insert(0, boosted)
                        break

        # Look up license info for each match
        results = []
        for m in deduped[:k]:
            license_id = self._get_font_license(m.name) or "unknown"
            results.append(
                {
                    "name": m.name,
                    "distance": m.distance,
                    "metric_distance": m.metric_distance,
                    "perceptual_distance": m.perceptual_distance,
                    "family": m.fingerprint.family,
                    "license_id": license_id,
                }
            )
        return results

    def cache_result(self, query_hash: str, schema_version: int, result: list[dict]):
        self.conn.execute(
            """INSERT OR REPLACE INTO match_cache (query_hash, schema_version, result_json)
               VALUES (?, ?, ?)""",
            (query_hash, schema_version, json.dumps(result)),
        )
        self.conn.commit()

    def get_cached_result(self, query_hash: str, schema_version: int) -> list[dict] | None:
        row = self.conn.execute(
            "SELECT result_json FROM match_cache WHERE query_hash = ? AND schema_version = ?",
            (query_hash, schema_version),
        ).fetchone()
        if row is None:
            return None
        return json.loads(row["result_json"])

    def log_request(self, endpoint: str) -> None:
        """Record an API request for counting."""
        self.conn.execute("INSERT INTO request_log (endpoint) VALUES (?)", (endpoint,))
        self.conn.commit()

    def request_count(self, endpoint: str | None = None) -> int:
        """Count logged requests, optionally filtered by endpoint."""
        if endpoint:
            row = self.conn.execute(
                "SELECT COUNT(*) FROM request_log WHERE endpoint = ?", (endpoint,)
            ).fetchone()
        else:
            row = self.conn.execute("SELECT COUNT(*) FROM request_log").fetchone()
        return row[0]

    def save_user_score(
        self,
        query_font: str,
        match_font: str,
        score: int,
        ip_address: str,
        session_id: str = "",
    ) -> bool:
        """Save a user's quality score for a match. One rating per IP per pair.

        Returns True if saved, False if this IP already rated this pair.
        """
        existing = self.conn.execute(
            """SELECT id FROM user_scores
               WHERE ip_address = ? AND query_font = ? AND match_font = ?""",
            (ip_address, query_font, match_font),
        ).fetchone()
        if existing is not None:
            return False
        self.conn.execute(
            """INSERT INTO user_scores
               (query_font, match_font, score, ip_address, session_id)
               VALUES (?, ?, ?, ?, ?)""",
            (query_font, match_font, score, ip_address, session_id),
        )
        self.conn.commit()
        return True

    def save_report(
        self,
        query_font: str,
        match_font: str,
        ip_address: str,
    ) -> bool:
        """Save a 'bad match' report. One per IP per pair. Returns True if saved."""
        existing = self.conn.execute(
            """SELECT id FROM match_reports
               WHERE ip_address = ? AND query_font = ? AND match_font = ?""",
            (ip_address, query_font, match_font),
        ).fetchone()
        if existing is not None:
            return False
        self.conn.execute(
            """INSERT INTO match_reports (query_font, match_font, ip_address)
               VALUES (?, ?, ?)""",
            (query_font, match_font, ip_address),
        )
        self.conn.commit()
        return True

    def get_average_score(self, query_font: str, match_font: str) -> tuple[float | None, int]:
        """Return (average_score, vote_count) for a match pair."""
        row = self.conn.execute(
            """SELECT AVG(score), COUNT(*) FROM user_scores
               WHERE query_font = ? AND match_font = ?""",
            (query_font, match_font),
        ).fetchone()
        return (row[0], row[1])

    def list_font_families(self, clean_only: bool = False, indexed_only: bool = False) -> list[str]:
        """Return all distinct font families sorted alphabetically.

        If clean_only is True, filter out garbage names from crawled data.
        If indexed_only is True, only return families that have a fingerprint
        and a known license (i.e. fonts whose /similar-to/ page will work).
        """
        if indexed_only:
            from fontmatch.features.perceptual import FINGERPRINT_SCHEMA_VERSION

            rows = self.conn.execute(
                """SELECT DISTINCT f.family FROM fonts f
                   JOIN fingerprints fp ON fp.font_id = f.id
                     AND fp.schema_version = ?
                   WHERE f.license_id != ''
                   ORDER BY f.family""",
                (FINGERPRINT_SCHEMA_VERSION,),
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT DISTINCT family FROM fonts ORDER BY family").fetchall()
        families = [row[0] for row in rows]
        if clean_only:
            families = [f for f in families if self._is_clean_family(f)]
        return families

    def search_font_families(
        self,
        query: str = "",
        category: str = "",
        offset: int = 0,
        limit: int = 40,
    ) -> tuple[list[dict], int]:
        """Search fonts with optional name query and category filter.

        Returns (results, total_count) where each result is a dict with
        family, category, and license_id.
        """
        from fontmatch.features.perceptual import FINGERPRINT_SCHEMA_VERSION

        # Build base query joining fonts with fingerprints to get serif_score.
        # Group by family to deduplicate weight variants.
        sql = """
            SELECT f.family, f.license_id,
                   MIN(fp.metric_vec) AS metric_vec
            FROM fonts f
            JOIN fingerprints fp ON fp.font_id = f.id
                AND fp.schema_version = ?
            WHERE f.license_id != ''
        """
        params: list = [FINGERPRINT_SCHEMA_VERSION]

        if query:
            sql += " AND LOWER(f.family) LIKE ?"
            params.append(f"%{query.lower()}%")

        sql += " GROUP BY f.family ORDER BY f.family"

        rows = self.conn.execute(sql, params).fetchall()

        # Post-filter: clean names and category
        results = []
        for row in rows:
            family = row["family"]
            if not self._is_clean_family(family):
                continue
            metric_arr = np.frombuffer(row["metric_vec"], dtype=np.float64)
            serif_score = metric_arr[-1] if len(metric_arr) > 0 else 0.0
            if serif_score > 0.7:
                cat = "serif"
            elif serif_score > 0.3:
                cat = "mono"
            else:
                cat = "sans-serif"

            if category and cat != category:
                continue

            from fontmatch.web.helpers import google_fonts_url

            results.append(
                {
                    "family": family,
                    "category": cat,
                    "license_id": row["license_id"] or "unknown",
                    "google_fonts_url": google_fonts_url(family)
                    if self.has_google_fonts_source(family)
                    else None,
                }
            )

        total = len(results)
        page = results[offset : offset + limit]
        return page, total

    @staticmethod
    def _is_clean_family(family: str) -> bool:
        """Check if a family name is clean enough to display to users.

        Filters out: hashes, license text, single chars, numeric-only,
        very long names, names starting with digits, and names that
        are mostly non-alphabetic.
        """
        if not family or len(family) < 2 or len(family) > 60:
            return False
        # Must start with a letter
        if not family[0].isalpha():
            return False
        alpha_count = sum(1 for c in family if c.isalpha())
        # Must be at least 60% alphabetic
        if alpha_count < len(family) * 0.6:
            return False
        # Filter out names that look like hashes (>16 hex chars)
        stripped = family.replace("-", "").replace(" ", "")
        if len(stripped) > 16 and all(c in "0123456789abcdef" for c in stripped.lower()):
            return False
        return True

    def get_font_by_family(self, family: str, licensed_only: bool = True) -> dict | None:
        """Look up a font row by family name (case-insensitive).

        Prefers fonts with a known license and 'Regular' subfamily so
        comparisons use the base weight of an open-source font.
        """
        rows = self.conn.execute(
            "SELECT * FROM fonts WHERE LOWER(family) = LOWER(?)",
            (family,),
        ).fetchall()

        # Fallback: normalize hyphens/spaces for slug round-trip mismatches
        # e.g. "Thabit Bold" should match "Thabit-Bold"
        if not rows:
            rows = self.conn.execute(
                "SELECT * FROM fonts WHERE LOWER(REPLACE(family, '-', ' ')) = LOWER(REPLACE(?, '-', ' '))",
                (family,),
            ).fetchall()

        if not rows:
            return None

        # Partition into licensed vs unlicensed
        licensed = [r for r in rows if r["license_id"]]
        if licensed_only and not licensed:
            return None
        pool = licensed if licensed_only else rows

        # Prefer Regular variant
        for row in pool:
            if row["subfamily"] and row["subfamily"].lower() == "regular":
                return dict(row)
        return dict(pool[0])

    def get_font_source(self, name: str) -> str | None:
        """Return the source path for a font by its filename."""
        row = self.conn.execute(
            "SELECT source FROM fonts WHERE name = ? LIMIT 1", (name,)
        ).fetchone()
        return row[0] if row else None

    def _get_font_license(self, name: str) -> str | None:
        """Return the license_id for a font by filename."""
        row = self.conn.execute(
            "SELECT license_id FROM fonts WHERE name = ? LIMIT 1", (name,)
        ).fetchone()
        return row[0] if row else None

    def has_google_fonts_source(self, family: str) -> bool:
        """Check if any font in this family was ingested from Google Fonts.

        Matches explicit prefixes (ofl/, apache/, ufl/) as well as the bare
        ``familyslug/FontFile.ttf`` pattern produced when the google-fonts-repo
        is walked without a license-category prefix.  Crawled web-font sources
        are excluded by requiring the source to end with a font extension.
        """
        row = self.conn.execute(
            """SELECT 1 FROM fonts
               WHERE family = ? AND (
                   source LIKE 'ofl/%'
                   OR source LIKE 'apache/%'
                   OR source LIKE 'ufl/%'
                   OR (source LIKE '%/%' AND (
                       source LIKE '%.ttf' OR source LIKE '%.otf'
                       OR source LIKE '%.woff' OR source LIKE '%.woff2'))
               )
               LIMIT 1""",
            (family,),
        ).fetchone()
        return row is not None

    def increment_daily_usage(self, ip: str) -> int:
        """Atomically increment today's request count for an IP. Returns the new count."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        cur = self.conn.cursor()
        try:
            cur.execute("BEGIN IMMEDIATE")
            cur.execute(
                """INSERT INTO daily_usage (ip_address, date, request_count)
                   VALUES (?, ?, 1)
                   ON CONFLICT(ip_address, date) DO UPDATE
                   SET request_count = request_count + 1""",
                (ip, today),
            )
            row = cur.execute(
                "SELECT request_count FROM daily_usage WHERE ip_address = ? AND date = ?",
                (ip, today),
            ).fetchone()
            self.conn.commit()
            return row[0]
        except Exception:
            self.conn.rollback()
            raise

    def get_daily_usage(self, ip: str) -> int:
        """Return today's request count for an IP (0 if none)."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        row = self.conn.execute(
            "SELECT request_count FROM daily_usage WHERE ip_address = ? AND date = ?",
            (ip, today),
        ).fetchone()
        return row[0] if row else 0

    def list_heavy_users(self, threshold: int = 200) -> list[dict]:
        """Return IPs exceeding the threshold today, with their counts."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        rows = self.conn.execute(
            """SELECT ip_address, request_count FROM daily_usage
               WHERE date = ? AND request_count > ?
               ORDER BY request_count DESC""",
            (today, threshold),
        ).fetchall()
        return [{"ip_address": r[0], "request_count": r[1]} for r in rows]

    def cleanup_old_usage(self, days: int = 90) -> int:
        """Delete daily_usage rows older than N days. Returns rows deleted."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        cur = self.conn.execute(
            "DELETE FROM daily_usage WHERE date < ?", (cutoff,)
        )
        self.conn.commit()
        return cur.rowcount

    def close(self):
        self.conn.close()


class _StoredFingerprint(Fingerprint):
    """A fingerprint reconstructed from stored blobs (no MetricVector object)."""

    def __init__(
        self,
        file_hash: str,
        family: str,
        subfamily: str,
        metric_arr: np.ndarray,
        perceptual_vec: np.ndarray,
        schema_version: int,
        renderer_version: str,
    ):
        # Bypass frozen dataclass init — we store pre-computed arrays
        object.__setattr__(self, "file_hash", file_hash)
        object.__setattr__(self, "family", family)
        object.__setattr__(self, "subfamily", subfamily)
        object.__setattr__(self, "metric_vec", None)
        object.__setattr__(self, "perceptual_vec", perceptual_vec)
        object.__setattr__(self, "schema_version", schema_version)
        object.__setattr__(self, "renderer_version", renderer_version)
        object.__setattr__(self, "_metric_arr", metric_arr)

    def metric_array(self) -> np.ndarray:
        return self._metric_arr
