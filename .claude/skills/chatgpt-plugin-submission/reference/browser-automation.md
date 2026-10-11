# Browser automation notes (Claude in Chrome)

Mechanics that made the portal and ChatGPT drivable from the extension. Observed 2026-10-10.

## File uploads

- The portal's upload buttons create their `<input type=file>` on click and open a native
  picker you cannot drive. Install this hook **before** clicking, on every page load (it does not
  survive navigation):

```js
if (!window.__origInputClick) {
  window.__origInputClick = HTMLInputElement.prototype.click;
  HTMLInputElement.prototype.click = function () {
    if (this.type === 'file') {
      if (!this.isConnected) document.body.appendChild(this);
      return;                       // no native picker; input stays in the DOM
    }
    return window.__origInputClick.call(this);
  };
}
```

  Then click the button, `find` "file input (type=file) in the upload dialog", and call
  `file_upload` with that ref. After a rejected upload the same input is still there and
  accepts `file_upload` again.
- ChatGPT's composer has permanent hidden inputs ("Attach files", "Attach photos"); `find` the
  "Attach files" one and `file_upload` to it directly, then wait ~4 s before typing.
- `file_upload` only takes files under the project directory or the session scratchpad; copy
  build outputs there if needed.

## Reading things the UI hides

```js
// drawer / dialog text (Connect MCP server, Submit for review are outside <main>)
document.querySelector('[role=dialog]').innerText

// enum values the UI never lists (e.g. categories): scan the loaded JS bundles around a
// known value and return ONLY the matched strings — raw code in the result is blocked by the
// extension's output filter.
const urls = [...new Set(performance.getEntriesByType('resource').map(e => e.name).filter(u => /\.js(\?|$)/.test(u)))];
const out = new Set();
for (const u of urls) { try { const t = await (await fetch(u)).text(); let i = 0;
  while ((i = t.indexOf('Developer Tools', i)) !== -1) {
    for (const m of t.slice(Math.max(0, i - 600), i + 600).matchAll(/"([A-Z][A-Za-z&' ]{2,30})"/g)) out.add(m[1]);
    i += 15; } } catch (e) {} }
[...out].join(' | ')
```

## Hygiene

- Refs go stale after navigation; `find` again rather than reusing.
- Keep each `browser_batch` under ~90 s of waits; longer batches timed out.
- A JS call that triggers navigation (`location.reload()`) and then sleeps fails with
  "Inspected target navigated"; reload, then read in a second call.
- Do not click Submit, Accept or Delete on the owner's behalf; stop and report.

## A ChatGPT test run, end to end

1. `navigate` to `https://chatgpt.com/`, wait 4 s.
2. `find` "message composer text box" and, if the case has an image, "hidden file input
   labelled Attach files".
3. `file_upload` the image, wait 4 s.
4. Click the composer, `type` the prompt, `key` `End Return`.
5. Wait ~30 s, then `get_page_text` (shows "Opened <tool title>" when a tool ran, and the
   full answer) and optionally a screenshot of the widget.
6. Confirm the call in the server's request log before recording a pass.
