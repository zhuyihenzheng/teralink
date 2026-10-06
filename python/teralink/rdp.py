"""Windows Remote Desktop: build a per-connection .rdp profile and open it with mstsc.exe."""
from __future__ import annotations

import hashlib
import ipaddress
import os
import subprocess
from typing import List, Optional, Tuple

from . import vault
from .model import KIND_RDP, Connection, validate_host
from .paths import data_dir

_REMOVED_ON_IMPORT = {"username", "password 51", "prompt for credentials", "signature", "signscope"}


def _key(line: str) -> str:
    return line.split(":", 1)[0].strip().lower()


def _has_control(value: str) -> bool:
    return any(ord(c) < 32 or ord(c) == 127 for c in value)


def _read_settings(profile: str) -> List[str]:
    if len(profile) > 1024 * 1024 or "\0" in profile:
        raise ValueError("RDP 文件过大或编码无效。")
    lines = [line for line in profile.replace("\r", "\n").split("\n") if line]
    for line in lines:
        parts = line.split(":", 2)
        if (len(parts) != 3 or not parts[0] or parts[0] != parts[0].strip()
                or parts[1] not in ("s", "i", "b") or _has_control(line)):
            raise ValueError("RDP 文件中存在无效设置，请用远程桌面客户端重新另存为 .rdp 文件。")
        if parts[0].lower() in ("full address", "username", "domain") and parts[1] != "s":
            raise ValueError("RDP 地址或账户设置类型无效。")
    keys = [_key(line) for line in lines]
    if len(set(keys)) != len(keys):
        raise ValueError("RDP 文件包含重复设置，请用远程桌面客户端重新另存为。")
    if "full address" not in keys:
        raise ValueError("RDP 文件缺少 full address 目标地址。")
    return lines


def inspect(profile: str) -> Tuple[str, int, str]:
    lines = _read_settings(profile)

    def value(key: str) -> str:
        line = next((item for item in lines if _key(item) == key), None)
        return line.split(":", 2)[2] if line else ""

    endpoint = value("full address")
    host, port = endpoint, 3389
    if endpoint.startswith("["):
        end = endpoint.find("]")
        if end < 0:
            raise ValueError("RDP IPv6 地址无效。")
        host = endpoint[1:end]
        if len(endpoint) > end + 1:
            if endpoint[end + 1] != ":" or not endpoint[end + 2:].isdigit():
                raise ValueError("RDP 端口无效。")
            port = int(endpoint[end + 2:])
    elif endpoint.count(":") == 1:
        host, _, port_text = endpoint.rpartition(":")
        if not port_text.isdigit():
            raise ValueError("RDP 端口无效。")
        port = int(port_text)
    validate_host(host)
    if not 1 <= port <= 65535:
        raise ValueError("RDP 端口无效。")
    username, domain = value("username"), value("domain")
    if username and domain and "\\" not in username and "@" not in username:
        username = domain + "\\" + username
    return host, port, username


