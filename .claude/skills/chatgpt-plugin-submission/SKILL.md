---
name: chatgpt-plugin-submission
description: Use when creating, packaging, testing, submitting or publishing a ChatGPT app, ChatGPT plugin, connector or Apps SDK widget backed by an MCP server, including the OpenAI plugin directory at platform.openai.com/plugins and ChatGPT developer-mode apps. Not for Claude Code or Codex CLI plugins.
version: 1.1.0
user-invocable: true
---

# ChatGPT plugin submission

Procedure for shipping a ChatGPT plugin: an MCP server (optionally with a widget), a listing
package, review test cases, and the submission on `platform.openai.com/plugins`. Provenance:
distilled from one full submission on 2026-10-10 and checked against OpenAI's docs that day.
Where the portal behaved differently from the docs, the portal is what is written here.
Facts that are likely to drift are collected under "Volatile facts"; re-check the linked docs
when something disagrees.

Two different objects exist, each with its own `plugin_asdk_app_…` id:

| | Dev-mode app (testing) | Directory plugin (public) |
|---|---|---|
| Where | ChatGPT → Settings → Apps → developer mode; app page at `chatgpt.com/plugins/plugin_asdk_app_<dev-id>` | Platform dashboard `platform.openai.com/plugins/manage/plugin_asdk_app_<public-id>` |
| Package binding | `.app.json` + `"apps"` in the manifest (see `reference/app-json-template.json`) | **must not** contain `.app.json`, `apps` or lifecycle hooks |
| Who reviews | nobody; your account only | OpenAI, after Submit for review |

Scope: servers without user authentication. If your server needs OAuth, the Connect drawer
has an Authentication selector and reviewers will need a fully featured test account; that
path is not covered here.

## Workflow

1. **Prerequisites** (owner items, ask early): verified individual/organization on the
   Platform dashboard (`platform.openai.com/settings/organization/general`); an HTTPS MCP
   server (streamable HTTP) on a domain you control; public HTTPS privacy, terms and support
   pages; a square logo ≥ 48 px (PNG/JPEG/WebP/SVG, ≤ 5 MiB); a public demo video URL
   (unlisted YouTube is fine); a way to set an env var and restart the web server.
2. **Server readiness** → "MCP server requirements" and the snippets in
   `reference/templates.md`.
3. **Package** → `reference/manifest-template.json`, "Package layout". Build with
   `reference/build.sh` (`PLUGIN_NAME`, `SRC`, `DEVMODE_SRC` env vars); it refuses a public
   package that contains `.app.json`/`apps` and checks the manifest limits.
4. **Test cases** → "Review test cases". Five positive, three negative, each run ≥ 3 times in
   fresh ChatGPT chats before they go in the manifest. Host attachments on your own domain.
   Log runs with the table in `reference/templates.md`.
5. **Portal** → `reference/portal-walkthrough.md` (browser mechanics in
   `reference/browser-automation.md`). Upload, verify domain, connect MCP, clear findings,
   re-upload with a bumped version until "No findings".
6. **Policy pass** → `reference/policy-checklist.md`. Produce the one-screen risk summary.
7. **Submit** — the owner ticks the six attestations and presses Submit. Do not do this for
   them unless they explicitly say so: it binds them to the App Developer Terms (indemnity,
   arbitration). Only one review can be active per plugin.
8. **Record** everything in a `docs/<plugin>-submission.md` runbook (template in
   `reference/templates.md`) and commit.

## Package layout

Use the Codex layout; the public portal and the dev-mode uploader both accept it:

```
.codex-plugin/plugin.json   manifest: root "interface" for the listing, review data under extensions
.mcp.json                   {"mcpServers": {"<key>": {"url": "https://host/mcp"}}}   (no "type")
skills/<name>/SKILL.md      YAML header needs name + description (template in reference/templates.md)
assets/logo.png             every file the manifest references must be in the ZIP
```

The manifest declares `"skills": "./skills/"` so the portal knows where to look; the
portal then scans each skill automatically ("Checking" → "Checks passed").

