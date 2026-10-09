#!/usr/bin/env python3
"""Real-world image eval set from r/identifythisfont (dev only).

Solved posts (flair "Identified") give a photo or screenshot plus the font the
community named. Three steps:

    scrape  Camoufox at human speed: listings, post JSON, images. Needs the
            separate scrape venv (Playwright 1.60 crashes on Reddit page errors
            with Camoufox's Firefox 135; 1.51 works):
                python3 -m venv venv-scrape
                venv-scrape/bin/pip install -r dev/requirements-scrape.txt
                venv-scrape/bin/python dev/identifythisfont.py scrape --max-posts 200
    label   pick the answer comment and resolve the font name (project venv).
    build   apply hand annotations (crop box + transcript, annotations.json)
            and write reddit_dev/ and reddit_test/ in the browser_dev manifest
            format, so tune_image_ranker.py --browser can read them. Truths that
            are not in the image catalog go to manifest_acceptable.json (eval
            only, scored against PROPRIETARY_TO_OPEN_SOURCE / aliases).

Output lives in eval_reports/reddit_itf/ (gitignored, local only). The raw
post JSON (raw/, kept for re-labelling) includes usernames; nothing derived
from it (labels, manifests) does.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import random
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = Path("/opt/projects/font_simil/eval_reports/reddit_itf")
BASE = "https://www.reddit.com"
SUB = "/r/identifythisfont"
LISTINGS = ("top:all", "top:year")

IMAGE_HOSTS = ("i.redd.it", "preview.redd.it")
BOTS = {"automoderator"}
DELETED = {"[deleted]", "[removed]", ""}


class Blocked(Exception):
    """Reddit answered with something other than the JSON we asked for."""


# --- listings and images ----------------------------------------------------------------------


def image_urls(d: dict) -> list[str]:
    """Full-size image URLs of a post, gallery order; [] for anything else."""
    if d.get("is_gallery"):
        meta = d.get("media_metadata") or {}
        urls = []
        for item in (d.get("gallery_data") or {}).get("items", []):
            m = meta.get(item.get("media_id"), {})
            if m.get("status") == "valid" and m.get("e") == "Image" and m.get("s", {}).get("u"):
                urls.append(html.unescape(m["s"]["u"]))
        return urls
    if d.get("post_hint") == "image" and urlparse(d.get("url", "")).hostname in IMAGE_HOSTS:
        return [d["url"]]
    return []


def keep_post(d: dict) -> bool:
    flair = (d.get("link_flair_text") or "").strip().lower()
    if flair != "identified":  # also drops "Lettering" (custom, not a font)
        return False
    if d.get("over_18") or d.get("is_video"):
        return False
    return bool(image_urls(d))


def parse_json_body(text: str):
    """JSON from a .json page, or Blocked for challenge pages and API errors."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        raise Blocked(f"not JSON: {text[:80]!r}") from None
    if isinstance(data, dict) and ("error" in data or data.get("kind") not in ("Listing", "t3")):
        raise Blocked(f"error response: {str(data)[:120]}")
    return data


class Pacer:
    """Delays (seconds) that look like a person reading: 8-20 s per page and a
    longer break every 20-30 requests."""

    def __init__(self, rng: random.Random):
        self.rng = rng
        self._until_break = rng.randint(20, 30)

    def next_delay(self) -> float:
        self._until_break -= 1
        if self._until_break <= 0:
            self._until_break = self.rng.randint(20, 30)
            return self.rng.uniform(60, 180)
        return self.rng.triangular(8, 20, 11)


# --- answer selection -------------------------------------------------------------------------

THANKS = re.compile(
    r"\b(thank|thanks|thx|ty|tysm|that'?s it|that is it|that'?s the one|solved|perfect|"
    r"exactly it|nailed it|bingo|found it|you'?re right|legend|lifesaver|life saver|spot on)\b",
    re.I,
)
NEGATIVE = re.compile(
    r"\b(not quite|not it|isn'?t it|not the (one|same|font)|but no|nope|no luck|close but|"
    r"doesn'?t (match|look)|unfortunately|sadly|not exactly|not really|thanks? anyway|"
    r"thanks? (for|4) (trying|the try|the effort)|thanks? though|still looking|keep looking|"
    r"any other|not what)\b|^\s*no\b",
    re.I,
)
SIMILAR = re.compile(
    r"\b(similar|alternative|lookalike|look-alike|resembl\w*|close to|close enough|vibes?|"
    r"(fairly|pretty|very|quite|is) close|closest|reminds? me|in the style of|"
    r"might (also )?like|also try|free version|dupe|knock-?off|not exact)\b",
    re.I,
)


