# CLAUDE.md — xflippa project rules

## Preferences
Use Python as the primary programming language

## Dev server
Run dev server on `0.0.0.0:8087` so it is accessible on the LAN

## Expert subagent review

For any non-trivial feature or fix, use a two-pass review cycle with an expert subagent:

1. **Before implementing**: draft a plan, then spawn an `Explore` subagent to critique it. Brief the subagent with the goal, the constraints, what you've already ruled out, and specific questions. Incorporate the feedback before writing code.
2. **After implementing**: once tests are green, spawn a second `Explore` subagent to review the final code. Give it the file paths, what each function is supposed to do, and what edge cases you handled. Ask it to look for bugs, fragile assumptions, and test coverage gaps. Fix anything blocking.

The subagent should never be asked to write code — only to read files and report findings. Keep prompts under 500 words; be specific about what to look for.

---

## TDD strategy

**Always write a failing test first.** The cycle is:

1. Write a test that expresses the intended behaviour and fails (import errors are fine)
2. Run it to confirm it fails for the right reason
3. Implement the minimum code to make it pass
4. Run the full suite to check for regressions
5. Refactor if needed, re-run to stay green

**Test structure:**
- Pure functions (parsers, formatters, diff logic) → unit tests using fixtures or constructed inputs, no network
- Browser/network behaviour → integration tests marked `@pytest.mark.integration`
- Group integration tests with `@pytest.mark.xdist_group("integration")` so they share one xdist worker and avoid concurrent login attempts (see `dev/pytest.ini`)

**Running tests:**
```bash
# Unit tests only — fast, no network
pytest dev/tests/ -m "not integration" -n auto

# Full suite (reuses cached Flippa cookies when available)
pytest dev/tests/ -n auto
```

**Parallel safety:**
- Tests must be safe to run under `pytest-xdist` (`-n auto`)
- Do not share global mutable state across tests
- Use `monkeypatch` (not `try/finally`) to patch module-level constants like `ADSENSE_THRESHOLD`
- Use `tmp_path` for any test that writes files (SQLite DBs, cookies, output files)
- Never write to `dev/scrape_history.db` or `dev/flippa_cookies.json` from a test

---

## /dev directory and venv

All scripts, tests, fixtures, and generated output that are **not part of the production system** live under `dev/`:


**Always activate the venv before running any Python:**
```bash
source venv/bin/activate
```

**Never commit the venv directory.** It is large, platform-specific, and regeneratable:
```bash
python3 -m venv venv
pip install -r requirements.txt
python -m camoufox fetch   # downloads the Camoufox browser binary once
```

**Adding packages:** install with pip, then record the exact version:
```bash
pip install somepackage
pip show somepackage | grep Version   # get the installed version
# then add "somepackage==X.Y.Z" to requirements.txt manually
```
Do not run `pip freeze > requirements.txt` — it dumps transitive dependencies and makes the file noisy.

---

## Camoufox

Camoufox is a Firefox-based anti-detection browser built on Playwright. It is used for **all** Flippa page fetches (login, search results, individual listings) to maintain a consistent TLS fingerprint and avoid Cloudflare bot detection. Do not switch to `requests` mid-session — it will trigger Cloudflare.

**One-time setup:**
```bash
python -m camoufox fetch
```

**Basic usage pattern:**
```python
from camoufox.sync_api import Camoufox

with Camoufox(headless=True) as browser:
    page = browser.new_page()
    page.set_default_timeout(30000)
    page.goto("https://example.com", wait_until="load")
    html = page.content()
    cookies = page.context.cookies()   # Playwright-format list of dicts
```

**`wait_until` strategy:**
- `"networkidle"` — use only for the Flippa **login page** (`/login`). The login form is React-rendered; networkidle ensures the JS has fully executed before you interact. This can be slow but is necessary.
- `"load"` — use for all other pages (search results, individual listings). Analytics scripts on listing pages keep connections open indefinitely, so networkidle will time out.
- `"domcontentloaded"` — use for the homepage (just cookie consent, no interaction needed).

**Injecting saved cookies into a new browser session** (skips login):
```python
raw_cookies = json.load(open("dev/flippa_cookies.json"))  # Playwright-format list
page.context.add_cookies(raw_cookies)
# now navigate directly to the authenticated page
```

## If using Django

- Run dev server on `127.0.0.1:8087`
- Always use Django migrations; never make DB changes manually
