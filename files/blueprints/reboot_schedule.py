"""Scheduled reboots for InkyPi.

The schedule is saved in device.json under "reboot_schedule" and carried out
by a systemd timer (inkypi-reboot.timer), so reboots still happen if the
InkyPi app itself has hung. The InkyPi service runs as root, which lets it
write the unit files and manage the timer.
"""

import logging
import os
import re
import shutil
import subprocess
from datetime import datetime

import psutil
import pytz
from flask import Blueprint, current_app, jsonify, render_template, request

logger = logging.getLogger(__name__)
reboot_schedule_bp = Blueprint("reboot_schedule", __name__)

CONFIG_KEY = "reboot_schedule"
SYSTEMD_DIR = "/etc/systemd/system"
UNIT_NAME = "inkypi-reboot"
SYSTEMCTL = shutil.which("systemctl") or "/bin/systemctl"
SYSTEMD_ANALYZE = shutil.which("systemd-analyze") or "/usr/bin/systemd-analyze"
MIN_UPTIME_SECONDS = 600  # never reboot within 10 minutes of booting (guards against reboot loops)

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DEFAULT_SCHEDULE = {"enabled": False, "frequency": "weekly", "days": ["Sun"], "day_of_month": 1, "time": "03:00"}

SERVICE_UNIT = """\
# Managed by InkyPi (Reboot Schedule page). Changes here will be overwritten.
[Unit]
Description=InkyPi scheduled reboot

[Service]
Type=oneshot
ExecStart=/bin/sh -c 'up=$$(cut -d. -f1 /proc/uptime); if [ "$$up" -lt {min_uptime} ]; then echo "Skipping scheduled reboot: system has only been up $${{up}}s"; exit 0; fi; echo "InkyPi scheduled reboot"; exec {systemctl} reboot'
"""

TIMER_UNIT = """\
# Managed by InkyPi (Reboot Schedule page). Changes here will be overwritten.
[Unit]
Description=InkyPi reboot schedule

[Timer]
OnCalendar={on_calendar}
# Don't catch up on a missed reboot after the Pi was off.
Persistent=false
AccuracySec=1min

[Install]
WantedBy=timers.target
"""


class ScheduleError(Exception):
    pass


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

@reboot_schedule_bp.route("/reboot-schedule")
def reboot_schedule_page():
    device_config = current_app.config["DEVICE_CONFIG"]
    schedule = get_schedule(device_config)
    return render_template(
        "reboot_schedule.html",
        schedule=schedule,
        weekdays=WEEKDAYS,
        timezone=device_timezone(device_config),
        time_format=device_config.get_config("time_format", default="12h"),
        status=get_status(device_config, schedule),
    )


@reboot_schedule_bp.route("/reboot-schedule/save", methods=["POST"])
def save_reboot_schedule():
    device_config = current_app.config["DEVICE_CONFIG"]
    data = request.get_json(silent=True) or {}
    try:
        try:
            schedule = validate_schedule(data)
        except ScheduleError:
            # Turning reboots off must always work, even if the other fields are incomplete.
            if data.get("enabled") in (True, "true", "on", 1):
                raise
            schedule = {**get_schedule(device_config), "enabled": False}
        if schedule["enabled"]:
            on_calendar = build_on_calendar(schedule, device_timezone(device_config))
            install_timer(on_calendar)
            message = "Reboot schedule saved."
        else:
            remove_timer()
            message = "Scheduled reboots are turned off."
        device_config.update_value(CONFIG_KEY, schedule, write=True)
        logger.info(f"Reboot schedule updated: {schedule}")
    except ScheduleError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.exception("Failed to update reboot schedule")
        return jsonify({"error": f"Couldn't update the reboot schedule: {e}"}), 500

    return jsonify({"success": True, "message": message, "status": get_status(device_config, schedule)})


# --------------------------------------------------------------------------
# Schedule handling
# --------------------------------------------------------------------------

def get_schedule(device_config):
    saved = device_config.get_config(CONFIG_KEY, default={}) or {}
    return {**DEFAULT_SCHEDULE, **saved}


def device_timezone(device_config):
    name = device_config.get_config("timezone", default="America/New_York") or "UTC"
    return name if name in pytz.all_timezones_set else "UTC"