Rules that bite:

- `displayName` ≤ 30 chars, `shortDescription` ≤ 30, `longDescription` ≤ 4000, up to three
  `defaultPrompt` entries ≤ 128 chars each. Icon paths start with `./`.
- `category` must be on the portal's **current** list (see "Volatile facts"; extraction recipe
  in `reference/browser-automation.md`). Old directory names such as "Design" are rejected
  with "Select a valid category".
- Required for MCP review: `websiteURL`, `supportURL`, `privacyPolicyURL`, `termsOfServiceURL`,
  all HTTPS. Required for the Codex layout: `capabilities` (`["Interactive"]` for an app with a
  widget), `composerIcon`, `logo`.
- Test cases, demo video URL and the commerce attestation live in
  `extensions.com.openai.review`; `publication.release_notes` beside it. The portal imports
  them **read-only**; to change a test case, edit the manifest and upload a new version.
  `tools_triggered` is a string (comma-separate several tools).
- Bump `version` on every re-upload. Metadata and skills are versioned per upload; the MCP
  connection is per plugin and survives re-uploads.
- Keep a second directory for the dev-mode package: same files plus `.app.json` and
  `"apps": "./.app.json"` in the manifest, built by the same script with `devmode`.

## MCP server requirements

- Streamable HTTP at a stable URL. `GET /.well-known/openai-apps-challenge` at the **origin
  root** returns the verification token as `text/plain` (read from an env var; 404 when
  unset). Snippet and `curl` checks in `reference/templates.md`.
- Every tool: explicit `readOnlyHint`, `destructiveHint`, `openWorldHint`; a unique, verb-based,
  human-readable name; a description that says when to use it and does not over-trigger;
  inputs only for the task (no transcripts, no raw location). Responses carry only what the
  request needs: no timestamps, request ids or logging metadata.
- Widgets: `_meta["openai/outputTemplate"]` = a `ui://` resource URI; `openai/widgetCSP` with
  `resource_domains` for images; `openai/widgetDomain`. ChatGPT **caches widget templates by
  URI** and caches each tool's outputTemplate URI: a changed widget needs a new URI, and the
  old URI must stay served for a release (keep a list of legacy URIs registered as aliases of
  the current resource). Model-visible content is `structuredContent`; anything the model
  should not quote goes in result `_meta`, which reaches the widget as
  `window.openai.toolResponseMetadata` / `params._meta`. Example: per-result image URLs in
  `structuredContent` were pasted into prose as broken markdown images; in `_meta` they were not.
- Rate-limit per caller (`openai/subject` is a stable pseudonymous id), keep a request log you
  can grep to prove a tool was called, and consider mTLS on `/mcp` with OpenAI's published
  client CA (`https://developers.openai.com/plugins/mtls/`).
- Unit tests for: tool list and annotations, output schema, widget resource + CSP, error
  results, `_meta` contents, legacy URI still served.

## Review test cases

Positive fields: `description`, `prompt`, `tools_triggered`, `expected_behavior`, optional
`file_attachment_urls` (public HTTPS). Negative: `description`, `prompt` only, read as "must
not invoke".

- **Run them yourself in ChatGPT, ≥ 3 fresh chats each**, before they go in the manifest.
  ChatGPT is non-deterministic; one pass means nothing. Verify every "tool called" claim in the
  server's request log, not from the chat UI.
- **Name the app in positive prompts** ("Use <app> to …"). Bare prompts are commonly answered
  from ChatGPT's own knowledge with no tool call. Matching is case-insensitive;
  `@<first letters>` + Enter in the composer also selects the app. Keep negative prompts bare:
  naming the app there invites a call.
- Choose inputs your engine handles well. Rewording a case so it passes reliably is fine;
  hiding bad behaviour is not.
- **Own every test asset.** Attachments are hosted on your domain and warranted as yours in
  the attestations; make them yourself rather than lifting third-party images.
- Expected results describe shape ("≥ 3 results, each with field X") plus the typical top
  result, not an exact list, so later model changes do not falsify them.
