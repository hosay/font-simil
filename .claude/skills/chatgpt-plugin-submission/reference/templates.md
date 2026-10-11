# Templates and snippets

## Domain-verification route (Flask; adapt to your framework)

```python
import os
from flask import Blueprint, Response, abort

bp = Blueprint("openai_challenge", __name__)

@bp.get("/.well-known/openai-apps-challenge")
def openai_apps_challenge():
    token = (os.environ.get("OPENAI_APPS_CHALLENGE") or "").strip()
    if not token:
        abort(404)
    return Response(token, mimetype="text/plain")
```

Serve it on the origin root of the MCP host (paths in the portal's "Challenge Base URL" are
ignored). Set the env var in the service's environment file, restart, then:

```sh
curl -si https://HOST/.well-known/openai-apps-challenge | sed -n '1p;/^content-type/Ip;$p'
#   HTTP/2 200 … content-type: text/plain … <token>

# MCP reachability (expect a JSON-RPC result listing your tools; 403 means mTLS/WAF in the way)
curl -s https://HOST/mcp -H 'content-type: application/json' -H 'accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}' | head -c 400
```

## Plugin skill (`skills/<name>/SKILL.md` inside the package)

```markdown
---
name: <plugin-name>
description: <one or two sentences: what it does and when ChatGPT should use it; this is scanned and shown>
---

# <Display name>

Use <Display name> when the user <concrete triggers>. Do not use it for <concrete non-triggers>.

## Tools
- `tool_one` — when to call it, what it returns, what to tell the user.
- `tool_two` — …

## Behaviour
- Prefer the widget/card for results; do not repeat the full list in prose.
- Link to <your domain> URLs returned by the tool rather than inventing links.
- On an error result, relay the message plainly and do not guess.
```

## Test-run log (keep in `docs/<plugin>-golden-prompts.md`)

| Date | Case | Run | Tool called (log-verified) | Key result | Pass |
|---|---|---|---|---|---|
| 2026-01-01 | P1 | 1/3 | tool_one | top result X, N results | yes |

Rules: three fresh chats per case; "Tool called" only after the server log confirms it; a
single failing run means reword or fix, not ship.

## Submission runbook (`docs/<plugin>-submission.md`)

```markdown
# <Plugin> — ChatGPT directory submission

## 1. Owner items
- [ ] identity verified (org/individual) — date
- [ ] demo video URL —
- [ ] support address live —
- [ ] attestations + Submit — date

## 2. Runbook (in order, with dates)
1. Package built: <path>, version, layout notes
2. Uploaded → plugin id `plugin_asdk_app_…`
3. Domain verification: token location (env var, file), verified date
4. MCP connected, tools discovered: <list>
5. Findings and how each was cleared
6. Submitted / review feedback / published

## 3. Listing copy (as shipped)
## 4. Test cases (5 + 3) with inputs, expected results, and the run tallies
## 5. Risk summary handed to the owner (date)
## 6. Engineering notes (widget URI history, legacy aliases to drop, dev-mode refresh steps)
```
