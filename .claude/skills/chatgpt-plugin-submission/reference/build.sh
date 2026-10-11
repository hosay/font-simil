#!/bin/sh
# Build a ChatGPT plugin ZIP (public submission or dev-mode). POSIX sh.
#
#   PLUGIN_NAME=myplugin SRC=path/to/public-pkg            build.sh
#   PLUGIN_NAME=myplugin DEVMODE_SRC=path/to/devmode-pkg   build.sh devmode
#
# Defaults: SRC = directory containing this script; DEVMODE_SRC = $SRC/../<basename of SRC>-devmode.
# Output: $SRC/dist/$PLUGIN_NAME-submission.zip  or  $DEVMODE_SRC/dist/$PLUGIN_NAME-devmode.zip
# Package layout expected: .codex-plugin/plugin.json, .mcp.json, skills/, assets/
set -eu
here=$(cd "$(dirname "$0")" && pwd)
name=${PLUGIN_NAME:-plugin}
SRC=${SRC:-$here}
DEVMODE_SRC=${DEVMODE_SRC:-$SRC/../$(basename "$SRC")-devmode}
if [ "${1:-}" = "devmode" ]; then src=$DEVMODE_SRC; kind=devmode; else src=$SRC; kind=submission; fi
src=$(cd "$src" && pwd)
mkdir -p "$src/dist"; out="$src/dist/$name-$kind.zip"; rm -f "$out"
( cd "$src" && zip -r -X "$out" . \
    -x 'dist/*' -x 'build.sh' -x '.git/*' -x 'node_modules/*' -x '*.zip' -x '*.pyc' -x '.DS_Store' >/dev/null )

listing=$(unzip -Z1 "$out")
has() { printf '%s\n' "$listing" | grep -qx "$1"; }
fail() { echo "refusing: $*" >&2; rm -f "$out"; exit 1; }

has .codex-plugin/plugin.json || fail "no .codex-plugin/plugin.json in package"
if [ "$kind" = submission ]; then
  has .mcp.json || echo "note: no .mcp.json (skills-only plugin?)" >&2
  has .app.json && fail "public package contains .app.json"
else
  has .app.json || fail "dev-mode package lacks .app.json"
fi

# Manifest checks: parse, limits, apps/hooks policy, referenced assets present, HTTPS URLs,
# review test-case counts. Clear messages instead of tracebacks.
unzip -p "$out" .codex-plugin/plugin.json | KIND="$kind" LISTING="$listing" python3 -c '
import json, os, sys
def die(msg): print("refusing: " + msg, file=sys.stderr); sys.exit(1)
try: m = json.load(sys.stdin)
except Exception as e: die("manifest is not valid JSON: %s" % e)
kind, files = os.environ["KIND"], set(os.environ["LISTING"].split("\n"))
i = m.get("interface") or die("manifest has no root interface object")
for k, n in (("displayName", 30), ("shortDescription", 30), ("longDescription", 4000)):
    v = i.get(k) or die("interface.%s missing" % k)
    if len(v) > n: die("interface.%s is %d chars (max %d)" % (k, len(v), n))
dp = i.get("defaultPrompt", [])
if len(dp) > 3 or any(len(p) > 128 for p in dp): die("defaultPrompt: max 3 entries of 128 chars")
for k in ("developerName", "category", "capabilities", "logo", "composerIcon"):
    if not i.get(k): die("interface.%s missing" % k)
for k in ("websiteURL", "supportURL", "privacyPolicyURL", "termsOfServiceURL"):
    v = i.get(k)
    if not v:
        if kind == "submission": die("interface.%s missing (required for MCP review)" % k)
        print("warning: interface.%s missing (fine for dev mode, required for review)" % k, file=sys.stderr)
    elif not v.startswith("https://"): die("interface.%s must be https" % k)
for k in ("logo", "composerIcon"):
    p = i[k]
    if not p.startswith("./"): die("interface.%s must start with ./" % k)
    if p[2:] not in files: die("%s referenced by interface.%s is not in the ZIP" % (p, k))
if kind == "submission":
    if "apps" in m: die("public manifest declares apps")
    if "hooks" in m: die("public manifest declares lifecycle hooks")
else:
    if m.get("apps") != "./.app.json": die("dev-mode manifest must set apps to ./.app.json")
r = (m.get("extensions") or {}).get("com.openai", {}).get("review")
if kind == "submission" and r:
    tc = r.get("test_cases") or {}
    pos, neg = tc.get("positive", []), tc.get("negative", [])
    if (len(pos), len(neg)) != (5, 3):
        print("warning: review wants 5 positive + 3 negative, found %d + %d" % (len(pos), len(neg)), file=sys.stderr)
    for c in pos:
        for k in ("description", "prompt", "tools_triggered", "expected_behavior"):
            if not c.get(k): die("positive test case missing %s" % k)
    if not r.get("demo_recording_url", "").startswith("https://"):
        print("warning: review.demo_recording_url missing (required at submission)", file=sys.stderr)
print("manifest ok: %s %s category=%s" % (m.get("name"), m.get("version"), i["category"]))
' || { rm -f "$out"; exit 1; }

echo "$out"
printf '%s\n' "$listing"