def validate_schedule(data):
    enabled = data.get("enabled") in (True, "true", "on", 1)
    frequency = data.get("frequency", "weekly")
    if frequency not in ("daily", "weekly", "monthly"):
        raise ScheduleError("Choose how often to reboot.")

    time_str = str(data.get("time", "")).strip()
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", time_str):
        raise ScheduleError("Enter a valid time.")

    days = [d for d in WEEKDAYS if d in (data.get("days") or [])]
    if frequency == "weekly" and not days:
        raise ScheduleError("Pick at least one day of the week.")

    try:
        day_of_month = int(data.get("day_of_month", 1))
    except (TypeError, ValueError):
        raise ScheduleError("Enter a day of the month between 1 and 28.")
    if not 1 <= day_of_month <= 28:
        raise ScheduleError("Enter a day of the month between 1 and 28.")

    return {"enabled": enabled, "frequency": frequency, "days": days or ["Sun"],
            "day_of_month": day_of_month, "time": time_str}


def build_on_calendar(schedule, timezone):
    hh_mm = f"{schedule['time']}:00"
    if schedule["frequency"] == "daily" or (schedule["frequency"] == "weekly" and len(schedule["days"]) == 7):
        expr = f"*-*-* {hh_mm}"
    elif schedule["frequency"] == "weekly":
        expr = f"{','.join(schedule['days'])} *-*-* {hh_mm}"
    else:
        expr = f"*-*-{schedule['day_of_month']:02d} {hh_mm}"
    return f"{expr} {system_timezone_name(timezone)}"


# Old alias names (US/Eastern etc.) that newer Debian/Raspberry Pi OS only ships in the
# optional tzdata-legacy package, mapped to the current name of the same zone.
TZ_ALIASES = {
    "US/Eastern": "America/New_York", "US/Central": "America/Chicago",
    "US/Mountain": "America/Denver", "US/Pacific": "America/Los_Angeles",
    "US/Alaska": "America/Anchorage", "US/Hawaii": "Pacific/Honolulu",
    "US/Arizona": "America/Phoenix", "US/East-Indiana": "America/Indiana/Indianapolis",
    "US/Michigan": "America/Detroit", "US/Aleutian": "America/Adak",
    "US/Samoa": "Pacific/Pago_Pago", "Canada/Eastern": "America/Toronto",
    "Canada/Central": "America/Winnipeg", "Canada/Mountain": "America/Edmonton",
    "Canada/Pacific": "America/Vancouver", "Canada/Atlantic": "America/Halifax",
    "Canada/Newfoundland": "America/St_Johns", "Canada/Saskatchewan": "America/Regina",
    "Brazil/East": "America/Sao_Paulo", "Mexico/General": "America/Mexico_City",
    "Chile/Continental": "America/Santiago", "GB": "Europe/London",
    "Australia/NSW": "Australia/Sydney", "Australia/Victoria": "Australia/Melbourne",
    "Australia/Queensland": "Australia/Brisbane", "Australia/West": "Australia/Perth",
    "Asia/Calcutta": "Asia/Kolkata", "Asia/Saigon": "Asia/Ho_Chi_Minh",
    "Asia/Katmandu": "Asia/Kathmandu", "Asia/Rangoon": "Asia/Yangon",
    "Europe/Kiev": "Europe/Kyiv", "Japan": "Asia/Tokyo", "Singapore": "Asia/Singapore",
}
ZONEINFO = "/usr/share/zoneinfo"


def system_timezone_name(name):
    """Return a name for `name`'s time zone that this system's systemd accepts."""
    if os.path.isfile(os.path.join(ZONEINFO, name)):
        return name
    alias = TZ_ALIASES.get(name)
    if alias and os.path.isfile(os.path.join(ZONEINFO, alias)):
        return alias
    match = find_equivalent_zone(name)
    if match:
        return match
    raise ScheduleError(f"This Pi doesn't recognize the time zone '{name}'. "
                        "Pick a city-based time zone (e.g. America/New_York) in Settings, then save again.")


def find_equivalent_zone(name):
    """Find a zone installed on this system with the same rules as `name` (per pytz)."""
    try:
        target = pytz.timezone(name)
    except pytz.UnknownTimeZoneError:
        return None
    candidates = []
    for table in ("zone1970.tab", "zone.tab"):
        try:
            with open(os.path.join(ZONEINFO, table)) as f:
                candidates += [line.split("\t")[2].strip() for line in f
                               if line.strip() and not line.startswith("#")]
        except (OSError, IndexError):
            continue
    probes = [datetime(y, m, 15, 12) for y in range(2024, 2036) for m in range(1, 13)]

    def offsets(tz):
        return [tz.utcoffset(p, is_dst=False) for p in probes]

    want = offsets(target)
    for cand in dict.fromkeys(candidates):
        if not os.path.isfile(os.path.join(ZONEINFO, cand)):
            continue
        try:
            tz = pytz.timezone(cand)
        except pytz.UnknownTimeZoneError:
            continue
        if offsets(tz) == want and getattr(tz, "_utc_transition_times", None) is not None:
            # also require the same DST switch moments in recent years
            recent = lambda z: [t for t in getattr(z, "_utc_transition_times", []) if t.year >= 2024]
            if recent(tz) == recent(target):
                return cand
    return None


