"""Install a NEXO export in the current Debian XFCE user's account."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
HOME = Path.home()
DATA = Path(os.environ.get("XDG_DATA_HOME") or HOME / ".local/share")
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config")
BASE = DATA / "nexo-builder"
ACTIVE = BASE / "active"
STATE = BASE / "original.json"
AUTOSTART = CONFIG / "autostart"
WIDGET_ENTRY = AUTOSTART / "nexo-builder.desktop"
ALLOWED = {
    ("xfce4-desktop", "/desktop-icons/style"): "int",
    ("xfwm4", "/general/theme"): "string",
    ("xsettings", "/Net/ThemeName"): "string",
    ("xsettings", "/Net/IconThemeName"): "string",
    ("xsettings", "/Gtk/CursorThemeName"): "string",
    ("xsettings", "/Gtk/FontName"): "string",
}
WALL = re.compile(r"^/backdrop/screen\d+/monitor[^/]+/(?:workspace\d+/)?last-image$")
ASSET = re.compile(r"[A-Fa-f0-9-]{36}\.(png|jpe?g|webp)")
DESKTOP = re.compile(r"[a-zA-Z0-9_][a-zA-Z0-9_.-]*\.desktop")
PACKAGE = re.compile(r"[a-z0-9][a-z0-9+.-]+")


def run(*args, check=True):
    return subprocess.run(args, check=check, capture_output=True, text=True)


def fail(message):
    raise RuntimeError(message)


def check_system(project, needs_runtime):
    if os.geteuid() == 0:
        fail("Run this installer as the regular XFCE user.")
    os_release = Path("/etc/os-release").read_text()
    if not re.search(r'^ID="?debian"?$', os_release, re.M) or not re.search(
        r'^VERSION_ID="?13"?$', os_release, re.M
    ):
        fail("This export requires Debian 13 (trixie).")
    desktop = ":".join(os.environ.get(k, "") for k in ("XDG_CURRENT_DESKTOP", "DESKTOP_SESSION"))
    if "xfce" not in desktop.lower() or not os.environ.get("DISPLAY") or os.environ.get("XDG_SESSION_TYPE") == "wayland":
        fail("Run this from a terminal inside your XFCE X11 session.")
    if run("dpkg", "--print-architecture").stdout.strip() != project["architecture"]:
        fail("This project was exported for " + project["architecture"] + ".")
    if not shutil.which("xfconf-query"):
        fail("Install xfconf with APT first.")
    run("xfconf-query", "-l")
    if needs_runtime:
        result = run("/usr/bin/python3", "-c",
                     "import gi; gi.require_version('Gtk','3.0'); from gi.repository import Gtk", check=False)
        if result.returncode or not shutil.which("xdg-open"):
            fail("Install python3-gi gir1.2-gtk-3.0 xdg-utils with APT first.")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as writer:
        temporary = Path(writer.name)
        json.dump(value, writer, indent=2)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def xf_current(record):
    result = run("xfconf-query", "-c", record["channel"], "-p", record["property"], check=False)
    if result.returncode == 0:
        return {**record, "value": result.stdout.removesuffix("\n")}
    channel = run("xfconf-query", "-c", record["channel"], "-l", check=False)
    if channel.returncode == 0 and record["property"] in channel.stdout.splitlines():
        fail("Could not read XFCE setting " + record["property"])
    return {**record, "value": None}


def xf_apply(record):
    channel, prop, value = (record[key] for key in ("channel", "property", "value"))
    expected_type = ALLOWED.get((channel, prop))
    if channel == "xfce4-desktop" and WALL.fullmatch(prop):
        expected_type = "string"
    if expected_type != record["type"]:
        fail("Unexpected XFCE setting " + prop)
    args = ("xfconf-query", "-c", channel, "-p", prop)
    if value is None:
        if xf_current(record)["value"] is not None:
            run(*args, "-r")
    else:
        if xf_current(record)["value"] is None:
            run(*args, "-n", "-t", expected_type, "-s", value)
        else:
            run(*args, "-s", value)


def settings(project):
    records = read_json(ROOT / "configs/xfce.json")
    for record in records:
        if ALLOWED.get((record["channel"], record["property"])) != record["type"]:
            fail("Export contains an unsupported XFCE setting.")
    wallpaper = project["appearance"]["wallpaper"] or "background.png"
    if wallpaper:
        if wallpaper != "background.png" and not ASSET.fullmatch(wallpaper):
            fail("Invalid wallpaper path.")
        props = run("xfconf-query", "-c", "xfce4-desktop", "-l").stdout.splitlines()
        matches = [prop for prop in props if WALL.fullmatch(prop)]
        if not matches:
            fail("XFCE has no wallpaper properties. Set any wallpaper in XFCE once and retry.")
        records += [dict(channel="xfce4-desktop", property=prop, type="string",
                         value=str(ACTIVE / "assets" / wallpaper)) for prop in matches]
    return records


def desktop_quote(value):
    if any(char in value for char in ("\n", "\r")):
        fail("Autostart paths cannot contain a line break.")
    value = value.replace("%", "%%")
    for char in ('\\', '"', '`', '$'):
        value = value.replace(char, '\\' + char)
    return '"' + value.replace('\\', '\\\\') + '"'


def owned_autostart():
    if not WIDGET_ENTRY.exists():
        return False
    if WIDGET_ENTRY.is_symlink() or "X-NEXO-Builder=true" not in WIDGET_ENTRY.read_text(encoding="utf-8").splitlines():
        fail("An unrelated application already uses " + str(WIDGET_ENTRY))
    return True


def install_autostart():
    AUTOSTART.mkdir(parents=True, exist_ok=True)
    owned_autostart()
    entry = ("[Desktop Entry]\nType=Application\nName=NEXO Desktop\nOnlyShowIn=XFCE;\n"
             "X-NEXO-Builder=true\nTerminal=false\nExec=/usr/bin/python3 "
             + desktop_quote(str(ACTIVE / "runtime" / "desktop.py")) + "\n")
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=AUTOSTART, delete=False) as writer:
        writer.write(entry)
        temporary = Path(writer.name)
    try:
        temporary.replace(WIDGET_ENTRY)
    finally:
        temporary.unlink(missing_ok=True)


def stage(project, stage_dir):
    for subdir in ("runtime", "assets"):
        (stage_dir / subdir).mkdir()
    shutil.copyfile(ROOT / "runtime/desktop.py", stage_dir / "runtime/desktop.py")
    if not project["appearance"]["wallpaper"]:
        shutil.copyfile(ROOT / "configs/background.png", stage_dir / "assets" / "background.png")
    for name in {project["logo"], project["appearance"]["wallpaper"], *(item["asset"] for item in project["items"])} - {""}:
        path = ROOT / "assets" / name
        if not ASSET.fullmatch(name) or path.is_symlink() or not path.is_file():
            fail("An exported asset is missing or invalid.")
        shutil.copyfile(path, stage_dir / "assets" / name)
    shutil.copyfile(ROOT / "project.nexo.json", stage_dir / "project.nexo.json")


def manage(args):
    project = read_json(ROOT / "project.nexo.json")
    if project.get("schema") != 1 or project.get("debian") != 13:
        fail("Unsupported NEXO project format.")
    removing = args.action == "uninstall"
    needs_runtime = not removing and (any(item["visible"] for item in project["items"]) or bool(project["startup"].split()))
    check_system(project, needs_runtime)
    if args.dry_run and not BASE.exists():
        if args.action == "update":
            fail("Install this project before updating it.")
        if removing:
            print("This project is already uninstalled.")
        else:
            describe(args, project, settings(project), needs_runtime)
        return
    BASE.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (BASE / "lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        baseline = read_json(STATE) if STATE.is_file() else None
        if baseline is not None and baseline["project"] != project["id"]:
            fail("A different NEXO project is installed. Uninstall it using its original export first.")
        if args.action == "update" and not baseline:
            fail("Install this project before updating it.")
        if removing and not baseline:
            print("This project is already uninstalled.")
            return
        if ACTIVE.exists() and not baseline:
            fail("There is already an unmanaged active installation directory.")
        desired = [] if removing else settings(project)
        describe(args, project, desired, needs_runtime)
        if args.dry_run:
            return
        if not args.yes and input("Continue? [y/N] ").strip().lower() not in ("yes", "y"):
            print("Cancelled.")
            return
        if not removing:
            verify_packages(project)
        had_entry = owned_autostart()
        original = dict(project=project["id"], settings=[])
        if baseline:
            original["settings"] = list(baseline["settings"])
        keys = {(entry["channel"], entry["property"]) for entry in original["settings"]}
        original["settings"] += [xf_current(entry) for entry in desired
                                 if (entry["channel"], entry["property"]) not in keys]
        targets = {(entry["channel"], entry["property"]): entry for entry in original["settings"]}
        targets.update({(entry["channel"], entry["property"]): entry for entry in desired})
        before = [xf_current(entry) for entry in targets.values()]
        with tempfile.TemporaryDirectory(prefix=".install-", dir=BASE) as temporary:
            temp = Path(temporary)
            next_dir, previous = temp / "next", temp / "previous"
            if not removing:
                next_dir.mkdir()
                stage(project, next_dir)
            if had_entry:
                shutil.copyfile(WIDGET_ENTRY, temp / "prior.desktop")
            moved_old = moved_new = False
            try:
                if ACTIVE.exists():
                    ACTIVE.rename(previous)
                    moved_old = True
                if not removing:
                    write_json(STATE, original)
                    next_dir.rename(ACTIVE)
                    moved_new = True
                for entry in targets.values():
                    xf_apply(entry)
                if needs_runtime:
                    install_autostart()
                elif had_entry:
                    WIDGET_ENTRY.unlink()
                if removing:
                    STATE.unlink()
            except (Exception, KeyboardInterrupt):
                if moved_new:
                    shutil.rmtree(ACTIVE)
                if moved_old:
                    previous.rename(ACTIVE)
                if had_entry:
                    shutil.copyfile(temp / "prior.desktop", WIDGET_ENTRY)
                elif WIDGET_ENTRY.exists() and owned_autostart():
                    WIDGET_ENTRY.unlink()
                restored = True
                for entry in before:
                    try:
                        xf_apply(entry)
                    except Exception as error:
                        restored = False
                        print(f"Could not restore {entry['property']}: {error}", file=sys.stderr)
                if baseline:
                    write_json(STATE, baseline)
                elif restored:
                    STATE.unlink(missing_ok=True)
                raise
        print("Complete. Log out of XFCE and back in to refresh the NEXO desktop.")


def package_names(project):
    names = project["packages"].split()
    if not all(PACKAGE.fullmatch(name) for name in names):
        fail("The project contains an invalid Debian package name.")
    return names


def verify_packages(project):
    missing = [name for name in package_names(project)
               if run("dpkg-query", "-W", "-f=${Status}", name, check=False).stdout != "install ok installed"]
    if missing:
        fail("Install these optional packages with APT, then retry: " + " ".join(missing))


def describe(args, project, desired, runtime):
    print(f"NEXO {args.action} for {project['name']} on Debian 13 ({project['architecture']}).")
    if args.action == "uninstall":
        print("Restore backed-up XFCE settings and remove NEXO's files and autostart entry.")
    else:
        print(f"Install files under {ACTIVE}.")
        for entry in desired:
            print(f"  {entry['channel']} {entry['property']} → {entry['value']}")
        if runtime:
            print("Register the NEXO desktop in XFCE autostart.")
        if package_names(project):
            print("Optional APT packages:", " ".join(package_names(project)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("install", "update", "uninstall"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--yes", action="store_true")
    try:
        manage(parser.parse_args())
    except (RuntimeError, OSError, ValueError, subprocess.CalledProcessError, KeyboardInterrupt, EOFError) as error:
        detail = error.stderr if isinstance(error, subprocess.CalledProcessError) else str(error)
        sys.exit("NEXO: " + (detail.strip() or "Operation interrupted."))
