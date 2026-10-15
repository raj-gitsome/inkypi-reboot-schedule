#!/bin/bash
# Removes the Reboot Schedule page and any scheduled reboot. Run with: sudo bash uninstall.sh
set -e
[ "$(id -u)" -eq 0 ] || { echo "Please run with sudo: sudo bash uninstall.sh"; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="${INKYPI_SRC:-$(readlink -f /usr/local/inkypi/src)}"

systemctl disable --now inkypi-reboot.timer 2>/dev/null || true
rm -f /etc/systemd/system/inkypi-reboot.timer /etc/systemd/system/inkypi-reboot.service
systemctl daemon-reload

python3 "$HERE/edit_core.py" "$SRC" remove
rm -f "$SRC/blueprints/reboot_schedule.py" "$SRC/templates/reboot_schedule.html" "$SRC/static/icons/reboot_schedule.png"
systemctl restart inkypi.service
echo "Reboot Schedule removed."
