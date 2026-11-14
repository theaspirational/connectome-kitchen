#!/bin/sh
# Prove that scripts/install-offline.sh opens no network socket.
# usage: scripts/check-offline-install.sh <pins directory> [file to keep the trace in]
#
# The installer runs in a scratch copy of the package with a fresh npm cache and
# a fresh home, under `strace -f -e trace=network`. The environment is emptied
# first, so a CI variable or an npm setting of the caller cannot hide a lookup.
# The check fails on any IPv4 or IPv6 socket call, loopback included: a stub
# resolver listens on loopback, so a DNS query is a loopback call. The scratch
# npm configuration names a closed local port as the registry, so a lookup that
# does come back is refused on this machine instead of reaching a registry.
set -eu
pins=${1:?usage: scripts/check-offline-install.sh <pins directory> [trace file]}
keep=${2:-}
command -v strace >/dev/null || { echo 'check-offline-install: strace is required' >&2; exit 2; }
pins=$(cd "$pins" && pwd)
root=$(cd "$(dirname "$0")/.." && pwd)
mkdir -p "$root/.cache"
work=$(mktemp -d "$root/.cache/offline-install-check.XXXXXX")
trap 'rm -rf "$work"' EXIT
mkdir "$work/pkg" "$work/pkg/scripts" "$work/user"
cp "$root/package.json" "$root/package-lock.json" "$work/pkg/"
cp "$root/scripts/install-offline.sh" "$work/pkg/scripts/"
echo 'registry=http://localhost:9/' > "$work/user/.npmrc"
trace="$work/network.trace"
status=0
env -i PATH="$PATH" HOME="$work/user" \
  strace -f -e trace=network -o "$trace" \
  "$work/pkg/scripts/install-offline.sh" "$pins" > "$work/install.log" 2>&1 || status=$?
if [ -n "$keep" ]; then cp "$trace" "$keep"; fi
if [ "$status" -ne 0 ]; then
  cat "$work/install.log" >&2
  echo "check-offline-install: installer exited $status" >&2
  exit 1
fi
if ! grep -q 'exited with 0' "$trace"; then
  echo 'check-offline-install: the trace records no traced process' >&2
  exit 1
fi
if [ ! -f "$work/pkg/node_modules/typescript/lib/typescript.js" ] ||
   [ ! -f "$work/pkg/node_modules/@types/node/index.d.ts" ] ||
   [ ! -f "$work/pkg/node_modules/undici-types/index.d.ts" ]; then
  echo 'check-offline-install: the pinned packages were not installed' >&2
  exit 1
fi
# Interface listings over netlink also name these families; only socket() and
# socket addresses are matched.
if grep -n -E 'socket\(AF_INET|sa_family=AF_INET' "$trace" >&2; then
  echo 'check-offline-install: FAIL, the installer made the IPv4/IPv6 socket calls above' >&2
  exit 1
fi
echo "check-offline-install: PASS, $(grep -c 'exited with' "$trace") traced processes, no IPv4 or IPv6 socket call"
