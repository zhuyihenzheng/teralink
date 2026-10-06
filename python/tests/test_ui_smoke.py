"""Drive the UI code against a minimal fake tkinter, so it runs headless and without Tcl/Tk installed.

This catches wrong attribute names, broken callbacks and logic errors in ui.py; it does not check the layout.
On a machine with real tkinter the fake is still used, so the test stays headless.
"""
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

os.environ["TERALINK_INSECURE_TEST_VAULT"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class _Var:
    def __init__(self, master=None, value=None):
        self._value = value if value is not None else self._default
        self._traces = []

    def get(self):
        return self._value

    def set(self, value):
        self._value = value
        for callback in self._traces:
            callback()

    def trace_add(self, _mode, callback):
        self._traces.append(callback)


class StringVar(_Var):
    _default = ""


class BooleanVar(_Var):
    _default = False


class Widget:
    def __init__(self, master=None, *args, **kwargs):
        self.master = master
        self.options = dict(kwargs)
        self._children = []
        self._state = set()
        if isinstance(master, Widget):
            master._children.append(self)

    def __getattr__(self, name):  # pack, grid, bind, configure, columnconfigure, ...
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *a, **k: None

    def configure(self, *_style, **kwargs):
        self.options.update(kwargs)

    config = configure

    def cget(self, key):
        return self.options.get(key)

    def __setitem__(self, key, value):
        self.options[key] = value

    def __getitem__(self, key):
        return self.options[key]

    def winfo_children(self):
        return list(self._children)

    def state(self, spec=None):
        for item in spec or []:
            if item.startswith("!"):
                self._state.discard(item[1:])
            else:
                self._state.add(item)
        return tuple(self._state)

    def winfo_exists(self):
        return True

    def grab_current(self):
        return None

    def winfo_width(self):
        return 800

    winfo_height = winfo_rootx = winfo_rooty = winfo_width


class Tk(Widget):
    def __init__(self, *args, **kwargs):
        super().__init__(None)
        self.after_calls = []

    def after(self, delay, callback=None, *args):
        self.after_calls.append((delay, callback))


class Toplevel(Widget):
    pass


class Text(Widget):
    def __init__(self, master=None, **kwargs):
        super().__init__(master, **kwargs)
        self.text = ""

    def insert(self, _index, value):
        self.text += value

    def get(self, *_args):
        return self.text

    def delete(self, *_args):
        self.text = ""

    def index(self, _spec):
        return "%d.0" % (self.text.count("\n") + 1)


class Treeview(Widget):
    def __init__(self, master=None, **kwargs):
        super().__init__(master, **kwargs)
        self.items, self.order, self.selected = {}, [], ()

    def insert(self, _parent, _index, iid=None, values=()):
        iid = iid or str(len(self.order))
        self.items[iid] = values
        self.order.append(iid)
        return iid

    def delete(self, *iids):
        for iid in iids:
            self.items.pop(iid, None)
            self.order.remove(iid)
        if self.selected and self.selected[0] not in self.items:
            self.selected = ()

    def get_children(self, *_args):
        return tuple(self.order)

    def exists(self, iid):
        return iid in self.items

    def selection(self):
        return self.selected

    def selection_set(self, iid):
        self.selected = (iid,)


class Menu(Widget):
    def __init__(self, master=None, **kwargs):
        super().__init__(master, **kwargs)
        self.commands = {}

    def add_command(self, label, command):
        self.commands[label] = command


class Font:
    def configure(self, **kwargs):
        pass

    def actual(self, _key):
        return "Arial"


def build_fake_tk():
    tk = types.ModuleType("tkinter")
    for name, value in dict(Tk=Tk, Toplevel=Toplevel, Text=Text, Menu=Menu, StringVar=StringVar,
                            BooleanVar=BooleanVar, Widget=Widget).items():
        setattr(tk, name, value)
    ttk = types.ModuleType("tkinter.ttk")
    for name in ("Frame", "Label", "Button", "Entry", "Checkbutton", "Scrollbar", "Notebook", "PanedWindow",
                 "Menubutton", "Combobox", "Style"):
        setattr(ttk, name, type(name, (Widget,), {}))
    ttk.Treeview = Treeview
    messagebox = types.ModuleType("tkinter.messagebox")
    messagebox.errors = []
    messagebox.answer = True
    messagebox.showerror = lambda title, message, **k: messagebox.errors.append((title, message))
    messagebox.showinfo = lambda *a, **k: None
    messagebox.askyesno = lambda *a, **k: messagebox.answer
    filedialog = types.ModuleType("tkinter.filedialog")
    filedialog.next_path = ""
    for name in ("askopenfilename", "asksaveasfilename", "askdirectory"):
        setattr(filedialog, name, lambda *a, **k: filedialog.next_path)
    scrolled = types.ModuleType("tkinter.scrolledtext")
    scrolled.ScrolledText = Text
    font = types.ModuleType("tkinter.font")
    font.families = lambda *a: []
    font.nametofont = lambda name: Font()
    tk.ttk, tk.messagebox, tk.filedialog, tk.scrolledtext, tk.font = ttk, messagebox, filedialog, scrolled, font
    return {"tkinter": tk, "tkinter.ttk": ttk, "tkinter.messagebox": messagebox, "tkinter.filedialog": filedialog,
            "tkinter.scrolledtext": scrolled, "tkinter.font": font}


class UiSmokeTest(unittest.TestCase):
    def setUp(self):
        self.modules = build_fake_tk()
        self.patch = mock.patch.dict(sys.modules, self.modules)
        self.patch.start()
        sys.modules.pop("teralink.ui", None)
        self.folder = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"TERALINK_DATA_DIR": self.folder.name,
                                                "LOCALAPPDATA": self.folder.name})
        self.env.start()
        import importlib
        from teralink.model import Store
        ui = importlib.import_module("teralink.ui")  # fresh import bound to this test's fake tkinter
        self.ui = ui
        self.store = Store(self.folder.name)
        self.messagebox = self.modules["tkinter.messagebox"]
        self.app = ui.App(Tk(), self.store, self.store.load())

    def tearDown(self):
        self.store.close()
        self.env.stop()
        self.patch.stop()
        sys.modules.pop("teralink.ui", None)
        self.folder.cleanup()

    def no_errors(self):
        self.assertEqual(self.messagebox.errors, [])

    def drain(self):
        self.app._drain_events()

    def test_connection_task_and_run_flow(self):
        ui = self.ui

        def fill_connection(dialog):
            dialog.name.set("测试服务器")
            dialog.host.set("10.0.0.5")
            dialog.username.set("deploy")
            dialog.password.set("secret")
            dialog.group.set("开发")
            dialog.on_ok()

        with mock.patch.object(ui._Dialog, "show", fill_connection):
            self.app.connections_tab.edit_connection(None)
        self.no_errors()
        self.assertEqual(len(self.app.data.connections), 1)
        connection = self.app.data.connections[0]
        self.assertEqual(self.app.connections_tab.tree.selection(), (connection.id,))
        self.assertIn("10.0.0.5:22", self.app.connections_tab.details.options["text"])

        # Edit with an empty password keeps the stored one; switching to RDP moves the default port.
        def to_rdp(dialog):
            dialog.kind.set(ui.KIND_LABELS[ui.KIND_RDP])
            dialog._kind_changed()
            dialog.on_ok()

        with mock.patch.object(ui._Dialog, "show", to_rdp):
            self.app.connections_tab.edit_connection(connection)
        self.no_errors()
        edited = self.app.data.connections[0]
        self.assertEqual((edited.kind, edited.port, edited.protected_password),
                         ("rdp", 3389, connection.protected_password))
        with mock.patch.object(ui._Dialog, "show", lambda d: (d.kind.set(ui.KIND_LABELS[ui.KIND_SSH]),
                                                             d._kind_changed(), d.on_ok())):
            self.app.connections_tab.edit_connection(edited)
        self.assertEqual(self.app.data.connections[0].port, 22)

        self.app.connections_tab.toggle_favorite()
        self.assertTrue(self.app.data.connections[0].favorite)
        self.app.connections_tab.search.set("没有这个")
        self.assertEqual(self.app.connections_tab.tree.get_children(), ())
        self.app.connections_tab.search.set("")

        # Every template opens and saves through the task dialog.
        with mock.patch.object(ui._Dialog, "show", lambda dialog: dialog.on_ok()):
            for name in ui.tasks.templates():
                self.app.tasks_tab.from_template(name)
        self.no_errors()
        self.assertEqual(len(self.app.data.tasks), len(ui.tasks.templates()))

        # Build a task step by step through the step dialogs.
        script_dir = os.path.join(self.folder.name, "scripts")
        os.makedirs(script_dir)

        def build_task(dialog):
            dialog.name.set("脚本任务")
            with mock.patch.object(ui._Dialog, "show", lambda step: (step.command.insert("1.0", "echo hello"),
                                                                     step.vars["cwd"].set(script_dir),
                                                                     step.on_ok())):
                dialog.add_step(ui.STEP_LOCAL)
            with mock.patch.object(ui._Dialog, "show", lambda step: (step.command.insert("1.0", "uname -a"),
                                                                     step.on_ok())):
                dialog.add_step(ui.STEP_REMOTE)
            with mock.patch.object(ui._Dialog, "show", lambda step: step.on_ok()):
                dialog.tree.selection_set("0")
                dialog.edit_step()
            dialog.tree.selection_set("1")
            dialog.move(-1)
            dialog.move(1)
            dialog.on_ok()

        with mock.patch.object(ui._Dialog, "show", build_task):
            self.app.tasks_tab.edit_task(None)
        self.no_errors()
        task = next(t for t in self.app.data.tasks if t.name == "脚本任务")
        self.assertEqual([s.type for s in task.steps], ["local", "remote"])
        self.assertEqual(task.steps[1].connection_id, connection.id)

        # Run it with a fake SSH session; the log lands in the UI through the event queue.
        calls = []

        class Session(ui.remote.Session):
            def run(self, command, log, cancel):
                calls.append(command)
                log("Linux fake")
                return 0

        self.app.tasks_tab.tree.selection_set(task.id)
        self.app.tasks_tab.show_details()
        with mock.patch.object(ui.tasks.remote, "open_session", lambda *a: Session()), \
                mock.patch.object(ui.tasks.Runner.__init__, "__defaults__", (lambda *a: Session(),)):
            self.app.tasks_tab.run()
            runner = self.app.runner
            self.assertIsNotNone(runner)
            for _ in range(100):
                self.drain()
                if self.app.runner is None:
                    break
                import time
                time.sleep(0.05)
        self.no_errors()
        self.assertEqual(calls, ["uname -a"])
        log_text = self.app.log_text.text
        self.assertIn("hello", log_text)
        self.assertIn("任务完成", log_text)

        # Copy / export / import / delete.
        self.app.tasks_tab.copy()
        export_path = os.path.join(self.folder.name, "task.json")
        self.modules["tkinter.filedialog"].next_path = export_path
        self.app.tasks_tab.export_task()
        with open(export_path, encoding="utf-8") as handle:
            exported = handle.read()
        self.assertNotIn(connection.protected_password, exported)
        self.assertIn("测试服务器", exported)
        before = len(self.app.data.tasks)
        with mock.patch.object(ui._Dialog, "show", lambda dialog: dialog.on_ok()):
            self.app.tasks_tab.import_task()
        self.no_errors()
        self.assertEqual(len(self.app.data.tasks), before + 1)
        self.app.tasks_tab.delete()
        self.assertEqual(len(self.app.data.tasks), before)

        # Deleting a connection used by tasks still works after confirmation.
        self.app.connections_tab.tree.selection_set(connection.id)
        self.app.connections_tab.delete()
        self.assertEqual(self.app.data.connections, [])
        self.app.tasks_tab.refresh()
        self.no_errors()

    def test_validation_errors_are_shown_not_raised(self):
        ui = self.ui
        with mock.patch.object(ui._Dialog, "show", lambda dialog: (dialog.host.set("bad host"), dialog.on_ok())):
            self.app.connections_tab.edit_connection(None)
        self.assertEqual(len(self.messagebox.errors), 1)
        self.assertEqual(self.app.data.connections, [])

    def test_host_key_confirmation_from_worker_thread(self):
        import threading
        result = {}
        worker = threading.Thread(target=lambda: result.setdefault(
            "answer", self.app.confirm_host_key("h", 22, "ssh-ed25519", "SHA256:x")))
        worker.start()
        for _ in range(50):
            self.drain()
            worker.join(0.02)
            if not worker.is_alive():
                break
        self.assertEqual(result.get("answer"), True)

    def test_legacy_import_offer(self):
        legacy_dir = os.path.join(self.folder.name, "TeraLink")
        os.makedirs(legacy_dir)
        with open(os.path.join(legacy_dir, "connections.json"), "w", encoding="utf-8") as handle:
            handle.write('{"Version": 2, "Connections": [{"Id": "0f8fad5b-d9cb-469f-a165-70867728950e", '
                         '"Name": "old", "Host": "10.0.0.9", "Port": 22, "Username": "u", "Group": "", '
                         '"Notes": "", "ProtectedPassword": "QUJD", "Kind": 0}]}')
        self.app.offer_legacy_import()
        self.no_errors()
        self.assertEqual([c.name for c in self.app.data.connections], ["old"])


if __name__ == "__main__":
    unittest.main()
