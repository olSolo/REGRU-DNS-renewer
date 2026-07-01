#!/usr/bin/env bash
# Generate and install systemd units for dns-renewer.
set -euo pipefail

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_USER="${SUDO_USER:-$USER}"
HOME_DIR="$(getent passwd "$RUN_USER" | cut -d: -f6)"

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo: sudo $0" >&2
  exit 1
fi

if [[ ! -f "$INSTALL_DIR/config.json" ]]; then
  echo "Missing $INSTALL_DIR/config.json — copy from config.json.example first." >&2
  exit 1
fi

sed \
  -e "s|@INSTALL_DIR@|$INSTALL_DIR|g" \
  -e "s|@USER@|$RUN_USER|g" \
  -e "s|@HOME_DIR@|$HOME_DIR|g" \
  "$INSTALL_DIR/systemd/dns-renewer.service.example" \
  > "$INSTALL_DIR/systemd/dns-renewer.service"

install -m 644 "$INSTALL_DIR/systemd/dns-renewer.service" /etc/systemd/system/dns-renewer.service
install -m 644 "$INSTALL_DIR/systemd/dns-renewer.timer" /etc/systemd/system/dns-renewer.timer

systemctl daemon-reload
systemctl enable dns-renewer.timer
systemctl restart dns-renewer.timer

echo "Installed for user=$RUN_USER dir=$INSTALL_DIR"
systemctl status dns-renewer.timer --no-pager | head -8
