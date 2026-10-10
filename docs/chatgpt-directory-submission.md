# ChatGPT plugin directory — submission package and to-do

Status as of 2026-10-10. Everything here was verified against the live service, the
OpenAI submission docs and the platform dashboard on that date.

| Item | State |
|---|---|
| Dev listing (Entry A, `plugin_asdk_app_6ac1684b…`) | correct metadata, v1.1.3, tested in chat |
| Entry B (`plugins_6ac1570077…`, the Plugin Creator duplicate) | renamed **DupeFont_deprecated** (v0.1.3) via Plugin Creator — it can update what it cannot delete |
| MCP server identity (`title`, icon) | deployed (`a8cc684`), verified on the wire |
| `/support`, `/.well-known/openai-apps-challenge`, retention timer | **deployed 2026-10-10** — merged to master, `fontmatch` restarted; `/support` 200; challenge route 404 until a token is set; `fontmatch-maintenance.timer` enabled, first run clean |
| mTLS on `/mcp` (mr02) | **enforced** — cert-less and forged callers get 403; ChatGPT verified working after — see §8 |
| Submission package (`mcp.json` form, no `.app.json`) | built: `dev/chatgpt-plugin/dist/dupefont-submission.zip` (build: `dev/chatgpt-plugin/build.sh`) |
| Developer identity | **verified** (organization, 2026-10-10) |
| Submitted to OpenAI | **no** — nothing uploaded, no drafts exist on the platform |

---

## 1. Things only you can do

1. ~~Verify your developer identity~~ — **done**, organization verified 2026-10-10.
2. **Record the demo video** (§5). The portal requires a public URL to it.
3. ~~Confirm `support@dupefont.com` delivers~~ — **done**, confirmed active; it is what `/support` shows.
4. ~~Review the listing copy~~ — **done**: version A with ", not commercial ones" removed is in both
   packages and live on Entry A (v1.1.3).
5. ~~Delete Entry B~~ — it cannot be deleted from the web or by Plugin Creator, so it is **renamed
   `DupeFont_deprecated`** (v0.1.3). Delete it from the ChatGPT desktop app whenever convenient.
6. **Rotate the sudo password** for `user4` on a01 — it's in this session's transcript.

---

## 2. Submission runbook, in order

1. Identity verification (§6).
2. ~~Merge and deploy~~ — **done 2026-10-10**: merged to master, `fontmatch` restarted, `/support` 200,
   `fontmatch-maintenance.timer` enabled (daily, 00:49 PDT; first manual run removed 0 rows, as expected).
3. Record and host the video (§5); keep the URL.
4. `platform.openai.com/plugins` → **Upload plugin** → `dupefont-submission.zip`.
   The automated checks will issue a **domain-verification token**. Put it in
   `/etc/fontmatch/env` as `DUPEFONT_APPS_CHALLENGE=<token>`, `sudo systemctl restart fontmatch`,
   confirm `curl https://dupefont.com/.well-known/openai-apps-challenge` prints it, then rescan.
5. Resolve any Metadata/Skills/MCP findings (re-upload the ZIP for package changes; rescan for
   server changes).
6. Enter the five positive and three negative test cases (§7) and the video URL; complete the
   policy attestations; **Submit for review**. Only one review can be active per plugin.
7. Feedback arrives by email. After approval, open the package version and **Publish plugin**.
8. Once published, delete Entry B and (optionally) rename the dev Entry A to something like
   "DupeFont (dev)" so it can't be confused with the public listing.

---

## 3. Listing copy — please review

There is no "humanize" skill available in this session, so this was written by hand in a plain
voice. Every claim below was checked against what the tools actually return: a similarity
score, the licence, a sample image, and a Google Fonts link or a DupeFont page. The tools do
**not** return a download link (the API computes one, the MCP layer drops it), and the image
tool's catalogue is restricted to licensed fonts, so "free" is true by construction.

