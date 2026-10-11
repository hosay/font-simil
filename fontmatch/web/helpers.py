"""Utility functions for the Flask web layer."""

from __future__ import annotations

import functools
import math
import re
from pathlib import Path

from fontmatch import licenses

# Weight/style suffixes to strip when building a Google Fonts URL.
_WEIGHT_SUFFIXES = re.compile(
    r"\s+(Extra\s*Light|Ultra\s*Light|Thin|Light|Regular|Medium|"
    r"Semi\s*Bold|Demi\s*Bold|Bold|Extra\s*Bold|Ultra\s*Bold|"
    r"Black|Heavy|Italic|Oblique|Condensed|Expanded|Narrow|Wide|"
    r"Display|Text|Caption|SemiBold|ExtraBold|Book|Roman)\s*$",
    re.IGNORECASE,
)

# Proprietary font -> open-source equivalent family name in our corpus
PROPRIETARY_TO_OPEN_SOURCE = {
    # Classic system / Office fonts
    "Times New Roman": "Tinos",
    "Arial": "Arimo",
    "Helvetica": "Arimo",
    "Helvetica Neue": "Inter",
    "Georgia": "Gelasio",
    "Courier New": "Cousine",
    "Verdana": "DejaVu Sans",
    "Calibri": "Carlito",
    "Cambria": "Caladea",
    "Trebuchet MS": "Fira Sans",
    "Segoe UI": "Source Sans 3 ExtraLight",
    "Aptos": "Inter",
    # Serif classics
    "Garamond": "EB Garamond",
    "Palatino": "Lora",
    "Bodoni": "Libre Bodoni",
    "Didot": "GFS Didot",
    "Trajan": "Cinzel",
    "Trajan Pro": "Cinzel",
    "Optima": "Lato",
    "Copperplate": "Cinzel",
    "Cooper Black": "Fraunces",
    # Geometric / grotesque sans
    "Futura": "Jost",
    "Gotham": "Montserrat Thin",
    "Proxima Nova": "Montserrat Thin",
    "Avenir": "Nunito Sans 12pt ExtraLight",
    "Century Gothic": "Poppins",
    "Avant Garde": "Poppins",
    "Brandon Grotesque": "Nunito ExtraLight",
    "Museo Sans": "Nunito Sans 12pt ExtraLight",
    "Gilroy": "Poppins",
    "Garet": "Sora",
    # Humanist / neo-grotesque sans
    "Myriad Pro": "Source Sans 3 ExtraLight",
    "Gill Sans": "Cabin",
    "Frutiger": "Source Sans 3 ExtraLight",
    "Univers": "Inter",
    "DIN": "Barlow",
    # Tech / modern sans
    "San Francisco": "Inter",
    "SF Pro": "Inter",
    "Product Sans": "Poppins",
    "Google Sans": "Poppins",
    "Canva Sans": "DM Sans 9pt",
    "Satoshi": "DM Sans 9pt",
    "Sofia Pro": "Sofia Sans",
    "Spotify": "Figtree Light",
    # Impact / display
    "Impact": "Anton",
    "Comic Sans": "Comic Neue",
    "Knockout": "Oswald",
    "Eurostile": "Michroma",
    "Recoleta": "Fraunces",
    # Script
    "Monotype Corsiva": "Great Vibes",
    # Added 2026-10: well-attested open-source revivals and lookalikes
    "Times": "Tinos",
    "Franklin Gothic": "Libre Franklin Thin",
    "Akzidenz-Grotesk": "Work Sans",
    "Avenir Next": "Nunito Sans 12pt ExtraLight",
    "Futura PT": "Jost",
    "Gotham Rounded": "Nunito ExtraLight",
    "Arial Rounded": "Varela Round",
    "Circular": "Figtree Light",
    "Tahoma": "DejaVu Sans",
    "Interstate": "Overpass",
    "Microgramma": "Michroma",
    "Baskerville": "Libre Baskerville",
    "Caslon": "Libre Caslon Text",
    "Minion Pro": "Crimson Pro",
    "Bembo": "Cardo",
    "Sabon": "EB Garamond",
    "Adobe Garamond": "EB Garamond",
    "Goudy Old Style": "Sorts Mill Goudy",
    "Rockwell": "Arvo",
    "Courier": "Courier Prime",
    "Consolas": "Inconsolata",
    "Menlo": "DejaVu Sans Mono",
    "Edwardian Script": "Pinyon Script",
}

# Case-insensitive lookup for proprietary font names
_PROP_LOOKUP = {k.lower(): k for k in PROPRIETARY_TO_OPEN_SOURCE}

# Aliases for corpus fonts whose DB name differs from the common search term.
# Maps the common name (as typed by users) to the actual DB family name.
CORPUS_ALIASES = {
    "DM Sans": "DM Sans 9pt",
    "Raleway": "Raleway Thin",
    "League Spartan": "League Spartan Thin",
    "Source Sans Pro": "Source Sans 3",
    "Cormorant Garamond": "Cormorant Garamond Light",
    "Old English": "UnifrakturMaguntia",
    "Sans Serif": "Inter",
    # Variable fonts whose DB family is their default instance name
    "Nunito Sans": "Nunito Sans 12pt ExtraLight",
    "Figtree": "Figtree Light",
    "Libre Franklin": "Libre Franklin Thin",
}

# Proprietary names that are the same typeface as another entry: one page
# is indexed (the value), the alias page canonicalises to it.
PROPRIETARY_CANONICAL = {
    "Trajan Pro": "Trajan",
    "San Francisco": "SF Pro",
    "Futura PT": "Futura",
    "Times": "Times New Roman",
}

# Case-insensitive lookup for corpus aliases
_ALIAS_LOOKUP = {k.lower(): k for k in CORPUS_ALIASES}

