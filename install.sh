#!/bin/bash
# Installs the InkyPi Reboot Schedule page. Run with: sudo bash install.sh
set -e
[ "$(id -u)" -eq 0 ] || { echo "Please run with sudo: sudo bash install.sh"; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="${INKYPI_SRC:-$(readlink -f /usr/local/inkypi/src)}"
[ -f "$SRC/inkypi.py" ] || { echo "Couldn't find InkyPi at $SRC. Set INKYPI_SRC=/path/to/InkyPi/src"; exit 1; }
OWNER="$(stat -c %U:%G "$SRC/inkypi.py")"
echo "Installing Reboot Schedule into $SRC"

python3 "$HERE/edit_core.py" "$SRC" add
for f in blueprints/reboot_schedule.py templates/reboot_schedule.html static/icons/reboot_schedule.png; do
    install -m 644 -o "${OWNER%%:*}" -g "${OWNER##*:}" "$HERE/files/$f" "$SRC/$f"
    echo "  copied $f"
done

systemctl restart inkypi.service
sleep 3
if systemctl is-active --quiet inkypi.service; then
    echo "Done. Open the InkyPi web page and click the clock icon at the top."
else
    echo "InkyPi didn't start. Check: journalctl -u inkypi -n 50"; exit 1
fi