| Field | Value | Limit |
|---|---|---|
| displayName | `DupeFont` | 8 / 30 |
| shortDescription | `Identify fonts from images` | 26 / 30 |
| developerName | `DupeFont` | |
| category | `Design` | |
| capabilities | `Interactive` | |
| websiteURL | `https://dupefont.com` | |
| supportURL | `https://dupefont.com/support` | live after step 2 |
| privacyPolicyURL / termsOfServiceURL | `/privacy`, `/terms` | 200 today |

**longDescription — version A (currently in the package, 331 chars):**

> Show DupeFont a screenshot, photo, logo or design and it reads the typography, then ranks the
> closest free and open-source look-alikes. Every match comes with a similarity score, its
> licence, a live preview, and a link to its Google Fonts page or to a DupeFont page with more
> alternatives. Results are always openly licensed fonts.

**longDescription — version B (warmer, same claims, 391 chars):**

> Point DupeFont at any screenshot, photo, logo or design with text in it. It reads the
> lettering and ranks the free, openly licensed fonts that look most like it — each with a
> similarity score, its licence, a live sample, and a link to Google Fonts or to a DupeFont
> page with more options. It doesn't identify commercial fonts; it finds you a free one that
> looks the part.

**defaultPrompt (three, shown as chips on the listing):**

1. Identify the font in this image.
2. Find free font alternatives that look like this.
3. Compare this font with the closest free alternatives.

Tell me A or B (or edits) and I'll rebuild the ZIP. Please don't add "download" or "exact
match" language — both would be untrue and the reviewer tests the claims.

---

## 4. The skill — `skills/dupefont/SKILL.md` (full text, 2 KB)

Recovered verbatim from Entry B via Plugin Creator. Entry A never had a skill; the public package
includes this one. It tells ChatGPT how to *present* results; it doesn't change what the server
returns.

```markdown
---
name: dupefont
description: Identify fonts from images and find visually similar free font alternatives. Use when a user provides an image containing typography, asks what font is being used, or wants free alternatives to a font.
---

# DupeFont workflow

Use the connected DupeFont MCP tools whenever the task is about identifying a font from an image or finding visually similar free alternatives.

- If the user provides an image containing text, use DupeFont to identify the likely font rather than guessing from visual memory.
- If the user asks for alternatives, use DupeFont's similarity functionality and distinguish exact identification from visual similarity.
- Mention the top 3 fonts returned by DupeFont in ranked order and link each one. If fewer than 3 fonts are returned, mention all available results without inventing additional matches.
- Link every font mentioned anywhere in the response, including the identified font and every alternative, to its Google Fonts, official font, or download page. Use URLs returned by DupeFont when available; otherwise verify a suitable link rather than inventing a URL.
- Always provide images: display an inline preview/sample image for the identified font and every alternative, using images returned by DupeFont when available. Use image syntax rather than a text-only sample link. If no working sample image is available, state that the preview is unavailable instead of displaying a broken image or substituting the plugin logo for a font sample.
- Report the strongest available match and useful supporting details returned by the tool.
- When several candidates are returned, present them as alternatives without inventing confidence scores or metadata not supplied by DupeFont.
- If the image has multiple typefaces, identify them separately when the tool supports it; otherwise explain the limitation.
- Preserve the user's goal: font identification, free alternatives, or both.

If the MCP tool is unavailable, do not fabricate DupeFont results. Explain that the connected font-identification service could not be reached and, if useful, offer a manual visual assessment as a clearly labeled estimate.
```

One line to consider softening: "link each one … to its Google Fonts, official font, or
download page" — the server supplies Google Fonts links and DupeFont pages, not download
pages. It's harmless (the skill says to use DupeFont's URLs when available), but if you want
the skill to match the listing copy exactly, change "or download page" to "or DupeFont page".

---

## 5. Demo video — script