# Extra metadata for the /alternative-to/ pages
PROPRIETARY_FONTS = {
    "Times New Roman": {
        "css_family": "'Times New Roman', Times, serif",
        "category": "serif",
        "vendor": "Monotype",
        "description": "The classic serif typeface bundled with Windows and widely used in print and academic documents.",
    },
    "Arial": {
        "css_family": "Arial, Helvetica, sans-serif",
        "category": "sans-serif",
        "vendor": "Monotype",
        "description": "One of the most widely used sans-serif typefaces, bundled with Windows and macOS.",
    },
    "Helvetica": {
        "css_family": "'Helvetica Neue', Helvetica, Arial, sans-serif",
        "category": "sans-serif",
        "vendor": "Linotype",
        "description": "The iconic Swiss sans-serif typeface, a staple of modern graphic design. Bundled with macOS.",
    },
    "Georgia": {
        "css_family": "Georgia, 'Times New Roman', serif",
        "category": "serif",
        "vendor": "Microsoft",
        "description": "A serif typeface designed specifically for screen readability, bundled with Windows and macOS.",
    },
    "Courier New": {
        "css_family": "'Courier New', Courier, monospace",
        "category": "monospace",
        "vendor": "Monotype",
        "description": "The standard monospaced typeface bundled with most operating systems.",
    },
    "Verdana": {
        "css_family": "Verdana, Geneva, sans-serif",
        "category": "sans-serif",
        "vendor": "Microsoft",
        "description": "A humanist sans-serif designed for screen readability at small sizes.",
    },
    "Garamond": {
        "css_family": "Garamond, 'EB Garamond', serif",
        "category": "serif",
        "vendor": "Various",
        "description": "A family of old-style serif typefaces named after the 16th-century engraver Claude Garamond.",
    },
    "Futura": {
        "css_family": "Futura, 'Century Gothic', sans-serif",
        "category": "sans-serif",
        "vendor": "Bauer",
        "description": "An influential geometric sans-serif typeface designed in 1927.",
    },
    "Palatino": {
        "css_family": "'Palatino Linotype', 'Book Antiqua', Palatino, serif",
        "category": "serif",
        "vendor": "Linotype",
        "description": "An old-style serif typeface designed by Hermann Zapf, bundled with macOS and Windows.",
    },
    "Trebuchet MS": {
        "css_family": "'Trebuchet MS', 'Lucida Grande', sans-serif",
        "category": "sans-serif",
        "vendor": "Microsoft",
        "description": "A humanist sans-serif typeface designed by Vincent Connare for Microsoft.",
    },
    "Calibri": {
        "css_family": "Calibri, 'Gill Sans', sans-serif",
        "category": "sans-serif",
        "vendor": "Microsoft",
        "description": "The default font in Microsoft Office since 2007, a modern humanist sans-serif.",
    },
    "Cambria": {
        "css_family": "Cambria, Georgia, serif",
        "category": "serif",
        "vendor": "Microsoft",
        "description": "A transitional serif typeface designed for on-screen reading and body text in Microsoft Office.",
    },
    "Helvetica Neue": {
        "css_family": "'Helvetica Neue', Helvetica, Arial, sans-serif",
        "category": "sans-serif",
        "vendor": "Linotype",
        "description": "The refined successor to Helvetica with improved legibility and a wider range of weights.",
    },
    "Segoe UI": {
        "css_family": "'Segoe UI', Tahoma, Geneva, sans-serif",
        "category": "sans-serif",
        "vendor": "Microsoft",
        "description": "The default UI typeface for Windows and Microsoft products since Windows Vista.",
    },
    "Aptos": {
        "css_family": "Aptos, Calibri, sans-serif",
        "category": "sans-serif",
        "vendor": "Microsoft",
        "description": "Microsoft's new default font for Office apps, replacing Calibri in 2023.",
    },
    "Bodoni": {
        "css_family": "Bodoni, 'Bodoni MT', 'Libre Bodoni', serif",
        "category": "serif",
        "vendor": "Various",
        "description": "A high-contrast modern serif typeface designed by Giambattista Bodoni in the late 18th century.",
    },
    "Didot": {
        "css_family": "Didot, 'Bodoni MT', serif",
        "category": "serif",
        "vendor": "Various",
        "description": "A French modern serif known for extreme contrast between thick and thin strokes, widely used in fashion magazines.",
    },
    "Trajan": {
        "css_family": "Trajan, 'Trajan Pro', serif",
        "category": "serif",
        "vendor": "Adobe",
        "description": "An all-caps serif typeface inspired by the inscriptions on Trajan's Column in Rome, popular in movie posters.",
    },
    "Trajan Pro": {
        "css_family": "'Trajan Pro', Trajan, serif",
        "category": "serif",
        "vendor": "Adobe",
        "description": "The professional version of Trajan with expanded OpenType features, inspired by Roman inscriptional lettering.",
    },
    "Optima": {
        "css_family": "Optima, 'Palatino Linotype', sans-serif",
        "category": "sans-serif",
        "vendor": "Linotype",
        "description": "A humanist sans-serif with subtle flared stroke endings, designed by Hermann Zapf in 1958.",
    },
    "Copperplate": {
        "css_family": "'Copperplate Gothic', Copperplate, serif",
        "category": "serif",
        "vendor": "Various",
        "description": "A distinctive all-caps typeface with small serifs, inspired by copperplate engraving.",
    },
    "Cooper Black": {
        "css_family": "'Cooper Black', serif",
        "category": "serif",
        "vendor": "Various",
        "description": "A heavy, rounded serif typeface popular in 1970s advertising and retro designs.",
    },
    "Gotham": {
        "css_family": "Gotham, Montserrat, sans-serif",
        "category": "sans-serif",
        "vendor": "Hoefler & Co.",
        "description": "A geometric sans-serif inspired by architectural signage in New York City, made famous by the Obama 2008 campaign.",
    },
    "Proxima Nova": {
        "css_family": "'Proxima Nova', Montserrat, sans-serif",
        "category": "sans-serif",
        "vendor": "Mark Simonson Studio",
        "description": "A geometric sans-serif that bridges the gap between Futura and Akzidenz-Grotesk, one of the most popular web fonts.",
    },
    "Avenir": {
        "css_family": "Avenir, 'Avenir Next', Nunito, sans-serif",
        "category": "sans-serif",
        "vendor": "Linotype",
        "description": "A geometric sans-serif designed by Adrian Frutiger in 1988, meaning 'future' in French.",
    },
    "Century Gothic": {
        "css_family": "'Century Gothic', 'Apple Gothic', sans-serif",
        "category": "sans-serif",
        "vendor": "Monotype",
        "description": "A geometric sans-serif typeface inspired by Futura, bundled with Windows.",
    },
    "Avant Garde": {
        "css_family": "'ITC Avant Garde Gothic', 'Century Gothic', sans-serif",
        "category": "sans-serif",
        "vendor": "ITC",
        "description": "A geometric sans-serif originally designed for Avant Garde magazine, known for tight letter spacing and geometric forms.",
    },
    "Brandon Grotesque": {
        "css_family": "'Brandon Grotesque', Nunito, sans-serif",
        "category": "sans-serif",
        "vendor": "HVD Fonts",
        "description": "A geometric sans-serif with a warm, friendly character, popular in branding and editorial design.",
    },
    "Museo Sans": {
        "css_family": "'Museo Sans', Nunito, sans-serif",
        "category": "sans-serif",
        "vendor": "exljbris",
        "description": "A clean, geometric sans-serif companion to Museo, popular in web and print design.",
    },
    "Gilroy": {
        "css_family": "Gilroy, Poppins, sans-serif",
        "category": "sans-serif",
        "vendor": "Radomir Tinkov",
        "description": "A modern geometric sans-serif with a clean, professional look, popular in tech and startup branding.",
    },
    "Garet": {
        "css_family": "Garet, Sora, sans-serif",
        "category": "sans-serif",
        "vendor": "Colophon Foundry",
        "description": "A clean geometric sans-serif with distinctive rounded terminals.",
    },
    "Myriad Pro": {
        "css_family": "'Myriad Pro', 'Myriad', sans-serif",
        "category": "sans-serif",
        "vendor": "Adobe",
        "description": "Adobe's signature humanist sans-serif, used in Apple branding from 2002 to 2015.",
    },
    "Gill Sans": {
        "css_family": "'Gill Sans', 'Gill Sans MT', sans-serif",
        "category": "sans-serif",
        "vendor": "Monotype",
        "description": "A British humanist sans-serif designed by Eric Gill in 1928, inspired by Edward Johnston's typeface for the London Underground.",
    },
    "Frutiger": {
        "css_family": "Frutiger, 'Frutiger Neue', sans-serif",
        "category": "sans-serif",
        "vendor": "Linotype",
        "description": "A humanist sans-serif originally designed for airport signage, prized for legibility at all sizes.",
    },
    "Univers": {
        "css_family": "Univers, 'Helvetica Neue', sans-serif",
        "category": "sans-serif",
        "vendor": "Linotype",
        "description": "A neo-grotesque sans-serif designed by Adrian Frutiger in 1957, known for its systematic weight and width numbering.",
    },
    "DIN": {
        "css_family": "'DIN Pro', 'DIN Next', sans-serif",
        "category": "sans-serif",
        "vendor": "Various",
        "description": "A sans-serif originally designed for German industrial standards (Deutsches Institut für Normung), widely used in signage and tech.",
    },
    "San Francisco": {
        "css_family": "-apple-system, BlinkMacSystemFont, sans-serif",
        "category": "sans-serif",
        "vendor": "Apple",
        "description": "Apple's system font used across iOS, macOS, and watchOS since 2015.",
    },
    "SF Pro": {
        "css_family": "-apple-system, BlinkMacSystemFont, sans-serif",
        "category": "sans-serif",
        "vendor": "Apple",
        "description": "The professional variant of San Francisco, Apple's system typeface for macOS and iOS.",
    },
    "Product Sans": {
        "css_family": "'Product Sans', 'Google Sans', Poppins, sans-serif",
        "category": "sans-serif",
        "vendor": "Google",
        "description": "Google's custom geometric sans-serif used in the Google logo and product branding.",
    },
    "Google Sans": {
        "css_family": "'Google Sans', 'Product Sans', Poppins, sans-serif",
        "category": "sans-serif",
        "vendor": "Google",
        "description": "Google's proprietary text typeface used across Google products and Android UI.",
    },
    "Canva Sans": {
        "css_family": "'Canva Sans', 'DM Sans', sans-serif",
        "category": "sans-serif",
        "vendor": "Canva",
        "description": "Canva's custom sans-serif typeface used as the default in Canva designs.",
    },
    "Satoshi": {
        "css_family": "Satoshi, 'DM Sans', sans-serif",
        "category": "sans-serif",
        "vendor": "Indian Type Foundry",
        "description": "A modern, clean sans-serif with geometric shapes and friendly personality, popular in UI design.",
    },
    "Sofia Pro": {
        "css_family": "'Sofia Pro', 'Sofia Sans', sans-serif",
        "category": "sans-serif",
        "vendor": "Mostardesign",
        "description": "A geometric sans-serif with soft, rounded forms and a contemporary feel.",
    },
    "Spotify": {
        "css_family": "'Circular Std', Montserrat, sans-serif",
        "category": "sans-serif",
        "vendor": "Spotify / Lineto",
        "description": "Spotify uses Circular, a geometric sans-serif by Lineto. Find similar open-source alternatives.",
    },
    "Impact": {
        "css_family": "Impact, 'Arial Black', sans-serif",
        "category": "sans-serif",
        "vendor": "Monotype",
        "description": "A bold condensed sans-serif designed for headlines and impact, widely used in memes and web graphics.",
    },
    "Comic Sans": {
        "css_family": "'Comic Sans MS', 'Comic Sans', cursive",
        "category": "sans-serif",
        "vendor": "Microsoft",
        "description": "A casual sans-serif typeface designed to mimic comic book lettering, bundled with Windows.",
    },
    "Knockout": {
        "css_family": "Knockout, Oswald, sans-serif",
        "category": "sans-serif",
        "vendor": "Hoefler & Co.",
        "description": "A wide family of condensed sans-serifs inspired by 19th-century wood type, popular in sports and editorial design.",
    },
    "Eurostile": {
        "css_family": "Eurostile, 'Eurostile Extended', sans-serif",
        "category": "sans-serif",
        "vendor": "URW",
        "description": "A geometric sans-serif with distinctive squared letterforms, widely used in sci-fi and tech design.",
    },
    "Recoleta": {
        "css_family": "Recoleta, Georgia, serif",
        "category": "serif",
        "vendor": "Latinotype",
        "description": "A soft, friendly serif with rounded terminals inspired by the Cooper Black and Windsor typefaces.",
    },
    "Monotype Corsiva": {
        "css_family": "'Monotype Corsiva', cursive",
        "category": "script",
        "vendor": "Monotype",
        "description": "An italic script typeface bundled with Windows, popular for invitations and formal documents.",
    },
    "Times": {
        "css_family": "Times, 'Times New Roman', serif",
        "category": "serif",
        "vendor": "Linotype",
        "description": "Linotype's cut of the Times Roman newspaper typeface, the version of Times New Roman shipped with macOS.",
    },
    "Franklin Gothic": {
        "css_family": "'Franklin Gothic Medium', 'Franklin Gothic', sans-serif",
        "category": "sans-serif",
        "vendor": "ATF / Monotype",
        "description": "Morris Fuller Benton's 1902 American gothic, a long-time staple of newspaper headlines and advertising.",
    },
    "Akzidenz-Grotesk": {
        "css_family": "'Akzidenz-Grotesk', sans-serif",
        "category": "sans-serif",
        "vendor": "Berthold",
        "description": "The 1898 Berthold grotesque that inspired Helvetica and Univers, a favourite of Swiss-style designers.",
    },
    "Avenir Next": {
        "css_family": "'Avenir Next', sans-serif",
        "category": "sans-serif",
        "vendor": "Linotype",
        "description": "The 2004 expansion of Avenir by Adrian Frutiger and Akira Kobayashi, widely used in branding and apps.",
    },
    "Futura PT": {
        "css_family": "'Futura PT', Futura, sans-serif",
        "category": "sans-serif",
        "vendor": "ParaType",
        "description": "ParaType's digital Futura, the version many websites license through Adobe Fonts.",
    },
    "Gotham Rounded": {
        "css_family": "'Gotham Rounded', sans-serif",
        "category": "sans-serif",
        "vendor": "Hoefler & Co.",
        "description": "The rounded companion to Gotham, with softened stroke endings for a friendlier tone.",
    },
    "Arial Rounded": {
        "css_family": "'Arial Rounded MT Bold', sans-serif",
        "category": "sans-serif",
        "vendor": "Monotype",
        "description": "Arial Rounded MT, the rounded version of Arial bundled with Microsoft Office and macOS.",
    },
    "Circular": {
        "css_family": "'Circular', 'Circular Std', sans-serif",
        "category": "sans-serif",
        "vendor": "Lineto",
        "description": "Laurenz Brunner's 2013 geometric sans, best known from Spotify's branding.",
    },
    "Tahoma": {
        "css_family": "Tahoma, Verdana, sans-serif",
        "category": "sans-serif",
        "vendor": "Microsoft",
        "description": "Matthew Carter's 1994 screen sans, Verdana's narrower sibling and a long-time Windows interface font.",
    },
    "Interstate": {
        "css_family": "'Interstate', sans-serif",
        "category": "sans-serif",
        "vendor": "Font Bureau",
        "description": "Tobias Frere-Jones's 1993 sans based on the lettering of US highway signs.",
    },
    "Microgramma": {
        "css_family": "'Microgramma', 'Eurostile Extended', sans-serif",
        "category": "sans-serif",
        "vendor": "Nebiolo",
        "description": "The squared, extended 1952 display face that Eurostile grew out of, a sci-fi and tech classic.",
    },
    "Baskerville": {
        "css_family": "Baskerville, 'Libre Baskerville', serif",
        "category": "serif",
        "vendor": "Various",
        "description": "John Baskerville's transitional serif from the 1750s, admired for its crisp contrast and readability. Bundled with macOS.",
    },
    "Caslon": {
        "css_family": "'Adobe Caslon Pro', Caslon, serif",
        "category": "serif",
        "vendor": "Various",
        "description": "William Caslon's 18th-century old-style serif, used for the first printings of the US Declaration of Independence.",
    },
    "Minion Pro": {
        "css_family": "'Minion Pro', serif",
        "category": "serif",
        "vendor": "Adobe",
        "description": "Robert Slimbach's 1990 Renaissance-style serif, bundled with Adobe apps and a common book typeface.",
    },
    "Bembo": {
        "css_family": "Bembo, serif",
        "category": "serif",
        "vendor": "Monotype",
        "description": "A Renaissance serif based on the roman Aldus Manutius printed in 1495, a classic of book publishing.",
    },
    "Sabon": {
        "css_family": "Sabon, serif",
        "category": "serif",
        "vendor": "Linotype",
        "description": "Jan Tschichold's 1967 Garamond-style book typeface.",
    },
    "Adobe Garamond": {
        "css_family": "'Adobe Garamond Pro', Garamond, serif",
        "category": "serif",
        "vendor": "Adobe",
        "description": "Robert Slimbach's 1989 interpretation of Claude Garamond's 16th-century roman types.",
    },
    "Goudy Old Style": {
        "css_family": "'Goudy Old Style', serif",
        "category": "serif",
        "vendor": "ATF / Monotype",
        "description": "Frederic Goudy's 1915 old-style serif with its distinctive diamond-shaped dots, bundled with Microsoft Office.",
    },
    "Rockwell": {
        "css_family": "Rockwell, serif",
        "category": "serif",
        "vendor": "Monotype",
        "description": "A geometric slab serif from 1934, common on posters and packaging and bundled with Microsoft Office.",
    },
    "Courier": {
        "css_family": "Courier, 'Courier New', monospace",
        "category": "monospace",
        "vendor": "IBM",
        "description": "The 1955 IBM typewriter face, still the standard for screenplays.",
    },
    "Consolas": {
        "css_family": "Consolas, monospace",
        "category": "monospace",
        "vendor": "Microsoft",
        "description": "Lucas de Groot's coding font for Windows, long the default in Visual Studio.",
    },
    "Menlo": {
        "css_family": "Menlo, monospace",
        "category": "monospace",
        "vendor": "Apple",
        "description": "Apple's former default coding font in Terminal and Xcode.",
    },
    "Edwardian Script": {
        "css_family": "'Edwardian Script ITC', cursive",
        "category": "script",
        "vendor": "ITC",
        "description": "A formal copperplate script from 1994, a favourite for wedding invitations. Bundled with Microsoft Office.",
    },
}


