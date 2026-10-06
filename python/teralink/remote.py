"""SSH / SFTP to Linux servers for task steps.

Preferred backend: paramiko (password login, host-key confirmation, SFTP with progress).
Fallback when paramiko is not installed: Windows' built-in OpenSSH (ssh.exe / scp.exe), key login only.
"""
from __future__ import annotations

import base64
import hashlib
import os
import posixpath
import re
import shlex
import shutil
import subprocess
import threading
from datetime import datetime
from typing import Callable, Optional

from . import vault
from .model import Connection, validate_remote_dir, validate_remote_name
from .paths import known_hosts_file

Log = Callable[[str], None]
# (host, port, key_type, sha256_fingerprint) -> accept?
ConfirmHostKey = Callable[[str, int, str, str], bool]

try:  # pragma: no cover - depends on the machine
    import paramiko  # type: ignore
except Exception:  # ImportError, or a broken cryptography install
    paramiko = None


def backend_name() -> str:
    if paramiko is not None:
        return "paramiko %s" % getattr(paramiko, "__version__", "")
    if openssh_available():
        return "Windows OpenSSH（仅密钥登录）"
    return "无（请运行 install-deps.bat 安装 paramiko）"


def openssh_available() -> bool:
    return bool(shutil.which("ssh") and shutil.which("scp"))


def sha256_fingerprint(key_bytes: bytes) -> str:
    return "SHA256:" + base64.b64encode(hashlib.sha256(key_bytes).digest()).decode("ascii").rstrip("=")


def backup_name(name: str, when: Optional[datetime] = None) -> str:
    return "%s.bak-%s" % (name, (when or datetime.now()).strftime("%Y%m%d-%H%M%S"))


class RemoteError(Exception):
    pass


SUDO_PROMPT = "[teralink-sudo-password]"
_OWNER_RE = r"^[A-Za-z0-9._-]+(:[A-Za-z0-9._-]+)?$"


def sudo_wrap(command: str) -> str:
    """sudo reads the password from stdin (-S) and prints a recognisable prompt, so the password is only
    sent after sudo really asks for it -- never into the command's own stdin."""
    return "sudo -S -p %s sh -c %s" % (shlex.quote(SUDO_PROMPT), shlex.quote(command))


def sudo_install_script(staged: str, staging_dir: str, remote_dir: str, name: str, backup: bool,
                        owner: str) -> str:
    q = shlex.quote
    target = posixpath.join(remote_dir, name)
    partial = posixpath.join(remote_dir, "." + name + ".part")
    lines = ["set -e", "mkdir -p %s" % q(remote_dir)]
    if backup:
        lines.append("if [ -e %s ]; then mv %s %s; echo backup:%s; fi" % (
            q(target), q(target), q(posixpath.join(remote_dir, backup_name(name))), backup_name(name)))
    lines.append("cp %s %s" % (q(staged), q(partial)))
    if owner:
        lines.append("chown %s %s" % (q(owner), q(partial)))
    lines.append("chmod 644 %s" % q(partial))
    lines.append("mv -f %s %s" % (q(partial), q(target)))
    lines.append("rm -rf %s" % q(staging_dir))
    return "; ".join(lines)


class Session:
    def upload(self, local: str, remote_dir: str, remote_name: str, backup: bool, log: Log,
               cancel: threading.Event, sudo_password: Optional[str] = None, owner: str = "") -> str:
        raise NotImplementedError

    def run(self, command: str, log: Log, cancel: threading.Event, sudo_password: Optional[str] = None) -> int:
        raise NotImplementedError

    def close(self) -> None:
        pass


def open_session(connection: Connection, confirm: ConfirmHostKey, log: Log) -> Session:
    connection.validate()
    if paramiko is not None:
        return ParamikoSession(connection, confirm, log)
    if openssh_available():
        log("未安装 paramiko，改用 Windows OpenSSH（需要已配置 SSH 密钥，不能用密码）。")
        return OpenSshSession(connection)
    raise RemoteError("没有可用的 SSH 组件。请运行 install-deps.bat 安装 paramiko，或启用 Windows 的 OpenSSH 客户端。")


if paramiko is not None:  # pragma: no cover - exercised on Windows with paramiko installed
    class _ConfirmPolicy(paramiko.MissingHostKeyPolicy):
        def __init__(self, confirm: ConfirmHostKey, host: str, port: int, path: str):
            self.confirm, self.host, self.port, self.path = confirm, host, port, path

        def missing_host_key(self, client, hostname, key):
            # paramiko passes "[host]:port" for non-22 ports; that is also the right known_hosts key.
            fingerprint = sha256_fingerprint(key.asbytes())
            if not self.confirm(self.host, self.port, key.get_name(), fingerprint):
                raise RemoteError("未信任服务器指纹，已取消连接。")
            client.get_host_keys().add(hostname, key.get_name(), key)
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            client.save_host_keys(self.path)