@dataclass
class Answer:
    name: str  # resolved display name, or the raw candidate when unknown
    known: bool  # resolved through the gazetteer
    confidence: str  # high | medium | similar
    method: str  # op_thanks | agreement
    excerpt: str


def _comments(children, parent_author=None):
    """Depth-first (comment data, parent comment data or None)."""
    for k in children or []:
        if not isinstance(k, dict) or k.get("kind") != "t1":
            continue
        d = k["data"]
        yield d, parent_author
        replies = d.get("replies")
        if isinstance(replies, dict):
            yield from _comments(replies.get("data", {}).get("children"), d)


def _usable(d: dict, op: str) -> bool:
    a = (d.get("author") or "").strip()
    return (
        a not in DELETED
        and a.lower() not in BOTS
        and a != op
        and not d.get("distinguished")
        and not d.get("stickied")
        and (d.get("body") or "").strip() not in DELETED
    )


def find_answer(post: dict, comments: list, gaz: Gazetteer) -> Answer | None:
    op = post.get("author")
    thanked = []
    for d, parent in _comments(comments):
        if op in DELETED or op is None:
            break  # a deleted OP's replies can't be told from other deleted accounts
        if d.get("author") != op or parent is None:
            continue
        body = d.get("body") or ""
        if THANKS.search(body) and not NEGATIVE.search(body) and _usable(parent, op):
            thanked.append((parent, body))
    for parent, reply in sorted(thanked, key=lambda pr: -(pr[0].get("score") or 0)):
        cands = name_candidates(parent.get("body", ""), gaz)
        if cands:
            name, known = _display(cands[0], gaz)
            similar = SIMILAR.search(parent["body"]) or SIMILAR.search(reply)
            conf = "similar" if similar else "high"
            return Answer(name, known, conf, "op_thanks", parent["body"][:300])

    # Agreement: total score of the top-level comments naming each font. The
    # winner needs two distinct authors and over twice the runner-up's score.
    votes: dict[tuple, set] = {}
    scores: dict[tuple, int] = {}
    bodies = {}
    for k in comments or []:
        if not isinstance(k, dict) or k.get("kind") != "t1":
            continue
        d = k["data"]
        if not _usable(d, op) or (d.get("score") or 0) <= 0:
            continue
        cands = name_candidates(d.get("body", ""), gaz)
        if cands:
            vote = _display(cands[0], gaz)  # "futura" and "Futura PT Bold" are one vote
            if d["author"] in votes.get(vote, ()):
                continue
            votes.setdefault(vote, set()).add(d["author"])
            scores[vote] = scores.get(vote, 0) + d["score"]
            bodies.setdefault(vote, d["body"])
    ranked = sorted(scores, key=lambda v: -scores[v])
    if ranked and len(votes[ranked[0]]) >= 2:
        runner_up = scores[ranked[1]] if len(ranked) > 1 else 0
        if scores[ranked[0]] > 2 * runner_up:
            name, known = ranked[0]
            return Answer(name, known, "medium", "agreement", bodies[ranked[0]][:300])
    return None


def _display(candidate: str, gaz: Gazetteer) -> tuple[str, bool]:
    resolved = gaz.resolve(candidate)
    return (resolved, True) if resolved else (candidate.title(), False)


# --- font names -------------------------------------------------------------------------------