# Fonts whose open-source alternative has the same character widths, so a
# document switched to it keeps its line breaks and page count. Only these
# pages may say "metrically compatible".
METRIC_COMPATIBLE = {
    "Times New Roman",
    "Times",
    "Arial",
    "Helvetica",
    "Courier New",
    "Calibri",
    "Cambria",
    "Georgia",
}

# How each alternative compares: unique copy for every /similar-to page.
PROPRIETARY_NOTES = {
    "Times New Roman": "Tinos was designed by Steve Matteson to have the same character widths as Times New Roman, so documents keep their line breaks and page count.",
    "Times": "Tinos has the same character widths as Times and Times New Roman, so switching fonts doesn't reflow your text.",
    "Arial": "Arimo has exactly the same character widths as Arial; Microsoft Office documents and web layouts reflow identically when you swap one for the other.",
    "Helvetica": "Arimo has Arial's character widths, which were themselves matched to Helvetica, so it is a drop-in replacement that keeps your layout. Liberation Sans is the same design packaged for Linux desktops.",
    "Helvetica Neue": "Inter is a modern neo-grotesque with Helvetica Neue's neutral, evenly spaced look, drawn for screens. If a document must keep Helvetica's line breaks, use Arimo instead.",
    "Georgia": "Gelasio was drawn to match Georgia's character widths, keeping Georgia's sturdy, screen-friendly serif look and layout.",
    "Courier New": "Cousine matches Courier New's character widths, but its strokes are heavier and more even, so code and screenplays read better on screen.",
    "Verdana": "DejaVu Sans grew out of Bitstream Vera and shares Verdana's large x-height and wide, open letters; line lengths are similar but not identical.",
    "Calibri": "Carlito has the same character widths as Calibri, so Word and PowerPoint files open with identical line breaks and slide layouts.",
    "Cambria": "Caladea matches Cambria's character widths, so documents keep their pagination while the letters stay sturdy and readable.",
    "Trebuchet MS": "Fira Sans is a humanist sans with a similar friendly, slightly informal feel; it is narrower, so text takes a little less space.",
    "Segoe UI": "Source Sans 3 is Adobe's open-source UI typeface with Segoe UI's clean humanist shapes. Text runs a little narrower.",
    "Aptos": "Inter is a neutral, highly legible UI sans much like Aptos; it was designed for screens and has a large x-height and tabular figures.",
    "Garamond": "EB Garamond is a faithful revival of Claude Garamond's types, with true small caps and old-style figures.",
    "Palatino": "Lora is a contemporary calligraphic serif with Palatino's moderate contrast and brushed curves; it is a little more condensed.",
    "Bodoni": "Libre Bodoni keeps Bodoni's extreme stroke contrast and vertical stress but was reworked for text sizes, so its hairlines hold up on screen.",
    "Didot": "GFS Didot is based on Firmin Didot's types, with the same high contrast and fine hairlines that suit fashion headlines.",
    "Trajan": "Cinzel is an all-caps typeface inspired by classical Roman inscriptions, the same source as Trajan.",
    "Trajan Pro": "Cinzel captures Trajan Pro's Roman inscriptional capitals; it has no lowercase, just as Trajan only has capitals and small caps.",
    "Optima": "Lato shares Optima's warm, classical proportions, though its strokes don't flare the way Optima's do.",
    "Copperplate": "Cinzel gives the same engraved, all-caps formality as Copperplate, with sharper classical serifs instead of Copperplate's tiny wedge serifs.",
    "Cooper Black": "Fraunces is a soft, heavy serif in the 'Old Style soft' tradition of Cooper Black and Windsor; set it in its Black weight for the retro look.",
    "Futura": "Jost is an open-source sans directly inspired by Futura's 1920s geometric forms: circular O, pointed A and M, single-storey a.",
    "Futura PT": "Jost follows Futura's geometric construction closely and comes in a full range of weights with matching italics.",
    "Gotham": "Montserrat is inspired by old posters and signs in Buenos Aires and shares Gotham's wide, geometric shapes; it is a little wider and rounder.",
    "Gotham Rounded": "Nunito is a rounded geometric sans with the same soft stroke endings as Gotham Rounded.",
    "Proxima Nova": "Montserrat has Proxima Nova's geometric, wide proportions and works well for headings; it is wider, so set body text a size smaller.",
    "Avenir": "Nunito Sans has Avenir's geometric-humanist balance and crisp terminals, which makes it a closer match than plain (rounded) Nunito.",
    "Avenir Next": "Nunito Sans comes in a similar range of weights and widths to Avenir Next and shares its clean, geometric-humanist shapes.",
    "Century Gothic": "Poppins is a geometric sans with Century Gothic's round O and wide stance; it has a larger x-height, so text looks bigger.",
    "Avant Garde": "Poppins has Avant Garde's pure geometric circles and straight lines but without its many tight-fitting ligatures.",
    "Brandon Grotesque": "Nunito has the same soft, slightly rounded geometric feel as Brandon Grotesque, with more strongly rounded ends.",
    "Museo Sans": "Nunito Sans matches Museo Sans' friendly geometric shapes and comes in a similar range of weights.",
    "Gilroy": "Poppins is a geometric sans with Gilroy's round shapes and modern startup feel.",
    "Garet": "Sora is a geometric sans with Garet's clean, wide letterforms and works well for headlines and UI.",
    "Myriad Pro": "Source Sans 3 comes from the same Adobe humanist tradition as Myriad and has a similar open, legible texture.",
    "Gill Sans": "Cabin is a humanist sans inspired by Edward Johnston's and Eric Gill's typefaces, so it shares Gill Sans' classical British proportions.",
    "Frutiger": "Source Sans 3 is an open, humanist sans with Frutiger's signage-friendly legibility.",
    "Univers": "Inter is a neutral neo-grotesque with Univers' even, rational texture, tuned for screens.",
    "DIN": "Barlow is a slightly rounded, low-contrast grotesk inspired by highway signs and number plates, with DIN's engineered, condensed feel.",
    "San Francisco": "Inter is the closest open-source match to Apple's system font: a neo-grotesque designed for UI, with a large x-height and tight, even spacing.",
    "SF Pro": "Inter mirrors SF Pro's neutral UI shapes and also comes as a variable font with optical sizes for text and display.",
    "Product Sans": "Poppins shares the circular, geometric construction of Google's logo font. Google has since replaced Product Sans with Google Sans in most products.",
    "Google Sans": "Poppins is a geometric sans with Google Sans' round, friendly shapes. Google Sans Code, the coding version, is already open source on Google Fonts.",
    "Canva Sans": "DM Sans is a low-contrast geometric sans with Canva Sans' clean, friendly look and works well at small sizes.",
    "Satoshi": "DM Sans has Satoshi's modernist geometric shapes and neutral tone, and is available as a variable font.",
    "Sofia Pro": "Sofia Sans is a different design from a different foundry despite the name; it is a clean, slightly condensed sans that suits the same UI and branding uses.",
    "Spotify": "Spotify's branding typeface, Circular, is a geometric sans; Figtree has a similarly friendly, round and clean look.",
    "Circular": "Figtree is a clean geometric sans with Circular's friendly round shapes and simple, approachable tone.",
    "Impact": "Anton is a heavy, condensed display sans in the same tradition as Impact, ideal for posters and headlines.",
    "Comic Sans": "Comic Neue keeps Comic Sans' casual, handwritten feel but with cleaner, more consistent letterforms.",
    "Knockout": "Oswald reworks classic gothic condensed sans styles, like the wood-type faces Knockout is based on, for the screen.",
    "Eurostile": "Michroma reworks Microgramma, the font Eurostile grew out of, so it has the same squared, extended letters.",
    "Microgramma": "Michroma is a reworking of Microgramma's squared, extended letterforms, so the two look nearly the same.",
    "Recoleta": "Fraunces is a soft serif inspired by the same 'Old Style soft' faces as Recoleta, such as Windsor and Cooper.",
    "Monotype Corsiva": "Great Vibes is a flowing calligraphic script for invitations and headings; it is more decorative than Corsiva's chancery italic.",
    "Franklin Gothic": "Libre Franklin is an open-source interpretation of Morris Fuller Benton's Franklin Gothic, so the shapes and proportions closely match.",
    "Akzidenz-Grotesk": "Work Sans is loosely based on the early grotesques of Akzidenz-Grotesk's era, with the same plain, slightly irregular shapes.",
    "Arial Rounded": "Varela Round is a rounded sans with Arial Rounded's simple, even strokes and friendly tone.",
    "Tahoma": "DejaVu Sans shares Tahoma's large x-height and open shapes; it is wider, so text runs longer.",
    "Interstate": "Overpass is based on the same US highway-sign lettering (Highway Gothic) that Interstate is drawn from.",
    "Baskerville": "Libre Baskerville is based on the 1941 American Type Founders Baskerville, with a taller x-height and wider counters for screens.",
    "Caslon": "Libre Caslon Text is a Caslon revival drawn for body text on screens, with a slightly larger x-height than print Caslons.",
    "Minion Pro": "Crimson Pro is a book typeface in the Renaissance tradition of Minion and Garamond, with a similar calm, classical texture.",
    "Bembo": "Cardo is modelled on the same Aldine roman that Bembo comes from, and adds a large character set for scholarly work.",
    "Sabon": "EB Garamond revives the Garamond types Sabon is based on; it is a little lighter and more historical in feel.",
    "Adobe Garamond": "EB Garamond is drawn from the same 16th-century Garamond specimens and has true small caps and old-style figures.",
    "Goudy Old Style": "Sorts Mill Goudy is a digital revival of Goudy Old Style itself, including its italic.",
    "Rockwell": "Arvo is a geometric slab serif with Rockwell's even, monoline strokes and blunt slab serifs.",
    "Courier": "Courier Prime was made for screenwriters as a better-looking Courier with the same fixed character width, so script page counts stay the same.",
    "Consolas": "Inconsolata was designed by Raph Levien as a free coding font in the spirit of Consolas.",
    "Menlo": "Menlo is based on Bitstream Vera Sans Mono, and DejaVu Sans Mono is the open-source extension of the same font: they are close relatives.",
    "Edwardian Script": "Pinyon Script is a formal round-hand script in the same English copperplate tradition.",
}


