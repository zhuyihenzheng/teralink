"""Entry point: set up the data store, then hand over to the tkinter UI."""
from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime

from .paths import data_dir, log_dir


def _enable_dpi_awareness() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # crisp text on scaled displays
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _fatal(message: str) -> None:
    try:
        import tkinter
        from tkinter import messagebox
        root = tkinter.Tk()
        root.withdraw()
        messagebox.showerror("TeraLink", message)
        root.destroy()
    except Exception:
        sys.stderr.write(message + "\n")


def _write_crash_log() -> str:
    os.makedirs(log_dir(), exist_ok=True)
    path = os.path.join(log_dir(), "crash-%s.log" % datetime.now().strftime("%Y%m%d-%H%M%S"))
    with open(path, "w", encoding="utf-8") as handle:
        traceback.print_exc(file=handle)
    return path


def main() -> int:
    if sys.version_info < (3, 8):
        _fatal("需要 Python 3.8 或更新版本，当前是 %d.%d。" % sys.version_info[:2])
        return 1
    try:
        import tkinter  # noqa: F401
    except ImportError:
        sys.stderr.write("这个 Python 没有安装 tkinter（Tcl/Tk）。安装 Python 时请勾选 tcl/tk and IDLE。\n")
        return 1
    _enable_dpi_awareness()
    from .model import Store
    from .paths import migrate_store_python_data
    try:
        note = migrate_store_python_data()
    except OSError as error:
        note = "无法迁移旧数据：%s" % error
    try:
        store = Store(data_dir())
    except RuntimeError as error:
        _fatal(str(error))
        return 1
    try:
        try:
            data = store.load()
        except Exception as error:
            _fatal("无法读取数据：%s\n\n数据目录：%s" % (error, data_dir()))
            return 1
        from . import ui
        ui.run(store, data, note)
        return 0
    except Exception:
        _fatal("TeraLink 意外出错，详情已写入：\n%s" % _write_crash_log())
        return 1
    finally:
        store.close()
