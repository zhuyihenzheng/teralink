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
from typing import Callable, Optional

from . import vault
from .model import MAX_AFTER_LOGIN, Connection
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


# What the macro reports as its last stage, and what that means for the user.
STAGE_HINTS = {
    "": "宏根本没有运行：ttpmacro.exe 可能被安全软件拦截，或宏文件所在路径无法读取。",
    "started": "宏已启动，但没有走到打开 Tera Term 这一步。",
    "link": "宏没能打开 Tera Term 或与它建立 DDE 连接：ttermpro.exe 可能被安全软件拦截，或 Tera Term 启动时弹出了对话框。",
    "pipe-open": "宏打不开本机管道（\\\\.\\pipe\\TeraLink-…），可能被安全软件拦截。",
    "pipe-read": "宏没有从管道读到连接信息。",
    "relink": "读取连接信息后，宏与 Tera Term 的连接断开了。",
    "connect": "Tera Term 已收到登录信息，但连接未完成：请在 Tera Term 窗口查看网络、指纹或认证提示。",
    "after-login": "已登录，但执行登录后命令时中断，请在 Tera Term 窗口查看。",
}


def create_macro(pipe_name: str, report_path: str) -> str:
    """The macro appends one line per stage to report_path, so a failure says where it stopped."""
    # Windows paths cannot contain double quotes; TTL does not treat backslashes as escapes.
    if ('"' in report_path or any(ord(c) < 32 for c in report_path)
            or not re.fullmatch(r"TeraLink-[a-f0-9]{32}", pipe_name)):
        raise ValueError("宏路径无效。")
    report = '"%s"' % report_path

    def mark(stage: str):
        return ["stage = '%s'" % stage,
                "fileopen report %s 1" % report,
                "if report <> -1 then",
                "  filewriteln report stage",
                "  fileclose report",
                "endif"]

    return "\n".join(
        ["; TeraLink transport. This file contains NO password or connection credentials.",
         "; Attach before receiving secrets: connect will use DDE, not process arguments.",
         "getver version",
         "fileopen report %s 0" % report,
         "if report <> -1 then",
         "  strconcat version ' started'",
         "  filewriteln report version",
         "  fileclose report",
         "endif"]
        + mark("link")
        + ["connect '/DS'",
           "testlink",
           "if result <> 1 goto failed"]
        + mark("pipe-open")
        + ["fileopen channel '\\\\.\\pipe\\%s' 0 1" % pipe_name,
           "if channel = -1 goto failed"]
        + mark("pipe-read")
        + ["strdim cmds %d" % MAX_AFTER_LOGIN,
           "ncmds = 0",
           "usesudo = 0",
           "sudopw = ''",
           "filereadln channel command",
           "received = result",
           "filereadln channel sudoline",
           "if result <> 0 received = 1",
           ":readcmds",
           "if received <> 0 goto readdone",
           "filereadln channel line",
           "if result <> 0 goto readdone",
           "strlen line",
           "if result = 0 goto readdone",
           "if ncmds >= %d goto readdone" % MAX_AFTER_LOGIN,
           "cmds[ncmds] = line",
           "ncmds = ncmds + 1",
           "goto readcmds",
           ":readdone",
           "fileclose channel",
           "line = ''",
           "if received <> 0 goto failed",
           "strlen command",
           "if result = 0 goto failed",
           "strcopy sudoline 1 1 sudoflag",
           "strcmp sudoflag '1'",
           "if result = 0 then",
           "  usesudo = 1",
           "  strlen sudoline",
           "  strcopy sudoline 2 result sudopw",
           "endif",
           "sudoline = ''"]
        + mark("relink")
        + ["; Recheck after the pipe wait: never launch a fresh process with the secret.",
           "testlink",
           "if result <> 1 goto failed"]
        + mark("connect")
        + ["connect command",
           "command = ''",
           "if result <> 2 goto failed"]
        + mark("connected")
        + ["if ncmds = 0 goto finish"]
        + mark("after-login")
        + ["; Type the after-login commands. The sudo password is sent ONLY when a password prompt is seen.",
           "timeout = 20",
           "wait '$ ' '# ' '> '",
           "i = 0",
           "while i < ncmds",
           "  sendln cmds[i]",
           "  if usesudo = 1 then",
           "    strscan cmds[i] 'sudo'",
           "    if result > 0 then",
           "      timeout = 10",
           "      wait 'assword' 'パスワード' '密码' '$ ' '# '",
           "      if result >= 1 then",
           "        if result <= 3 then",
           "          sendln sudopw",
           "        endif",
           "      endif",
           "    endif",
           "  endif",
           "  timeout = 20",
           "  wait '$ ' '# ' '> '",
           "  i = i + 1",
           "endwhile",
           "timeout = 0"]
        + mark("done")
        + [":finish",
           "sudopw = ''",
           "end",
           ":failed",
           "command = ''",
           "sudopw = ''",
           "strconcat stage ':failed'",
           "fileopen report %s 1" % report,
           "if report <> -1 then",
           "  filewriteln report stage",
           "  fileclose report",
           "endif",
           "end",
           ""])


def read_report(path: str):
    """Returns (stages written by the macro, last stage without the ':failed' suffix)."""
    lines = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            lines = [line.strip() for line in handle if line.strip()]
    last = lines[-1] if lines else ""
    if last.endswith(" started"):
        last = "started"
    return lines, last.replace(":failed", "")


