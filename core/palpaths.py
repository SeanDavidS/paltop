"""
palpaths.py - where paltop keeps and finds things.

Every path paltop uses comes from here, so the terminal, the web page and the
collector agree on them wherever they are installed. Each can be set with an
environment variable (install.sh writes them into the systemd units):

    PALTOP_STATE   the state directory: history database, caches, the web
                   page's certificate          (default: <paltop>/state)
    PALTOP_OODLE   libooz.so, the Kraken decompressor for the save files
                                               (default: core/oodle/libooz.so)
    PALTOP_PAK     the game's Pal-LinuxServer.pak (default: found through the
                   server container's /palworld mount)

<paltop> is the directory that holds core/, terminal/ and web/.
"""

from __future__ import annotations

import os

CORE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(CORE)

STATE_DIR = os.environ.get("PALTOP_STATE") or os.path.join(ROOT, "state")
OODLE_LIB = os.environ.get("PALTOP_OODLE") or \
    os.path.join(CORE, "oodle", "libooz.so")

PAK_IN_DATA = os.path.join("Pal", "Content", "Paks", "Pal-LinuxServer.pak")
_pak = os.environ.get("PALTOP_PAK") or None


def set_data_dir(data_dir):
    """The server's data directory (the container's /palworld mount): the
    pak is found in it unless PALTOP_PAK says otherwise."""
    global _pak
    if data_dir and not os.environ.get("PALTOP_PAK"):
        _pak = os.path.join(data_dir, PAK_IN_DATA)


def pak():
    """The game's pak file, or None while the server is not found yet."""
    return _pak
