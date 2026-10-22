"""Startup-items collector: programs that launch automatically at sign-in.

Windows sources (no admin needed to read):

* Registry Run keys in HKCU and HKLM (plus the 32-bit WOW6432Node view).
* The per-user and all-users Startup folders.
* Enabled/disabled state from ``Explorer\\StartupApproved``, which is where
  Task Manager's Startup tab stores its toggle. An item with no
  StartupApproved entry is enabled.

Linux, for parity: ``~/.config/autostart/*.desktop``.

Scheduled tasks and services can also start at boot; they are out of scope
for v0.1.0.

Added in v0.1.0
"""
from __future__ import annotations

import configparser
import os
from pathlib import Path
from typing import Any, Callable, Optional

from ..models import StartupItem, SystemSnapshot
from . import _shell

_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN32 = r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"
_APPROVED = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved"

#: (hive, Run key, StartupApproved subkey) triples to scan.
RUN_KEYS = [
    ("HKCU", _RUN, "Run"),
    ("HKLM", _RUN, "Run"),
    ("HKLM", _RUN32, "Run32"),
]

#: Signature of a registry reader: (hive, key path) -> {value name: data}.
#: Real implementation uses winreg; tests pass a fake.
RegistryReader = Callable[[str, str], dict[str, Any]]


def read_registry_values(hive: str, path: str) -> dict[str, Any]:
    """Return all values under a registry key, or {} if it doesn't exist."""
    try:
        import winreg  # Windows-only

        root = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}[hive]
        values: dict[str, Any] = {}
        with winreg.OpenKey(root, path) as key:
            i = 0
            while True:
                try:
                    name, data, _type = winreg.EnumValue(key, i)
                except OSError:  # raised when there are no more values
                    break
                values[name] = data
                i += 1
        return values
    except (OSError, ImportError, KeyError):
        return {}


def is_approved_enabled(data: Any) -> Optional[bool]:
    """Decode a StartupApproved binary value.

    Windows quirk: the value is 12 bytes; the first byte is 0x02 or 0x06
    when enabled and 0x03 / 0x01 / 0x07 when disabled in Task Manager (the
    rest is the timestamp of the toggle). An even first byte = enabled.
    """
    if isinstance(data, (bytes, bytearray)) and data:
        return data[0] % 2 == 0
    return None


def collect_windows(reader: RegistryReader = read_registry_values,
                    startup_dirs: Optional[list[tuple[str, Path]]] = None) -> list[StartupItem]:
    """Gather Run-key and Startup-folder entries using ``reader``."""
    items: list[StartupItem] = []
    for hive, key, approved_sub in RUN_KEYS:
        approved = reader(hive, f"{_APPROVED}\\{approved_sub}")
        for name, command in reader(hive, key).items():
            if not name:  # the unnamed "(Default)" value isn't a startup entry
                continue
            enabled = is_approved_enabled(approved.get(name))
            items.append(StartupItem(name=name, command=str(command), location=f"{hive}\\{key}",
                                     enabled=True if enabled is None else enabled))

    if startup_dirs is None:
        startup_dirs = []
        appdata, programdata = os.environ.get("APPDATA"), os.environ.get("PROGRAMDATA")
        if appdata:
            startup_dirs.append(("HKCU", Path(appdata) / r"Microsoft\Windows\Start Menu\Programs\Startup"))
        if programdata:
            startup_dirs.append(("HKLM", Path(programdata) / r"Microsoft\Windows\Start Menu\Programs\StartUp"))
    for hive, folder in startup_dirs:
        # Folder items' toggles live under the same hive as the folder's owner.
        approved = reader(hive, f"{_APPROVED}\\StartupFolder")
        try:
            entries = sorted(folder.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.name.lower() == "desktop.ini":  # hidden folder-metadata file
                continue
            enabled = is_approved_enabled(approved.get(entry.name))
            items.append(StartupItem(name=entry.stem, command=str(entry), location=f"Startup folder ({folder})",
                                     enabled=True if enabled is None else enabled))
    return items


def collect_linux(autostart_dir: Optional[Path] = None) -> list[StartupItem]:
    """Parse XDG autostart ``.desktop`` files for the current user."""
    folder = autostart_dir or Path.home() / ".config" / "autostart"
    items: list[StartupItem] = []
    try:
        files = sorted(folder.glob("*.desktop"))
    except OSError:
        return items
    for f in files:
        cp = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            cp.read(f, encoding="utf-8")
            entry = cp["Desktop Entry"]
        except (configparser.Error, KeyError, OSError):
            continue
        # Either key disables an autostart entry per the XDG spec / GNOME.
        disabled = entry.get("Hidden", "false").lower() == "true" or \
            entry.get("X-GNOME-Autostart-enabled", "true").lower() == "false"
        items.append(StartupItem(name=entry.get("Name", f.stem), command=entry.get("Exec"),
                                 location=str(folder), enabled=not disabled))
    return items


def collect(snapshot: SystemSnapshot) -> list[StartupItem]:
    """Collect startup items for this platform."""
    try:
        return collect_windows() if _shell.is_windows() else collect_linux()
    except Exception as exc:
        snapshot.add_error("startup", f"startup items unavailable: {exc}")
        return []