def lookup_proprietary(name: str) -> str | None:
    """Case-insensitive lookup of a proprietary font name.

    Returns the canonical (correctly cased) name, or None.
    """
    return _PROP_LOOKUP.get(name.lower()) or _PROP_SLUGS.get(slugify(name))


def lookup_corpus_alias(name: str) -> str | None:
    """Case-insensitive lookup of a corpus alias.

    Returns the actual DB family name, or None if not an alias.
    """
    canonical = _ALIAS_LOOKUP.get(name.lower())
    if canonical:
        return CORPUS_ALIASES[canonical]
    return None


def slugify(family: str) -> str:
    """Convert 'Open Sans' -> 'open-sans'."""
    return re.sub(r"[^a-z0-9]+", "-", family.lower()).strip("-")


# Slug -> proprietary name, so names with punctuation ("Akzidenz-Grotesk")
# still resolve after the slug round trip.
_PROP_SLUGS = {slugify(k): k for k in PROPRIETARY_TO_OPEN_SOURCE}


def deslugify(slug: str) -> str:
    """Convert 'open-sans' -> 'Open Sans' (title case)."""
    return slug.replace("-", " ").title()


def base_family_name(family: str) -> str:
    """Strip weight/style suffixes: 'Alegreya Sans ExtraBold' -> 'Alegreya Sans'."""
    return _WEIGHT_SUFFIXES.sub("", family).strip()


