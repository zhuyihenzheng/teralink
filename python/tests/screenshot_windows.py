"""CI helper: render the real UI on Windows and save screenshots (needs Pillow). Not a unit test.

Usage: python tests/screenshot_windows.py <output-folder>
"""
import os
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def main(out: str) -> None:
    import tkinter as tk
    from PIL import ImageGrab
    from teralink import tasks, ui, vault
    from teralink.app import _enable_dpi_awareness
    from teralink.model import AppData, Connection, Step, Store

    _enable_dpi_awareness()
    os.makedirs(out, exist_ok=True)
    folder = tempfile.mkdtemp()
    os.environ["TERALINK_DATA_DIR"] = folder
    os.environ["LOCALAPPDATA"] = folder
    store = Store(folder)
    ssh = Connection(name="测试服务器", host="10.0.0.5", username="deploy", group="开发",
                     notes="Tomcat 9 / JDK 17", protected_password=vault.protect("x"))
    desk = Connection(name="远程桌面", kind="rdp", host="pc.example.com", port=3389, username="CORP\\me",
                      protected_password=vault.protect("x"), favorite=True)
    data = AppData(connections=[ssh, desk], tasks=list(tasks.templates(ssh.id).values()))
    store.save(data)
    root = tk.Tk()
    app = ui.App(root, store, data)

    def grab(widget, name):
        widget.update()
        widget.after(300)
        widget.update()
        x, y = widget.winfo_rootx(), widget.winfo_rooty()
        box = (x - 8, y - 32, x + widget.winfo_width() + 8, y + widget.winfo_height() + 8)
        ImageGrab.grab(bbox=box, all_screens=True).save(os.path.join(out, name + ".png"))

    root.deiconify()
    root.lift()
    root.attributes("-topmost", True)
    grab(root, "1-connections")
    app.notebook.select(1)
    for line in ("══ 开始任务「Maven 打包并部署」", "── 第 1/3 步：Maven 打包", "> mvn -q clean package -DskipTests",
                 "── 第 2/3 步：上传 WAR", "  上传 50%（12.0 MB / 24.0 MB）", "══ 任务完成，用时 18.2 秒"):
        app.log(line)
    app._drain_events()
    grab(root, "2-tasks")

    def show(dialog):
        dialog.deiconify()
        dialog.lift()
        dialog.attributes("-topmost", True)
        grab(dialog, "3-" + type(dialog).__name__ + "-" + show.name)
        dialog.destroy()

    with mock.patch.object(ui._Dialog, "show", show):
        show.name = "ssh"
        ui.ConnectionDialog(root, ssh)
        show.name = "task"
        ui.TaskDialog(root, app, data.tasks[0])
        for step in ("local", "war", "upload", "remote"):
            show.name = step
            ui.StepDialog(root, app, Step(type=step, connection_id=ssh.id))
    app.closing = True
    root.destroy()
    store.close()


if __name__ == "__main__":
    main(sys.argv[1])
