"""Locations of the data directory and the legacy C# TeraLink data."""
from __future__ import annotations

import os


def _local_appdata() -> str:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return base
    return os.path.join(os.path.expanduser("~"), ".local", "share")


def data_dir() -> str:
    override = os.environ.get("TERALINK_DATA_DIR")
    return override or os.path.join(_local_appdata(), "TeraLinkPy")


def legacy_data_file() -> str:
    return os.path.join(_local_appdata(), "TeraLink", "connections.json")


def known_hosts_file() -> str:
    return os.path.join(data_dir(), "known_hosts")


def log_dir() -> str:
    return os.path.join(data_dir(), "logs")
