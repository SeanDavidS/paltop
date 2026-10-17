#!/usr/bin/env bash
# install.sh - set up paltop on the machine that runs the Palworld server.
#
#   ./install.sh --terminal          the terminal dashboard (paltop)
#   ./install.sh --web               the web page for the players
#   ./install.sh --terminal --web    both (the same as --all)
#   ./install.sh --uninstall         remove the services and the command
#
# Options:
#   --container NAME   the server's docker container (default palworld-server)
#   --port PORT        the web page's port (default 8765)
#   --no-collector     terminal only: no background collector (history is
#                      then only recorded while paltop is open)
#   --no-oodle         do not build the save-file decompressor (the Pals, bases
#                      and Palpedia need it)
#
# paltop runs from this directory: install.sh builds what needs building,
# puts `paltop` in ~/.local/bin and sets up systemd user services. It does
# not touch the Palworld server or its container. Everything is per user; no
# root is needed, except that your user must be allowed to run `docker`.
set -euo pipefail

HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
BIN="${XDG_BIN_HOME:-$HOME/.local/bin}"
UNITS="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
STATE="${PALTOP_STATE:-$HERE/state}"

want_terminal=0 want_web=0 collector=1 oodle=1 uninstall=0
container=palworld-server port=8765

usage() { sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while [ $# -gt 0 ]; do
    case "$1" in
        --terminal) want_terminal=1 ;;
        --web) want_web=1 ;;
        --all) want_terminal=1; want_web=1 ;;
        --container) container="$2"; shift ;;
        --port) port="$2"; shift ;;
        --no-collector) collector=0 ;;
        --no-oodle) oodle=0 ;;
        --uninstall) uninstall=1 ;;
        -h|--help) usage ;;
        *) echo "install.sh: unknown option $1" >&2; usage 2 ;;
    esac
    shift
done

say() { printf '\033[1m%s\033[0m\n' "$*"; }
warn() { printf '\033[33mwarning:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

if [ "$uninstall" = 1 ]; then
    for svc in paltop-web paltop-collector; do
        if [ -f "$UNITS/$svc.service" ]; then
            systemctl --user disable --now "$svc" 2>/dev/null || true
            rm -f "$UNITS/$svc.service"
            say "removed $svc"
        fi
    done
    systemctl --user daemon-reload 2>/dev/null || true
    if [ "$(readlink -f "$BIN/paltop" 2>/dev/null)" = "$HERE/terminal/paltop" ]; then
        rm -f "$BIN/paltop"
        say "removed $BIN/paltop"
    fi
    say "the history and settings in $STATE are kept; delete it by hand if you want"
    exit 0
fi

[ "$want_terminal$want_web" = 00 ] && usage 2
[ -d "$HERE/core" ] || die "core/ is missing: download a complete release"
[ "$want_terminal" = 0 ] || [ -d "$HERE/terminal" ] ||
    die "terminal/ is missing: this download does not have the terminal"
[ "$want_web" = 0 ] || [ -d "$HERE/web" ] ||
    die "web/ is missing: this download does not have the web page"
[ "$want_web" = 1 ] && collector=1        # the web page reads its snapshot

# -- what paltop needs -------------------------------------------------------
have python3 || die "python3 is needed"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' ||
    die "python 3.9 or newer is needed"
have docker || die "docker is needed (paltop reads the server's container)"
docker inspect "$container" >/dev/null 2>&1 ||
    warn "cannot inspect container '$container' - is the name right, and may your user run docker?"
[ "$want_web" = 0 ] || have openssl ||
    warn "openssl is missing: the web page cannot make its HTTPS certificate"

# -- the save-file decompressor ----------------------------------------------
if [ "$oodle" = 1 ]; then
    if [ -f "$HERE/core/oodle/libooz.so" ]; then
        say "libooz.so is already built"
    elif have g++ && { have git || have curl; }; then
        say "building libooz.so (downloads ooz, see core/oodle/build.sh)"
        "$HERE/core/oodle/build.sh"
    else
        warn "g++ and git (or curl) are needed to build libooz.so; without it paltop cannot read the save files"
    fi
fi

mkdir -p "$STATE"
chmod 700 "$STATE"

# -- the command -------------------------------------------------------------
if [ "$want_terminal" = 1 ]; then
    mkdir -p "$BIN"
    ln -sfn "$HERE/terminal/paltop" "$BIN/paltop"
    say "installed $BIN/paltop"
    case ":$PATH:" in *":$BIN:"*) ;; *) warn "$BIN is not in your PATH" ;; esac
fi

# -- services ----------------------------------------------------------------
unit() {       # unit NAME DESCRIPTION EXECSTART [EXTRA]
    mkdir -p "$UNITS"
    cat > "$UNITS/$1.service" <<EOF
[Unit]
Description=$2
Documentation=file://$HERE/README.md
After=network-online.target

[Service]
ExecStart=$3
WorkingDirectory=$HERE
Environment=PALTOP_CONTAINER=$container
Environment=PALTOP_STATE=$STATE
Restart=always
RestartSec=15
Nice=10
${4:-}

[Install]
WantedBy=default.target
EOF
}

if have systemctl && systemctl --user show-environment >/dev/null 2>&1; then
    if [ "$collector" = 1 ]; then
        unit paltop-collector "paltop background collector (Palworld server history)" \
            "$(command -v python3) $HERE/core/palcollect.py" \
            "IOSchedulingClass=idle
LogRateLimitIntervalSec=60
LogRateLimitBurst=30"
        systemctl --user daemon-reload
        systemctl --user enable --now paltop-collector >/dev/null
        systemctl --user restart paltop-collector
        say "started paltop-collector"
    fi
    if [ "$want_web" = 1 ]; then
        unit paltop-web "paltop web - read-only Palworld server page for the players" \
            "$(command -v python3) $HERE/web/palweb.py --port $port -c $container" \
            "NoNewPrivileges=yes"
        systemctl --user daemon-reload
        systemctl --user enable --now paltop-web >/dev/null
        systemctl --user restart paltop-web
        say "started paltop-web on https://$(hostname -I 2>/dev/null | awk '{print $1}'):$port"
    fi
    if have loginctl && [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != yes ]; then
        warn "the services stop when you log out; to keep them running: sudo loginctl enable-linger $USER"
    fi
else
    warn "no systemd user session: start the collector yourself with 'python3 $HERE/core/palcollect.py'"
    [ "$want_web" = 0 ] || warn "... and the web page with 'python3 $HERE/web/palweb.py'"
fi

say "done"
