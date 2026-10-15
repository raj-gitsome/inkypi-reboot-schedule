"""Adds (or removes) the Reboot Schedule hooks in InkyPi's inkypi.py and inky.html.
Safe to run more than once."""
import sys

src, mode = sys.argv[1], sys.argv[2]   # mode: add | remove

IMPORT = "from blueprints.reboot_schedule import reboot_schedule_bp\n"
REGISTER = "app.register_blueprint(reboot_schedule_bp)\n"
ICON = """            <a href="{{ url_for('reboot_schedule.reboot_schedule_page') }}" class="settings-button">
                <img src="{{ url_for('static', filename='icons/reboot_schedule.png') }}" title="Reboot Schedule" alt="reboot schedule icon" class="icon-image">
            </a>
"""

def edit(path, add_fn, remove_fn):
    with open(path) as f:
        text = f.read()
    new = add_fn(text) if mode == "add" else remove_fn(text)
    if new != text:
        with open(path, "w") as f:
            f.write(new)
        print(f"  updated {path}")
    else:
        print(f"  {path} already up to date")

def insert_after(text, anchor, line):
    if line in text:
        return text
    if anchor not in text:
        sys.exit(f"Could not find '{anchor.strip()}' - this InkyPi version isn't supported by the installer.")
    return text.replace(anchor, anchor + line, 1)

def add_py(t):
    t = insert_after(t, "from blueprints.playlist import playlist_bp\n", IMPORT)
    return insert_after(t, "app.register_blueprint(playlist_bp)\n", REGISTER)

def add_html(t):
    if ICON in t:
        return t
    # Put the icon just before the first existing header icon link.
    for anchor in ('            <a href="{{ url_for(\'apikeys.apikeys_page\') }}"',
                   '            <a href="{{ url_for(\'playlist.playlists\') }}"'):
        if anchor in t:
            return t.replace(anchor, ICON + anchor, 1)
    sys.exit("Could not find the header icons in inky.html - this InkyPi version isn't supported by the installer.")

edit(f"{src}/inkypi.py", add_py, lambda t: t.replace(IMPORT, "").replace(REGISTER, ""))
edit(f"{src}/templates/inky.html", add_html, lambda t: t.replace(ICON, ""))
