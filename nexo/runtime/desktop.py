"""Render NEXO's exported widgets on an XFCE X11 desktop."""

import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from datetime import datetime

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk

ROOT = Path(__file__).resolve().parent.parent
PROJECT = json.loads((ROOT / "project.nexo.json").read_text())
COLOR = re.compile(r"#[0-9A-Fa-f]{6}")
APP = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*\.desktop")


def launch(app):
    try:
        app.launch([], None)
    except GLib.Error as error:
        print(error)


def open_folder(path):
    folder = Path(path or "~").expanduser()
    if folder.is_absolute() and folder.is_dir():
        subprocess.Popen(["xdg-open", str(folder)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def application(item):
    target = item["target"]
    if APP.fullmatch(target):
        app = Gio.DesktopAppInfo.new(target)
        if app:
            launch(app)


def text_for(item):
    kind = item["kind"]
    if kind == "Clock":
        return datetime.now().strftime("%H:%M")
    if kind == "Calendar":
        return datetime.now().strftime("%a %b %d")
    if kind == "System Monitor":
        return "Load  %.2f" % os.getloadavg()[0]
    if kind == "Network Indicator":
        online = Gio.NetworkMonitor.get_default().get_network_available()
        return "Online" if online else "Offline"
    if kind == "Battery Indicator":
        batteries = list(Path("/sys/class/power_supply").glob("BAT*/capacity"))
        try:
            return "Battery  " + batteries[0].read_text().strip() + "%" if batteries else "AC power"
        except OSError:
            return "Battery"
    if kind == "Volume Control" and shutil.which("wpctl"):
        result = subprocess.run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"], capture_output=True, text=True)
        match = re.search(r"([\d.]+)", result.stdout)
        return "Muted" if "[MUTED]" in result.stdout else (
            "Volume  " + str(round(float(match[1]) * 100)) + "%" if match else item["title"])
    return (item["icon"] + " " if item["icon"] else "") + item["title"]


def app_popup(anchor, search=False):
    popup = Gtk.Popover.new(anchor)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=10)
    filter_field = Gtk.SearchEntry()
    if search:
        box.pack_start(filter_field, False, False, 0)
    listing = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    scroll = Gtk.ScrolledWindow(min_content_width=255, min_content_height=245)
    scroll.add(listing)
    box.pack_start(scroll, True, True, 0)
    for app in sorted((app for app in Gio.AppInfo.get_all() if app.should_show()), key=lambda app: app.get_display_name().casefold()):
        button = Gtk.Button(label=app.get_display_name(), relief=Gtk.ReliefStyle.NONE)
        button.connect("clicked", lambda _button, selected=app: (popup.popdown(), launch(selected)))
        listing.add(button)
    listing.set_filter_func(lambda row: filter_field.get_text().casefold() in row.get_child().get_label().casefold())
    filter_field.connect("search-changed", lambda _entry: listing.invalidate_filter())
    popup.add(box)
    popup.show_all()
    popup.popup()


def widget(item, width, height):
    kind = item["kind"]
    if kind == "Image" and item["asset"]:
        try:
            bitmap = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(ROOT / "assets" / item["asset"]), width, height, True)
            return Gtk.Image.new_from_pixbuf(bitmap)
        except GLib.Error:
            pass
    if kind == "Dock":
        box = Gtk.Box(homogeneous=True)
        for name, icon, desktop in [("Files", "system-file-manager", "thunar.desktop"),
                                    ("Browser", "web-browser", "firefox-esr.desktop"),
                                    ("Terminal", "utilities-terminal", "xfce4-terminal.desktop")]:
            button = Gtk.Button(relief=Gtk.ReliefStyle.NONE)
            button.set_tooltip_text(name)
            button.add(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.LARGE_TOOLBAR))
            def activate(_button, target=desktop):
                app = Gio.DesktopAppInfo.new(target)
                if app:
                    launch(app)
            button.connect("clicked", activate)
            box.pack_start(button, True, True, 0)
        return box
    if kind in ("App Launcher", "Search Bar", "Menu", "Taskbar", "Application Shortcut", "Folder Shortcut", "Volume Control"):
        button = Gtk.Button(label=text_for(item), relief=Gtk.ReliefStyle.NONE)
        if kind == "Application Shortcut":
            button.connect("clicked", lambda _button: application(item))
        elif kind == "Folder Shortcut":
            button.connect("clicked", lambda _button: open_folder(item["target"]))
        elif kind == "Volume Control":
            def volume(_button):
                app = Gio.DesktopAppInfo.new("org.pulseaudio.pavucontrol.desktop") or Gio.DesktopAppInfo.new("pavucontrol.desktop")
                if app:
                    launch(app)
            button.connect("clicked", volume)
            def update_volume():
                button.set_label(text_for(item))
                return True
            GLib.timeout_add_seconds(10, update_volume)
        else:
            button.connect("clicked", lambda source: app_popup(source, kind != "Menu"))
        return button
    label = Gtk.Label(label=text_for(item), ellipsize=3)
    if kind in ("Clock", "Calendar", "System Monitor", "Network Indicator", "Battery Indicator"):
        def refresh():
            label.set_text(text_for(item))
            return True
        GLib.timeout_add_seconds(5, refresh)
    return label


