#!/bin/sh
# Install the pinned compiler and Node typings without a registry.
# usage: scripts/install-offline.sh <directory holding the three pinned tarballs>
# npm verifies every tarball against the sha512 recorded in package-lock.json.
set -eu
pins=${1:?usage: scripts/install-offline.sh <pins directory>}
cd "$(dirname "$0")/.."
cache="$PWD/.cache/npm"
for archive in typescript-5.9.3.tgz types-node-24.19.2.tgz undici-types-7.24.6.tgz; do
  npm cache add --offline --cache "$cache" "$pins/$archive" >/dev/null
done
exec npm ci --offline --ignore-scripts --no-audit --no-fund --cache "$cache"