def distance_to_score(distance: float) -> int:
    """Convert a raw distance to a 0-100 match score for display."""
    d = max(0.0, min(distance, 1.0))
    score = 100 * math.exp(-6.0 * d)
    return max(0, min(100, round(score)))


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
GOOGLE_FONTS_REPOS = [_PROJECT_ROOT / "google-fonts-repo"]
_LICENSE_DIRS = tuple(licenses.GOOGLE_FONTS_DIRS)
_METADATA_NAME = re.compile(r'^name:\s*"([^"]+)"', re.MULTILINE)


@functools.lru_cache(maxsize=8192)
def google_fonts_name(source: str) -> str | None:
    """The family's name on Google Fonts (``name:`` in the google/fonts
    METADATA.pb next to the font file), or None if unknown.

    ``source`` is the DB's source path: "ofl/redhattext/RedHatText[wght].ttf"
    or, for repos walked without the license level, "redhattext/....ttf"."""
    rel = Path(source)
    if rel.is_absolute() or ".." in rel.parts or len(rel.parts) < 2:
        return None
    if rel.parts[0] in _LICENSE_DIRS:
        candidates = [rel.parent]
    else:
        candidates = [Path(lic) / rel.parent for lic in _LICENSE_DIRS]
    for repo in GOOGLE_FONTS_REPOS:
        for cand in candidates:
            meta = repo / cand / "METADATA.pb"
            try:
                text = meta.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            m = _METADATA_NAME.search(text)
            if m:
                return m.group(1)
    return None


