# Policy and terms checklist (owner risk summary)

Read the live documents before each submission: `https://developers.openai.com/plugins/plugin-guidelines`,
`https://openai.com/policies/developer-apps-terms/`, `https://openai.com/policies/usage-policies/`.
This is their reading as of 2026-10-10 (Developer Terms revision dated 2026-09-28). Produce a
one-screen summary for the owner that leads with anything they must fix or decide, then the
contractual points, then what passes.

## Verify in the plugin before attesting

- [ ] **Rights to every asset**: logo, listing copy, test attachments, data sources, anything
      rendered in widgets. No third-party photos lifted from eval sets or the web (attestation 4
      plus the indemnity make this the owner's liability).
- [ ] **Listing claims are verifiable**: no "always", "best", "official", no comparisons, no
      pricing/trial/discount language. Check the data behind any absolute claim.
- [ ] **No ads in the plugin or in embedded pages**; no digital goods, subscriptions, upgrade
      prompts or checkout links. Ordinary links to your site are outside the plugin; any iframed
      page is inside it.
- [ ] **Annotations** explicit and truthful on every tool; descriptions do not over-trigger.
- [ ] **Data minimisation**: inputs only for the task; no transcripts, no raw/precise location,
      no chat history; responses without timestamps/ids/logging metadata.
- [ ] **Restricted data never processed**: payment card data (PCI), health data (HIPAA),
      government IDs, credentials/secrets.
- [ ] **Tracking disclosed**: if you log per-user identifiers (even hashed), timestamps, locale,
      country or query text, the privacy policy must say what, why, who receives it, how long,
      and the user controls. Keep it narrow.
- [ ] **Privacy policy** published, reachable before install, covering data categories,
      purposes, recipient categories, retention, user controls/rights, contact, children.
- [ ] **Support contact** live; identity verified; terms page live.
- [ ] **Name**: not a generic dictionary word, no "MCP"/"Plugin" suffix, no implied OpenAI
      endorsement anywhere (site copy included); OpenAI marks only per brand guidelines.
- [ ] **Not a trial/demo**; no sign-up or 2FA needed to review (provide a test account if the
      server authenticates users).
- [ ] **Audience**: suitable for 13–17; no under-13 targeting; no mature content.
- [ ] **Third-party services**: no scraping, no unofficial pass-through connectors, their terms
      respected.
- [ ] **Widgets/iframes**: same registrable domain as the MCP server; each iframe justified.

## What the owner agrees to (App Developer Terms highlights)

- **Indemnity, uncapped**: owner defends/indemnifies OpenAI for third-party claims relating to
  the app, its responses, the API and the connected website. OpenAI's liability cap: $100.
- **Mandatory arbitration + class waiver** (California law). Opt-out within 30 days of agreeing
  via the form linked in the terms' dispute-resolution section.
- **Licence to App Responses**: worldwide, royalty-free, to OpenAI and to users; survives removal
  of the app. Responses inside conversations may be used to improve OpenAI services under the
  user's terms.
- **Proactive calls**: OpenAI may call the server for "ongoing or proactive interactions".
  Rate-limit accordingly.
- **No guarantees**: no placement/ranking; rejection or removal at any time for any reason;
  fees may be introduced with notice; terms change with 15 days' notice and continued listing is
  acceptance.
- **Separate controllers**: each party handles personal data under its own terms; the developer
  must present a legally adequate privacy notice before processing.
- **Security duty**: reasonable technical/organisational measures; report breaches to OpenAI
  promptly.
- **Trade controls / sanctions** representations.

## Usage-policy items that can apply to ordinary utilities

- If the tool handles images of people: no facial recognition databases, no biometric
  identification, no inference of sensitive attributes; ignore faces.
- No tailored legal/medical/financial advice without licensed involvement.
- No facilitation of IP infringement (e.g. serving pirated assets). If users type brand or
  product names as queries, nominative use is fine; do not reproduce protected assets.

## Hand-over format

1. One thing to fix before submitting (if any), with the fix offered.
2. Contractual risks (the bullets above that matter for this owner).
3. Guideline rules touching this plugin specifically, with the evidence.
4. Checks that pass.
5. The six attestations, one line each: true / true with caveat / not yet.
6. Portal state table (version, findings, review status).
