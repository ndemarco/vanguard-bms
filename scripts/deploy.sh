#!/usr/bin/env bash
# Deploy this working tree to the Pi install (/opt/vanguard-bridge) and restart the services.
#
#   scripts/deploy.sh            # app + bridge code, bridge.toml, restart everything
#   scripts/deploy.sh --app-only # app code only, restart only the web app
#
# Refuses while the bridge reports RUNNING: restarting the bridge or the app ends the session
# and the pack opens its contactors. Stop the session in the page first.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
OPT=/opt/vanguard-bridge
APP_ONLY=0
[[ "${1:-}" == "--app-only" ]] && APP_ONLY=1

state=$(curl -s --max-time 3 http://127.0.0.1:8000/api/state \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); l=d.get("status_lines") or []; print(next((s["text"] for s in reversed(l) if s["text"].startswith("STATE")), "unknown"))' \
  2>/dev/null || echo "app not reachable")
echo "bridge session: $state"
if [[ "$state" == *RUNNING* ]]; then
  echo "Refusing: a pack session is RUNNING. Press Stop in the page, then run this again." >&2
  exit 1
fi

echo "copying $REPO -> $OPT/src"
sudo rsync -a --delete --exclude .git --exclude .venv --exclude __pycache__ --exclude '*.egg-info' \
  "$REPO/" "$OPT/src/"
echo "installing packages into $OPT/venv"
sudo "$OPT/venv/bin/pip" install -q --force-reinstall --no-deps "$OPT/src/app" "$OPT/src/bridge"

if (( APP_ONLY )); then
  sudo systemctl restart vanguard-app
else
  sudo install -m 0644 "$REPO/bridge/system/bridge.toml" /etc/vanguard-bridge/bridge.toml
  sudo systemctl stop vanguard-app vanguard-bridge
  sudo systemctl restart vanguard-can-setup
  sudo systemctl start vanguard-bridge
  sleep 2
  sudo systemctl start vanguard-app
fi
sleep 2
systemctl is-active vanguard-can-setup vanguard-bridge vanguard-app | paste -sd' '
journalctl -u vanguard-bridge -n 4 --no-pager | tail -n 3
echo "done: reload http://127.0.0.1:8000/"
