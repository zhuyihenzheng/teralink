"""tkinter user interface (standard library only)."""
from __future__ import annotations

import json
import os
import queue
import sys
import threading
import tkinter as tk
from dataclasses import replace
from datetime import datetime
from tkinter import filedialog, font as tkfont, messagebox, scrolledtext, ttk
from typing import Callable, List, Optional

from . import __version__, rdp, remote, tasks, teraterm, vault
from .model import (KIND_RDP, KIND_SSH, STEP_LABELS, STEP_LOCAL, STEP_REMOTE, STEP_TYPES, STEP_UPLOAD, STEP_WAR,
                    AppData, Connection, Step, Task, clone_task, import_legacy, merge_connections, new_id, now_iso,
                    validate_host)
from .paths import data_dir, is_store_python, legacy_data_file, log_dir

KIND_LABELS = {KIND_SSH: "Tera Term · SSH", KIND_RDP: "Windows 远程桌面 · RDP"}
_TEXT_STYLE = {"relief": "solid", "borderwidth": 1, "highlightthickness": 0, "undo": True}
_PROSE_FONT = "TkDefaultFont"  # tk.Text defaults to a monospace font
VARIABLE_HELP = ("可用变量：${ARTIFACT} 上一步生成/上传的本地文件，${REMOTE_FILE} 上一次上传到服务器的路径，"
                 "${NOW} 时间戳 20261006-153000，${TODAY} 日期，${TASK} 任务名。")


def _setup_fonts(root: tk.Tk) -> None:
    families = set(tkfont.families(root))
    for family in ("Microsoft YaHei UI", "Microsoft YaHei", "Yu Gothic UI", "Meiryo UI", "Segoe UI"):
        if family in families:
            for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
                tkfont.nametofont(name).configure(family=family, size=10)
            break


