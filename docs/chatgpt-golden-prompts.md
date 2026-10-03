# ChatGPT golden prompts (validation layer L5)

Run every prompt **3 times** (fresh chat each time) in ChatGPT **Developer mode** with the Dupefont app connected
(`https://dupefont.com/mcp`, no auth). Use a fresh chat per prompt. Record for each row:
did ChatGPT call a Dupefont tool (and which), did it pass `text_hint`, and was the answer
useful (1–3). Check `journalctl -u dupefont-mcp` for the matching `image tool:` log line.

Targets: invocation precision ≥ 0.9 (no calls on the "must not invoke" rows, 7 × 3 runs), recall
≥ 0.8 on "should invoke" rows, `text_hint` passed on ≥ 80% of image calls, median usefulness ≥ 2.

Test images: keep them in a local folder (not committed). Suggested set: a website hero
screenshot, a restaurant menu photo, a product logo, a slide deck title, a handwritten-style
poster, a monospace code screenshot, a tilted phone photo of a sign, a light-on-dark banner.

## Should invoke `find_free_font_from_image` (attach an image)

| # | Prompt | Image |
|---|---|---|
| 1 | What font is this? | website hero |
| 2 | Find me a free font that looks like this | restaurant menu photo |
| 3 | Is there a Google Font similar to this logo? | product logo |
| 4 | I want my site's headings to look like this screenshot. Which font should I use? | slide title |
| 5 | what typeface is used here | tilted sign photo |
| 6 | Need a free alternative to the font in this image for a commercial project | banner |
| 7 | Can you identify the font? It's for my band poster | handwritten-style poster |
| 8 | Which monospace font is this? | code screenshot |

## Should invoke `find_free_alternatives` (no image)

| # | Prompt |
|---|---|
| 9 | What's a free alternative to Helvetica? |
| 10 | Gotham is too expensive, what open-source font is closest? |
| 11 | Fonts similar to Montserrat? |
| 12 | I need something like Futura but free for commercial use |
| 13 | Replacement for Calibri that I can embed on a website |

## Must NOT invoke any Dupefont tool

| # | Prompt |
|---|---|
| 14 | What is a font? |
| 15 | Write a CSS rule that sets the body font to Inter |
| 16 | Design me a logo for a coffee shop |
| 17 | Translate the text in this image (attach a menu photo) |
| 18 | What's the difference between serif and sans-serif? |
| 19 | How do I install a font on Windows? |
| 20 | Summarise this screenshot (attach a website screenshot) |

## Edge cases (should handle gracefully)

| # | Prompt | Expected |
|---|---|---|
| 21 | What font is this, it looks like Helvetica? (attach image) | image tool, not alternatives; answer frames results as free alternatives |
| 22 | What font is this? (attach a photo with no text) | tool error "No readable text…" relayed politely, or no call |
| 23 | What font is this? (attach Japanese/Arabic text) | graceful: low-confidence or no-match message, no invented font name |
| 24 | Which of these two logos uses a free font? (attach 2 images) | one call per image, or a clear explanation |
| 25 | Same as #1 from the ChatGPT mobile app | same behaviour as desktop |
| 26 | Send 25 image requests in a minute | "Too many requests…" surfaced, no crash |

Also check in every answer: does ChatGPT present results as *free alternatives* rather than
claiming "this is font X" when X is one of our matches?

## Results log

| Date | # | Tool called | text_hint passed | Top match | Useful (1-3) | Notes |
|---|---|---|---|---|---|---|
