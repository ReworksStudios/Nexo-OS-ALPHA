# NEXO Debian customization

Target: Debian 13 (trixie), XFCE in an X11 session, on the architecture selected in project.nexo.json.
This is a per-user customization for an existing Debian desktop.

Open a terminal in your XFCE session and review install.sh, scripts/manage.py and
runtime/desktop.py. Install missing dependencies with Debian's package manager:

```sh
sudo apt update
sudo apt install python3 python3-gi gir1.2-gtk-3.0 xfconf xdg-utils
./install.sh --dry-run
./install.sh
```

Install optional packages listed by the dry run with APT before continuing.
Log out and back in to start the desktop widgets. XFCE handles the wallpaper,
desktop icons and appearance. The NEXO widgets run separately from XFCE's own panel.
The primary display is used for widget placement. System boot, login and lock
settings are outside the scope of this export.

Pull or copy a newer export, then run `./update.sh` from the newer export.
Run `./uninstall.sh` or `./rollback.sh` to restore the original XFCE values and remove
this user's installed NEXO assets and autostart entries. This leaves Debian packages in
place. Log out and back in to stop any already running widgets.