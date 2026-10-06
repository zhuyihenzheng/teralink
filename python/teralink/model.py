"""Connection / task data model, validation and the on-disk store.

Only the Python standard library is used here so the logic can be tested on any OS.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import datetime
from typing import Any, Dict, List, Optional

KIND_SSH = "ssh"
KIND_RDP = "rdp"
KINDS = (KIND_SSH, KIND_RDP)

STEP_LOCAL = "local"     # run a local command / script
STEP_WAR = "war"         # zip a web application directory into a .war
STEP_UPLOAD = "upload"   # upload a local file to a Linux server over SFTP
STEP_REMOTE = "remote"   # run a command on a Linux server over SSH
STEP_TYPES = (STEP_LOCAL, STEP_WAR, STEP_UPLOAD, STEP_REMOTE)
STEP_LABELS = {
    STEP_LOCAL: "本地命令/脚本",
    STEP_WAR: "生成 WAR",
    STEP_UPLOAD: "上传到服务器",
    STEP_REMOTE: "服务器命令",
}

DATA_VERSION = 1
MAX_AFTER_LOGIN = 20
_HOST_RE = re.compile(r"^(?=.{1,253}$)[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?\.?$")


def _has_control(value: str) -> bool:
    return any(ord(c) < 32 or ord(c) == 127 for c in value)


def new_id() -> str:
    return uuid.uuid4().hex


def validate_host(host: str) -> str:
    if not host or not host.strip() or len(host) > 253 or host != host.strip():
        raise ValueError("请输入主机名或 IP 地址，不要包含协议、端口或空格。")
    # Never allow Tera Term switches, URLs, userinfo or command-line quoting in a host.
    if any(c.isspace() or _has_control(c) or c in "/\\\"';@" for c in host):
        raise ValueError("主机只填写主机名或 IP 地址，端口请单独填写。")
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    if not _HOST_RE.match(host):
        raise ValueError("主机名格式无效；国际域名请填写 punycode。")
    for label in host.rstrip(".").split("."):
        if not 1 <= len(label) <= 63 or label.startswith("-") or label.endswith("-"):
            raise ValueError("主机名格式无效。")
    return host


def validate_remote_dir(path: str) -> str:
    if not path or not path.startswith("/") or _has_control(path) or len(path) > 1024:
        raise ValueError("服务器路径必须是以 / 开头的绝对路径，例如 /opt/tomcat/webapps。")
    return path


def validate_remote_name(name: str) -> str:
    if name and (name in (".", "..") or "/" in name or "\\" in name or _has_control(name) or len(name) > 255):
        raise ValueError("服务器上的文件名不能包含 / 或 \\。")
    return name


@dataclass
class Connection:
    id: str = field(default_factory=new_id)
    name: str = ""
    kind: str = KIND_SSH
    host: str = ""
    port: int = 22
    username: str = ""
    group: str = ""
    notes: str = ""
    protected_password: str = ""
    rdp_full_screen: bool = False
    rdp_file_path: str = ""
    rdp_file_hash: str = ""
    favorite: bool = False
    last_launched: str = ""
    after_login: str = ""          # SSH: commands typed into Tera Term after login, one per line
    sudo_auto_password: bool = False  # answer sudo's password prompt with the login password

    def after_login_commands(self) -> List[str]:
        return [line.strip() for line in self.after_login.splitlines() if line.strip()]

    def validate(self) -> "Connection":
        if not re.fullmatch(r"[0-9a-f]{32}", self.id or ""):
            raise ValueError("连接 ID 无效。")
        if self.kind not in KINDS:
            raise ValueError("连接类型无效。")
        if (len(self.rdp_file_path) > 4096 or _has_control(self.rdp_file_path)
                or (self.rdp_file_path and not self.rdp_file_path.lower().endswith(".rdp"))):
            raise ValueError("请选择有效的 .rdp 连接文件。")
        if self.rdp_file_path and not re.fullmatch(r"[0-9A-Fa-f]{64}", self.rdp_file_hash or ""):
            raise ValueError("RDP 文件校验信息无效，请重新选择文件。")
        if not self.name.strip() or len(self.name) > 100:
            raise ValueError("连接名称必填，最多 100 字。")
        validate_host(self.host)
        if not isinstance(self.port, int) or isinstance(self.port, bool) or not 1 <= self.port <= 65535:
            raise ValueError("端口必须在 1–65535 之间。")
        if not self.username.strip() or len(self.username) > 255 or _has_control(self.username):
            raise ValueError("请输入有效的用户名（最多 255 字）。")
        if len(self.group) > 100 or len(self.notes) > 2000:
            raise ValueError("分组最多 100 字，备注最多 2000 字。")
        commands = self.after_login_commands()
        if len(commands) > MAX_AFTER_LOGIN or any(_has_control(c) or len(c.encode("utf-8")) > 400 for c in commands):
            raise ValueError("登录后命令最多 %d 行，每行最多 400 字节，不能包含控制字符。" % MAX_AFTER_LOGIN)
        if not self.protected_password or len(self.protected_password) > 32768:
            raise ValueError("请保存登录密码。")
        if not re.fullmatch(r"[A-Za-z0-9+/=]+", self.protected_password):
            raise ValueError("保存的密码数据格式无效。")
        return self

    def macro_connect_command(self, password: str) -> str:
        """For the already-linked Tera Term macro only. NEVER use as process arguments or write to disk."""
        if self.kind != KIND_SSH:
            raise ValueError("只有 SSH 连接可使用 Tera Term 宏。")
        validate_host(self.host)
        if not 1 <= self.port <= 65535:
            raise ValueError("端口无效。")
        if not self.username.strip() or _has_control(self.username) or not password or _has_control(password):
            raise ValueError("用户名和密码不能为空，且不能包含换行或控制字符。")

        def quote(value: str) -> str:
            return '"' + value.replace('"', '""') + '"'

        command = "%s /P=%d /ssh /2 /auth=password /user=%s /passwd=%s" % (
            self.host, self.port, quote(self.username), quote(password))
        if len(command.encode("utf-8")) > 511:
            raise ValueError("连接信息过长：Tera Term 宏的连接参数最多 511 个 UTF-8 字节，请缩短主机名、用户名或密码。")
        return command

    def macro_payload(self, password: str) -> str:
        """Everything the Tera Term macro reads from the pipe, one item per line: connect command,
        sudo flag ("1"/"0"), sudo answer (password, or "-"), the number of after-login commands, the commands.
        Never an empty line (TTL filereadln does not return on one), and simple fields only (no string
        slicing in TTL)."""
        commands = self.after_login_commands()
        use_sudo = self.sudo_auto_password and bool(commands)
        lines = [self.macro_connect_command(password), "1" if use_sudo else "0", password if use_sudo else "-",
                 str(len(commands))] + commands
        return "\r\n".join(lines) + "\r\n"

    def search_text(self) -> str:
        return "\n".join([self.name, self.host, self.username, self.group, self.kind]).lower()


@dataclass
class Step:
    type: str = STEP_LOCAL
    name: str = ""
    # local
    command: str = ""
    cwd: str = ""
    # war
    source_dir: str = ""
    output: str = ""
    excludes: str = ""
    # upload / remote
    connection_id: str = ""
    local: str = ""
    remote_dir: str = ""
    remote_name: str = ""
    backup: bool = True
    use_sudo: bool = False   # upload / remote: run with sudo, answering its prompt with the login password
    owner: str = ""          # upload with sudo: chown target, e.g. tomcat:tomcat
    # common
    ignore_error: bool = False

    def validate(self) -> "Step":
        if self.type not in STEP_TYPES:
            raise ValueError("步骤类型无效：%s" % self.type)
        if len(self.name) > 100:
            raise ValueError("步骤名称最多 100 字。")
        if self.type == STEP_LOCAL and not self.command.strip():
            raise ValueError("本地命令不能为空。")
        if self.type == STEP_WAR and (not self.source_dir.strip() or not self.output.strip()):
            raise ValueError("生成 WAR 需要填写源目录和输出文件。")
        if self.type == STEP_WAR and not self.output.lower().endswith(".war"):
            raise ValueError("输出文件请以 .war 结尾。")
        if self.type in (STEP_UPLOAD, STEP_REMOTE) and not self.connection_id:
            raise ValueError("请选择服务器连接。")
        if self.type == STEP_UPLOAD:
            if not self.local.strip():
                raise ValueError("请填写要上传的本地文件。")
            validate_remote_dir(self.remote_dir)
            validate_remote_name(self.remote_name)
        if self.type == STEP_REMOTE and not self.command.strip():
            raise ValueError("服务器命令不能为空。")
        if self.owner and (self.type != STEP_UPLOAD or not self.use_sudo
                           or not re.fullmatch(r"[A-Za-z0-9._-]+(:[A-Za-z0-9._-]+)?", self.owner)):
            raise ValueError("所有者需要勾选 sudo，格式为 用户 或 用户:组，例如 tomcat:tomcat。")
        return self

    def title(self) -> str:
        if self.name:
            return self.name
        if self.type == STEP_LOCAL:
            return self.command.splitlines()[0][:60] if self.command else "本地命令"
        if self.type == STEP_WAR:
            return "打包 %s" % os.path.basename(self.output.rstrip("/\\"))
        if self.type == STEP_UPLOAD:
            return "上传到 %s" % self.remote_dir
        return self.command.splitlines()[0][:60] if self.command else "服务器命令"


@dataclass
class Task:
    id: str = field(default_factory=new_id)
    name: str = ""
    description: str = ""
    confirm: bool = True
    steps: List[Step] = field(default_factory=list)

    def validate(self) -> "Task":
        if not re.fullmatch(r"[0-9a-f]{32}", self.id or ""):
            raise ValueError("任务 ID 无效。")
        if not self.name.strip() or len(self.name) > 100:
            raise ValueError("任务名称必填，最多 100 字。")
        if len(self.description) > 2000:
            raise ValueError("任务说明最多 2000 字。")
        if not self.steps:
            raise ValueError("任务至少需要一个步骤。")
        if len(self.steps) > 100:
            raise ValueError("任务步骤最多 100 个。")
        for index, step in enumerate(self.steps, 1):
            try:
                step.validate()
            except ValueError as error:
                raise ValueError("第 %d 步：%s" % (index, error))
        return self

    def connection_ids(self) -> List[str]:
        return [s.connection_id for s in self.steps if s.type in (STEP_UPLOAD, STEP_REMOTE)]


@dataclass
class AppData:
    version: int = DATA_VERSION
    teraterm_path: str = ""
    exit_after_launch: bool = False
    connections: List[Connection] = field(default_factory=list)
    tasks: List[Task] = field(default_factory=list)

    def validate(self) -> "AppData":
        if self.version != DATA_VERSION:
            raise ValueError("数据文件来自不支持的版本，请升级工具。")
        if len(self.connections) > 10000 or len(self.tasks) > 10000:
            raise ValueError("数据文件结构无效。")
        ids = [c.id for c in self.connections]
        if len(set(ids)) != len(ids):
            raise ValueError("数据文件包含重复的连接 ID。")
        task_ids = [t.id for t in self.tasks]
        if len(set(task_ids)) != len(task_ids):
            raise ValueError("数据文件包含重复的任务 ID。")
        for c in self.connections:
            c.validate()
        for t in self.tasks:
            t.validate()
        return self

    def connection(self, connection_id: str) -> Optional[Connection]:
        return next((c for c in self.connections if c.id == connection_id), None)

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: Dict[str, Any]) -> "AppData":
        if not isinstance(raw, dict):
            raise ValueError("数据文件为空或损坏。")
        return cls(
            version=raw.get("version", DATA_VERSION),
            teraterm_path=_str(raw.get("teraterm_path", "")),
            exit_after_launch=bool(raw.get("exit_after_launch", False)),
            connections=[_load(Connection, c) for c in _list(raw.get("connections"))],
            tasks=[_load_task(t) for t in _list(raw.get("tasks"))],
        )


def _str(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("数据文件包含无效字段。")
    return value


def _list(value: Any) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("数据文件结构无效。")
    return value


def _load(cls, raw: Any):
    if not isinstance(raw, dict):
        raise ValueError("数据文件包含无效记录。")
    names = {f.name: f for f in fields(cls)}
    values = {}
    for key, value in raw.items():
        if key not in names or key == "steps":
            continue
        expected = names[key].type
        if expected in ("str", str) and not isinstance(value, str):
            raise ValueError("字段 %s 类型无效。" % key)
        if expected in ("bool", bool) and not isinstance(value, bool):
            raise ValueError("字段 %s 类型无效。" % key)
        if expected in ("int", int) and (not isinstance(value, int) or isinstance(value, bool)):
            raise ValueError("字段 %s 类型无效。" % key)
        values[key] = value
    return cls(**values)


def _load_task(raw: Any) -> Task:
    task = _load(Task, raw)
    task.steps = [_load(Step, s) for s in _list(raw.get("steps"))]
    return task


def import_legacy(raw: Dict[str, Any]) -> List[Connection]:
    """Convert the C# TeraLink v1/v2 connections.json (PascalCase, numeric enum) into connections.

    Both versions DPAPI-protect the UTF-8 password with the current-user scope, so the ciphertext is reused as-is.
    """
    if not isinstance(raw, dict) or raw.get("Version") not in (1, 2):
        raise ValueError("不是可识别的旧版 TeraLink 连接文件。")
    result = []
    for item in _list(raw.get("Connections")):
        if not isinstance(item, dict):
            raise ValueError("旧版连接文件包含无效记录。")
        kind = item.get("Kind", 0)
        last = item.get("LastLaunched") or ""
        connection = Connection(
            id=str(item.get("Id", "")).replace("-", "").lower() or new_id(),
            name=_str(item.get("Name", "")),
            kind=KIND_RDP if kind in (1, "Rdp") else KIND_SSH,
            host=_str(item.get("Host", "")),
            port=int(item.get("Port", 22)),
            username=_str(item.get("Username", "")),
            group=_str(item.get("Group", "")),
            notes=_str(item.get("Notes", "")),
            protected_password=_str(item.get("ProtectedPassword", "")),
            rdp_full_screen=bool(item.get("RdpFullScreen", False)),
            rdp_file_path=_str(item.get("RdpFilePath", "")),
            rdp_file_hash=_str(item.get("RdpFileHash", "")),
            favorite=bool(item.get("Favorite", False)),
            last_launched=str(last),
        )
        result.append(connection.validate())
    return result


def merge_connections(existing: List[Connection], incoming: List[Connection]) -> List[Connection]:
    known = {c.id for c in existing}
    return list(existing) + [c for c in incoming if c.id not in known]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def clone_task(task: Task) -> Task:
    return replace(task, id=new_id(), name=(task.name + " 副本")[:100], steps=[replace(s) for s in task.steps])


class Store:
    """JSON store with an exclusive process lock and atomic replace on save."""

    def __init__(self, directory: str):
        self.directory = directory
        os.makedirs(directory, exist_ok=True)
        self.path = os.path.join(directory, "data.json")
        self._lock = open(os.path.join(directory, "data.lock"), "a+")
        try:
            _lock_file(self._lock)
        except OSError:
            self._lock.close()
            raise RuntimeError("TeraLink 已在运行。请切换到已打开的窗口；同时只允许一个实例修改数据。")

    def load(self) -> AppData:
        if not os.path.exists(self.path):
            return AppData()
        if os.path.getsize(self.path) > 16 * 1024 * 1024:
            raise ValueError("数据文件过大。")
        with open(self.path, "r", encoding="utf-8") as handle:
            try:
                raw = json.load(handle)
            except json.JSONDecodeError:
                raise ValueError("数据文件损坏：%s。请修复或移走后重启（不会自动重置）。" % self.path)
        return AppData.from_json(raw).validate()

    def save(self, data: AppData) -> None:
        data.validate()
        temporary = "%s.%s.tmp" % (self.path, new_id())
        try:
            with open(temporary, "x", encoding="utf-8") as handle:
                json.dump(data.to_json(), handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)

    def close(self) -> None:
        try:
            _unlock_file(self._lock)
        except OSError:
            pass
        self._lock.close()


if os.name == "nt":
    import msvcrt

    def _lock_file(handle) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock_file(handle) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock_file(handle) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock_file(handle) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
