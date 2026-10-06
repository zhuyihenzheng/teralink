"""Locations of the data directory and the legacy C# TeraLink data."""
from __future__ import annotations

import os
import shutil
import sys
from typing import Optional


def _local_appdata() -> str:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return base
    return os.path.join(os.path.expanduser("~"), ".local", "share")


def is_store_python() -> bool:
    """Microsoft Store Python runs packaged: its writes under AppData go to a private copy
    (%LOCALAPPDATA%\\Packages\\PythonSoftwareFoundation...\\LocalCache) that Tera Term, mstsc and Explorer
    cannot see. Detected from where the interpreter is installed."""
    if os.name != "nt":
        return False
    where = (sys.base_prefix + "|" + sys.executable).lower()
    return "\\windowsapps\\" in where or "\\packages\\pythonsoftwarefoundation" in where


def _appdata_dir() -> str:
    return os.path.join(_local_appdata(), "TeraLinkPy")


def data_dir() -> str:
    override = os.environ.get("TERALINK_DATA_DIR")
    if override:
        return override
    if is_store_python():
        # Outside AppData, so other programs see the same files (macro, .rdp, logs).
        return os.path.join(os.path.expanduser("~"), "TeraLinkPy")
    return _appdata_dir()


def migrate_store_python_data() -> Optional[str]:
    """Copy data saved by an earlier version under the redirected AppData path. Returns a note, or None."""
    if os.environ.get("TERALINK_DATA_DIR") or not is_store_python():
        return None
    target, source = data_dir(), _appdata_dir()
    if os.path.exists(os.path.join(target, "data.json")) or not os.path.exists(os.path.join(source, "data.json")):
        return None
    os.makedirs(target, exist_ok=True)
    for name in ("data.json", "known_hosts"):
        if os.path.exists(os.path.join(source, name)):
            shutil.copy2(os.path.join(source, name), os.path.join(target, name))
    return "已把之前保存的数据迁移到 %s（Microsoft Store 版 Python 的 AppData 对 Tera Term 不可见）。" % target


def legacy_data_file() -> str:
    return os.path.join(_local_appdata(), "TeraLink", "connections.json")


def known_hosts_file() -> str:
    return os.path.join(data_dir(), "known_hosts")


def log_dir() -> str:
    return os.path.join(data_dir(), "logs")
