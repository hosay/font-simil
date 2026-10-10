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
