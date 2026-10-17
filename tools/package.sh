#!/usr/bin/env bash
# tools/package.sh - make the release archives in dist/:
#
#   paltop-terminal-VERSION.tar.gz   core + terminal
#   paltop-web-VERSION.tar.gz        core + web
#   paltop-VERSION.tar.gz            everything
#
# Each has install.sh, the docs and the license. Only what belongs in the
# repository goes in: no state/, no built or downloaded decompressor.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."
version="$(cat VERSION)"
mkdir -p dist

pack() {       # pack NAME PART...
    local name="$1" top="$1-$version"; shift
    local tmp; tmp="$(mktemp -d)"
    mkdir "$tmp/$top"
    cp -r README.md LICENSE VERSION install.sh docs "$tmp/$top/"
    for part in core "$@"; do cp -r "$part" "$tmp/$top/"; done
    rm -rf "$tmp/$top/core/oodle/ooz" "$tmp/$top/core/oodle/libooz.so" \
        "$tmp/$top/docs/images"           # screenshots: only for GitHub
    find "$tmp/$top" -name __pycache__ -prune -exec rm -rf {} +
    tar czf "dist/$top.tar.gz" -C "$tmp" "$top"
    rm -rf "$tmp"
    echo "dist/$top.tar.gz"
}

pack paltop-terminal terminal
pack paltop-web web
pack paltop terminal web
