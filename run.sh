#!/usr/bin/env bash
# Sets up (if needed) and launches the netemu-control web UI.
#
# Usage:
#   ./run.sh                                  # settings from config.yaml (or defaults)
#   NETEMU_CONTROL_INTERFACE=eno1 ./run.sh    # override any config key for this run
#   NETEMU_SOURCE=../6G-Testbed ./run.sh      # use a local 6G-Testbed checkout's netemu
#                                             # (editable install) instead of GitHub main
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

VENV=.venv
STAMP="$VENV/.deps-stamp"

if [ ! -d "$VENV" ]; then
  echo "Creating virtual environment in $VENV ..."
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install --upgrade pip -q
fi

# Reinstall whenever requirements.txt or the netemu source choice changes, so bumping the pinned
# 6G-Testbed commit (or switching to/from a local checkout) takes effect without deleting .venv.
WANT="$(sha256sum requirements.txt | cut -d' ' -f1) ${NETEMU_SOURCE:-pinned}"
if [ "$(cat "$STAMP" 2>/dev/null)" != "$WANT" ]; then
  echo "Installing dependencies ..."
  "$VENV/bin/pip" install -r requirements.txt -q
  if [ -n "${NETEMU_SOURCE:-}" ]; then
    echo "Using local netemu from $NETEMU_SOURCE/netemu"
    "$VENV/bin/pip" install -e "$NETEMU_SOURCE/netemu" -q
  fi
  echo "$WANT" > "$STAMP"
fi

# Track 6G-Testbed main: pip won't reinstall a branch URL on its own once something is installed,
# so compare the installed commit with main's current head and reinstall when they differ (this
# also switches back from a local NETEMU_SOURCE checkout). Offline: keep what's installed.
if [ -z "${NETEMU_SOURCE:-}" ]; then
  NETEMU_SPEC="$(grep '^netemu' requirements.txt)"
  REPO_URL="$(echo "$NETEMU_SPEC" | sed -E 's|.*git\+([^@#]+).*|\1|')"
  REMOTE=$(git ls-remote "$REPO_URL" refs/heads/main 2>/dev/null | cut -f1)
  INSTALLED=$("$VENV/bin/python" - <<'PY' 2>/dev/null
import json, importlib.metadata as m
d = m.distribution("netemu").read_text("direct_url.json") or "{}"
print(json.loads(d).get("vcs_info", {}).get("commit_id", ""))
PY
)
  if [ -z "$REMOTE" ]; then
    echo "WARNING: can't reach $REPO_URL, keeping installed netemu ${INSTALLED:0:7}"
  elif [ "$REMOTE" != "$INSTALLED" ]; then
    echo "Updating netemu to 6G-Testbed main (${REMOTE:0:7}) ..."
    "$VENV/bin/pip" install --force-reinstall --no-deps -q "$NETEMU_SPEC"
  else
    echo "netemu up to date with 6G-Testbed main (${REMOTE:0:7})"
  fi
fi

if ! sudo -n tc -V >/dev/null 2>&1; then
  TC=$(command -v tc || echo /usr/sbin/tc)
  IP=$(command -v ip || echo /usr/sbin/ip)
  MODPROBE=$(command -v modprobe || echo /usr/sbin/modprobe)
  cat <<EOF

WARNING: passwordless sudo for tc/ip/modprobe is not configured.
The UI will load, but applying/clearing impairments will fail until you run:

    sudo visudo -f /etc/sudoers.d/netemu

and add:

    $(whoami) ALL=(ALL) NOPASSWD: $TC, $IP, $MODPROBE

EOF
fi

exec "$VENV/bin/python" server.py
