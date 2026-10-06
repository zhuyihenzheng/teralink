"""Run with:  python -m unittest discover -s tests   (from the python/ folder)"""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import zipfile

os.environ["TERALINK_INSECURE_TEST_VAULT"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from teralink import rdp, remote, tasks, teraterm, vault  # noqa: E402
from teralink.model import (KIND_RDP, STEP_LOCAL, STEP_REMOTE, STEP_UPLOAD, STEP_WAR, AppData,  # noqa: E402
                            Connection, Step, Store, Task, clone_task, import_legacy, merge_connections,
                            validate_host)


def make_connection(**changes):
    values = dict(name="开发服务器", host="dev.example.com", username="user",
                  protected_password=vault.protect("secret"))
    values.update(changes)
    return Connection(**values)


class ConnectionTests(unittest.TestCase):
    def test_valid_profile(self):
        make_connection().validate()

    def test_accept_hosts(self):
        for host in ["localhost", "a", "192.168.0.1", "::1", "2001:db8::1", "dev-box.example.com", "server.example.com."]:
            validate_host(host)

    def test_reject_injection_and_malformed_hosts(self):
        for host in ["/passwd=leak", "x /passwd=leak", "ssh://user@host", 'x" /m=evil', "host;command",
                     "host\n/m=evil", "host:22", "-host", "host..example", "host.-bad", "abc\\file", "", " host"]:
            with self.assertRaises(ValueError, msg=host):
                validate_host(host)

    def test_port_boundaries(self):
        for port in (0, -1, 65536):
            with self.assertRaises(ValueError):
                make_connection(port=port).validate()
        make_connection(port=65535).validate()

    def test_required_fields(self):
        for changes in ({"name": " "}, {"username": "bad\0name"}, {"protected_password": "not base64!"},
                        {"protected_password": ""}, {"kind": "telnet"}, {"id": "xyz"}):
            with self.assertRaises(ValueError, msg=changes):
                make_connection(**changes).validate()

    def test_macro_command_quotes_credentials(self):
        command = make_connection(username="domain\\user name").macro_connect_command('a "b"; /m=evil')
        self.assertEqual(command, 'dev.example.com /P=22 /ssh /2 /auth=password /user="domain\\user name" '
                                  '/passwd="a ""b""; /m=evil"')
        self.assertNotIn("/nosecuritywarning", command)

    def test_macro_rejects_framing_and_overlong(self):
        c = make_connection()
        for password in ["", "line\nbreak", "line\rbreak", "null\0byte", "tab\there", "密" * 200]:
            with self.assertRaises(ValueError):
                c.macro_connect_command(password)
        overhead = len(c.macro_connect_command("x").encode("utf-8")) - 1
        self.assertEqual(len(c.macro_connect_command("a" * (511 - overhead)).encode("utf-8")), 511)
        with self.assertRaises(ValueError):
            c.macro_connect_command("a" * (512 - overhead))

    def test_macro_only_for_ssh(self):
        with self.assertRaises(ValueError):
            make_connection(kind=KIND_RDP, port=3389).macro_connect_command("x")


class VaultTests(unittest.TestCase):
    def test_roundtrip(self):
        self.assertEqual(vault.unprotect(vault.protect("pässwörd 密码")), "pässwörd 密码")

    def test_rejects_empty_and_nul(self):
        for value in ("", "a\0b", "x" * 1025):
            with self.assertRaises(ValueError):
                vault.protect(value)


class RdpTests(unittest.TestCase):
    def setUp(self):
        self.c = make_connection(kind=KIND_RDP, port=3389, username="DOMAIN\\user")

    def test_new_profile(self):
        profile = rdp.create(replace_full(self.c), "AABB01")
        for expected in ("full address:s:dev.example.com:3389\r\n", "username:s:DOMAIN\\user",
                         "screen mode id:i:2\r\n", "enablecredsspsupport:i:1\r\n", "authentication level:i:2\r\n",
                         "gatewayusagemethod:i:0\r\n", "password 51:b:AABB01\r\n"):
            self.assertIn(expected, profile)
        self.assertNotIn(self.c.protected_password, profile)

    def test_ipv6(self):
        c = make_connection(kind=KIND_RDP, host="2001:db8::1", port=3390)
        self.assertTrue(rdp.create(c, "00").startswith("full address:s:[2001:db8::1]:3390\r\n"))

    def test_existing_file_keeps_settings_and_replaces_credentials(self):
        existing = ("full address:s:dev.example.com\r\ngatewayhostname:s:gw.example.com\r\nusername:s:old\r\n"
                    "domain:s:CORP\r\nsignature:s:AAAA\r\nprompt for credentials:i:1\r\n")
        host, port, username = rdp.inspect(existing)
        self.assertEqual((host, port, username), ("dev.example.com", 3389, "CORP\\old"))
        profile = rdp.create(self.c, "ABCD", existing)
        self.assertIn("gatewayhostname:s:gw.example.com", profile)
        self.assertIn("username:s:DOMAIN\\user", profile)
        self.assertIn("prompt for credentials:i:0", profile)
        self.assertNotIn("signature", profile)
        self.assertNotIn("domain:s:CORP", profile)  # full DOMAIN\user given, so the old domain is dropped

    def test_existing_file_target_mismatch(self):
        with self.assertRaises(ValueError):
            rdp.create(self.c, "AB", "full address:s:other.example.com:3389\r\n")

    def test_rejects_bad_files(self):
        for profile in ("username:s:x\r\n", "full address:s:a\r\nfull address:s:b\r\n", "full address:i:5\r\n",
                        "garbage\r\n", "full address:s:[::1\r\n", "full address:s:host:99999\r\n"):
            with self.assertRaises(ValueError, msg=profile):
                rdp.inspect(profile)

    def test_bad_password_hex(self):
        for value in ("", "ABC", "XYZ1"):
            with self.assertRaises(ValueError):
                rdp.create(self.c, value)

    def test_read_profile_utf16_and_fingerprint(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "a.rdp")
            with open(path, "w", encoding="utf-16", newline="") as handle:
                handle.write("full address:s:dev.example.com\r\n")
            text = rdp.read_profile(path)
            self.assertEqual(text, "full address:s:dev.example.com\r\n")
            self.assertRegex(rdp.fingerprint(text), r"^[0-9A-F]{64}$")


def replace_full(c):
    from dataclasses import replace
    return replace(c, rdp_full_screen=True)


class StoreTests(unittest.TestCase):
    def test_roundtrip_lock_and_corruption(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(folder)
            try:
                with self.assertRaises(RuntimeError):
                    Store(folder)
                task = Task(name="部署", steps=[Step(type=STEP_LOCAL, command="echo hi")])
                data = AppData(teraterm_path="C:\\tt\\ttermpro.exe", connections=[make_connection()], tasks=[task])
                store.save(data)
                loaded = store.load()
                self.assertEqual(loaded, data)
                with open(store.path, "w", encoding="utf-8") as handle:
                    handle.write("{broken")
                with self.assertRaises(ValueError):
                    store.load()
            finally:
                store.close()

    def test_rejects_wrong_types_and_duplicates(self):
        c = make_connection().__dict__
        for raw in ({"version": 1, "connections": [dict(c, port="22")]},
                    {"version": 1, "connections": [c, c]},
                    {"version": 99},
                    {"version": 1, "connections": "x"}):
            with self.assertRaises(ValueError):
                AppData.from_json(raw).validate()

    def test_unknown_fields_are_ignored(self):
        raw = {"version": 1, "connections": [dict(make_connection().__dict__, future_field=1)]}
        self.assertEqual(len(AppData.from_json(raw).validate().connections), 1)


class LegacyImportTests(unittest.TestCase):
    def test_csharp_file(self):
        legacy = {"Version": 2, "TeraTermPath": "", "ExitAfterLaunch": False, "Connections": [
            {"Id": "0f8fad5b-d9cb-469f-a165-70867728950e", "Name": "web", "Host": "10.0.0.5", "Port": 22,
             "Username": "deploy", "Group": "开发", "Notes": "", "ProtectedPassword": "QUJD", "Kind": 0,
             "RdpFullScreen": False, "RdpFilePath": "", "RdpFileHash": "", "Favorite": True,
             "LastLaunched": "2026-09-01T10:00:00+09:00"},
            {"Id": "7c9e6679-7425-40de-944b-e07fc1f90ae7", "Name": "desk", "Host": "pc.example.com", "Port": 3389,
             "Username": "CORP\\me", "Group": "", "Notes": "", "ProtectedPassword": "QUJD", "Kind": 1,
             "RdpFullScreen": True, "RdpFilePath": "", "RdpFileHash": "", "Favorite": False, "LastLaunched": None},
        ]}
        connections = import_legacy(legacy)
        self.assertEqual([c.kind for c in connections], ["ssh", "rdp"])
        self.assertEqual(connections[0].id, "0f8fad5bd9cb469fa16570867728950e")
        self.assertEqual(connections[0].protected_password, "QUJD")
        merged = merge_connections(connections[:1], connections)
        self.assertEqual(len(merged), 2)

    def test_rejects_other_files(self):
        with self.assertRaises(ValueError):
            import_legacy({"version": 1})


class TaskModelTests(unittest.TestCase):
    def test_step_validation(self):
        bad = [Step(type="x"), Step(type=STEP_LOCAL), Step(type=STEP_WAR, source_dir="a", output="b.zip"),
               Step(type=STEP_UPLOAD, local="a.war", remote_dir="/opt"),
               Step(type=STEP_UPLOAD, connection_id="c", local="a.war", remote_dir="relative/path"),
               Step(type=STEP_UPLOAD, connection_id="c", local="a.war", remote_dir="/opt", remote_name="../x"),
               Step(type=STEP_REMOTE, connection_id="c")]
        for step in bad:
            with self.assertRaises(ValueError, msg=step):
                step.validate()
        Step(type=STEP_UPLOAD, connection_id="c", local="${ARTIFACT}", remote_dir="/opt/tomcat/webapps").validate()

    def test_task_needs_steps_and_reports_step_number(self):
        with self.assertRaises(ValueError):
            Task(name="x").validate()
        with self.assertRaisesRegex(ValueError, "第 2 步"):
            Task(name="x", steps=[Step(command="a"), Step(command="")]).validate()

    def test_clone(self):
        task = Task(name="a", steps=[Step(command="x")])
        copy = clone_task(task)
        self.assertNotEqual(copy.id, task.id)
        copy.steps[0].command = "y"
        self.assertEqual(task.steps[0].command, "x")

    def test_templates_are_valid_with_connection(self):
        for task in tasks.templates("a" * 32).values():
            task.validate()


class ExpandTests(unittest.TestCase):
    def test_only_known_placeholders(self):
        variables = {"ARTIFACT": "C:\\a.war", "NOW": "20261006-120000"}
        self.assertEqual(tasks.expand("cp ${ARTIFACT} /x-${NOW}; echo $HOME ${FOO} %PATH%", variables),
                         "cp C:\\a.war /x-20261006-120000; echo $HOME ${FOO} %PATH%")


class WarTests(unittest.TestCase):
    def test_build_war_with_excludes(self):
        with tempfile.TemporaryDirectory() as folder:
            web = os.path.join(folder, "web")
            for relative in ("index.jsp", "WEB-INF/web.xml", "WEB-INF/classes/A.class", "old.bak",
                             ".git/config", "css/site.css"):
                path = os.path.join(web, *relative.split("/"))
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w") as handle:
                    handle.write(relative)
            output = os.path.join(web, "dist", "app.war")  # inside the source: must not include itself
            lines = []
            tasks.build_war(web, output, "*.bak, .git", lines.append, threading.Event())
            with zipfile.ZipFile(output) as archive:
                names = sorted(archive.namelist())
            self.assertEqual(names, sorted(["META-INF/MANIFEST.MF", "WEB-INF/classes/A.class", "WEB-INF/web.xml",
                                            "css/site.css", "index.jsp"]))
            self.assertFalse(any("WEB-INF" in line and "注意" in line for line in lines))

    def test_missing_source(self):
        with self.assertRaises(tasks.StepFailed):
            tasks.build_war("/nonexistent/dir", "/tmp/x.war", "", print, threading.Event())

    def test_resolve_glob_picks_newest(self):
        with tempfile.TemporaryDirectory() as folder:
            older, newer = os.path.join(folder, "a-1.war"), os.path.join(folder, "a-2.war")
            for path, stamp in ((older, 1000), (newer, 2000)):
                open(path, "w").close()
                os.utime(path, (stamp, stamp))
            self.assertEqual(tasks.resolve_local_file(os.path.join(folder, "*.war")), newer)
            with self.assertRaises(tasks.StepFailed):
                tasks.resolve_local_file(os.path.join(folder, "*.jar"))


class FakeSession(remote.Session):
    def __init__(self, log_calls, fail_command=None):
        self.calls = log_calls
        self.fail_command = fail_command
        self.closed = False

    def upload(self, local, remote_dir, remote_name, backup, log, cancel):
        self.calls.append(("upload", local, remote_dir, remote_name, backup))
        return remote_dir + "/" + (remote_name or os.path.basename(local))

    def run(self, command, log, cancel):
        self.calls.append(("run", command))
        return 1 if command == self.fail_command else 0

    def close(self):
        self.closed = True


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.connection = make_connection()
        self.calls = []
        self.sessions = []
        self.folder = tempfile.mkdtemp()
        web = os.path.join(self.folder, "web", "WEB-INF")
        os.makedirs(web)
        open(os.path.join(web, "web.xml"), "w").close()

    def runner(self, fail_command=None):
        def open_session(connection, confirm, log):
            session = FakeSession(self.calls, fail_command)
            self.sessions.append(session)
            return session
        lines = []
        runner = tasks.Runner(AppData(connections=[self.connection]), lines.append, lambda *a: True, open_session)
        return runner, lines

    def test_war_upload_remote_pipeline(self):
        output = os.path.join(self.folder, "out", "app-${TODAY}.war")
        task = Task(name="部署", steps=[
            Step(type=STEP_LOCAL, command="echo building"),
            Step(type=STEP_WAR, source_dir=os.path.join(self.folder, "web"), output=output),
            Step(type=STEP_UPLOAD, connection_id=self.connection.id, local="${ARTIFACT}",
                 remote_dir="/opt/tomcat/webapps", remote_name="app.war"),
            Step(type=STEP_REMOTE, connection_id=self.connection.id, command="ls -l ${REMOTE_FILE}"),
        ])
        runner, lines = self.runner()
        self.assertTrue(runner.run(task), "\n".join(lines))
        self.assertEqual(self.calls[0][0], "upload")
        self.assertTrue(self.calls[0][1].endswith(".war"))
        self.assertNotIn("${TODAY}", self.calls[0][1])
        self.assertEqual(self.calls[1], ("run", "ls -l /opt/tomcat/webapps/app.war"))
        self.assertEqual(len(self.sessions), 1, "one SSH session is reused within a task")
        self.assertTrue(self.sessions[0].closed)
        self.assertTrue(any("building" in line for line in lines))

    def test_failure_stops_and_ignore_error_continues(self):
        task = Task(name="t", steps=[
            Step(type=STEP_REMOTE, connection_id=self.connection.id, command="bad", ignore_error=True),
            Step(type=STEP_REMOTE, connection_id=self.connection.id, command="bad"),
            Step(type=STEP_REMOTE, connection_id=self.connection.id, command="never"),
        ])
        runner, lines = self.runner(fail_command="bad")
        self.assertFalse(runner.run(task))
        self.assertEqual([c[1] for c in self.calls], ["bad", "bad"])

    def test_local_exit_code_fails(self):
        runner, lines = self.runner()
        self.assertFalse(runner.run(Task(name="t", steps=[Step(type=STEP_LOCAL, command="exit 3")])))
        self.assertTrue(any("退出码 3" in line for line in lines))

    def test_multiline_local_command_stops_at_first_failure(self):
        self.assertEqual(tasks.join_lines("echo a\n\n  exit 3\r\necho never\n"), "echo a && exit 3 && echo never")
        runner, lines = self.runner()
        self.assertFalse(runner.run(Task(name="t", steps=[Step(type=STEP_LOCAL, command="echo first\nexit 3\necho never")])))
        text = "\n".join(lines)
        self.assertIn("first", text)
        self.assertNotIn("  never", text)

    def test_unexpected_exception_type_is_reported_and_ignorable(self):
        class Boom(Exception):
            pass

        def open_session(connection, confirm, log):
            raise Boom("kaputt")
        lines = []
        runner = tasks.Runner(AppData(connections=[self.connection]), lines.append, lambda *a: True, open_session)
        task = Task(name="t", steps=[Step(type=STEP_REMOTE, connection_id=self.connection.id, command="x",
                                          ignore_error=True), Step(type=STEP_LOCAL, command="echo after")])
        self.assertTrue(runner.run(task))
        self.assertTrue(any("Boom: kaputt" in line for line in lines))

    def test_deleted_connection(self):
        runner, lines = self.runner()
        task = Task(name="t", steps=[Step(type=STEP_REMOTE, connection_id="f" * 32, command="x")])
        self.assertFalse(runner.run(task))
        self.assertTrue(any("已被删除" in line for line in lines))

    def test_cancel_stops_long_local_command(self):
        runner, lines = self.runner()
        command = "sleep 30" if os.name != "nt" else "ping -n 30 127.0.0.1"
        task = Task(name="t", steps=[Step(type=STEP_LOCAL, command=command)])
        timer = threading.Timer(0.5, runner.cancel.set)
        timer.start()
        started = time.monotonic()
        self.assertFalse(runner.run(task))
        self.assertLess(time.monotonic() - started, 10)
        self.assertTrue(any("已停止" in line for line in lines))


class MacroTests(unittest.TestCase):
    def test_macro_has_no_secret_and_validates_inputs(self):
        macro = teraterm.create_macro("TeraLink-" + "a" * 32, "C:\\Users\\me\\result.txt")
        self.assertIn("connect '/DS'", macro)
        self.assertIn("fileopen channel '\\\\.\\pipe\\TeraLink-" + "a" * 32 + "' 0 1", macro)
        self.assertNotIn("passwd", macro)
        for pipe, report in (("evil", "C:\\r.txt"), ("TeraLink-" + "a" * 32, 'C:\\"x.txt')):
            with self.assertRaises(ValueError):
                teraterm.create_macro(pipe, report)


class RemoteHelperTests(unittest.TestCase):
    def test_fingerprint_and_backup_name(self):
        self.assertTrue(remote.sha256_fingerprint(b"key").startswith("SHA256:"))
        from datetime import datetime
        self.assertEqual(remote.backup_name("app.war", datetime(2026, 10, 6, 15, 30, 0)), "app.war.bak-20261006-153000")


if __name__ == "__main__":
    unittest.main()