_DIR_LICENSES = licenses.GOOGLE_FONTS_DIRS


@functools.lru_cache(maxsize=8192)
def google_fonts_dir(family: str) -> str | None:
    """Pseudo source path ("ofl/montserrat/METADATA.pb") when google/fonts has
    a directory for this exact family name. Covers rows ingested from other
    folders (license "unknown", bare file name) whose family is on Google
    Fonts under the same name, e.g. Montserrat, Arimo, Source Sans 3."""
    slug = re.sub(r"[^a-z0-9]", "", family.lower())
    if not slug:
        return None
    for repo in GOOGLE_FONTS_REPOS:
        for lic in _LICENSE_DIRS:
            rel = f"{lic}/{slug}/METADATA.pb"
            name = google_fonts_name(rel)
            if name and name.lower() == family.lower():
                return rel
    return None


def google_fonts_source_for(store, family: str) -> str | None:
    """The family's Google Fonts source: an ingested google/fonts file, else
    a matching google/fonts directory."""
    return (store.google_fonts_source(family) if store is not None else None) or (
        google_fonts_dir(family)
    )


def google_fonts_license(source: str | None) -> str | None:
    """License id implied by the google/fonts directory a source lives in."""
    if not source:
        return None
    rel = Path(source)
    if rel.parts and rel.parts[0] in _DIR_LICENSES:
        return _DIR_LICENSES[rel.parts[0]]
    for repo in GOOGLE_FONTS_REPOS:
        for lic in _LICENSE_DIRS:
            if len(rel.parts) >= 2 and (repo / lic / rel.parent / "METADATA.pb").is_file():
                return _DIR_LICENSES[lic]
    return None


