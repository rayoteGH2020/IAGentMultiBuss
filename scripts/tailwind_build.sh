#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -f bin/tailwindcss.exe ]]; then
  bin/tailwindcss.exe -i app/static/css/input.css -o app/static/css/app.css --minify
else
  ./bin/tailwindcss -i app/static/css/input.css -o app/static/css/app.css --minify
fi
