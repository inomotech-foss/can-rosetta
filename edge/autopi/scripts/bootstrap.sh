#!/usr/bin/env bash
# CAN-Rosetta edge — one-time bootstrap for an AutoPi (or any SocketCAN Linux box).
#
# Run this ONCE (SSH into the AutoPi, paste the one-liner from docs/provisioning.md).
# It installs the control service and enables it as a restart-always systemd unit.
# After this, the phone provisions and updates everything over the local link —
# `POST /api/update` self-updates and the service relaunches into the new code.
#
#   sudo ./bootstrap.sh [REF]
#
# REF is the git ref/tag to install (default: the latest edge-v* release, else main).
# Installs ONLY the official package over HTTPS; see SAFETY.md.
set -euo pipefail

REPO="inomotech-foss/can-rosetta"
REF="${1:-main}"
PREFIX="canrosetta-edge[control] @ git+https://github.com/${REPO}@${REF}#subdirectory=edge/autopi"
CFG_DIR="/etc/canrosetta"
CFG="${CFG_DIR}/config.yaml"
UNIT="/etc/systemd/system/canrosetta-edge.service"
PORT="${CANROSETTA_PORT:-8765}"

echo "==> Installing canrosetta-edge ($REF) from $REPO"
python3 -m pip install --upgrade "pip"
python3 -m pip install --upgrade "$PREFIX"
CANBIN="$(command -v canrosetta-edge)"

mkdir -p "$CFG_DIR"
if [ ! -f "$CFG" ]; then
  TOKEN="$(python3 -c 'import secrets;print(secrets.token_hex(16))')"
  cat > "$CFG" <<YAML
transport: socketcan
channel: can0
bitrate: 500000
control_host: 0.0.0.0
control_port: ${PORT}
control_token: "${TOKEN}"
output_dir: /data/canrosetta/sessions
update_repo: ${REPO}
allow_remote_update: true
YAML
  echo "==> Wrote $CFG (generated a control_token)"
else
  echo "==> Keeping existing $CFG"
fi
mkdir -p /data/canrosetta/sessions

# The service listens on the config's control_port, which on a re-run with a
# kept config may differ from $PORT (this run's env/default) — read it back so
# the firewall permit below always matches what serve actually binds.
CFG_PORT="$(awk '$1 == "control_port:" {print $2; exit}' "$CFG" 2>/dev/null || true)"
PORT="${CFG_PORT:-$PORT}"

cat > "$UNIT" <<UNITEOF
[Unit]
Description=CAN-Rosetta edge control service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
# The AutoPi hotspot firewall default-drops INPUT on uap0 except 22/53/67/80,
# so the phone can't reach the control port without this permit. Idempotent
# (-C checks before -I inserts), guarded on iptables existing, and the '-'
# prefix means a failure never blocks the service from starting.
ExecStartPre=-/bin/sh -c 'command -v iptables >/dev/null && { iptables -C INPUT -i uap0 -p tcp --dport ${PORT} -j ACCEPT || iptables -I INPUT -i uap0 -p tcp --dport ${PORT} -j ACCEPT; }'
ExecStart=${CANBIN} serve --config ${CFG}
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
UNITEOF

systemctl daemon-reload
systemctl enable --now canrosetta-edge.service

echo
echo "==> canrosetta-edge is running on port ${PORT}. Pair the phone with this"
echo "    (scan the QR straight off this SSH terminal, or type Host + Token):"
echo
"$CANBIN" --config "$CFG" pairing || true
echo
echo "    From now on, update from the phone (Settings → Update AutoPi) — no SSH needed."
echo
echo "    NOTE: the unit opens TCP ${PORT} on uap0 at each start (the AutoPi hotspot"
echo "    firewall default-drops other inbound ports). For a permit that survives"
echo "    AutoPi Cloud config re-syncs, add the same rule in AutoPi Cloud >"
echo "    Advanced Settings (firewall permits)."
