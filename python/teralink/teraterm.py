"""Open Tera Term and submit the SSH login through a macro.

The password never appears in process arguments, the macro file, logs or the clipboard: the macro first attaches
to a fresh Tera Term over DDE, then reads the connect command from a named pipe that only the current Windows user
can open, and whose client PID must match the macro process we started.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from typing import Optional

from . import vault
from .model import Connection
from .paths import data_dir

TIMEOUT_SECONDS = 120


class Cancelled(Exception):
    pass


def find_executable() -> Optional[str]:
    roots = [os.environ.get("ProgramFiles", r"C:\Program Files"),
             os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
             os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")]
    for root in roots:
        for folder in ("teraterm5", "teraterm", "Tera Term", "Tera Term 5"):
            candidate = os.path.join(root, folder, "ttermpro.exe")
            if os.path.isfile(candidate):
                return candidate
    found = shutil.which("ttermpro.exe") or shutil.which("ttermpro")
    return found


def validate_executable(path: str) -> None:
    if (not path or not os.path.isabs(path) or not os.path.isfile(path)
            or os.path.basename(path).lower() != "ttermpro.exe"):
        raise ValueError("请在「Tera Term 路径」选择已安装的 ttermpro.exe。")
    if path.startswith("\\\\"):
        raise ValueError("请选择本机磁盘上的 Tera Term 程序。")
    major = _file_major_version(path)
    if major is not None and major < 5:
        raise ValueError("需要官方 Tera Term 5.x，请选择对应的 ttermpro.exe。")
    if not os.path.isfile(os.path.join(os.path.dirname(path), "ttpmacro.exe")):
        raise ValueError("同一目录下缺少 ttpmacro.exe，请完整安装或解压 Tera Term。")


def _file_major_version(path: str) -> Optional[int]:
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        version = ctypes.WinDLL("version")
        size = version.GetFileVersionInfoSizeW(path, None)
        if not size:
            return None
        buffer = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(path, 0, size, buffer):
            return None
        pointer, length = ctypes.c_void_p(), wintypes.UINT()
        if not version.VerQueryValueW(buffer, "\\", ctypes.byref(pointer), ctypes.byref(length)):
            return None
        # VS_FIXEDFILEINFO: dwSignature, dwStrucVersion, dwFileVersionMS, ...
        fixed = ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD * 4)).contents
        return fixed[2] >> 16
    except Exception:
        return None


def create_macro(pipe_name: str, report_path: str) -> str:
    # Windows paths cannot contain double quotes; TTL does not treat backslashes as escapes.
    if ('"' in report_path or any(ord(c) < 32 for c in report_path)
            or not re.fullmatch(r"TeraLink-[a-f0-9]{32}", pipe_name)):
        raise ValueError("宏路径无效。")
    return "\n".join([
        "; TeraLink transport. This file contains NO password or connection credentials.",
        "; Attach before receiving secrets: connect will use DDE, not process arguments.",
        "connect '/DS'",
        "testlink",
        "if result <> 1 goto failed",
        "fileopen channel '\\\\.\\pipe\\%s' 0 1" % pipe_name,
        "if channel = -1 goto failed",
        "filereadln channel command",
        "received = result",
        "fileclose channel",
        "if received <> 0 goto failed",
        "strlen command",
        "if result = 0 goto failed",
        "; Recheck after the pipe wait: never launch a fresh process with the secret.",
        "testlink",
        "if result <> 1 goto failed",
        "connect command",
        "command = ''",
        "if result <> 2 goto failed",
        'fileopen report "%s" 0' % report_path,
        "if report = -1 end",
        "filewriteln report 'connected'",
        "fileclose report",
        "end",
        ":failed",
        "command = ''",
        'fileopen report "%s" 0' % report_path,
        "if report = -1 end",
        "filewriteln report 'failed'",
        "fileclose report",
        "end",
        "",
    ])


def launch(executable: str, connection: Connection, cancel: threading.Event) -> None:
    """Blocking. Run in a worker thread; set `cancel` to stop waiting (the terminal stays open)."""
    if os.name != "nt":
        raise OSError("Tera Term 只能在 Windows 上启动。")
    from . import winpipe

    validate_executable(executable)
    connection.validate()
    password = vault.unprotect(connection.protected_password)
    payload = bytearray((connection.macro_connect_command(password) + "\r\n").encode("utf-8"))
    password = ""
    session = os.path.join(data_dir(), "sessions", os.urandom(16).hex())
    macro = None
    pipe = None
    deadline = time.monotonic() + TIMEOUT_SECONDS
    try:
        os.makedirs(session)
        pipe_name = "TeraLink-" + os.urandom(16).hex()
        script = os.path.join(session, "connect.ttl")
        report = os.path.join(session, "result.txt")
        with open(script, "w", encoding="utf-8", newline="\r\n") as handle:
            handle.write(create_macro(pipe_name, report))
        pipe = winpipe.SecureOutboundPipe(pipe_name)
        pipe.start_accept()
        folder = os.path.dirname(executable)
        macro = subprocess.Popen([os.path.join(folder, "ttpmacro.exe"), "/V", script], cwd=folder)

        while not pipe.connected.is_set():
            _check(cancel, deadline)
            if macro.poll() is not None:
                raise OSError("Tera Term 宏在接收连接信息前退出，请检查安装是否完整。")
            pipe.connected.wait(0.1)
        if pipe.error:
            raise pipe.error
        if pipe.client_pid() != macro.pid:
            raise OSError("接收端不是本次启动的 Tera Term 宏，已停止传递密码。")
        _check(cancel, deadline)
        pipe.write(payload)
        while macro.poll() is None:
            _check(cancel, deadline)
            time.sleep(0.1)
        result = ""
        if os.path.exists(report):
            with open(report, "r", encoding="utf-8", errors="replace") as handle:
                result = handle.read().strip()
        if result != "connected":
            raise OSError("Tera Term 未完成自动连接。请在终端查看网络、认证或主机指纹提示；错误密码不会被自动重复提交。")
    finally:
        for index in range(len(payload)):
            payload[index] = 0
        if pipe is not None:
            pipe.close()
        # Stop only our helper; leave the user's terminal/session open.
        if macro is not None and macro.poll() is None:
            try:
                macro.kill()
            except OSError:
                pass
        shutil.rmtree(session, ignore_errors=True)  # Residue holds only paths and status, never credentials.


def _check(cancel: threading.Event, deadline: float) -> None:
    if cancel.is_set():
        raise Cancelled("已停止等待。登录可能已提交，请在 Tera Term 查看；现有会话保持打开。")
    if time.monotonic() > deadline:
        raise TimeoutError("等待 Tera Term 超时。请检查网络及主机指纹提示；已打开的终端可继续手动使用。")