class App:
    def __init__(self, root: tk.Tk, store, data: AppData):
        self.root, self.store, self.data = root, store, data
        self.events: "queue.Queue[tuple]" = queue.Queue()
        self.busy_connection: Optional[threading.Event] = None
        self.runner: Optional[tasks.Runner] = None
        self.closing = False
        os.makedirs(log_dir(), exist_ok=True)
        self.log_path = os.path.join(log_dir(), datetime.now().strftime("%Y%m%d") + ".log")

        _setup_fonts(root)
        root.title("TeraLink %s · Tera Term / RDP / 部署脚本" % __version__)
        # Fit small laptop screens (1366x768 leaves ~728 px); with DPI awareness these are physical pixels.
        width = min(1120, root.winfo_screenwidth() - 40)
        height = min(760, root.winfo_screenheight() - 90)
        root.geometry("%dx%d+%d+%d" % (width, height, max(0, (root.winfo_screenwidth() - width) // 2), 10))
        root.minsize(min(900, width), min(560, height))
        style = ttk.Style(root)
        style.configure("Title.TLabel", font=(tkfont.nametofont("TkDefaultFont").actual("family"), 18, "bold"))
        style.configure("Muted.TLabel", foreground="#666666")
        style.configure("Accent.TButton", font=(tkfont.nametofont("TkDefaultFont").actual("family"), 10, "bold"))

        header = ttk.Frame(root, padding=(16, 12, 16, 4))
        header.pack(fill="x")
        ttk.Label(header, text="TeraLink", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="  连接服务器 · 打包 WAR · 上传部署", style="Muted.TLabel").pack(side="left", pady=(8, 0))

        paned = ttk.PanedWindow(root, orient="vertical")
        paned.pack(fill="both", expand=True, padx=16, pady=(4, 0))
        self.notebook = ttk.Notebook(paned)
        self.connections_tab = ConnectionsTab(self.notebook, self)
        self.tasks_tab = TasksTab(self.notebook, self)
        self.notebook.add(self.connections_tab, text="  连接  ")
        self.notebook.add(self.tasks_tab, text="  任务 / 脚本  ")
        paned.add(self.notebook, weight=3)

        log_frame = ttk.Frame(paned, padding=(0, 8, 0, 0))
        bar = ttk.Frame(log_frame)
        bar.pack(fill="x")
        ttk.Label(bar, text="执行日志").pack(side="left")
        ttk.Button(bar, text="打开日志文件夹", command=lambda: _open_folder(log_dir())).pack(side="right")
        ttk.Button(bar, text="清空", command=self.clear_log).pack(side="right", padx=4)
        self.log_text = scrolledtext.ScrolledText(log_frame, height=10, state="disabled", wrap="none",
                                                  font=("Consolas", 10))
        self.log_text.pack(fill="both", expand=True, pady=(4, 0))
        paned.add(log_frame, weight=2)

        self.status = tk.StringVar(value="就绪 · 密码由 Windows 账户加密保存在本机 · SSH 组件：%s" % remote.backend_name())
        ttk.Label(root, textvariable=self.status, style="Muted.TLabel", padding=(16, 6)).pack(fill="x")

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.bind("<Control-n>", lambda _e: self.connections_tab.edit_connection(None))
        root.bind("<Control-f>", lambda _e: self.connections_tab.search_entry.focus_set())
        root.after(100, self._drain_events)
        self.log("TeraLink %s · Python %s%s · 数据目录 %s" % (
            __version__, sys.version.split()[0], "（Microsoft Store 版）" if is_store_python() else "", data_dir()))
        self.offer_legacy_import(first_run=True)

    # ---------- shared helpers ----------
    def commit(self, data: AppData) -> None:
        self.store.save(data)
        self.data = data
        self.connections_tab.refresh()
        self.tasks_tab.refresh()

    def error(self, error: BaseException, title: str = "出错了") -> None:
        messagebox.showerror(title, str(error) or type(error).__name__, parent=self.root)

    def post(self, kind: str, *payload) -> None:
        """Thread-safe: queue work for the UI thread."""
        self.events.put((kind,) + payload)

    def log(self, line: str) -> None:
        self.post("log", line)

    def call_in_ui(self, func: Callable, *args):
        """Called from a worker thread: run func on the UI thread and wait for its result."""
        done, box = threading.Event(), {}
        self.post("call", func, args, done, box)
        done.wait()
        if "error" in box:
            raise box["error"]
        return box.get("result")

    def _drain_events(self) -> None:
        lines: List[str] = []
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "log":
                    lines.append(event[1])
                elif event[0] == "call":
                    _, func, args, done, box = event
                    try:
                        box["result"] = func(*args)
                    except BaseException as error:
                        box["error"] = error
                    finally:
                        done.set()
                elif event[0] == "do":
                    event[1]()
        except queue.Empty:
            pass
        if lines:
            self._append_log(lines)
        if not self.closing:
            self.root.after(100, self._drain_events)

    def _append_log(self, lines: List[str]) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        text = "".join("[%s] %s\n" % (stamp, line) for line in lines)
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text)
        if int(self.log_text.index("end-1c").split(".")[0]) > 5000:
            self.log_text.delete("1.0", "1000.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        try:
            with open(self.log_path, "a", encoding="utf-8") as handle:
                handle.write(text)
        except OSError:
            pass

    def clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def confirm_host_key(self, host: str, port: int, key_type: str, fingerprint: str) -> bool:
        return self.call_in_ui(lambda: messagebox.askyesno(
            "确认服务器指纹",
            "第一次连接 %s:%d。\n\n密钥类型：%s\n指纹：%s\n\n请与服务器管理员提供的指纹核对。确认后会保存，"
            "以后指纹变化时会拦截。\n\n信任并继续？" % (host, port, key_type, fingerprint),
            icon="warning", default="no", parent=self.root))

    def offer_legacy_import(self, first_run: bool = False) -> None:
        path = legacy_data_file()
        if not os.path.isfile(path):
            if not first_run:
                messagebox.showinfo("导入旧版连接", "没有找到旧版 TeraLink 的数据：\n%s" % path, parent=self.root)
            return
        if first_run and self.data.connections:
            return
        if not messagebox.askyesno("导入旧版连接", "发现旧版 TeraLink（exe 版）的连接数据：\n%s\n\n导入到这里？"
                                   "（原文件不会被修改；密码沿用 Windows 账户加密，同一账户下可直接使用）" % path,
                                   parent=self.root):
            return
        try:
            with open(path, "r", encoding="utf-8-sig") as handle:
                incoming = import_legacy(json.load(handle))
            merged = merge_connections(self.data.connections, incoming)
            added = len(merged) - len(self.data.connections)
            self.commit(replace(self.data, connections=merged))
            self.status.set("已导入 %d 个连接（跳过 %d 个已存在的）。" % (added, len(incoming) - added))
        except Exception as error:
            self.error(error, "导入失败")

    def on_close(self) -> None:
        if self.runner is not None:
            if not messagebox.askyesno("退出", "任务还在运行，停止并退出？", parent=self.root):
                return
            self.runner.cancel.set()
        if self.busy_connection is not None:
            self.busy_connection.set()
        self.closing = True
        self.root.after(300, self.root.destroy)


class ConnectionsTab(ttk.Frame):
    def __init__(self, parent, app: App):
        super().__init__(parent, padding=12)
        self.app = app
        self.search = tk.StringVar()
        self.only_favorites = tk.BooleanVar(value=False)
        self.exit_after = tk.BooleanVar(value=app.data.exit_after_launch)

        left = ttk.Frame(self)
        left.pack(side="left", fill="both", expand=True)
        top = ttk.Frame(left)
        top.pack(fill="x")
        ttk.Label(top, text="搜索").pack(side="left")
        self.search_entry = ttk.Entry(top, textvariable=self.search)
        self.search_entry.pack(side="left", fill="x", expand=True, padx=6)
        ttk.Checkbutton(top, text="只看收藏", variable=self.only_favorites, command=self.refresh).pack(side="left")
        self.search.trace_add("write", lambda *_: self.refresh())

        columns = ("name", "kind", "host", "user", "group")
        self.tree = ttk.Treeview(left, columns=columns, show="headings", selectmode="browse")
        for column, title, width in zip(columns, ("连接", "类型", "主机", "用户", "分组"), (180, 60, 160, 100, 100)):
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width, anchor="w")
        scroll = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True, pady=(8, 0))
        scroll.pack(side="left", fill="y", pady=(8, 0))
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self.show_details())
        self.tree.bind("<Double-1>", lambda _e: self.connect())
        self.tree.bind("<Return>", lambda _e: self.connect())

        right = ttk.Frame(self, padding=(16, 0, 0, 0), width=360)
        right.pack(side="left", fill="y")
        right.pack_propagate(False)
        # Bottom row first (side="bottom") so settings stay visible however tall the details get.
        self.path_label = ttk.Label(right, text="", style="Muted.TLabel", wraplength=340)
        self.path_label.pack(side="bottom", anchor="w", pady=(6, 0))
        more = ttk.Frame(right)
        more.pack(side="bottom", anchor="w", pady=(6, 0))
        ttk.Button(more, text="Tera Term 路径…", command=self.configure_teraterm).pack(side="left", padx=(0, 6))
        ttk.Button(more, text="导入旧版连接", command=lambda: self.app.offer_legacy_import()).pack(side="left")
        ttk.Checkbutton(right, text="连接后退出启动器", variable=self.exit_after,
                        command=self.save_exit_after).pack(side="bottom", anchor="w")
        self.title = ttk.Label(right, text="", style="Title.TLabel", wraplength=340)
        self.title.pack(anchor="w")
        actions = ttk.Frame(right)
        actions.pack(anchor="w", fill="x", pady=(8, 0))
        self.connect_button = ttk.Button(actions, text="连接 →", style="Accent.TButton", command=self.connect)
        self.connect_button.pack(side="left", padx=(0, 6))
        self.new_button = ttk.Button(actions, text="＋ 新增", command=lambda: self.edit_connection(None))
        self.new_button.pack(side="left", padx=(0, 6))
        self.cancel_button = ttk.Button(actions, text="停止等待", command=self.cancel)
        buttons = ttk.Frame(right)
        buttons.pack(anchor="w", pady=(6, 0))
        self.edit_button = ttk.Button(buttons, text="编辑", command=lambda: self.edit_connection(self.selected()))
        self.fav_button = ttk.Button(buttons, text="收藏", command=self.toggle_favorite)
        self.delete_button = ttk.Button(buttons, text="删除", command=self.delete)
        for button in (self.edit_button, self.fav_button, self.delete_button):
            button.pack(side="left", padx=(0, 6))
        self.details = ttk.Label(right, text="", wraplength=340, justify="left")
        self.details.pack(anchor="w", pady=(12, 0))
        self.refresh()

    def selected(self) -> Optional[Connection]:
        selection = self.tree.selection()
        return self.app.data.connection(selection[0]) if selection else None

    def refresh(self, select: Optional[str] = None) -> None:
        current = select or (self.tree.selection()[0] if self.tree.selection() else None)
        query = self.search.get().strip().lower()
        rows = [c for c in self.app.data.connections
                if (not self.only_favorites.get() or c.favorite) and query in c.search_text()]
        rows.sort(key=lambda c: (not c.favorite, c.group.lower(), c.name.lower()))
        self.tree.delete(*self.tree.get_children())
        for c in rows:
            self.tree.insert("", "end", iid=c.id, values=(("★ " if c.favorite else "") + c.name,
                                                          "RDP" if c.kind == KIND_RDP else "SSH",
                                                          "%s:%d" % (c.host, c.port), c.username, c.group))
        if current and self.tree.exists(current):
            self.tree.selection_set(current)
            self.tree.see(current)
        elif rows:
            self.tree.selection_set(rows[0].id)
        self.path_label.configure(text=("Tera Term：%s" % self.app.data.teraterm_path) if self.app.data.teraterm_path
                                  else "Tera Term 路径：首次连接时自动检测，也可手动选择。")
        self.show_details()

    def show_details(self) -> None:
        c = self.selected()
        busy = self.app.busy_connection is not None
        if c is None:
            self.title.configure(text="连接，只需一步" if not self.app.data.connections else "没有匹配的连接")
            self.details.configure(text="点击「＋ 新增」保存第一台服务器（Ctrl+N）。\n"
                                        "任务里的上传和服务器命令也使用这里保存的 SSH 连接。")
        else:
            self.title.configure(text=c.name)
            last = c.last_launched[:16].replace("T", " ") if c.last_launched else "尚未连接"
            self.details.configure(text="%s  ·  %s\n\n主机：%s:%d\n用户：%s\n密码：已加密保存（Windows 当前账户）"
                                        "\n上次连接：%s\n\n%s" % (
                                            KIND_LABELS[c.kind], c.group or "未分组", c.host, c.port, c.username,
                                            last, ("备注：" + c.notes) if c.notes else ""))
        state = "!disabled" if c is not None and not busy else "disabled"
        for button in (self.connect_button, self.edit_button, self.fav_button, self.delete_button):
            button.state([state])
        self.fav_button.configure(text="取消收藏" if c is not None and c.favorite else "收藏")
        if busy:
            self.cancel_button.pack(side="left", padx=6)
        else:
            self.cancel_button.pack_forget()

    def edit_connection(self, connection: Optional[Connection]) -> None:
        dialog = ConnectionDialog(self.app.root, connection)
        result = dialog.result
        if result is None:
            return
        try:
            others = [c for c in self.app.data.connections if c.id != result.id]
            if connection is not None:
                rdp.forget(connection.id)
            self.app.commit(replace(self.app.data, connections=others + [result]))
            self.refresh(select=result.id)
            self.app.status.set("已保存「%s」。" % result.name)
        except Exception as error:
            self.app.error(error)

    def delete(self) -> None:
        c = self.selected()
        if c is None:
            return
        users = [t.name for t in self.app.data.tasks if c.id in t.connection_ids()]
        message = "删除「%s」及其保存的密码？" % c.name
        if users:
            message += "\n\n注意：这些任务在使用它，删除后需要重新选择服务器：\n" + "\n".join(users)
        if not messagebox.askyesno("删除连接", message, default="no", parent=self.app.root):
            return
        try:
            rdp.forget(c.id)
            self.app.commit(replace(self.app.data, connections=[x for x in self.app.data.connections if x.id != c.id]))
            self.app.status.set("连接已删除。")
        except Exception as error:
            self.app.error(error)

    def toggle_favorite(self) -> None:
        c = self.selected()
        if c is None:
            return
        updated = [replace(x, favorite=not x.favorite) if x.id == c.id else x for x in self.app.data.connections]
        try:
            self.app.commit(replace(self.app.data, connections=updated))
            self.refresh(select=c.id)
        except Exception as error:
            self.app.error(error)

    def save_exit_after(self) -> None:
        try:
            self.app.commit(replace(self.app.data, exit_after_launch=self.exit_after.get()))
        except Exception as error:
            self.exit_after.set(self.app.data.exit_after_launch)
            self.app.error(error)

    def configure_teraterm(self) -> bool:
        path = filedialog.askopenfilename(parent=self.app.root, title="选择 Tera Term 的 ttermpro.exe",
                                          filetypes=[("Tera Term", "ttermpro.exe"), ("程序", "*.exe")])
        if not path:
            return False
        path = os.path.normpath(path)
        try:
            teraterm.validate_executable(path)
            self.app.commit(replace(self.app.data, teraterm_path=path))
            return True
        except Exception as error:
            self.app.error(error)
            return False

    def cancel(self) -> None:
        if self.app.busy_connection is not None:
            self.app.busy_connection.set()

    def connect(self) -> None:
        c = self.selected()
        if c is None or self.app.busy_connection is not None:
            return
        if c.kind == KIND_SSH and not self.app.data.teraterm_path:
            detected = teraterm.find_executable()
            if detected:
                try:
                    self.app.commit(replace(self.app.data, teraterm_path=detected))
                except Exception as error:
                    self.app.error(error)
                    return
            elif not self.configure_teraterm():
                return
        cancel = threading.Event()
        self.app.busy_connection = cancel
        self.show_details()
        path = self.app.data.teraterm_path
        if c.kind == KIND_RDP:
            self.app.status.set("正在打开「%s」的远程桌面。" % c.name)
        else:
            self.app.status.set("正在打开「%s」；首次连接请在 Tera Term 核对主机指纹。" % c.name)

        def work():
            error = None
            self.app.log("══ 连接「%s」 %s@%s:%d（%s）" % (c.name, c.username, c.host, c.port, c.kind.upper()))
            try:
                if c.kind == KIND_RDP:
                    rdp.launch(c)
                else:
                    teraterm.launch(path, c, cancel, self.app.log)
                self.app.log("  完成。")
            except BaseException as caught:
                error = caught
                self.app.log("  ✗ %s: %s" % (type(caught).__name__, str(caught).replace("\n", " ")))
            self.app.post("do", lambda: self._connected(c, error))

        threading.Thread(target=work, name="teralink-connect", daemon=True).start()

    def _connected(self, c: Connection, error: Optional[BaseException]) -> None:
        self.app.busy_connection = None
        if self.app.closing:
            return
        if isinstance(error, teraterm.Cancelled):
            self.app.status.set(str(error))
        elif error is not None:
            self.app.status.set("自动登录未完成。")
            self.app.error(error)
        else:
            self.app.status.set("已打开「%s」的 RDP 客户端；登录结果请查看远程桌面窗口。" % c.name if c.kind == KIND_RDP
                                else "「%s」宏已完成连接，请在 Tera Term 查看会话。" % c.name)
            try:
                updated = [replace(x, last_launched=now_iso()) if x.id == c.id else x
                           for x in self.app.data.connections]
                self.app.commit(replace(self.app.data, connections=updated))
            except Exception as save_error:
                self.app.error(save_error)
            if self.app.data.exit_after_launch and self.app.runner is None:
                self.app.on_close()
                return
        self.refresh(select=c.id)


