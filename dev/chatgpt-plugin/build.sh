#!/bin/sh
# Build the ChatGPT plugin-directory submission ZIP from this directory.
#   dev/chatgpt-plugin/build.sh            -> dev/chatgpt-plugin/dist/dupefont-submission.zip
#   dev/chatgpt-plugin/build.sh devmode    -> dev/chatgpt-plugin-devmode/dist/dupefont-devmode.zip
# The public package must NOT contain .app.json (the portal rejects app references);
# the dev-mode package MUST keep it (it is the binding to the existing ChatGPT app).
set -eu
here=$(cd "$(dirname "$0")" && pwd)
if [ "${1:-}" = "devmode" ]; then src="$here/../chatgpt-plugin-devmode"; name=dupefont-devmode; else src="$here"; name=dupefont-submission; fi
mkdir -p "$src/dist"; out="$src/dist/$name.zip"; rm -f "$out"
( cd "$src" && zip -r -X "$out" . -x 'dist/*' -x 'build.sh' -x '*.py' -x '*.pyc' -x '*.tmp.ttf' >/dev/null )
if [ "$name" = dupefont-submission ] && unzip -l "$out" | grep -q '\.app\.json'; then
  echo "refusing: public package contains .app.json" >&2; rm -f "$out"; exit 1
fi
echo "$out"; unzip -l "$out" | tail -n +4 | head -n -2