class ParamikoSession(Session):
    def __init__(self, connection: Connection, confirm: ConfirmHostKey, log: Log):
        self.connection = connection
        path = known_hosts_file()
        client = paramiko.SSHClient()
        if os.path.exists(path):
            client.load_host_keys(path)
        client.set_missing_host_key_policy(_ConfirmPolicy(confirm, connection.host, connection.port, path))
        log("连接 %s@%s:%d …" % (connection.username, connection.host, connection.port))
        password = vault.unprotect(connection.protected_password)
        try:
            client.connect(connection.host, port=connection.port, username=connection.username,
                           password=password, look_for_keys=False, allow_agent=False,
                           timeout=15, banner_timeout=30, auth_timeout=30)
        except paramiko.BadHostKeyException:
            client.close()
            raise RemoteError("服务器指纹与已保存的不一致！可能是服务器重装，也可能是中间人攻击。"
                              "确认无误后，从 %s 删除该主机的记录再重试。" % path)
        except paramiko.AuthenticationException:
            client.close()
            raise RemoteError("登录失败：用户名或密码错误，或服务器不允许密码登录。")
        except RemoteError:
            client.close()
            raise
        except Exception as error:  # socket errors, timeouts, SSHException ...
            client.close()
            raise RemoteError("无法连接 %s:%d：%s" % (connection.host, connection.port,
                                                     str(error) or type(error).__name__))
        finally:
            password = ""
        self.client = client
        self._sftp = None

    def _sftp_client(self):
        if self._sftp is None:
            self._sftp = self.client.open_sftp()
        return self._sftp

    def _mkdirs(self, sftp, remote_dir: str) -> None:
        current = "/"
        for part in [p for p in remote_dir.split("/") if p]:
            current = posixpath.join(current, part)
            try:
                sftp.stat(current)
            except IOError:
                sftp.mkdir(current)

    def upload(self, local, remote_dir, remote_name, backup, log, cancel, sudo_password=None, owner=""):
        validate_remote_dir(remote_dir)
        name = validate_remote_name(remote_name) or os.path.basename(local)
        if sudo_password is not None:
            return self._upload_with_sudo(local, remote_dir, name, backup, log, cancel, sudo_password, owner)
        target = posixpath.join(remote_dir, name)
        partial = posixpath.join(remote_dir, "." + name + ".part")
        sftp = self._sftp_client()
        self._mkdirs(sftp, remote_dir)
        total = os.path.getsize(local)
        state = {"next": 10}

        def progress(sent, _size):
            if cancel.is_set():
                raise RemoteError("已停止。")
            percent = int(sent * 100 / total) if total else 100
            if percent >= state["next"]:
                log("  上传 %d%%（%s / %s）" % (percent, _human(sent), _human(total)))
                state["next"] = (percent // 10 + 1) * 10

        log("上传 %s → %s:%s（%s）" % (local, self.connection.host, target, _human(total)))
        try:
            # Upload to a hidden temp name first so auto-deployers (e.g. Tomcat) never see a half-written file.
            sftp.put(local, partial, callback=progress, confirm=True)
            if backup:
                try:
                    sftp.stat(target)
                    saved = posixpath.join(remote_dir, backup_name(name))
                    sftp.rename(target, saved)
                    log("  已备份原文件为 %s" % saved)
                except IOError:
                    pass
            try:
                sftp.posix_rename(partial, target)
            except IOError:  # server without posix-rename@openssh.com
                try:
                    sftp.remove(target)
                except IOError:
                    pass
                sftp.rename(partial, target)
        except Exception:
            try:
                sftp.remove(partial)
            except IOError:
                pass
            raise
        log("  上传完成：%s" % target)
        return target

    def _upload_with_sudo(self, local, remote_dir, name, backup, log, cancel, sudo_password, owner):
        if owner and not re.match(_OWNER_RE, owner):
            raise RemoteError("所有者格式应为 用户 或 用户:组，例如 tomcat:tomcat。")
        sftp = self._sftp_client()
        staging = "/tmp/teralink-%s" % os.urandom(8).hex()
        sftp.mkdir(staging, 0o700)  # private to the login user until sudo moves the file
        staged = posixpath.join(staging, name)
        total = os.path.getsize(local)
        state = {"next": 10}

        def progress(sent, _size):
            if cancel.is_set():
                raise RemoteError("已停止。")
            percent = int(sent * 100 / total) if total else 100
            if percent >= state["next"]:
                log("  上传 %d%%（%s / %s）" % (percent, _human(sent), _human(total)))
                state["next"] = (percent // 10 + 1) * 10

        target = posixpath.join(remote_dir, name)
        log("上传 %s → %s:%s（%s，经 %s 用 sudo 放入）" % (local, self.connection.host, target, _human(total), staging))
        try:
            sftp.put(local, staged, callback=progress, confirm=True)
            code = self.run(sudo_install_script(staged, staging, remote_dir, name, backup, owner),
                            log, cancel, sudo_password, quiet=True)
            if code != 0:
                raise RemoteError("sudo 放入 %s 失败（退出码 %d）。" % (remote_dir, code))
        finally:
            for path in (staged, staging):
                try:
                    (sftp.remove if path == staged else sftp.rmdir)(path)
                except IOError:
                    pass
        log("  上传完成：%s%s" % (target, "（所有者 %s）" % owner if owner else ""))
        return target

    def run(self, command, log, cancel, sudo_password=None, quiet=False):
        if not quiet:
            log("$ " + ("sudo " if sudo_password is not None else "") + command)
        channel = self.client.get_transport().open_session()
        asked = 0
        try:
            channel.set_combine_stderr(True)
            channel.exec_command(sudo_wrap(command) if sudo_password is not None else command)
            if sudo_password is None:
                channel.shutdown_write()  # commands that read stdin get EOF instead of hanging
            pending = b""
            marker = SUDO_PROMPT.encode()
            while True:
                if cancel.is_set():
                    channel.close()
                    raise RemoteError("已停止。")
                if channel.recv_ready():
                    pending += channel.recv(65536)
                    if sudo_password is not None and marker in pending:
                        pending = pending.replace(marker, b"")
                        asked += 1
                        if asked > 1:  # sudo asks again: wrong password; never retry automatically
                            channel.close()
                            raise RemoteError("sudo 拒绝了登录密码（密码不对，或该用户没有 sudo 权限）。")
                        channel.sendall((sudo_password + "\n").encode("utf-8"))
                        channel.shutdown_write()
                    *lines, pending = pending.split(b"\n")
                    for line in lines:
                        log("  " + line.decode("utf-8", "replace").rstrip("\r"))
                elif channel.exit_status_ready():
                    while channel.recv_ready():
                        pending += channel.recv(65536)
                    break
                else:
                    channel.status_event.wait(0.1)
            for line in pending.split(b"\n"):
                if line:
                    log("  " + line.decode("utf-8", "replace").rstrip("\r"))
            return channel.recv_exit_status()
        finally:
            channel.close()

    def close(self):
        if self._sftp is not None:
            self._sftp.close()
        self.client.close()


class OpenSshSession(Session):
    """Uses ssh.exe / scp.exe with BatchMode: key-based login only, never prompts for a password."""

    def __init__(self, connection: Connection):
        self.connection = connection
        self.known_hosts = known_hosts_file()
        os.makedirs(os.path.dirname(self.known_hosts), exist_ok=True)

    def _options(self, port_flag: str):
        return [port_flag, str(self.connection.port), "-o", "BatchMode=yes",
                "-o", "StrictHostKeyChecking=accept-new", "-o", "UserKnownHostsFile=" + self.known_hosts,
                "-o", "ConnectTimeout=15"]

    def _target(self) -> str:
        return "%s@%s" % (self.connection.username, self.connection.host)

    def _exec(self, args, log, cancel) -> int:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, creationflags=flags)
        reader = threading.Thread(target=_pump, args=(process, log), daemon=True)
        reader.start()
        try:
            while process.poll() is None:
                if cancel.is_set():
                    process.kill()
                    process.wait()
                    raise RemoteError("已停止。")
                reader.join(0.1)
        finally:
            reader.join(2)
            if not reader.is_alive():
                process.stdout.close()
        return process.returncode

    def upload(self, local, remote_dir, remote_name, backup, log, cancel, sudo_password=None, owner=""):
        if sudo_password is not None:
            raise RemoteError("sudo 步骤需要 paramiko，请先运行 install-deps.bat。")
        validate_remote_dir(remote_dir)
        name = validate_remote_name(remote_name) or os.path.basename(local)
        target = posixpath.join(remote_dir, name)
        partial = posixpath.join(remote_dir, "." + name + ".part")
        q = shlex.quote
        if self._exec(["ssh"] + self._options("-p") + [self._target(), "mkdir -p " + q(remote_dir)], log, cancel):
            raise RemoteError("无法在服务器创建目录 %s（OpenSSH 只支持密钥登录）。" % remote_dir)
        log("上传 %s → %s:%s" % (local, self.connection.host, target))
        if self._exec(["scp"] + self._options("-P") + [local, "%s:%s" % (self._target(), partial)], log, cancel):
            raise RemoteError("scp 上传失败。")
        script = ""
        if backup:
            script = "if [ -e %s ]; then mv %s %s; fi; " % (q(target), q(target),
                                                             q(posixpath.join(remote_dir, backup_name(name))))
        script += "mv -f %s %s" % (q(partial), q(target))
        if self._exec(["ssh"] + self._options("-p") + [self._target(), script], log, cancel):
            raise RemoteError("上传后重命名失败。")
        log("  上传完成：%s" % target)
        return target

    def run(self, command, log, cancel, sudo_password=None):
        if sudo_password is not None:
            raise RemoteError("sudo 步骤需要 paramiko，请先运行 install-deps.bat。")
        log("$ " + command)
        return self._exec(["ssh"] + self._options("-p") + [self._target(), command], log, cancel)


def _pump(process, log: Log) -> None:
    for raw in iter(process.stdout.readline, b""):
        log("  " + raw.decode("utf-8", "replace").rstrip("\r\n"))


def _human(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return ("%d %s" if unit == "B" else "%.1f %s") % (size, unit)
        size /= 1024.0
    return "%d B" % size