def create(connection: Connection, encrypted_password_hex: str, existing: Optional[str] = None) -> str:
    connection.validate()
    if connection.kind != KIND_RDP:
        raise ValueError("请选择 RDP 连接。")
    if (not encrypted_password_hex or len(encrypted_password_hex) % 2 or len(encrypted_password_hex) > 65536
            or any(c not in "0123456789abcdefABCDEF" for c in encrypted_password_hex)):
        raise ValueError("RDP 加密密码格式无效。")
    if existing is not None:
        host, port, _ = inspect(existing)
        if host.lower() != connection.host.lower() or port != connection.port:
            raise ValueError("原 RDP 文件的目标地址和连接记录不一致，请重新选择文件。")
        # Editing a signed RDP file invalidates its signature. The derived file is unsigned.
        imported = [line for line in _read_settings(existing) if _key(line) not in _REMOVED_ON_IMPORT]
        if "\\" in connection.username or "@" in connection.username:
            imported = [line for line in imported if _key(line) != "domain"]
        imported += ["username:s:%s" % connection.username,
                     "password 51:b:%s" % encrypted_password_hex,
                     "prompt for credentials:i:0"]
        return "\r\n".join(imported) + "\r\n"
    host = connection.host
    try:
        if ipaddress.ip_address(host).version == 6:
            host = "[%s]" % host
    except ValueError:
        pass
    return "\r\n".join([
        "full address:s:%s:%d" % (host, connection.port),
        "username:s:%s" % connection.username,
        "password 51:b:%s" % encrypted_password_hex,
        "prompt for credentials:i:0",
        "enablecredsspsupport:i:1",
        "authentication level:i:2",
        "gatewayusagemethod:i:0",
        "screen mode id:i:%d" % (2 if connection.rdp_full_screen else 1),
        "desktopwidth:i:1280", "desktopheight:i:800",
        "session bpp:i:32", "compression:i:1",
        "disable wallpaper:i:1", "allow font smoothing:i:0",
        "allow desktop composition:i:0", "disable full window drag:i:1",
        "disable menu anims:i:1", "disable themes:i:1",
        "redirectclipboard:i:1", "redirectprinters:i:0",
        "redirectcomports:i:0", "redirectsmartcards:i:0",
        "drivestoredirect:s:", "audiomode:i:2", "audiocapturemode:i:0", "",
    ])


def read_profile(path: str) -> str:
    if not os.path.isabs(path) or not path.lower().endswith(".rdp"):
        raise ValueError("请选择完整路径的 .rdp 文件。")
    if not os.path.isfile(path):
        raise FileNotFoundError("原 RDP 文件不存在，请编辑连接重新选择：%s" % path)
    if os.path.getsize(path) > 1024 * 1024:
        raise ValueError("RDP 文件过大。")
    with open(path, "rb") as handle:
        raw = handle.read()
    # mstsc writes UTF-16LE with BOM; hand-made files are usually UTF-8.
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16")
    return raw.decode("utf-8-sig")


def fingerprint(profile: str) -> str:
    """Same as the C# version: SHA-256 of the decoded text re-encoded as UTF-8, upper-case hex."""
    return hashlib.sha256(profile.encode("utf-8")).hexdigest().upper()


def _profile_dir() -> str:
    return os.path.join(data_dir(), "rdp")


def forget(connection_id: str) -> None:
    path = os.path.join(_profile_dir(), connection_id + ".rdp")
    if os.path.exists(path):
        os.remove(path)


def launch(connection: Connection) -> None:
    connection.validate()
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    executable = os.path.join(system_root, "System32", "mstsc.exe")
    if not os.path.isfile(executable):
        raise FileNotFoundError("未找到 Windows 远程桌面客户端 mstsc.exe。")
    existing = read_profile(connection.rdp_file_path) if connection.rdp_file_path else None
    if existing is not None:
        if fingerprint(existing) != connection.rdp_file_hash.upper():
            raise ValueError("原 RDP 文件的设置已改变，请编辑连接并重新选择文件后保存。")
        host, port, _ = inspect(existing)
        if host.lower() != connection.host.lower() or port != connection.port:
            raise ValueError("原 RDP 文件的目标地址已改变，请编辑连接并重新选择文件后保存。")
    profile = create(connection, vault.to_rdp_password(connection.protected_password), existing)
    os.makedirs(_profile_dir(), exist_ok=True)
    path = os.path.join(_profile_dir(), connection.id + ".rdp")
    temporary = path + ".tmp"
    try:
        # Kept so mstsc can read it after the launcher exits. No shared TERMSRV credentials are written.
        with open(temporary, "w", encoding="utf-16", newline="") as handle:
            handle.write(profile)
        os.replace(temporary, path)
        subprocess.Popen([executable, path], close_fds=True)
    except Exception:
        try:
            os.remove(path)
        except OSError:
            pass
        raise
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)
