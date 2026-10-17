#!/usr/bin/env bash
# Build libooz.so, the Oodle Kraken decompressor paltop uses to read the
# Palworld save files (palsave.py) and the game's pak (palpak.py).
#
# Palworld's .sav files are Kraken-compressed, and the game's own Oodle is
# statically linked into PalServer-Linux-Shipping with the public API
# unexported, so it cannot be loaded. ooz is an open-source reimplementation
# of the decompressor: https://github.com/zao/ooz
#
# ooz has no license file, so paltop does not ship it: this script downloads
# it (a fixed commit) into ooz/ and builds it on your machine. Only the
# DECOMPRESSOR is built (-DOOZ_BUILD_DLL=1 is the source's own guard that
# drops its CLI front end, which needs the compressor and some Windows-only
# shims). What is paltop's own here:
#
#   ooz_wrap.cpp     a stable C entry point, ooz_decompress()
#   simde_compat.h   maps the simde names ooz uses onto the native SSE2
#                    intrinsics (the simde submodule is not needed on x86_64)
#
# Needs git (or curl) and g++. Usage: core/oodle/build.sh
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

OOZ_REPO=https://github.com/zao/ooz
OOZ_COMMIT=ff5aeb9e45e362e8d6bb1199aa82406285dd2a18

case "$(uname -m)" in
    x86_64|amd64) ;;
    *) echo "build.sh: only x86_64 is supported (simde_compat.h maps SSE2)" >&2
       exit 1 ;;
esac

if [ ! -f ooz/kraken.cpp ]; then
    echo "downloading ooz ${OOZ_COMMIT:0:12} from $OOZ_REPO"
    rm -rf ooz
    if command -v git >/dev/null; then
        git init -q ooz
        git -C ooz fetch -q --depth 1 "$OOZ_REPO" "$OOZ_COMMIT"
        git -C ooz checkout -q FETCH_HEAD
    else
        mkdir ooz
        curl -fsSL "$OOZ_REPO/archive/$OOZ_COMMIT.tar.gz" |
            tar xz -C ooz --strip-components=1
    fi
fi

# ooz includes simde from a submodule; use the native intrinsics instead
sed -i 's|#include <simde/x86/sse2.h>|#include "simde_compat.h"|' ooz/stdafx.h
cp simde_compat.h ooz/

g++ -O2 -fPIC -shared -w -msse2 -DOOZ_BUILD_DLL=1 -Iooz \
    ooz/kraken.cpp ooz/bitknit.cpp ooz/lzna.cpp ooz_wrap.cpp -o libooz.so
echo "built $(pwd)/libooz.so"
