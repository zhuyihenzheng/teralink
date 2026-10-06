"""Task runner: local commands, WAR packaging, upload to Linux servers, remote commands."""
from __future__ import annotations

import fnmatch
import glob
import locale
import os
import re
import subprocess
import threading
import time
import zipfile
from datetime import datetime
from typing import Callable, Dict, List, Optional

from . import remote
from .model import STEP_LOCAL, STEP_REMOTE, STEP_UPLOAD, STEP_WAR, AppData, Step, Task

Log = Callable[[str], None]


class StepFailed(Exception):
    pass


class Stopped(Exception):
    pass


def expand(text: str, variables: Dict[str, str]) -> str:
    """Replace only ${NAME} placeholders this tool defines; shell syntax such as $HOME or ${FOO} is left alone."""
    if not text:
        return text
    return re.sub(r"\$\{([A-Z_]+)\}", lambda m: variables.get(m.group(1), m.group(0)), text)


def base_variables(task: Task, start: Optional[datetime] = None) -> Dict[str, str]:
    start = start or datetime.now()
    return {
        "TASK": task.name,
        "TODAY": start.strftime("%Y%m%d"),
        "NOW": start.strftime("%Y%m%d-%H%M%S"),
        "ARTIFACT": "",
    }


def resolve_local_file(pattern: str, base: str = "") -> str:
    """A file path or a glob such as target\\*.war; with several matches the newest file wins."""
    pattern = os.path.expanduser(pattern)
    if base and not os.path.isabs(pattern):
        pattern = os.path.join(base, pattern)
    if any(ch in pattern for ch in "*?["):
        matches = [p for p in glob.glob(pattern) if os.path.isfile(p)]
        if not matches:
            raise StepFailed("没有找到匹配的文件：%s" % pattern)
        return max(matches, key=os.path.getmtime)
    if not os.path.isfile(pattern):
        raise StepFailed("文件不存在：%s" % pattern)
    return pattern


def build_war(source_dir: str, output: str, excludes: str, log: Log, cancel: threading.Event) -> str:
    """A WAR is a zip of the web application root (the folder that contains WEB-INF)."""
    if not os.path.isdir(source_dir):
        raise StepFailed("源目录不存在：%s" % source_dir)
    if not os.path.isdir(os.path.join(source_dir, "WEB-INF")):
        log("  注意：%s 下没有 WEB-INF 目录，确认选的是 Web 应用根目录。" % source_dir)
    patterns = [p.strip().replace("\\", "/") for p in excludes.replace(";", ",").split(",") if p.strip()]
    output = os.path.abspath(output)
    source = os.path.abspath(source_dir)
    temporary = output + ".tmp"
    if output.startswith(source + os.sep):  # never pack the WAR (or its temp file) into itself
        for path in (output, temporary):
            patterns.append(os.path.relpath(path, source).replace("\\", "/"))
    os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
    count = 0
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            has_manifest = os.path.isfile(os.path.join(source, "META-INF", "MANIFEST.MF"))
            if not has_manifest:
                archive.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\r\nCreated-By: TeraLink\r\n\r\n")
            for root, dirs, files in os.walk(source):
                dirs.sort()
                relative_root = os.path.relpath(root, source).replace("\\", "/")
                relative_root = "" if relative_root == "." else relative_root + "/"
                dirs[:] = [d for d in dirs if not _excluded(relative_root + d, patterns)]
                for name in sorted(files):
                    if cancel.is_set():
                        raise Stopped()
                    relative = relative_root + name
                    if _excluded(relative, patterns):
                        continue
                    archive.write(os.path.join(root, name), relative)
                    count += 1
        os.replace(temporary, output)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)
    log("  已生成 %s（%d 个文件，%.1f MB）" % (output, count, os.path.getsize(output) / 1048576.0))
    return output


def _excluded(relative: str, patterns: List[str]) -> bool:
    name = relative.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(relative, p) or fnmatch.fnmatch(name, p) or relative.startswith(p.rstrip("/") + "/")
               for p in patterns)


def join_lines(command: str) -> str:
    """cmd /c only runs the first line, so multi-line commands become `a && b`: stop at the first failure."""
    lines = [line.strip() for line in command.splitlines() if line.strip()]
    return " && ".join(lines)


def run_local(command: str, cwd: str, log: Log, cancel: threading.Event) -> int:
    if cwd and not os.path.isdir(cwd):
        raise StepFailed("工作目录不存在：%s" % cwd)
    command = join_lines(command)
    log("> " + command + ("    （目录：%s）" % cwd if cwd else ""))
    encoding = locale.getpreferredencoding(False) or "utf-8"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(command, shell=True, cwd=cwd or None, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=flags,
                               start_new_session=os.name != "nt")

    def pump():
        for raw in iter(process.stdout.readline, b""):
            log("  " + raw.decode(encoding, "replace").rstrip("\r\n"))

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    try:
        while process.poll() is None:
            if cancel.is_set():
                _kill_tree(process)
                process.wait()
                raise Stopped()
            reader.join(0.1)
    finally:
        reader.join(2)
        if not reader.is_alive():  # closing while the reader still blocks would hang
            process.stdout.close()
    return process.returncode