def desktop_widget(item, geometry):
    sx, sy = geometry.width / PROJECT["width"], geometry.height / PROJECT["height"]
    width, height = max(1, round(item["width"] * sx)), max(1, round(item["height"] * sy))
    x, y = geometry.x + round(item["x"] * sx), geometry.y + round(item["y"] * sy)
    window = Gtk.Window()
    window.set_decorated(False)
    window.set_resizable(False)
    window.set_skip_taskbar_hint(True)
    window.set_skip_pager_hint(True)
    window.set_keep_below(True)
    window.set_focus_on_map(False)
    window.stick()
    window.set_app_paintable(True)
    visual = window.get_screen().get_rgba_visual()
    if visual:
        window.set_visual(visual)
    window.set_opacity(max(0, min(1, item["opacity"])))
    window.set_type_hint(Gdk.WindowTypeHint.UTILITY)
    window.set_default_size(width, height)
    content = widget(item, width, height)
    background = item["background"] if COLOR.fullmatch(item["background"]) else "#18223D"
    foreground = item["foreground"] if COLOR.fullmatch(item["foreground"]) else "#FFFFFF"
    family = re.sub(r'[^\w -]', '', item["font"]) or "Sans"
    radius = max(0, min(100, item["radius"])) * min(sx, sy)
    border = max(0, min(20, item["border"])) * min(sx, sy)
    size = max(8, min(120, item["fontSize"])) * min(sx, sy)
    provider = Gtk.CssProvider()
    css = (f'window {{ background: {background}; border-radius: {radius:.1f}px; '
           f'border: {border:.1f}px solid {foreground}; }} '
           f'window.nexo-animated:active {{ opacity: 0.8; }} '
           f'button, label {{ color: {foreground}; font: {size:.1f}px "{family}"; }}')
    provider.load_from_data(css.encode())
    window.get_style_context().add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    def style(control):
        control.get_style_context().add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        if isinstance(control, Gtk.Container):
            for child in control.get_children():
                style(child)
    style(content)
    if item["animated"]:
        window.get_style_context().add_class("nexo-animated")
    window.add(content)
    window.move(x, y)
    window.show_all()
    window.move(x, y)
    return window


def main():
    with (ROOT.parent / "runtime.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        display = Gdk.Display.get_default()
        if not display:
            return
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        if not monitor:
            return
        for desktop_id in PROJECT["startup"].split():
            if APP.fullmatch(desktop_id):
                app = Gio.DesktopAppInfo.new(desktop_id)
                if app:
                    launch(app)
        widgets = [desktop_widget(item, monitor.get_geometry()) for item in PROJECT["items"] if item["visible"]]
        if widgets:
            Gtk.main()


if __name__ == "__main__":
    main()