STYLE_WORDS = (
    r"thin|hairline|extra ?light|ultra ?light|light|book|regular|normal|roman|medium|semi ?bold|"
    r"demi ?bold|demi|bold|extra ?bold|ultra ?bold|heavy|black|ultra|italic|oblique|condensed|"
    r"compressed|narrow|extended|expanded|wide|display|text|pro|std|lt|mt|ot|ef|ps"
)
_TRAILING_STYLE = re.compile(rf"(\s+({STYLE_WORDS}))+$", re.I)
_LEAD = re.compile(
    r"^(?:yep |yes |yeah |yup |\w+'s |i think |i believe |pretty sure |probably |maybe |"
    r"possibly |actually |definitely |"
    r"looks like |look(?:s)? like |this is |that'?s |it'?s |it is |its |the font is |font is |"
    r"try |you're in luck,? |could be |seems like |might be |a font named |called )+",
    re.I,
)
_GENERIC_LINK = {
    "here", "this", "link", "this one", "source", "font", "it", "website", "the", "a", "img",
    "img proof", "proof", "image", "pic", "picture", "screenshot", "example", "download",
}  # fmt: skip
# Links to images are proof, not a name: "[IMG Proof](imgur...)".
_PROOF_HOSTS = ("imgur.com", "i.imgur.com", "i.redd.it", "preview.redd.it", "reddit.com")
_LINK = re.compile(r"\[([^\]]*)\]\((https?://(?:[^()\s]|\([^()\s]*\))+)\)")
_BARE_URL = re.compile(r"(?<!\()https?://[^\s)\]]+")
_BOLD = re.compile(r"\*\*([^*]{2,60})\*\*")