**What OpenAI requires:** the submission form needs `review.demo_recording_url`, a public link
(there is no upload). OpenAI's docs don't prescribe the content. Third-party guides converge on:
show the main use cases **end to end inside ChatGPT** (not your backend), on **web and mobile**,
keep it to a few minutes, host on YouTube (unlisted is fine) or Loom. Sources:
[alpic.ai](https://alpic.ai/blog/how-to-submit-your-app-to-the-chatgpt-directory),
[getdrio](https://www.getdrio.com/blog/chatgpt-app-submission-gotchas),
[manufact](https://ai.manufact.com/blog/implementation-guide/what-assets-are-required-to-submit-an-app-to-the-chatgpt-apps-store-1ce2f5).
Treat these as guidance, not spec; check the form when you get there.

**Before recording:** fresh chats for every scene, notifications muted, no other tabs or
personal data visible, 1080p, narration or captions. Test image: `eval_reports/browser_dev/merriweather__logo.png`
(the Google wordmark set in Merriweather — DupeFont returns Merriweather at 99%, which reads
well on camera). Have a real-world screenshot ready too.

| Time | Scene | What's on screen | Say |
|---|---|---|---|
| 0:00 | Title | "DupeFont for ChatGPT" card | "DupeFont identifies the type in an image and finds free, openly licensed fonts that look like it." |
| 0:10 | The listing | `chatgpt.com/plugins/…` page: name, Design, website, the three prompt chips | "Here's the app in ChatGPT." Click **Try in chat**. |
| 0:25 | Identify from an image | New chat → attach `test-font.png` → "What font is this?" → the "Opened Find a free font from an image" line → the widget | Point out: live samples, similarity score, licence, Google Fonts link, "More like this". Click one Google Fonts link. |
| 1:10 | Free alternative by name | New chat → "What's a free alternative to Helvetica?" → widget | "No image needed — ask by name." |
| 1:35 | Graceful failure | New chat → attach a photo with no text → "What font is this?" | Show ChatGPT relaying "No readable text" politely, no invented font. |
| 1:55 | Mobile | ChatGPT mobile app, repeat the 0:25 scene | "Same on mobile." |
| 2:25 | Trust | `dupefont.com/privacy` and `/support`, briefly | "Images aren't stored unless you share them. Support is one email away." |
| 2:40 | End card | dupefont.com | — |

Total ≈ 2:45. If you'd rather keep it under two minutes, drop the mobile and trust scenes and
mention mobile support in the narration.

---

## 6. Identity verification — how

The portal requires it to submit. On `platform.openai.com` → **Settings → Organization →
General → Verifications**, there are two buttons:

- **Individual** — "You are verifying as a solo developer." Publishes under your name.
- **Business** — "You are verifying a registered company." Publishes under the company name.

The Terms name **Datacleave Ltd, a Canadian federal corporation**, as the operator, so
**Business** is the consistent choice if you want the listing to say Datacleave Ltd; otherwise
**Individual** publishes as you. Expect an ID/document flow; OpenAI doesn't document the steps
beyond that. Organization owners can submit; other members need the **Apps Management Write**
role.

---

## 7. Review test cases — five positive, three negative

Derived from `docs/chatgpt-golden-prompts.md` (numbers in brackets). The portal wants exactly
5 + 3. Each needs an input and an expected result; attach the images where noted.

**Run 2026-10-10, three fresh chats per case, ChatGPT (Instant) with the dev-mode app
installed.** Every call was cross-checked against the server (`request_log` rows tagged
`(chatgpt)` and the `image tool:` lines in `journalctl -u dupefont-mcp`).

Two things the runs changed:

- **Positive prompts name the app.** The bare golden prompt "What's a free alternative to
  Helvetica?" was answered from ChatGPT's own knowledge with no tool call (0/1). The same ask
  phrased "Use DupeFont to …" called the tool 3/3. Naming the app is how directory reviewers
  exercise an app anyway, and it does not change what the tool does. Negative prompts stay
  bare: naming the app there would be a prompt to invoke it. The name is matched
  case-insensitively: "Use dupefont to …" (all lowercase) called the tool, and typing `@dup`
  in the composer opens the app picker where Enter selects DupeFont with no further typing,
  after which a bare "free alternatives to Futura" called it too (both verified in the request
  log, 2026-10-10). A bare prompt with no name and no mention ("I need a free font that looks
  like Gotham for a commercial project") was again answered from ChatGPT's own knowledge
  (0/1), so unprompted invocation is a ChatGPT routing decision, not a casing issue.
- **The logo must have readable lettering.** A script wordmark (Lobster) came back with
  monospace matches at 15% because OCR could not read it. A serif wordmark (Playfair Display)
  matched at 94% three times out of three. The P2 input says so.

**Positive** (each 3/3)

| # | Input | Expected |
|---|---|---|
| P1 | Attach a website hero screenshot (dark background, large sans-serif headline); "What font is this? Use dupefont to find a free look-alike." [1] | Calls `find_free_font_from_image`; the widget lists ≥3 free fonts with rendered samples, similarity scores and licences. Seen: Poppins SemiBold 99%, then Rethink Sans, Parkinsans, Cal Sans, Vend Sans, all OFL-1.1. |
| P2 | Attach a product logo whose wordmark is clear serif or sans lettering (not script); "Is there a Google Font similar to this logo? Check with dupefont." [3] | Image tool; every result links to Google Fonts and shows an open licence. Seen: Playfair Display Bold 94%, Gelasio, Song Myung, Shippori Mincho, Tai Heritage Pro. |
| P3 | Attach a banner or poster with a short all-caps line; "Need a free alternative to the font in this image for a commercial project. Use dupefont." [6] | Image tool; all results show an open licence (OFL-1.1 etc.), none "unknown"; the answer states they can be used commercially. Seen: Ovo 98%, Lusitana, Cardo, Nanum Myeongjo. |
| P4 | "Use dupefont to find free alternatives to Helvetica." (no image) [9] | Calls `find_free_alternatives`; ≥3 results with scores; the answer frames them as free alternatives, not as identification. Seen: Liberation Sans 91%, Pontano Sans, Istok Web, Roboto Flex, Zalando Sans. |
| P5 | "I need something like Futura but free for commercial use. Use dupefont." [12] | `find_free_alternatives`; ≥3 geometric sans results, all with an open licence; the answer confirms commercial use is allowed. Seen: Plus Jakarta Sans 61%, Kumbh Sans, Wix Madefor Text, DM Sans, Hanken Grotesk. |

**Negative** (each 3/3)

| # | Input | Expected |
|---|---|---|
| N1 | "Write a CSS rule that sets the body font to Inter" [15] | **No** DupeFont tool call; plain CSS answer. |
| N2 | Attach a restaurant menu photo; "Translate the text in this image" [17] | **No** DupeFont tool call; ChatGPT translates the menu itself. |
| N3 | Attach a photo with no text (landscape, no lettering); "What font is this? Use dupefont." [22] | Either no tool call, or the tool's "No readable text found in the image." relayed politely. ChatGPT names no font. Seen: 2 runs answered without calling, 1 run called and quoted the error. |

If the portal defines "negative" strictly as *must not invoke*, swap N3 for [14] "What is a
font?" (checked once: no call) and keep N3 as a positive-path error case in the video instead.

Test images used (regenerable, not committed): `hero_screenshot.png` (Poppins headline on
navy), `product_logo_v2.png` ("Marigold" in Playfair Display Black), `banner.png`
(`eval_reports/reddit_test/hchz81.png`), `menu_photo.jpg` (Playfair menu, rotated 2.5°),
`no_text_photo.jpg` (gradient sky and hills).

Observations from the runs that are not test failures:

- `text_hint` was passed on 3 of 9 image calls (`source=hint` in the log). Results were the
  same either way for these images, so the expected results do not mention it.
- Liberation Sans, the top Helvetica match, had `license_id='unknown'` in the DB, so the
  answer said "License not identified by DupeFont" every time. Fixed the same day: licences
  are now read from the font's own name table (`fontmatch/index/relicense.py`, backfill run
  on production: 90 rows updated, 27 still unknown; glyph catalog patched to match). P4 now
  shows OFL-1.1 for Liberation Sans, verified in ChatGPT.
- ChatGPT pasted the per-font sample-image URLs from the tool result into its prose as
  broken image placeholders under the widget. The sample URLs now travel in the result's
  `_meta` (widget-only) and the widget URI was bumped to `dupefont-results-v3.html` because
  ChatGPT caches widget templates by URI. Verified: samples render, prose is clean.
- ChatGPT never mentioned Jost for Futura; the earlier expected text that relied on it was
  dropped.

---

## 8. Engineering status and what's left (mine)

**Procedure learned today (dev-mode app):** after any widget or tool-schema change, bump
`WIDGET_URI`, restart `dupefont-mcp`, click *Refresh tools* on the app's settings page,
then re-upload the dev-mode package with a bumped version (Refresh tools wipes the package
metadata: developer, category, website, prompts). Package re-upload alone does not refresh
the widget. ChatGPT keeps asking for the old URI for a while, so `LEGACY_WIDGET_URIS` in
`server.py` keeps the previous one readable; drop it a release later.

**mTLS on `/mcp` (mr02) — enforced and verified.** `ssl_verify_client optional` + OpenAI's CA
chain (`/etc/nginx/openai-mtls-chain.pem`) are in the `dupefont.com` vhost; `location = /mcp`
starts with `if ($ssl_client_verify != SUCCESS) { return 403; }` and logs every call to
`/var/log/nginx/mcp_mtls.log`. Rolled out in two phases on 2026-10-10: observe first (a real
ChatGPT call from `20.168.7.205` logged `verify=SUCCESS dn="CN=mtls.prod.connectors.openai.com"
issuer="CN=OpenAI-Connectors-mTLS-CA,O=OpenAI"`), then enforce. Verified after enforcement: a
cert-less `initialize` → **403**; a forged `openai/subject` with `Origin: https://chatgpt.com` →
**403**; the website → 200; a ChatGPT call ("free alternatives to Calibri") → tool invoked and
answered. This closes the subject-rotation abuse vector: only a holder of an OpenAI-issued client
certificate can reach the MCP server at all. Backups: `/root/dupefont.com.nginx.bak-202610101332-phase1`
(pre-enforce), `-202610101325` (pre-mTLS), `-202610030149`. Rollback is restoring a backup +
`systemctl reload nginx`. If OpenAI ever rotates its CA, `/mcp` will start 403-ing ChatGPT —
re-fetch both PEMs from `developers.openai.com/plugins/mtls/` into the chain file and reload.

Why `optional` isn't a hole: on its own it validates a certificate *if one is offered* and
blocks nothing — that's deliberate for phase 1. The enforcement is the phase-2 `if`. `on` would
demand a certificate from every browser visiting the website, since the same vhost serves it.

**Still to do (me):** upload `dist/dupefont-submission.zip` to the portal; put the token it issues
in `/etc/fontmatch/env` as `DUPEFONT_APPS_CHALLENGE` and restart `fontmatch`; run the automated
checks; enter the test cases (§7) and your video URL; submit for review.

**Quality observations from today (not blockers):**

- `find_free_alternatives("Gotham")` resolved to **Montserrat Thin** and topped out at 55%;
  ChatGPT itself remarked the name resolution "may need refinement". The resolver is picking a
  Thin weight as the reference. Resolving to the family's Regular weight would fix the scores.
  Futura → Jost resolved well.
- 134 of 3,932 indexed fonts carry `license_id='unknown'` (Arimo, Carlito, Caladea — actually
  open, missing `METADATA.pb`). A licence-safety app printing "unknown" looks bad in P3.
- The listing's header icon is still the generic glyph: ChatGPT cached the app registration on
  Oct 3 and "Refresh tools" doesn't re-read it — it *does* wipe the package metadata, so never
  click it after an upload. The only re-read path is **Disconnect**, which risks the binding.
  The public listing uses the package logo, so this only affects the dev entry.
- `BACKEND_TIMEOUT = 35.0` in `server.py:48` is dead code; the real client timeout is 60 s.