- Log every run (template in `reference/templates.md`).

## Dev-mode app (testing in ChatGPT)

- First time: in ChatGPT, Settings → Apps → enable developer mode → create the app with the
  MCP URL. The app page URL carries the dev id (`plugin_asdk_app_<hex>`); the app id for
  `.app.json` is `asdk_app_<hex>` (same hex). Then upload the dev-mode ZIP from the app page's
  "…" menu → **Upload new version**. Format: `reference/app-json-template.json`.
- After any widget or tool-schema change: bump the widget URI, restart the server, click
  **Refresh tools** on the app's settings page, then **re-upload the dev-mode package with a
  bumped version** (see "Volatile facts": Refresh tools wiped the package metadata). Package
  re-upload alone does not refresh the widget.

## Troubleshooting (symptom → cause → fix)

| Symptom | Cause | Fix |
|---|---|---|
| Upload: "Invalid plugin package … must contain `.codex-plugin/plugin.json` …" | root `plugin.json` + `mcp.json` layout | Codex layout above |
| Finding: "Select a valid category." | category not on the current list | pick from "Volatile facts", bump version, re-upload |
| MCP configuration: "Configuration incomplete" | domain not verified / not connected | MCPs tab → Connect → token → Verify Domain → Connect → Continue |
| Widget shows old HTML after a deploy | template cached by URI | new widget URI + legacy alias, Refresh tools, re-upload dev package |
| Widget "Couldn't open" after a URI bump | tool's cached outputTemplate URI gone | keep serving the previous URI |
| Dev app lost developer/category/website after Refresh tools | Refresh tools wipes package metadata | re-upload the dev-mode package with a bumped version |
| Model pastes broken images / raw URLs under the widget | URLs in `structuredContent` | move them to result `_meta` |
| Bare test prompt answered without a tool call | ChatGPT routing | name the app in the prompt; keep as-is only for negatives |

## Owner-only actions

Identity verification, recording the video, the six attestations and Submit, accepting any
terms, deleting apps, anything that spends money. Prepare everything else and hand over a
one-screen summary: portal state, what the attestations mean for them, open risks.

## Volatile facts (observed 2026-10-10; re-verify when they matter)

- Categories accepted by the portal: Business & Operations, Communication, Creativity,
  Data & Analytics, Developer Tools, Education & Research, Entertainment, Finance, Healthcare,
  Other, Productivity, Scientific Research, Security, Travel.
- Initial MCP review requires exactly five positive and three negative test cases.
- The Submit dialog contains six attestations and no form fields; test cases and the video
  URL come only from the manifest.
- Re-uploading the same version number was not refused; bump anyway.
- The dev-mode app could not be deleted from the web UI (rename it; delete from the desktop
  app). "Refresh tools" on the dev app wiped the uploaded package metadata.
- Dev-mode uploads accepted old category names ("Design"); the public portal did not.
- Browser extension batches longer than ~3 minutes timed out; ~90 s per batch was safe.
- Developer Terms specifics quoted in the policy checklist ($100 liability cap, 15-day change
  notice, 30-day arbitration opt-out) are as of the 2026-09-28 revision of the terms.

## References

- Submission guide (package, manifest, review fields): https://developers.openai.com/plugins/deploy/submission
- Plugin guidelines (what reviewers enforce): https://developers.openai.com/plugins/plugin-guidelines
- App Developer Terms (what the attestation binds the owner to): https://openai.com/policies/developer-apps-terms/
- Usage policies: https://openai.com/policies/usage-policies/
- Security/privacy guide: https://developers.openai.com/plugins/guides/security-privacy
- mTLS CA for `/mcp`: https://developers.openai.com/plugins/mtls/
- In this skill: `reference/manifest-template.json`, `reference/app-json-template.json`,
  `reference/build.sh`, `reference/portal-walkthrough.md`, `reference/browser-automation.md`,
  `reference/policy-checklist.md`, `reference/templates.md`
