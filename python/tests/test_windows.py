"""Windows-only checks: real DPAPI, the secure named pipe, RDP profile writing, Tera Term detection and real Tk.

Run on a Windows machine or the windows-latest CI runner. Skipped elsewhere.
"""
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

WINDOWS = os.name == "nt"


@unittest.skipUnless(WINDOWS, "Windows only")
class DpapiTests(unittest.TestCase):
    def test_roundtrip_and_rdp_format(self):
        from teralink import vault
        protected = vault.protect("pässwörd 密码")
        self.assertEqual(vault.unprotect(protected), "pässwörd 密码")
        hex_value = vault.to_rdp_password(protected)
        self.assertRegex(hex_value, r"^[0-9A-F]+$")
        self.assertEqual(len(hex_value) % 2, 0)
        with self.assertRaises(ValueError):
            vault.unprotect("not base64!")
        with self.assertRaises(OSError):
            vault.unprotect("QUJD")  # valid base64, not a DPAPI blob


@unittest.skipUnless(WINDOWS, "Windows only")
class PipeTests(unittest.TestCase):
    def test_payload_reaches_child_and_pid_matches(self):
        from teralink import winpipe
        self.assertRegex(winpipe.current_user_sid(), r"^S-1-5-")
        name = "TeraLink-" + os.urandom(16).hex()
        pipe = winpipe.SecureOutboundPipe(name)
        try:
            pipe.start_accept()
            reader = ("import sys\nwith open(r'\\\\.\\pipe\\%s', 'rb') as h:\n"
                      "    sys.stdout.write(h.readline().decode())\n" % name)
            child = subprocess.Popen([sys.executable, "-c", reader], stdout=subprocess.PIPE)
            self.assertTrue(pipe.connected.wait(15), "child never connected")
            self.assertIsNone(pipe.error)
            self.assertEqual(pipe.client_pid(), child.pid)
            pipe.write(bytearray(b"hello pipe\r\n"))
            out, _ = child.communicate(timeout=15)
            self.assertEqual(out.decode().strip(), "hello pipe")
        finally:
            pipe.close()

    def test_close_without_client_does_not_hang(self):
        from teralink import winpipe
        pipe = winpipe.SecureOutboundPipe("TeraLink-" + os.urandom(16).hex())
        pipe.start_accept()
        started = time.monotonic()
        pipe.close()
        self.assertLess(time.monotonic() - started, 5)

    def test_second_instance_with_same_name_is_refused(self):
        from teralink import winpipe
        name = "TeraLink-" + os.urandom(16).hex()
        first = winpipe.SecureOutboundPipe(name)
        try:
            with self.assertRaises(OSError):
                winpipe.SecureOutboundPipe(name)
        finally:
            first.start_accept()
            first.close()


@unittest.skipUnless(WINDOWS, "Windows only")
class RdpLaunchTests(unittest.TestCase):
    def test_launch_writes_utf16_profile_with_encrypted_password(self):
        from teralink import rdp, vault
        from teralink.model import Connection
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ, {"TERALINK_DATA_DIR": folder}), \
                mock.patch("subprocess.Popen") as popen:
            c = Connection(name="d", kind="rdp", host="pc.example.com", port=3389, username="CORP\\me",
                           protected_password=vault.protect("secret"))
            rdp.launch(c)
            args = popen.call_args[0][0]
            self.assertTrue(args[0].lower().endswith("mstsc.exe"))
            with open(args[1], "rb") as handle:
                raw = handle.read()
            self.assertTrue(raw.startswith(b"\xff\xfe"), "mstsc expects UTF-16LE with BOM")
            text = raw.decode("utf-16")
            self.assertIn("full address:s:pc.example.com:3389", text)
            self.assertRegex(text, r"password 51:b:[0-9A-F]{100,}")
            self.assertNotIn("secret", text)
            rdp.forget(c.id)
            self.assertFalse(os.path.exists(args[1]))


@unittest.skipUnless(WINDOWS, "Windows only")
class TeraTermDetectionTests(unittest.TestCase):
    def test_find_and_validate_if_installed(self):
        from teralink import teraterm
        path = teraterm.find_executable()
        if not path:
            self.skipTest("Tera Term not installed")
        teraterm.validate_executable(path)
        major = teraterm._file_major_version(path)
        self.assertGreaterEqual(major, 5)

    def test_rejects_other_programs(self):
        from teralink import teraterm
        with self.assertRaises(ValueError):
            teraterm.validate_executable(sys.executable)


@unittest.skipUnless(WINDOWS, "Windows only")
class LocalCommandTests(unittest.TestCase):
    def test_cmd_multiline_and_chinese_output(self):
        from teralink import tasks
        lines = []
        code = tasks.run_local("echo first\necho 中文输出\nexit /b 4\necho never", "", lines.append, threading.Event())
        self.assertEqual(code, 4)
        text = "\n".join(lines)
        self.assertIn("first", text)
        self.assertIn("中文输出", text)
        self.assertNotIn("  never", text)


@unittest.skipUnless(WINDOWS, "Windows only")
class RealTkTests(unittest.TestCase):
    """Builds the real window and every dialog; catches Tcl errors the fake tkinter cannot."""

    def test_all_windows_build(self):
        import tkinter as tk
        from teralink import tasks, ui, vault
        from teralink.model import AppData, Connection, Step, Store
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ, {"TERALINK_DATA_DIR": folder,
                                                                                    "LOCALAPPDATA": folder}):
            store = Store(folder)
            try:
                ssh = Connection(name="服务器", host="10.0.0.5", username="deploy", protected_password=vault.protect("x"))
                rdp_c = Connection(name="桌面", kind="rdp", host="pc", port=3389, username="me",
                                   protected_password=vault.protect("x"))
                data = AppData(connections=[ssh, rdp_c], tasks=list(tasks.templates(ssh.id).values()))
                store.save(data)
                root = tk.Tk()
                errors = []
                root.report_callback_exception = lambda *a: errors.append(a)
                app = ui.App(root, store, data)
                root.update()
                app.notebook.select(1)
                root.update()

                def show(dialog):
                    dialog.deiconify()
                    dialog.update()
                    if hasattr(dialog, "on_ok"):
                        dialog.on_ok()
                    if dialog.winfo_exists():
                        dialog.destroy()

                with mock.patch.object(ui._Dialog, "show", show):
                    self.assertIsNotNone(ui.ConnectionDialog(root, ssh).result)
                    self.assertIsNotNone(ui.ConnectionDialog(root, rdp_c).result)
                    for task in data.tasks:
                        self.assertIsNotNone(ui.TaskDialog(root, app, task).result)
                    for step in data.tasks[0].steps + data.tasks[1].steps:
                        self.assertIsNotNone(ui.StepDialog(root, app, step).result)
                    ui.StepDialog(root, app, Step(type="local", command="echo"))
                app.log("日志")
                app._drain_events()
                root.update()
                self.assertEqual(errors, [])
                app.closing = True
                root.destroy()
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
