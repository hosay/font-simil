"""Site list loading (Majestic Million CSV format)."""

from __future__ import annotations

import csv
import io


def parse_site_list(csv_content: str, limit: int = 10000) -> list[str]:
    """Parse a Majestic Million CSV and return domain names.

    The CSV has columns: GlobalRank, TldRank, Domain, TLD, ...
    """
    reader = csv.DictReader(io.StringIO(csv_content))
    domains = []
    for row in reader:
        if len(domains) >= limit:
            break
        domain = row.get("Domain", "").strip()
        if domain:
            domains.append(domain)
    return domains
