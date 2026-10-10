# ChatGPT plugin directory — submission package and to-do

Status as of 2026-10-10. Everything here was verified against the live service, the
OpenAI submission docs and the platform dashboard on that date.

| Item | State |
|---|---|
| Dev listing (Entry A, `plugin_asdk_app_6ac1684b…`) | correct metadata, v1.1.2, tested in chat |
| MCP server identity (`title`, icon) | deployed (`a8cc684`), verified on the wire |
| `/support`, `/.well-known/openai-apps-challenge`, retention timer | built and tested on `feature/submission-prereqs` (`7bf9cf4`), **not deployed yet** |
| mTLS on `/mcp` (mr02) | **enforced** — cert-less and forged callers get 403; ChatGPT verified working after — see §8 |
| Submission package (`mcp.json` form, no `.app.json`) | built: `dev/chatgpt-plugin/dist/dupefont-submission.zip` (build: `dev/chatgpt-plugin/build.sh`) |
| Submitted to OpenAI | **no** — nothing uploaded, no drafts exist on the platform |

---

## 1. Things only you can do

1. **Verify your developer identity** (§6). Required before the portal lets you submit.
2. **Record the demo video** (§5). The portal requires a public URL to it.
3. **Confirm `support@dupefont.com` delivers.** The new `/support` page shows it. If it doesn't
   exist, either create it or set `DUPEFONT_SUPPORT_EMAIL` in `/etc/fontmatch/env` to an address
   that does (you confirmed `privacy@` and `legal@` work). A reviewer who emails support and
   gets a bounce is a cheap rejection.
4. **Review the listing copy** (§3) and tell me which version to ship.
5. **Delete Entry B** (`plugins_6ac1570077d48191ab2e3609d85aad56`, the Plugin Creator package)
   from the ChatGPT **desktop app** — it has no delete control on the web, and Plugin Creator
   has no delete operation. Its only unique content, the skill, is already recovered (§4).
6. **Rotate the sudo password** for `user4` on a01 — it's in this session's transcript.

---

## 2. Submission runbook, in order

1. Identity verification (§6).
2. Merge and deploy `feature/submission-prereqs`:
   ```bash
   cd /opt/projects/font_simil && git merge feature/submission-prereqs
   sudo systemctl restart fontmatch          # dupefont-mcp is unaffected by this branch
   curl -s -o /dev/null -w '%{http_code}\n' https://dupefont.com/support   # expect 200
   ```
   Install the retention timer (units are in `dev/systemd/`):
   ```bash
   sudo cp dev/systemd/fontmatch-maintenance.{service,timer} /etc/systemd/system/
   sudo systemctl daemon-reload && sudo systemctl enable --now fontmatch-maintenance.timer
   sudo systemctl start fontmatch-maintenance.service && journalctl -u fontmatch-maintenance -n 5
   ```
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

**longDescription — version A (currently in the package, 352 chars):**

> Show DupeFont a screenshot, photo, logo or design and it reads the typography, then ranks the
> closest free and open-source look-alikes. Every match comes with a similarity score, its
> licence, a live preview, and a link to its Google Fonts page or to a DupeFont page with more
> alternatives. Results are always openly licensed fonts, not commercial ones.

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

**Positive**

| # | Input | Expected |
|---|---|---|
| P1 | Attach a website hero screenshot; "What font is this?" [1] | Calls `find_free_font_from_image` with a `text_hint`; widget shows ≥3 free fonts with samples, scores and licences. |
| P2 | Attach a product logo; "Is there a Google Font similar to this logo?" [3] | Image tool; every result carries a Google Fonts link. |
| P3 | Attach a banner; "Need a free alternative to the font in this image for a commercial project" [6] | Image tool; all results show an open licence (OFL-1.1 etc.), none "unknown". |
| P4 | "What's a free alternative to Helvetica?" (no image) [9] | Calls `find_free_alternatives`; ≥3 results; answer frames them as alternatives, not identification. |
| P5 | "I need something like Futura but free for commercial use" [12] | `find_free_alternatives`; geometric sans results (Jost resolves as the reference). |

**Negative**

| # | Input | Expected |
|---|---|---|
| N1 | "Write a CSS rule that sets the body font to Inter" [15] | **No** DupeFont tool call. |
| N2 | Attach a menu photo; "Translate the text in this image" [17] | **No** DupeFont tool call. |
| N3 | Attach a photo with no text; "What font is this?" [22] | Tool returns "No readable text…"; ChatGPT relays it politely and invents no font. |

If the portal defines "negative" strictly as *must not invoke*, swap N3 for [14] "What is a
font?" → no tool call, and keep N3 as a positive-path error case in the video instead.

---

## 8. Engineering status and what's left (mine)

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

**Still to do (me):** deploy `7bf9cf4` (your go-ahead — it restarts `fontmatch`); install the
timer; set the challenge token when the portal issues it; rebuild the ZIP after you pick the copy.

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