def _kill_tree(process: subprocess.Popen) -> None:
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.call(["taskkill", "/T", "/F", "/PID", str(process.pid)],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
    else:
        try:
            os.killpg(process.pid, 9)
        except OSError:
            process.kill()


class Runner:
    def __init__(self, data: AppData, log: Log, confirm_host_key: remote.ConfirmHostKey,
                 open_session: Callable = remote.open_session):
        self.data = data
        self.log = log
        self.confirm_host_key = confirm_host_key
        self.open_session = open_session
        self.cancel = threading.Event()
        self.sessions: Dict[str, remote.Session] = {}

    def _session(self, connection_id: str) -> remote.Session:
        if connection_id not in self.sessions:
            connection = self.data.connection(connection_id)
            if connection is None:
                raise StepFailed("任务引用的服务器连接已被删除，请编辑任务重新选择。")
            if connection.kind != "ssh":
                raise StepFailed("「%s」是 RDP 连接，上传和服务器命令需要 SSH 连接。" % connection.name)
            self.sessions[connection_id] = self.open_session(connection, self.confirm_host_key, self.log)
        return self.sessions[connection_id]

    def run(self, task: Task) -> bool:
        task.validate()
        started = time.monotonic()
        variables = base_variables(task)
        self.log("══ 开始任务「%s」 %s" % (task.name, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        ok = True
        try:
            for index, step in enumerate(task.steps, 1):
                if self.cancel.is_set():
                    raise Stopped()
                self.log("── 第 %d/%d 步：%s" % (index, len(task.steps), step.title()))
                try:
                    self._run_step(step, variables)
                except Stopped:
                    raise
                except Exception as error:  # StepFailed, RemoteError, OSError, paramiko errors, ...
                    if self.cancel.is_set():
                        raise Stopped()
                    message = str(error) or type(error).__name__
                    if not isinstance(error, (StepFailed, remote.RemoteError, OSError, ValueError)):
                        message = "%s: %s" % (type(error).__name__, message)
                    if step.ignore_error:
                        self.log("  ✗ 失败但已设置忽略：%s" % message)
                        continue
                    self.log("  ✗ 失败：%s" % message)
                    ok = False
                    break
        except Stopped:
            self.log("■ 已停止。")
            ok = False
        except Exception as error:  # surface anything unexpected instead of killing the worker silently
            self.log("  ✗ 意外错误：%s: %s" % (type(error).__name__, error))
            ok = False
        finally:
            for session in self.sessions.values():
                try:
                    session.close()
                except Exception:
                    pass
            self.sessions.clear()
        self.log("══ %s，用时 %.1f 秒" % ("任务完成" if ok else "任务未完成", time.monotonic() - started))
        return ok

    def _run_step(self, step: Step, variables: Dict[str, str]) -> None:
        if step.type == STEP_LOCAL:
            cwd = expand(step.cwd, variables)
            code = run_local(expand(step.command, variables), cwd, self.log, self.cancel)
            if code != 0:
                raise StepFailed("命令退出码 %d" % code)
        elif step.type == STEP_WAR:
            output = build_war(expand(step.source_dir, variables), expand(step.output, variables),
                               step.excludes, self.log, self.cancel)
            variables["ARTIFACT"] = output
        elif step.type == STEP_UPLOAD:
            local = resolve_local_file(expand(step.local, variables))
            variables["ARTIFACT"] = local
            target = self._session(step.connection_id).upload(
                local, expand(step.remote_dir, variables), expand(step.remote_name, variables),
                step.backup, self.log, self.cancel)
            variables["REMOTE_FILE"] = target
        elif step.type == STEP_REMOTE:
            code = self._session(step.connection_id).run(expand(step.command, variables), self.log, self.cancel)
            if code != 0:
                raise StepFailed("服务器命令退出码 %d" % code)


def templates(connection_id: str = "") -> Dict[str, Task]:
    """Starting points offered by「从模板新建」."""
    return {
        "Maven 打包并部署到 Tomcat": Task(name="Maven 打包并部署", steps=[
            Step(type=STEP_LOCAL, name="Maven 打包", command="mvn -q clean package -DskipTests",
                 cwd=r"C:\work\myapp"),
            Step(type=STEP_UPLOAD, name="上传 WAR", connection_id=connection_id, local=r"C:\work\myapp\target\*.war",
                 remote_dir="/opt/tomcat/webapps", remote_name="myapp.war", backup=True),
            Step(type=STEP_REMOTE, name="查看部署结果", connection_id=connection_id,
                 command="sleep 5; ls -l /opt/tomcat/webapps; tail -n 30 /opt/tomcat/logs/catalina.out"),
        ]),
        "目录打包 WAR 并上传": Task(name="打包 WAR 并上传", steps=[
            Step(type=STEP_WAR, name="生成 WAR", source_dir=r"C:\work\myapp\WebContent",
                 output=r"C:\work\dist\myapp-${NOW}.war", excludes="*.bak, .git, Thumbs.db"),
            Step(type=STEP_UPLOAD, name="上传 WAR", connection_id=connection_id, local="${ARTIFACT}",
                 remote_dir="/opt/tomcat/webapps", remote_name="myapp.war", backup=True),
        ]),
        "运行本地脚本": Task(name="运行本地脚本", confirm=False, steps=[
            Step(type=STEP_LOCAL, name="运行脚本", command=r"call C:\work\scripts\build.bat", cwd=r"C:\work\scripts"),
        ]),
        "服务器命令（重启服务）": Task(name="重启服务", steps=[
            Step(type=STEP_REMOTE, name="重启", connection_id=connection_id,
                 command="sudo -n systemctl restart tomcat && systemctl status tomcat --no-pager | head -n 15"),
        ]),
    }
