# Portal walkthrough — platform.openai.com/plugins

What the dashboard does, step by step, as observed 2026-10-10. Browser mechanics (file
inputs, reading drawers) are in `browser-automation.md`.

## 1. Upload

- `platform.openai.com/plugins` → dropzone "Upload new or existing plugin" → modal **Upload
  Plugin** with a *Developer identity* selector (Business — <org> / individual) and an
  "Upload plugin" button that creates the file input on click.
- A rejected ZIP shows the reason inline ("Invalid plugin package …" plus the accepted
  layouts); fix and use **Reupload ZIP** in the same modal.
- An accepted ZIP creates the plugin and opens
  `platform.openai.com/plugins/manage/plugin_asdk_app_<id>?tab=details`. Record the id in the
  runbook.

## 2. Metadata & Skills tab

- Header badges: **Publication** (Not published), **MCP configuration** (Configuration
  incomplete → Configured), **Review status** (Needs attention → Not submitted → In review).
- Version selector "1.0.x · Draft". The metadata table mirrors `interface`; the Skills table
  shows each skill with status Checking → Checks passed.
- **Findings** panel (All / Blocking) with "Copy findings". Each finding names the rule and
  links Docs; fix in the package, bump the version, re-upload. The troubleshooting table in
  SKILL.md lists the ones seen so far; add new ones there.
- Re-upload: header **Upload new version** (or the "Upload plugin to …" button inside the
  findings panel) opens a **Reupload draft** dialog with a file input.

## 3. MCPs tab

- One card per `mcpServers` key: MCP URL, Authentication, MCP key, Domain verification, and
  **Connect**. A dot on the tab means attention needed.
- **Connect** opens the drawer *Connect MCP server*: read-only URL, Authentication selector
  (No Auth / OAuth…), advanced JSON, a **Connect** button, and **Domain verification**:
  *Challenge Base URL* (leave blank for the MCP origin), the exact URL it will fetch
  (`https://<host>/.well-known/openai-apps-challenge`), the **Token**, and **Verify Domain**.
- Put the token on the server (env var → restart → `curl` returns it as `text/plain`, 200),
  click **Verify Domain** → "Domain verified" within seconds.
- Then **Connect** (turns into **Reconnect**) → **Continue** at the bottom of the drawer. The
  card becomes **Configured**; the Tools table lists every discovered tool with availability
  "Not live" (normal before publication) and "Last checked". **Rescan** re-runs discovery after
  server changes. Package uploads do not touch the MCP connection.
- The token is per plugin; keep it in the server's environment permanently.

## 4. Submit for review

- Header **Submit for review** opens a dialog with six attestations and Submit:
  1. Reviewed and agree to OpenAI's Terms (App Developer Terms) and Plugin Guidelines; the
     plugin complies.
  2. Complies with all laws applicable to its industry.
  3. Does not initiate or execute money/crypto transfers or investment trades.
  4. Has all necessary rights to third-party content or API endpoints used.
  5. Suitable for under-18s, no mature content.
  6. Does not target under-13s or share their personal information with OpenAI.
- There is no form for test cases or the video: they come from the manifest's
  `extensions.com.openai.review`, imported read-only. Missing or short lists are flagged
  before submission; fix in the manifest and re-upload.
- The owner ticks and submits. Review status becomes "In review"; feedback arrives by email;
  after approval open the version and **Publish plugin**. One active review per plugin.

## 5. After publication

- Rename or remove the dev-mode app so it is not confused with the public listing.
- Drop legacy widget URIs one release later.
- Keep the runbook current: every re-upload, finding, and review round, with dates.
