#!/bin/sh
# Offline fallback build (no npm install needed when esbuild + react are available). Vercel uses `npm run build`.
set -e
cd "$(dirname "$0")"
NODE_PATH=${NODE_PATH:-/opt/npm-tools/node_modules} ${ESBUILD:-/opt/npm-tools/node_modules/.bin/esbuild} src/main.jsx \
  --bundle --minify --format=esm --jsx=automatic --loader:.js=jsx --outfile=dist/assets/main.js \
  --define:process.env.NODE_ENV='"production"'