class TasksTab(ttk.Frame):
    def __init__(self, parent, app: App):
        super().__init__(parent, padding=12)
        self.app = app
        left = ttk.Frame(self)
        left.pack(side="left", fill="both", expand=True)
        self.tree = ttk.Treeview(left, columns=("name", "steps", "servers"), show="headings", selectmode="browse")
        for column, title, width in (("name", "任务", 240), ("steps", "步骤", 60), ("servers", "服务器", 220)):
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width, anchor="w")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self.show_details())
        self.tree.bind("<Double-1>", lambda _e: self.run())

        right = ttk.Frame(self, padding=(16, 0, 0, 0), width=380)
        right.pack(side="left", fill="y")
        right.pack_propagate(False)
        self.title = ttk.Label(right, text="", style="Title.TLabel", wraplength=360)
        self.title.pack(anchor="w")
        self.details = ttk.Label(right, text="", wraplength=360, justify="left")
        self.details.pack(anchor="w", pady=(8, 12))
        run_bar = ttk.Frame(right)
        run_bar.pack(anchor="w")
        self.run_button = ttk.Button(run_bar, text="▶ 运行", style="Accent.TButton", command=self.run)
        self.run_button.pack(side="left")
        self.stop_button = ttk.Button(run_bar, text="■ 停止", command=self.stop)
        self.stop_button.pack(side="left", padx=6)
        edit_bar = ttk.Frame(right)
        edit_bar.pack(anchor="w", pady=(10, 0))
        ttk.Button(edit_bar, text="＋ 新建", command=lambda: self.edit_task(None)).pack(side="left", padx=(0, 6))
        template_button = ttk.Menubutton(edit_bar, text="从模板新建")
        template_menu = tk.Menu(template_button, tearoff=False)
        for label in tasks.templates():
            template_menu.add_command(label=label, command=lambda name=label: self.from_template(name))
        template_button["menu"] = template_menu
        template_button.pack(side="left")
        edit_bar2 = ttk.Frame(right)
        edit_bar2.pack(anchor="w", pady=(6, 0))
        self.edit_button = ttk.Button(edit_bar2, text="编辑", command=lambda: self.edit_task(self.selected()))
        self.copy_button = ttk.Button(edit_bar2, text="复制", command=self.copy)
        self.delete_button = ttk.Button(edit_bar2, text="删除", command=self.delete)
        for button in (self.edit_button, self.copy_button, self.delete_button):
            button.pack(side="left", padx=(0, 6))
        share = ttk.Frame(right)
        share.pack(anchor="w", pady=(6, 0))
        self.export_button = ttk.Button(share, text="导出任务…", command=self.export_task)
        self.export_button.pack(side="left", padx=(0, 6))
        ttk.Button(share, text="导入任务…", command=self.import_task).pack(side="left")
        self.refresh()

    def selected(self) -> Optional[Task]:
        selection = self.tree.selection()
        return next((t for t in self.app.data.tasks if selection and t.id == selection[0]), None)

    def _server_names(self, task: Task) -> str:
        names = []
        for connection_id in task.connection_ids():
            c = self.app.data.connection(connection_id)
            name = c.name if c else "（已删除）"
            if name not in names:
                names.append(name)
        return "、".join(names) or "—"

    def refresh(self, select: Optional[str] = None) -> None:
        current = select or (self.tree.selection()[0] if self.tree.selection() else None)
        self.tree.delete(*self.tree.get_children())
        for t in sorted(self.app.data.tasks, key=lambda t: t.name.lower()):
            self.tree.insert("", "end", iid=t.id, values=(t.name, len(t.steps), self._server_names(t)))
        if current and self.tree.exists(current):
            self.tree.selection_set(current)
        elif self.app.data.tasks:
            self.tree.selection_set(self.tree.get_children()[0])
        self.show_details()

    def show_details(self) -> None:
        t = self.selected()
        running = self.app.runner is not None
        if t is None:
            self.title.configure(text="把重复操作变成一键")
            self.details.configure(text="任务由多个步骤组成：本地命令/脚本、生成 WAR、上传到 Linux 服务器、"
                                        "在服务器执行命令。\n\n点「从模板新建」最快上手。上传和服务器命令使用"
                                        "「连接」页保存的 SSH 账号密码。")
        else:
            self.title.configure(text=t.name)
            lines = [t.description, ""] if t.description else []
            for index, step in enumerate(t.steps, 1):
                server = ""
                if step.type in (STEP_UPLOAD, STEP_REMOTE):
                    c = self.app.data.connection(step.connection_id)
                    server = " @ %s" % (c.name if c else "（连接已删除）")
                lines.append("%d. [%s] %s%s" % (index, STEP_LABELS[step.type], step.title(), server))
            lines.append("")
            lines.append("运行前确认：%s" % ("是" if t.confirm else "否"))
            self.details.configure(text="\n".join(lines))
        has = t is not None
        self.run_button.state(["!disabled" if has and not running else "disabled"])
        self.stop_button.state(["!disabled" if running else "disabled"])
        for button in (self.edit_button, self.copy_button, self.delete_button, self.export_button):
            button.state(["!disabled" if has and not running else "disabled"])

    def _default_connection(self) -> str:
        ssh = [c for c in self.app.data.connections if c.kind == KIND_SSH]
        return ssh[0].id if ssh else ""

    def from_template(self, name: str) -> None:
        template = tasks.templates(self._default_connection())[name]
        self.edit_task(replace(template, id=new_id()), is_new=True)

    def edit_task(self, task: Optional[Task], is_new: bool = False) -> None:
        dialog = TaskDialog(self.app.root, self.app, task)
        result = dialog.result
        if result is None:
            return
        try:
            others = [t for t in self.app.data.tasks if t.id != result.id]
            self.app.commit(replace(self.app.data, tasks=others + [result]))
            self.refresh(select=result.id)
            self.app.status.set("已保存任务「%s」。" % result.name)
        except Exception as error:
            self.app.error(error)

    def copy(self) -> None:
        t = self.selected()
        if t is None:
            return
        try:
            copied = clone_task(t)
            self.app.commit(replace(self.app.data, tasks=self.app.data.tasks + [copied]))
            self.refresh(select=copied.id)
        except Exception as error:
            self.app.error(error)

    def delete(self) -> None:
        t = self.selected()
        if t is None or not messagebox.askyesno("删除任务", "删除任务「%s」？" % t.name, default="no",
                                                parent=self.app.root):
            return
        try:
            self.app.commit(replace(self.app.data, tasks=[x for x in self.app.data.tasks if x.id != t.id]))
        except Exception as error:
            self.app.error(error)

    def export_task(self) -> None:
        t = self.selected()
        if t is None:
            return
        path = filedialog.asksaveasfilename(parent=self.app.root, title="导出任务（不含任何密码）",
                                            defaultextension=".json", initialfile=t.name + ".json",
                                            filetypes=[("任务 JSON", "*.json")])
        if not path:
            return
        payload = {"teralink_task": 1, "task": {k: v for k, v in AppData(tasks=[t]).to_json()["tasks"][0].items()}}
        # Servers are referenced by name so a teammate can map them to their own saved connections.
        for step in payload["task"]["steps"]:
            c = self.app.data.connection(step.get("connection_id", ""))
            step["connection_name"] = c.name if c else ""
            step["connection_id"] = ""
        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            self.app.status.set("已导出到 %s（不含密码）。" % path)
        except OSError as error:
            self.app.error(error)

    def import_task(self) -> None:
        path = filedialog.askopenfilename(parent=self.app.root, title="导入任务", filetypes=[("任务 JSON", "*.json")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8-sig") as handle:
                raw = json.load(handle)
            if not isinstance(raw, dict) or raw.get("teralink_task") != 1:
                raise ValueError("不是 TeraLink 导出的任务文件。")
            item = dict(raw["task"])
            names = {c.name: c.id for c in self.app.data.connections if c.kind == KIND_SSH}
            missing = set()
            for step in item.get("steps", []):
                name = step.pop("connection_name", "")
                if step.get("type") in (STEP_UPLOAD, STEP_REMOTE):
                    step["connection_id"] = names.get(name, self._default_connection())
                    if name and name not in names:
                        missing.add(name)
            item["id"] = new_id()
            task = AppData.from_json({"tasks": [item]}).tasks[0]
            if missing:
                messagebox.showinfo("导入任务", "本机没有这些名称的连接，已先替换为默认连接，请编辑任务确认：\n"
                                    + "\n".join(sorted(missing)), parent=self.app.root)
            self.edit_task(task, is_new=True)
        except Exception as error:
            self.app.error(error, "导入失败")

    def stop(self) -> None:
        if self.app.runner is not None:
            self.app.runner.cancel.set()
            self.app.status.set("正在停止…")

    def run(self) -> None:
        t = self.selected()
        if t is None or self.app.runner is not None:
            return
        try:
            t.validate()
        except ValueError as error:
            self.app.error(error, "任务配置有误")
            return
        if t.confirm:
            summary = "\n".join("%d. [%s] %s" % (i, STEP_LABELS[s.type], s.title()) for i, s in enumerate(t.steps, 1))
            if not messagebox.askyesno("运行任务", "运行「%s」？\n\n%s\n\n服务器：%s" % (t.name, summary,
                                                                                  self._server_names(t)),
                                       parent=self.app.root):
                return
        runner = tasks.Runner(self.app.data, self.app.log, self.app.confirm_host_key)
        self.app.runner = runner
        self.show_details()
        self.app.status.set("正在运行「%s」…" % t.name)

        def work():
            ok = False
            try:
                ok = runner.run(t)
            finally:
                self.app.post("do", lambda: self._finished(t, ok))

        threading.Thread(target=work, name="teralink-task", daemon=True).start()

    def _finished(self, task: Task, ok: bool) -> None:
        self.app.runner = None
        if self.app.closing:
            return
        self.app.status.set("「%s」%s。" % (task.name, "完成" if ok else "未完成，详情见日志"))
        self.show_details()
        if not ok:
            self.app.root.bell()


class _Dialog(tk.Toplevel):
    def __init__(self, parent, title: str, size: str):
        super().__init__(parent)
        self.withdraw()
        self.title(title)
        self.transient(parent)
        width, height = (int(v) for v in size.split("x"))
        width = min(width, self.winfo_screenwidth() - 40)
        height = min(height, self.winfo_screenheight() - 90)
        self.geometry("%dx%d" % (width, height))
        self.minsize(min(520, width), min(360, height))
        self.result = None
        footer = ttk.Frame(self, padding=(16, 0, 16, 14))
        footer.pack(fill="x", side="bottom")  # packed first so it is never squeezed out
        ttk.Button(footer, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(footer, text="保存", style="Accent.TButton", command=self.on_ok).pack(side="right", padx=6)
        self.body = ttk.Frame(self, padding=16)
        self.body.pack(fill="both", expand=True)
        self.body.columnconfigure(1, weight=1)
        self.row = 0
        self.bind("<Escape>", lambda _e: self.destroy())

    def show(self) -> None:
        self.update_idletasks()
        parent = self.master
        x = parent.winfo_rootx() + max(0, (parent.winfo_width() - self.winfo_width()) // 2)
        y = parent.winfo_rooty() + max(0, (parent.winfo_height() - self.winfo_height()) // 3)
        self.geometry("+%d+%d" % (x, y))
        self.deiconify()
        previous = self.grab_current()  # a parent dialog that must get its grab back afterwards
        self.grab_set()
        self.wait_window()
        if previous is not None and previous.winfo_exists():
            previous.grab_set()

    def field(self, label: str, widget, hint: str = "", parent=None, sticky: str = "ew") -> None:
        parent = parent or self.body
        ttk.Label(parent, text=label).grid(row=self.row, column=0, sticky="nw", padx=(0, 10), pady=4)
        widget.grid(row=self.row, column=1, sticky=sticky, pady=4)
        self.row += 1
        if hint:
            ttk.Label(parent, text=hint, style="Muted.TLabel", wraplength=420, justify="left").grid(
                row=self.row, column=1, sticky="w", pady=(0, 4))
            self.row += 1

    def on_ok(self) -> None:
        raise NotImplementedError


def _with_button(parent, variable: tk.StringVar, text: str, command: Callable) -> ttk.Frame:
    frame = ttk.Frame(parent)
    ttk.Entry(frame, textvariable=variable).pack(side="left", fill="x", expand=True)
    ttk.Button(frame, text=text, command=command).pack(side="left", padx=(6, 0))
    return frame


class ConnectionDialog(_Dialog):
    def __init__(self, parent, connection: Optional[Connection]):
        super().__init__(parent, "新增连接" if connection is None else "编辑连接", "640x760")
        self.original = connection
        c = connection or Connection()
        self.name = tk.StringVar(value=c.name)
        self.kind = tk.StringVar(value=KIND_LABELS[c.kind])
        self.host = tk.StringVar(value=c.host)
        self.port = tk.StringVar(value=str(c.port))
        self.username = tk.StringVar(value=c.username)
        self.password = tk.StringVar()
        self.group = tk.StringVar(value=c.group)
        self.full_screen = tk.BooleanVar(value=c.rdp_full_screen)
        self.rdp_file = tk.StringVar(value=c.rdp_file_path)
        self.rdp_hash = c.rdp_file_hash
        self.favorite = tk.BooleanVar(value=c.favorite)

        self.field("名称", ttk.Entry(self.body, textvariable=self.name))
        kind_box = ttk.Combobox(self.body, textvariable=self.kind, state="readonly", values=list(KIND_LABELS.values()))
        kind_box.bind("<<ComboboxSelected>>", lambda _e: self._kind_changed())
        self.field("类型", kind_box)
        self.rdp_row = ttk.Frame(self.body)
        ttk.Entry(self.rdp_row, textvariable=self.rdp_file, state="readonly").pack(side="left", fill="x", expand=True)
        ttk.Button(self.rdp_row, text="选择已有 .rdp", command=self._pick_rdp).pack(side="left", padx=(6, 0))
        ttk.Button(self.rdp_row, text="改为手填", command=self._clear_rdp).pack(side="left", padx=(6, 0))
        self.field("RDP 文件", self.rdp_row, "可选。沿用你平时的 .rdp 文件（网关、显示等设置），原文件不会被修改。")
        self.host_entry = ttk.Entry(self.body, textvariable=self.host)
        self.field("主机", self.host_entry, "例如 192.168.1.10 或 server.example.com，不要带端口")
        self.port_entry = ttk.Entry(self.body, textvariable=self.port, width=8)
        self.field("端口", self.port_entry)
        self.field("用户名", ttk.Entry(self.body, textvariable=self.username), "RDP 域账户可写 DOMAIN\\user 或 user@domain")
        self.field("密码", ttk.Entry(self.body, textvariable=self.password, show="●"),
                   "编辑时留空表示保留原密码" if connection else "")
        self.field("分组", ttk.Entry(self.body, textvariable=self.group), "例如 开发环境 / 生产环境")
        self.after_login = tk.Text(self.body, height=3, wrap="none", font=("Consolas", 10), **_TEXT_STYLE)
        self.after_login.insert("1.0", c.after_login)
        self.field("登录后执行", self.after_login, "仅 SSH。登录成功后依次输入，每行一条，例如：\nsudo su -\ncd /opt/tomcat/logs")
        self.sudo_auto = tk.BooleanVar(value=c.sudo_auto_password)
        self.sudo_check = ttk.Checkbutton(self.body, text="遇到 sudo 密码提示时自动输入登录密码（只在出现 Password / パスワード / 密码 提示时发送）",
                                          variable=self.sudo_auto)
        self.field("", self.sudo_check)
        self.notes = tk.Text(self.body, height=4, wrap="word", font=_PROSE_FONT, **_TEXT_STYLE)
        self.notes.insert("1.0", c.notes)
        self.field("备注", self.notes)
        self.full_screen_check = ttk.Checkbutton(self.body, text="远程桌面全屏", variable=self.full_screen)
        self.field("", self.full_screen_check)
        self.field("", ttk.Checkbutton(self.body, text="收藏此连接", variable=self.favorite))
        self._kind_changed(initial=True)
        self.show()

    def _kind_value(self) -> str:
        return KIND_RDP if self.kind.get() == KIND_LABELS[KIND_RDP] else KIND_SSH

    def _kind_changed(self, initial: bool = False) -> None:
        is_rdp = self._kind_value() == KIND_RDP
        if not initial and self.port.get() in ("22", "3389"):
            self.port.set("3389" if is_rdp else "22")
        use_file = is_rdp and bool(self.rdp_file.get())
        for child in self.rdp_row.winfo_children():
            child.state(["!disabled" if is_rdp else "disabled"])
        self.host_entry.state(["disabled" if use_file else "!disabled"])
        self.port_entry.state(["disabled" if use_file else "!disabled"])
        self.full_screen_check.state(["!disabled" if is_rdp and not use_file else "disabled"])
        self.after_login.configure(state="disabled" if is_rdp else "normal")
        self.sudo_check.state(["disabled" if is_rdp else "!disabled"])

    def _pick_rdp(self) -> None:
        path = filedialog.askopenfilename(parent=self, filetypes=[("远程桌面连接", "*.rdp")])
        if not path:
            return
        path = os.path.normpath(path)
        try:
            profile = rdp.read_profile(path)
            host, port, username = rdp.inspect(profile)
            self.rdp_file.set(path)
            self.rdp_hash = rdp.fingerprint(profile)
            self.host.set(host)
            self.port.set(str(port))
            if username:
                self.username.set(username)
            if not self.name.get():
                self.name.set(os.path.splitext(os.path.basename(path))[0])
            self._kind_changed(initial=True)
        except Exception as error:
            messagebox.showerror("无法使用此文件", str(error), parent=self)

    def _clear_rdp(self) -> None:
        self.rdp_file.set("")
        self.rdp_hash = ""
        self._kind_changed(initial=True)

    def on_ok(self) -> None:
        try:
            kind = self._kind_value()
            try:
                port = int(self.port.get().strip())
            except ValueError:
                raise ValueError("端口必须是数字。")
            host = self.host.get().strip()
            validate_host(host)
            password = self.password.get()
            if password:
                protected = vault.protect(password)
            elif self.original is not None:
                protected = self.original.protected_password
            else:
                raise ValueError("请输入登录密码。")
            use_file = kind == KIND_RDP and bool(self.rdp_file.get())
            base = self.original or Connection()
            result = replace(
                base, name=self.name.get().strip(), kind=kind, host=host, port=port,
                username=self.username.get().strip(), group=self.group.get().strip(),
                notes=self.notes.get("1.0", "end-1c").strip(), protected_password=protected,
                rdp_full_screen=kind == KIND_RDP and not use_file and self.full_screen.get(),
                rdp_file_path=self.rdp_file.get() if use_file else "",
                rdp_file_hash=self.rdp_hash if use_file else "", favorite=self.favorite.get(),
                after_login="" if kind == KIND_RDP else self.after_login.get("1.0", "end-1c").strip(),
                sudo_auto_password=kind == KIND_SSH and self.sudo_auto.get())
            if use_file:
                current = rdp.read_profile(result.rdp_file_path)
                if rdp.fingerprint(current) != result.rdp_file_hash.upper():
                    raise ValueError("RDP 文件在选择后被修改，请重新选择。")
            self.result = result.validate()
            self.password.set("")
            self.destroy()
        except Exception as error:
            messagebox.showerror("无法保存", str(error), parent=self)


class TaskDialog(_Dialog):
    def __init__(self, parent, app: App, task: Optional[Task]):
        super().__init__(parent, "编辑任务" if task else "新建任务", "760x620")
        self.app = app
        t = task or Task(name="")
        self.original = t
        self.steps: List[Step] = [replace(s) for s in t.steps]
        self.name = tk.StringVar(value=t.name)
        self.confirm = tk.BooleanVar(value=t.confirm)
        self.field("任务名称", ttk.Entry(self.body, textvariable=self.name))
        self.description = tk.Text(self.body, height=3, wrap="word", font=_PROSE_FONT, **_TEXT_STYLE)
        self.description.insert("1.0", t.description)
        self.field("说明", self.description)
        self.field("", ttk.Checkbutton(self.body, text="运行前弹出确认（涉及正式环境时建议勾选）", variable=self.confirm))

        ttk.Label(self.body, text="步骤").grid(row=self.row, column=0, sticky="nw", pady=(8, 0))
        steps_frame = ttk.Frame(self.body)
        steps_frame.grid(row=self.row, column=1, sticky="nsew", pady=(8, 0))
        self.body.rowconfigure(self.row, weight=1)
        self.row += 1
        self.tree = ttk.Treeview(steps_frame, columns=("index", "type", "title", "server"), show="headings",
                                 selectmode="browse", height=8)
        for column, title, width in (("index", "#", 36), ("type", "类型", 110), ("title", "内容", 300),
                                     ("server", "服务器", 140)):
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width, anchor="w", stretch=column == "title")
        self.tree.bind("<Double-1>", lambda _e: self.edit_step())
        buttons = ttk.Frame(steps_frame)
        buttons.pack(side="right", fill="y", padx=(8, 0))  # packed before the table so it is never squeezed
        self.tree.pack(side="left", fill="both", expand=True)
        add_button = ttk.Menubutton(buttons, text="＋ 添加步骤", width=11)
        add_menu = tk.Menu(add_button, tearoff=False)
        for step_type in STEP_TYPES:
            add_menu.add_command(label=STEP_LABELS[step_type], command=lambda st=step_type: self.add_step(st))
        add_button["menu"] = add_menu
        add_button.pack(fill="x")
        for text, command in (("编辑", self.edit_step), ("删除", self.delete_step),
                              ("上移", lambda: self.move(-1)), ("下移", lambda: self.move(1))):
            ttk.Button(buttons, text=text, command=command).pack(fill="x", pady=(6, 0))
        ttk.Label(self.body, text=VARIABLE_HELP, style="Muted.TLabel", wraplength=560, justify="left").grid(
            row=self.row, column=1, sticky="w", pady=(6, 0))
        self.row += 1
        self.refresh()
        self.show()

    def refresh(self, select: Optional[int] = None) -> None:
        self.tree.delete(*self.tree.get_children())
        for index, step in enumerate(self.steps):
            server = ""
            if step.type in (STEP_UPLOAD, STEP_REMOTE):
                c = self.app.data.connection(step.connection_id)
                server = c.name if c else "（未选择）"
            self.tree.insert("", "end", iid=str(index), values=(index + 1, STEP_LABELS[step.type], step.title(), server))
        if select is not None and 0 <= select < len(self.steps):
            self.tree.selection_set(str(select))

    def _index(self) -> Optional[int]:
        selection = self.tree.selection()
        return int(selection[0]) if selection else None

    def add_step(self, step_type: str) -> None:
        connection_id = next((c.id for c in self.app.data.connections if c.kind == KIND_SSH), "")
        step = Step(type=step_type, connection_id=connection_id if step_type in (STEP_UPLOAD, STEP_REMOTE) else "")
        if step_type == STEP_UPLOAD and any(s.type == STEP_WAR for s in self.steps):
            step.local = "${ARTIFACT}"
        result = StepDialog(self, self.app, step).result
        if result is not None:
            index = self._index()
            position = len(self.steps) if index is None else index + 1
            self.steps.insert(position, result)
            self.refresh(select=position)

    def edit_step(self) -> None:
        index = self._index()
        if index is None:
            return
        result = StepDialog(self, self.app, self.steps[index]).result
        if result is not None:
            self.steps[index] = result
            self.refresh(select=index)

    def delete_step(self) -> None:
        index = self._index()
        if index is not None:
            del self.steps[index]
            self.refresh(select=min(index, len(self.steps) - 1))

    def move(self, offset: int) -> None:
        index = self._index()
        if index is None or not 0 <= index + offset < len(self.steps):
            return
        self.steps[index], self.steps[index + offset] = self.steps[index + offset], self.steps[index]
        self.refresh(select=index + offset)

    def on_ok(self) -> None:
        try:
            self.result = replace(self.original, name=self.name.get().strip(),
                                  description=self.description.get("1.0", "end-1c").strip(),
                                  confirm=self.confirm.get(), steps=list(self.steps)).validate()
            self.destroy()
        except Exception as error:
            messagebox.showerror("无法保存", str(error), parent=self)


class StepDialog(_Dialog):
    def __init__(self, parent, app: App, step: Step):
        super().__init__(parent, "步骤 · " + STEP_LABELS[step.type], "640x520")
        self.app, self.step = app, step
        self.vars = {name: tk.StringVar(value=getattr(step, name))
                     for name in ("name", "cwd", "source_dir", "output", "excludes", "local", "remote_dir",
                                  "remote_name")}
        self.backup = tk.BooleanVar(value=step.backup)
        self.ignore_error = tk.BooleanVar(value=step.ignore_error)
        self.ssh = [c for c in app.data.connections if c.kind == KIND_SSH]
        self.server = tk.StringVar(value=next((self._label(c) for c in self.ssh if c.id == step.connection_id), ""))
        self.command: Optional[tk.Text] = None

        self.field("步骤名称", ttk.Entry(self.body, textvariable=self.vars["name"]), "可选，显示在日志和列表中")
        if step.type == STEP_LOCAL:
            self.command = self._text(step.command)
            self.field("命令", self.command, "按 Windows 命令提示符（cmd）执行；多行依次执行，任一行失败就停止。例如：\n"
                                            "mvn -q clean package -DskipTests\ncall build.bat")
            self.field("", ttk.Button(self.body, text="选择脚本文件…", command=self._pick_script), sticky="w")
            self.field("工作目录", _with_button(self.body, self.vars["cwd"], "浏览…",
                                            lambda: self._pick_dir("cwd")))
        elif step.type == STEP_WAR:
            self.field("Web 根目录", _with_button(self.body, self.vars["source_dir"], "浏览…",
                                               lambda: self._pick_dir("source_dir")),
                       "包含 WEB-INF 的目录，例如 WebContent、src\\main\\webapp 或 target\\myapp")
            self.field("输出 WAR", _with_button(self.body, self.vars["output"], "另存为…", self._pick_output),
                       "例如 C:\\work\\dist\\myapp-${NOW}.war。生成后可在后续步骤用 ${ARTIFACT} 引用")
            self.field("排除", ttk.Entry(self.body, textvariable=self.vars["excludes"]),
                       "逗号分隔，例如 *.bak, .git, WEB-INF/classes/test")
        else:
            box = ttk.Combobox(self.body, textvariable=self.server, state="readonly",
                               values=[self._label(c) for c in self.ssh])
            self.field("服务器", box, "" if self.ssh else "还没有 SSH 连接，请先在「连接」页新增（类型选 Tera Term · SSH）。")
            if step.type == STEP_UPLOAD:
                self.field("本地文件", _with_button(self.body, self.vars["local"], "浏览…", self._pick_local),
                           "可用通配符（target\\*.war 取最新的一个），或 ${ARTIFACT} 引用上一步生成的 WAR")
                self.field("服务器目录", ttk.Entry(self.body, textvariable=self.vars["remote_dir"]),
                           "绝对路径，例如 /opt/tomcat/webapps；不存在会自动创建")
                self.field("服务器文件名", ttk.Entry(self.body, textvariable=self.vars["remote_name"]),
                           "可选，留空沿用本地文件名；例如 myapp.war")
                self.field("", ttk.Checkbutton(self.body, text="覆盖前把原文件改名备份（.bak-时间）",
                                               variable=self.backup))
            else:
                self.command = self._text(step.command)
                self.field("命令", self.command, "通过 SSH 在服务器执行（非登录 shell，环境变量可能比 Tera Term 里少，"
                                                "需要时写 bash -lc '...'）。需要 sudo 时请用 sudo -n（免密配置），"
                                                "否则会卡在密码提示。")
        self.field("", ttk.Checkbutton(self.body, text="此步失败时继续执行后续步骤", variable=self.ignore_error))
        self.show()

    @staticmethod
    def _label(c: Connection) -> str:
        return "%s（%s@%s:%d）" % (c.name, c.username, c.host, c.port)

    def _text(self, value: str) -> tk.Text:
        widget = tk.Text(self.body, height=6, wrap="none", font=("Consolas", 10), **_TEXT_STYLE)
        widget.insert("1.0", value)
        return widget

    def _pick_dir(self, key: str) -> None:
        path = filedialog.askdirectory(parent=self, initialdir=self.vars[key].get() or None)
        if path:
            self.vars[key].set(os.path.normpath(path))

    def _pick_output(self) -> None:
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".war", filetypes=[("WAR", "*.war")])
        if path:
            self.vars["output"].set(os.path.normpath(path))

    def _pick_local(self) -> None:
        path = filedialog.askopenfilename(parent=self)
        if path:
            self.vars["local"].set(os.path.normpath(path))

    def _pick_script(self) -> None:
        path = filedialog.askopenfilename(parent=self, filetypes=[
            ("脚本", "*.bat *.cmd *.ps1 *.py *.sh"), ("所有文件", "*.*")])
        if not path:
            return
        path = os.path.normpath(path)
        extension = os.path.splitext(path)[1].lower()
        if extension in (".bat", ".cmd"):
            command = 'call "%s"' % path
        elif extension == ".ps1":
            command = 'powershell -NoProfile -ExecutionPolicy Bypass -File "%s"' % path
        elif extension == ".py":
            command = 'python "%s"' % path
        else:
            command = '"%s"' % path
        self.command.delete("1.0", "end")
        self.command.insert("1.0", command)
        if not self.vars["cwd"].get():
            self.vars["cwd"].set(os.path.dirname(path))

    def on_ok(self) -> None:
        try:
            values = {name: var.get().strip() for name, var in self.vars.items()}
            connection_id = next((c.id for c in self.ssh if self._label(c) == self.server.get()), "")
            command = self.command.get("1.0", "end-1c").strip() if self.command is not None else ""
            self.result = replace(self.step, command=command, connection_id=connection_id, backup=self.backup.get(),
                                  ignore_error=self.ignore_error.get(), **values).validate()
            self.destroy()
        except Exception as error:
            messagebox.showerror("无法保存", str(error), parent=self)


def _open_folder(path: str) -> None:
    os.makedirs(path, exist_ok=True)
    if os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]


def run(store, data: AppData, note: Optional[str] = None) -> None:
    root = tk.Tk()
    app = App(root, store, data)
    if note:
        app.log(note)
    root.mainloop()