GOOGLE_FONTS_LIVE_FILE = Path(__file__).resolve().parent / "google_fonts_live.txt"

# Families google/fonts still has directories for but fonts.google.com has
# retired, mapped to the family that replaced them. Retired families not
# listed here get no Google Fonts link.
GOOGLE_FONTS_SUCCESSORS = {
    **{
        f"Big Shoulders{kind}{size}{sc}": f"Big Shoulders{kind}"
        for kind in ("", " Inline", " Stencil")
        for size in (" Display", " Text")
        for sc in ("", " SC")
    },
    "Alumni Sans Collegiate One SC": "Alumni Sans Collegiate One",
    "Creepster Caps": "Creepster",
    "Ek Mukta": "Mukta",
    "Finlandica": "Finlandica Text",
    "Montserrat Subrayada": "Montserrat Underline",
    "Fragment Mono SC": "Fragment Mono",
    "Noto Naskh Arabic UI": "Noto Naskh Arabic",
    "Noto Serif Nyiakeng Puachue Hmong": "Noto Serif NP Hmong",
    "Noto Sans N Ko": "Noto Sans NKo",
    "Nosifer Caps": "Nosifer",
    "OFL Sorts Mill Goudy TT": "Sorts Mill Goudy",
    "Rubik One": "Rubik",
    "Saira Stencil One": "Saira Stencil",
    "Sansita One": "Sansita",
    "Signika Negative SC": "Signika Negative",
    "Signika SC": "Signika",
    "Yaldevi Colombo": "Yaldevi",
    **{
        f"Noto Sans {script} UI": f"Noto Sans {script}"
        for script in (
            "Arabic", "Bengali", "Devanagari", "Gujarati", "Gurmukhi", "Kannada", "Khmer",
            "Lao", "Malayalam", "Myanmar", "Oriya", "Sinhala", "Tamil", "Telugu", "Thai",
        )
    },
}  # fmt: skip