def normalize(name: str) -> str:
    s = unquote(name).replace("\\", "").replace("+", " ").replace("_", " ").replace("-", " ")
    s = re.sub(r"[*`\"“”.!?,;:]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    s = re.sub(r"\s+(font|typeface|family|fonts)$", "", s)
    return s


# Category words some aliases use ("Sans Serif" -> Inter); never a font answer.
GENERIC_NAMES = {
    "sans serif", "sans", "serif", "old english", "script", "monospace", "mono", "handwriting",
    "cursive", "gothic", "blackletter", "display", "slab serif", "grotesque", "geometric",
}  # fmt: skip
_SENTENCE_START = re.compile(r"(^|[.!?\n]\s*|\.\.\.\s*|…\s*)$")


class Gazetteer:
    """Known font names (normalized key -> display name) with longest-match search.

    common_words: one-word names that are also dictionary words (Play, Share,
    Average); in running text these only count when capitalized mid-sentence."""

    def __init__(self, names: dict[str, str], common_words=frozenset()):
        self.names = {
            normalize(k): v for k, v in names.items() if normalize(k) not in GENERIC_NAMES
        }
        self.common = {k for k in self.names if " " not in k and k in common_words}
        alts = sorted(self.names, key=len, reverse=True)
        pat = r"\b(" + "|".join(re.escape(a) for a in alts) + r")\b"
        self._re = re.compile(pat, re.I) if alts else None

    def key(self, name: str) -> str | None:
        """Known key for a name, dropping trailing weight/style words one at a
        time ("Futura PT Bold Italic" -> "futura pt"; "Cooper Black" stays)."""
        s = normalize(name)
        while s:
            if s in self.names:
                return s
            t = re.sub(rf"\s+({STYLE_WORDS})$", "", s, flags=re.I)
            if t == s:
                return None
            s = t
        return None

    def resolve(self, name: str) -> str | None:
        k = self.key(name)
        return self.names[k] if k else None

    def find(self, text: str) -> list[str]:
        """Known names in reading order. A one-word match must be capitalized;
        a dictionary-word name (Play, Share) must also not start a sentence."""
        if not self._re:
            return []
        text = normalize_text(text)
        hits = []
        for m in self._re.finditer(text):
            word = m.group(1)
            key = word.lower()
            if " " not in key:
                if not word[0].isupper():
                    continue
                if key in self.common and _SENTENCE_START.search(text[: m.start()]):
                    continue
            hits.append(key)
        return hits


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[*`\"“”]+", " ", text))


def _from_url(url: str) -> str | None:
    u = urlparse(url)
    host = (u.hostname or "").removeprefix("www.")
    parts = [p for p in u.path.split("/") if p]
    if host == "fonts.google.com" and len(parts) >= 2 and parts[0] == "specimen":
        return parts[1]
    if host == "fonts.adobe.com" and len(parts) >= 2 and parts[0] == "fonts":
        return parts[1]
    if host == "fontsinuse.com" and len(parts) >= 3 and parts[0] == "typefaces":
        return parts[2]
    if host.endswith("wikipedia.org") and len(parts) >= 2 and parts[0] == "wiki":
        return re.sub(r"_\((typeface|font)\)$", "", unquote(parts[1]))
    if host == "dafont.com" and parts and parts[-1].endswith(".font"):
        return parts[-1].removesuffix(".font")
    return None


_TRAIL = re.compile(
    r"(\s+(for sure|i think|i believe|maybe|probably|imo|perhaps|to me|lol|definitely))+$"
)


def _clean_candidate(s: str, gaz: Gazetteer) -> str | None:
    s = _TRAIL.sub("", _LEAD.sub("", normalize(s)).strip())
    known = gaz.key(s)
    if known:
        return known
    s = _TRAILING_STYLE.sub("", s).strip()
    if not s or s in _GENERIC_LINK or len(s) > 40 or not re.search(r"[a-z]", s):
        return None
    return s


def name_candidates(body: str, gaz: Gazetteer) -> list[str]:
    """Normalized font-name candidates in priority order: links, bold text, a
    short whole-comment answer, then known names in reading order."""
    out: list[str] = []

    def add(s):
        c = _clean_candidate(s, gaz) if s else None
        if c and c not in out:
            out.append(c)

    for text, url in _LINK.findall(body):
        host = (urlparse(url).hostname or "").removeprefix("www.")
        if host in _PROOF_HOSTS:
            continue
        url_like = re.search(r"\.[a-z]{2,4}(/|$)", text.lower()) and " " not in text.strip()
        if text.startswith("http") or url_like or normalize(text) in _GENERIC_LINK:
            add(_from_url(url) or None)
        else:
            add(text)
    for url in _BARE_URL.findall(_LINK.sub(" ", body)):
        add(_from_url(url))
    for b in _BOLD.findall(body):
        hit = gaz.find(b)
        add(hit[0] if hit else (b if gaz.key(_LEAD.sub("", normalize(b))) else None))
    # A short whole-comment answer ("Cooper black", "This is Futura.") is a name
    # even in lower case; otherwise known names in reading order.
    plain = _LINK.sub(" ", body).strip()
    short = plain if plain and len(plain.split()) <= 6 and "\n" not in plain else ""
    if short and gaz.key(_LEAD.sub("", normalize(short))):
        add(short)
    for hit in gaz.find(body):
        add(hit)
    # Last resort, unknown names: a very short answer that looks like a name
    # ("Friz Quadrata", "Papyrus."), not chat ("Baller shirt btw").
    if not out and short and len(short.split()) <= 3 and _looks_like_name(short):
        add(short)
    return out


_CHAT = {"btw", "lol", "lmao", "idk", "no", "yes", "thanks", "thank", "you", "i", "me", "it",
         "this", "that", "the", "is", "and", "or", "maybe", "same", "idea", "font"}  # fmt: skip


def _looks_like_name(text: str) -> bool:
    words = re.findall(r"[A-Za-z0-9']+", _TRAIL.sub("", _LEAD.sub("", normalize(text))))
    return bool(words) and not {w.lower() for w in words} & _CHAT


def box_pixels(box, size) -> tuple[int, int, int, int] | None:
    """Annotation crop box, fractions [x0, y0, x1, y1] of the image -> pixels."""
    if not box:
        return None
    w, h = size
    return (round(box[0] * w), round(box[1] * h), round(box[2] * w), round(box[3] * h))


# --- scrape -----------------------------------------------------------------------------------


def load_index(path: Path) -> list[dict]:
    """posts.jsonl, one record per post (a retried post's latest record wins)."""
    if not path.exists():
        return []
    recs = {}
    for line in path.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            recs[r["id"]] = r
    return list(recs.values())


def done_ids(recs: list[dict]) -> set[str]:
    """Posts not to fetch again: got images, or every image was a duplicate.
    A post whose download failed (or an older record with no images and no
    'dupes' flag) is retried."""
    return {r["id"] for r in recs if r["images"] or r.get("dupes")}


def split_for(fam: str, group: str, acceptable: list[str], split_of) -> str:
    """test when the truth, its group or any accepted substitute is a test
    family, so tuning on dev never rewards a test-split face."""
    return "test" if "test" in {split_of(f) for f in (fam, group, *acceptable)} else "dev"


class Session:
    def __init__(self, page, pacer: Pacer, max_requests: int):
        self.page = page
        self.pacer = pacer
        self.left = max_requests

    def _wait(self):
        time.sleep(self.pacer.next_delay())

    def _spend(self):
        if self.left <= 0:
            raise Blocked("request budget used up")
        self.left -= 1

    def _retry(self, fn):
        """One retry after a minute on a browser/network error, then stop."""
        from playwright.sync_api import Error as PlaywrightError

        try:
            return fn()
        except PlaywrightError:
            time.sleep(self.pacer.rng.uniform(50, 80))
            try:
                return fn()
            except PlaywrightError as e:
                raise Blocked(f"browser error: {str(e)[:120]}") from None

    def get_json(self, url: str):
        self._spend()
        self._wait()
        resp = self._retry(lambda: self.page.goto(url, wait_until="domcontentloaded"))
        status = resp.status if resp else 0
        if status in (403, 429) or status >= 500:
            raise Blocked(f"HTTP {status} for {url}")
        return parse_json_body(self.page.locator("body").inner_text())

    def get_image(self, url: str) -> bytes | None:
        """Image bytes; None for a removed/odd image (403/404, not image/*).
        Raises Failed for a transient error, Blocked on rate limiting."""
        self._spend()
        time.sleep(self.pacer.rng.uniform(2, 6))
        r = self._retry(lambda: self.page.request.get(url, timeout=30000))
        if r.status == 429:
            raise Blocked(f"HTTP 429 for image {url}")
        if r.status >= 500:
            raise Failed(f"HTTP {r.status}")
        if not r.ok or not r.headers.get("content-type", "").startswith("image/"):
            return None
        return r.body()


class Failed(Exception):
    """A transient image failure: record the post as failed so a later run retries it."""


def _image_ok(data: bytes) -> tuple[str, tuple[int, int]] | None:
    import io

    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            if min(im.size) < 64:
                return None
            return (im.format or "jpeg").lower(), im.size
    except Exception:
        return None


def scrape(args):
    from camoufox.sync_api import Camoufox

    out = Path(args.out)
    (out / "images").mkdir(parents=True, exist_ok=True)
    (out / "raw").mkdir(exist_ok=True)
    index = out / "posts.jsonl"
    recs = load_index(index)
    seen = done_ids(recs)
    hashes = {img["sha1"] for r in recs for img in r["images"]}
    pacer = Pacer(random.Random())
    got = 0
    log = lambda *a: print(time.strftime("%H:%M:%S"), *a, flush=True)  # noqa: E731

    with Camoufox(
        headless=True, persistent_context=True, user_data_dir=str(out / ".profile")
    ) as ctx:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.set_default_timeout(30000)
        page.goto(BASE + SUB + "/", wait_until="domcontentloaded")
        sess = Session(page, pacer, args.max_requests)
        try:
            for listing in args.listings.split(","):
                sort, _, t = listing.partition(":")
                after = None
                for _ in range(10):  # Reddit stops at ~1000 items per listing
                    url = f"{BASE}{SUB}/{sort}.json?limit=100&raw_json=1" + (
                        f"&t={t}" if t else ""
                    )
                    if after:
                        url += f"&after={after}"
                    data = sess.get_json(url)
                    kids = data["data"]["children"]
                    if not kids and after is None:
                        raise Blocked(f"empty first page for {listing}")
                    todo = [
                        k["data"]
                        for k in kids
                        if keep_post(k["data"]) and k["data"]["id"] not in seen
                    ]
                    log(f"{listing} page: {len(kids)} posts, {len(todo)} new identified")
                    for d in todo:
                        got += _scrape_post(sess, d, out, index, seen, hashes, log)
                        if got >= args.max_posts:
                            log(f"done: {got} posts")
                            return
                    after = data["data"].get("after")
                    if not after:
                        break
        except Blocked as e:
            log(f"stopped: {e}")
            (out / "last_block.html").write_text(page.content())
            sys.exit(2)
    log(f"done: {got} posts")


def _scrape_post(sess, d, out, index, seen, hashes, log) -> int:
    data = sess.get_json(f"{BASE}{d['permalink'].rstrip('/')}.json?raw_json=1")
    if not (isinstance(data, list) and len(data) == 2):
        raise Blocked(f"unexpected post JSON shape for {d['id']}")
    images = []
    failed = dupes = False
    for i, url in enumerate(image_urls(data[0]["data"]["children"][0]["data"])[:3]):
        try:
            blob = sess.get_image(url)
        except Failed:
            failed = True
            continue
        info = _image_ok(blob) if blob else None
        if not info:
            continue
        sha1 = hashlib.sha1(blob).hexdigest()
        if sha1 in hashes:
            dupes = True
            continue
        ext = {"jpeg": "jpg", "mpo": "jpg"}.get(info[0], info[0])
        name = f"{d['id']}_{i}.{ext}"
        (out / "images" / name).write_bytes(blob)
        hashes.add(sha1)
        images.append({"file": name, "url": url, "sha1": sha1, "size": list(info[1])})
    (out / "raw" / f"{d['id']}.json").write_text(json.dumps(data))
    rec = {
        "id": d["id"],
        "title": d.get("title", ""),
        "selftext": (d.get("selftext") or "")[:2000],
        "flair": d.get("link_flair_text"),
        "permalink": d["permalink"],
        "created_utc": d.get("created_utc"),
        "score": d.get("score"),
        "num_comments": d.get("num_comments"),
        "n_images": len(image_urls(d)),
        "images": images,
        **({"failed": True} if failed and not images else {}),
        **({"dupes": True} if dupes and not images and not failed else {}),
    }
    with index.open("a") as f:
        f.write(json.dumps(rec) + "\n")
    if images or rec.get("dupes"):
        seen.add(d["id"])
    status = " (failed)" if rec.get("failed") else ""
    log(f"  {d['id']} {len(images)} img{status}  {d.get('title', '')[:60]!r}")
    return 1 if images else 0


# --- label ------------------------------------------------------------------------------------


def build_gazetteer(catalog_path: Path) -> tuple[Gazetteer, dict]:
    """Image-catalog base families + proprietary names/aliases from helpers.py.
    Returns the gazetteer and {display name: catalog base family or None}."""
    sys.path.insert(0, str(ROOT))
    from fontmatch.image.catalog import load_catalog_json
    from fontmatch.web import helpers as H

    names: dict[str, str] = {}
    catalog = {}
    for e in load_catalog_json(catalog_path):
        names[e.base_family] = e.base_family
        catalog[e.base_family] = e
    for prop in H.PROPRIETARY_TO_OPEN_SOURCE:
        names.setdefault(normalize(prop), H.PROPRIETARY_CANONICAL.get(prop, prop))
    for alias, target in H.CORPUS_ALIASES.items():
        names.setdefault(normalize(alias), target)
    return Gazetteer(names, common_words=_dictionary()), catalog


def _dictionary() -> frozenset:
    """Lower-case English words (system word list), for one-word name guards."""
    for path in ("/usr/share/dict/words", "/usr/share/dict/american-english"):
        if Path(path).exists():
            words = Path(path).read_text(errors="ignore").split()
            return frozenset(w.lower() for w in words if w.isalpha())
    print("warning: no /usr/share/dict word list; one-word names are less guarded")
    return frozenset()


def label(args):
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "scripts"))
    from fontmatch.image.catalog import base_family
    from fontmatch.web import helpers as H

    out = Path(args.out)
    gaz, catalog = build_gazetteer(Path(args.catalog))
    labels = {}
    stats = {}
    for rec in load_index(out / "posts.jsonl"):
        if not rec["images"]:
            continue
        raw = json.loads((out / "raw" / f"{rec['id']}.json").read_text())
        post = raw[0]["data"]["children"][0]["data"]
        ans = find_answer(post, raw[1]["data"]["children"], gaz)
        if ans is None:
            stats["unlabelled"] = stats.get("unlabelled", 0) + 1
            continue
        fam = base_family(ans.name) if ans.known else None
        in_cat = fam in catalog
        alt = H.PROPRIETARY_TO_OPEN_SOURCE.get(ans.name) or H.CORPUS_ALIASES.get(ans.name)
        acceptable = [base_family(alt)] if not in_cat and ans.known and alt else []
        kind = "catalog" if in_cat else ("proprietary" if acceptable else "unknown")
        key = f"{ans.confidence}/{kind}"
        stats[key] = stats.get(key, 0) + 1
        labels[rec["id"]] = {
            **asdict(ans),
            "base_family": fam if in_cat else None,
            "kind": kind,
            "acceptable": acceptable,
            "images": [i["file"] for i in rec["images"]],
            "title": rec["title"],
        }
    (out / "labels.json").write_text(json.dumps(labels, indent=1))
    print(json.dumps(dict(sorted(stats.items())), indent=1))


# --- build ------------------------------------------------------------------------------------


def build(args):
    """labels.json + annotations.json -> reddit_dev/ and reddit_test/ manifests.

    annotations.json: {post_id: {"image": "<id>_0.jpg", "box": [x0, y0, x1, y1]
    (fractions) or null, "text": transcript of the boxed text, "category":
    sans|serif|mono|display|handwriting, optional "truth" (corrected name),
    "acceptable": [catalog base families], "skip": reason}}."""
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "scripts"))
    import shutil

    from eval_image_identify import split_of
    from PIL import Image, ImageOps

    from fontmatch.image.catalog import family_group, load_catalog_json

    out = Path(args.out)
    catalog = {e.base_family: e for e in load_catalog_json(Path(args.catalog))}
    labels = json.loads((out / "labels.json").read_text())
    notes = json.loads((out / "annotations.json").read_text())
    for split in ("dev", "test"):  # rebuilt from scratch every time
        shutil.rmtree(out.parent / f"reddit_{split}", ignore_errors=True)
    sets: dict[str, dict[str, list]] = {}
    for pid, note in sorted(notes.items()):
        lab = labels.get(pid)
        if not lab or note.get("skip") or lab["confidence"] == "similar":
            continue
        # Hand annotations may name substitutes for a truth outside the catalog
        # (validated against the catalog). A row with neither substitutes nor a
        # category can't be scored.
        if "acceptable" in note:
            acceptable = note["acceptable"]
        else:  # a corrected truth makes the label's substitutes meaningless
            acceptable = [] if note.get("truth") else lab["acceptable"]
        bad = [f for f in acceptable if f not in catalog]
        if bad:
            sys.exit(f"{pid}: acceptable families not in the catalog: {bad}")
        # A corrected truth ("Tangerine" the retro serif, not the catalog script)
        # replaces the label's family and kind.
        if note.get("truth"):
            fam = normalize(note["truth"])
            in_catalog = fam in catalog
        else:
            fam = lab["base_family"] or normalize(lab["name"])
            in_catalog = lab["kind"] == "catalog"
        if not in_catalog and not (acceptable or note.get("category")):
            continue
        group = family_group(fam)
        split = split_for(fam, group, acceptable, split_of)
        img = ImageOps.exif_transpose(Image.open(out / "images" / note["image"])).convert("RGB")
        if note.get("box"):
            img = img.crop(box_pixels(note["box"], img.size))
        d = out.parent / f"reddit_{split}"
        d.mkdir(parents=True, exist_ok=True)
        fname = f"{pid}.png"
        img.save(d / fname)
        entry = {
            "file": fname,
            "base_family": fam,
            "group": group,
            "category": catalog[fam].category if fam in catalog else note.get("category", ""),
            "style": "reddit",
            "text": note["text"],
            "confidence": lab["confidence"],
            "post": pid,
        }
        bucket = "manifest.json" if in_catalog else "manifest_acceptable.json"
        if bucket == "manifest_acceptable.json":
            entry["acceptable"] = acceptable
            entry["truth"] = note.get("truth") or lab["name"]
        sets.setdefault(split, {}).setdefault(bucket, []).append(entry)
    for split, buckets in sets.items():
        for bucket, rows in buckets.items():
            (out.parent / f"reddit_{split}" / bucket).write_text(json.dumps(rows, indent=1))
            print(split, bucket, len(rows))


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scrape")
    s.add_argument("--out", default=str(DEFAULT_OUT))
    s.add_argument("--max-posts", type=int, default=200)
    s.add_argument("--max-requests", type=int, default=700)
    s.add_argument("--listings", default=",".join(LISTINGS))
    for name in ("label", "build"):
        p = sub.add_parser(name)
        p.add_argument("--out", default=str(DEFAULT_OUT))
        p.add_argument("--catalog", default=str(ROOT / "glyph_atlas" / "catalog.json"))
    args = ap.parse_args()
    {"scrape": scrape, "label": label, "build": build}[args.cmd](args)


if __name__ == "__main__":
    main()