def describe(schedule, twelve_hour=True):
    t = schedule["time"]
    if twelve_hour:
        t = datetime.strptime(t, "%H:%M").strftime("%-I:%M %p")
    if schedule["frequency"] == "daily" or len(schedule["days"]) == 7:
        return f"Every day at {t}"
    if schedule["frequency"] == "weekly":
        return f"Every {', '.join(schedule['days'])} at {t}"
    return f"On day {schedule['day_of_month']} of each month at {t}"


# --------------------------------------------------------------------------
# systemd
# --------------------------------------------------------------------------

def run(cmd, check=True):
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        raise ScheduleError(f"{os.path.basename(cmd[0])} isn't available on this system.")
    except subprocess.TimeoutExpired:
        raise ScheduleError(f"Timed out running {os.path.basename(cmd[0])}.")
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise ScheduleError(detail[-1] if detail else f"{os.path.basename(cmd[0])} failed.")
    return result


def unit_path(suffix):
    return os.path.join(SYSTEMD_DIR, f"{UNIT_NAME}.{suffix}")


def install_timer(on_calendar):
    # Check the expression before touching anything on disk.
    run([SYSTEMD_ANALYZE, "calendar", on_calendar])

    service = SERVICE_UNIT.format(min_uptime=MIN_UPTIME_SECONDS, systemctl=SYSTEMCTL)
    timer = TIMER_UNIT.format(on_calendar=on_calendar)
    try:
        for suffix, content in (("service", service), ("timer", timer)):
            tmp = unit_path(suffix) + ".tmp"
            with open(tmp, "w") as f:
                f.write(content)
            os.replace(tmp, unit_path(suffix))
    except PermissionError:
        raise ScheduleError("InkyPi doesn't have permission to set up the reboot timer (it must run as root).")

    run([SYSTEMCTL, "daemon-reload"])
    run([SYSTEMCTL, "enable", f"{UNIT_NAME}.timer"])
    # restart so a changed OnCalendar takes effect immediately
    run([SYSTEMCTL, "restart", f"{UNIT_NAME}.timer"])


def remove_timer():
    if not os.path.exists(unit_path("timer")):
        return
    run([SYSTEMCTL, "disable", "--now", f"{UNIT_NAME}.timer"], check=False)
    for suffix in ("timer", "service"):
        try:
            os.remove(unit_path(suffix))
        except FileNotFoundError:
            pass
    run([SYSTEMCTL, "daemon-reload"], check=False)


def get_status(device_config, schedule):
    tz = pytz.timezone(device_timezone(device_config))
    twelve_hour = device_config.get_config("time_format", default="12h") == "12h"
    status = {
        "summary": describe(schedule, twelve_hour) if schedule["enabled"] else "Off",
        "timer_active": False,
        "next_reboot": None,
        "running_since": format_time(datetime.fromtimestamp(psutil.boot_time(), tz), twelve_hour),
        "problem": None,
    }
    if not schedule["enabled"]:
        return status

    try:
        active = run([SYSTEMCTL, "is-active", f"{UNIT_NAME}.timer"], check=False).stdout.strip()
        status["timer_active"] = active == "active"
        out = run([SYSTEMCTL, "show", f"{UNIT_NAME}.timer", "-P", "NextElapseUSecRealtime",
                   "--timestamp=unix"], check=False).stdout.strip()
        m = re.fullmatch(r"@(\d+)", out)
        if m:
            status["next_reboot"] = format_time(datetime.fromtimestamp(int(m.group(1)), tz), twelve_hour)
        else:
            # systemd older than 248 (Raspberry Pi OS Bullseye) has no --timestamp option
            raw = run([SYSTEMCTL, "show", f"{UNIT_NAME}.timer", "-p", "NextElapseUSecRealtime", "--value"],
                      check=False).stdout.strip()
            if raw and raw not in ("n/a", "0"):
                status["next_reboot"] = raw
    except ScheduleError as e:
        status["problem"] = str(e)
        return status

    if not status["timer_active"]:
        status["problem"] = "The reboot timer isn't running. Click Save to set it up again."
    return status


def format_time(dt, twelve_hour):
    return dt.strftime("%a %b %-d, %-I:%M %p %Z" if twelve_hour else "%a %b %-d, %H:%M %Z")
