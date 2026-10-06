"""Refresh fontmatch/web/google_fonts_live.txt: the families fonts.google.com
serves today. google/fonts keeps directories for retired families, so the repo
alone can't tell which specimen links work. Run when google-fonts-repo is
updated:

    python dev/update_google_fonts_live.py
"""

import json
import urllib.request
from pathlib import Path

URL = "https://fonts.google.com/metadata/fonts"
OUT = Path(__file__).resolve().parent.parent / "fontmatch" / "web" / "google_fonts_live.txt"


def main() -> None:
    with urllib.request.urlopen(URL, timeout=60) as resp:
        data = json.load(resp)
    families = sorted({f["family"] for f in data["familyMetadataList"]})
    if len(families) < 1500:
        raise SystemExit(f"only {len(families)} families; refusing to overwrite {OUT}")
    OUT.write_text(
        f"# Families served by fonts.google.com ({URL}); see {Path(__file__).name}\n"
        + "\n".join(families)
        + "\n"
    )
    print(f"wrote {len(families)} families to {OUT}")


if __name__ == "__main__":
    main()