def _failure(message: str, lines, last: str, session: str) -> OSError:
    hint = STAGE_HINTS.get(last, "")
    detail = "宏进度：%s" % (" → ".join(lines) if lines else "（没有任何记录）")
    return OSError("%s\n\n%s\n%s\n\n诊断文件（不含密码）：%s" % (message, hint, detail, session))


def _short_path(path: str) -> str:
    try:
        import ctypes
        buffer = ctypes.create_unicode_buffer(32768)
        if ctypes.windll.kernel32.GetShortPathNameW(path, buffer, len(buffer)):
            return buffer.value
    except Exception:
        pass
    return path


def visible_to_other_programs(path: str) -> bool:
    """True when a separate, non-Python process sees the file (catches AppData redirection)."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        # A string, not a list: list2cmdline would turn the quotes into \" which cmd does not understand.
        # Windows paths cannot contain double quotes, so quoting the path is safe.
        return subprocess.call('cmd /d /c if exist "%s" (exit 0) else (exit 3)' % path,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags) == 0
    except OSError:
        return True  # cannot check; let the macro report instead


def session_root() -> str:
    """Folder for the macro and its report. TTL file commands fail on non-ASCII paths (e.g. a Japanese or
    Chinese user name in %LOCALAPPDATA%), so fall back to the 8.3 short path, then to ProgramData.
    The folder never holds credentials: only the macro text and stage names."""
    root = os.path.join(data_dir(), "sessions")
    os.makedirs(root, exist_ok=True)
    if root.isascii():
        return root
    short = _short_path(root)
    if short.isascii():
        return short
    fallback = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "TeraLink", "sessions",
                            os.environ.get("USERNAME", "user").encode("ascii", "replace").decode().replace("?", "_"))
    os.makedirs(fallback, exist_ok=True)
    return fallback


def cleanup_sessions(max_age_seconds: float = 7 * 86400) -> None:
    """Failed launches keep their (credential-free) folder for diagnosis; drop old ones."""
    root = session_root()
    if not os.path.isdir(root):
        return
    now = time.time()
    for name in os.listdir(root):
        path = os.path.join(root, name)
        try:
            if now - os.path.getmtime(path) > max_age_seconds:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass


def launch(executable: str, connection: Connection, cancel: threading.Event,
           log: Callable[[str], None] = lambda line: None) -> None:
    """Blocking. Run in a worker thread; set `cancel` to stop waiting (the terminal stays open)."""
    if os.name != "nt":
        raise OSError("Tera Term 只能在 Windows 上启动。")
    from . import winpipe

    validate_executable(executable)
    connection.validate()
    cleanup_sessions()
    password = vault.unprotect(connection.protected_password)
    payload = bytearray(connection.macro_payload(password).encode("utf-8"))
    password = ""
    session = os.path.join(session_root(), os.urandom(16).hex())
    macro = None
    pipe = None
    succeeded = False
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
        if not visible_to_other_programs(script):
            raise OSError("Tera Term 看不到 TeraLink 写的宏文件：%s\n\n通常是 Microsoft Store 版 Python 把 AppData "
                          "重定向到了私有目录。请改用 python.org 的安装包，或设置环境变量 TERALINK_DATA_DIR "
                          "指向一个普通文件夹（例如 C:\\TeraLinkData）。" % script)
        log("Tera Term：%s（版本 %s）" % (executable, _file_major_version(executable) or "未知"))
        log("宏：%s" % script)
        macro = subprocess.Popen([os.path.join(folder, "ttpmacro.exe"), "/V", script], cwd=folder)

        while not pipe.connected.is_set():
            _check(cancel, deadline)
            if macro.poll() is not None:
                lines, last = read_report(report)
                log("宏提前退出，退出码 %s，进度：%s" % (macro.returncode, lines or "无"))
                raise _failure("Tera Term 宏在接收连接信息前退出。", lines, last, session)
            pipe.connected.wait(0.1)
        if pipe.error:
            raise pipe.error
        if pipe.client_pid() != macro.pid:
            raise OSError("接收端不是本次启动的 Tera Term 宏，已停止传递密码。")
        _check(cancel, deadline)
        pipe.write(payload)
        log("连接信息已通过本机管道交给宏。")
        commands = connection.after_login_commands()
        if commands:
            log("登录后将依次执行 %d 条命令%s。" % (len(commands), "（sudo 密码提示时自动输入）"
                                                 if connection.sudo_auto_password else ""))
        # Each after-login command may wait up to ~30 s for its prompt.
        deadline = max(deadline, time.monotonic() + 60 + 30 * len(commands))
        while macro.poll() is None:
            _check(cancel, deadline)
            time.sleep(0.1)
        lines, last = read_report(report)
        log("宏结束，退出码 %s，进度：%s" % (macro.returncode, lines))
        if "connected" not in lines:
            raise _failure("Tera Term 未完成自动连接。错误密码不会被自动重复提交。", lines, last, session)
        succeeded = True
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
        if succeeded:
            shutil.rmtree(session, ignore_errors=True)


def _check(cancel: threading.Event, deadline: float) -> None:
    if cancel.is_set():
        raise Cancelled("已停止等待。登录可能已提交，请在 Tera Term 查看；现有会话保持打开。")
    if time.monotonic() > deadline:
        raise TimeoutError("等待 Tera Term 超时。请检查网络及主机指纹提示；已打开的终端可继续手动使用。")