@functools.lru_cache(maxsize=1)
def google_fonts_live_families() -> frozenset[str] | None:
    """Families fonts.google.com serves (dev/update_google_fonts_live.py), or
    None when the list is missing (then every family is assumed live)."""
    try:
        text = GOOGLE_FONTS_LIVE_FILE.read_text(encoding="utf-8")
    except OSError:
        return None
    return frozenset(
        line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")
    )


def google_fonts_live_name(name: str) -> str | None:
    """The name to use in Google Fonts URLs: ``name`` if Google Fonts serves
    it, its successor if it was retired, else None."""
    live = google_fonts_live_families()
    if live is None or name in live:
        return name
    successor = GOOGLE_FONTS_SUCCESSORS.get(name)
    return successor if successor in live else None


def google_fonts_css_name(name: str | None) -> str | None:
    """``name`` if Google Fonts serves that exact family, for CSS embed code.
    Never a successor: "Big Shoulders" serves mixed case, not the small caps
    of "Big Shoulders Display SC"."""
    return name if name and google_fonts_live_name(name) == name else None


def google_fonts_url(family: str, source: str | None = None) -> str | None:
    """Google Fonts specimen URL for a family, or None if Google Fonts doesn't
    serve it. With the font's ``source`` path the repo's own family name is
    used ("Red Hat Text"); otherwise the family name with weight/style
    suffixes stripped (a guess that is wrong for names like "Playfair
    Display")."""
    name = (google_fonts_name(source) if source else None) or base_family_name(family)
    name = google_fonts_live_name(name)
    return "https://fonts.google.com/specimen/" + name.replace(" ", "+") if name else None


def _is_crawled_source(source: str) -> bool:
    """Check if a source looks like a crawled web font (domain name, not a file path)."""
    if not source:
        return True
    # Crawled sources look like "zoho.com" or "siemens.com/SiemensSerif"
    # Local sources look like "Tinos-Regular.ttf" or "ofl/tinos/Tinos-Regular.ttf"
    return "." in source.split("/")[0] and not source.endswith((".ttf", ".otf", ".woff", ".woff2"))


def distance_to_score_component(distance: float, scale: float = 6.0) -> int:
    """Convert a raw sub-distance to a 0-100 score."""
    d = max(0.0, min(distance, 1.0))
    return max(0, min(100, round(100 * math.exp(-scale * d))))


_LICENSE_LABELS = licenses.LICENSE_LABELS


def license_label(license_id: str) -> str:
    """Return a human-friendly license label."""
    return _LICENSE_LABELS.get(license_id, license_id or "Unknown")


def enrich_matches(matches: list[dict], store=None) -> list[dict]:
    """Add human-friendly fields to each match dict for template rendering."""
    for m in matches:
        m["score"] = distance_to_score(m["distance"])
        m["google_fonts_url"] = None

        # Score breakdown
        m["metric_score"] = distance_to_score_component(m.get("metric_distance", 0), scale=6.0)
        m["perceptual_score"] = distance_to_score_component(
            m.get("perceptual_distance", 0), scale=6.0
        )

        # License
        m.setdefault("license_id", "unknown")

        # Check if font file is available on disk (not a crawled web font)
        source = None
        if store is not None:
            source = store.get_font_source(m["name"])
        m["has_file"] = source is not None and not _is_crawled_source(source)

        # Google Fonts link, and the family's name there: the DB holds a
        # variable font's default instance ("Nunito Sans 12pt ExtraLight"),
        # which is wrong to show and breaks the Google Fonts CSS URL.
        gf_source = google_fonts_source_for(store, m["family"])
        m["gf_family"] = google_fonts_name(gf_source) if gf_source else None
        if m["license_id"] in ("", "unknown") and gf_source:
            m["license_id"] = google_fonts_license(gf_source) or m["license_id"]
        m["license_label"] = license_label(m["license_id"])
        m["display_family"] = m["gf_family"] or m["family"]
        m["gf_css_family"] = google_fonts_css_name(m["gf_family"])
        if gf_source:
            m["google_fonts_url"] = google_fonts_url(m["family"], gf_source)

    return matches
