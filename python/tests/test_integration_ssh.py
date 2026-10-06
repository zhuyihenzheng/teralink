"""Real SSH / SFTP against a live Linux sshd (paramiko required).

Skipped unless TERALINK_SSH_TEST_HOST is set. CI starts sshd on the Linux runner and sets:
  TERALINK_SSH_TEST_HOST, TERALINK_SSH_TEST_PORT, TERALINK_SSH_TEST_USER, TERALINK_SSH_TEST_PASSWORD
"""
import os
import sys
import tempfile
import threading
import unittest

os.environ.setdefault("TERALINK_INSECURE_TEST_VAULT", "1")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from teralink import remote, tasks, vault  # noqa: E402
from teralink.model import STEP_REMOTE, STEP_UPLOAD, STEP_WAR, AppData, Connection, Step, Task  # noqa: E402

HOST = os.environ.get("TERALINK_SSH_TEST_HOST")


@unittest.skipUnless(HOST and remote.paramiko is not None, "needs TERALINK_SSH_TEST_HOST and paramiko")
class RealSshTests(unittest.TestCase):
    def setUp(self):
        self.data_dir = tempfile.mkdtemp()
        os.environ["TERALINK_DATA_DIR"] = self.data_dir
        self.connection = Connection(
            name="ci", host=HOST, port=int(os.environ.get("TERALINK_SSH_TEST_PORT", "22")),
            username=os.environ["TERALINK_SSH_TEST_USER"],
            protected_password=vault.protect(os.environ["TERALINK_SSH_TEST_PASSWORD"]))
        self.prompts = []
        self.remote_dir = "/tmp/teralink-ci/%s/webapps" % os.urandom(4).hex()
        self.lines = []

    def confirm(self, host, port, key_type, fingerprint):
        self.prompts.append((host, port, key_type, fingerprint))
        return True

    def session(self):
        return remote.open_session(self.connection, self.confirm, self.lines.append)

    def test_host_key_prompt_once_then_remembered(self):
        self.session().close()
        self.assertEqual(len(self.prompts), 1)
        host, port, key_type, fingerprint = self.prompts[0]
        self.assertEqual((host, port), (HOST, self.connection.port))
        self.assertTrue(fingerprint.startswith("SHA256:"))
        self.session().close()
        self.assertEqual(len(self.prompts), 1, "known_hosts entry must match on the next connect")

    def test_rejecting_host_key_cancels(self):
        with self.assertRaises(remote.RemoteError):
            remote.open_session(self.connection, lambda *a: False, self.lines.append)

    def test_wrong_password(self):
        from dataclasses import replace
        bad = replace(self.connection, protected_password=vault.protect("definitely-wrong"))
        with self.assertRaisesRegex(remote.RemoteError, "登录失败"):
            remote.open_session(bad, self.confirm, self.lines.append)

    def test_upload_backup_and_run(self):
        session = self.session()
        try:
            local = os.path.join(self.data_dir, "app.war")
            with open(local, "wb") as handle:
                handle.write(os.urandom(300000))
            cancel = threading.Event()
            target = session.upload(local, self.remote_dir, "myapp.war", True, self.lines.append, cancel)
            self.assertEqual(target, self.remote_dir + "/myapp.war")
            session.upload(local, self.remote_dir, "myapp.war", True, self.lines.append, cancel)
            code = session.run("ls -a %s; stat -c %%s %s; echo err >&2; exit 7" % (self.remote_dir, target),
                               self.lines.append, cancel)
            self.assertEqual(code, 7)
            text = "\n".join(self.lines)
            self.assertIn("myapp.war.bak-", text)
            self.assertIn("300000", text)
            self.assertIn("err", text, "stderr is merged into the log")
            self.assertNotIn(".myapp.war.part", text.split("$ ls")[-1])
        finally:
            session.close()

    def test_cancel_mid_upload_leaves_no_part_file(self):
        session = self.session()
        try:
            local = os.path.join(self.data_dir, "big.war")
            with open(local, "wb") as handle:
                handle.write(os.urandom(20 * 1024 * 1024))
            cancel = threading.Event()

            def log(line):
                self.lines.append(line)
                if "上传 10%" in line:
                    cancel.set()

            with self.assertRaises(remote.RemoteError):
                session.upload(local, self.remote_dir, "big.war", False, log, cancel)
            self.lines.clear()
            session.run("ls -a %s" % self.remote_dir, self.lines.append, threading.Event())
            self.assertFalse(any(".part" in line or "big.war" in line for line in self.lines), self.lines)
        finally:
            session.close()

    def test_sudo_command_runs_as_root(self):
        session = self.session()
        try:
            password = vault.unprotect(self.connection.protected_password)
            code = session.run("whoami; id -u", self.lines.append, threading.Event(), password)
            self.assertEqual(code, 0, self.lines)
            text = "\n".join(self.lines)
            self.assertIn("root", text)
            self.assertNotIn(password, text)
            self.assertNotIn("teralink-sudo-password", text, "the prompt marker is hidden from the log")
        finally:
            session.close()

    def test_sudo_wrong_password_is_reported_not_retried(self):
        session = self.session()
        try:
            with self.assertRaisesRegex(remote.RemoteError, "sudo"):
                session.run("sudo -k; whoami", self.lines.append, threading.Event(), "definitely-wrong")
        finally:
            session.close()

    def test_command_reading_stdin_does_not_hang(self):
        session = self.session()
        try:
            self.assertEqual(session.run("cat; echo done", self.lines.append, threading.Event()), 0)
            self.assertIn("done", "\n".join(self.lines))
        finally:
            session.close()

    def test_sudo_upload_into_root_owned_dir_with_owner(self):
        session = self.session()
        try:
            password = vault.unprotect(self.connection.protected_password)
            target_dir = "/opt/teralink-ci/%s/webapps" % os.urandom(4).hex()
            local = os.path.join(self.data_dir, "app.war")
            with open(local, "wb") as handle:
                handle.write(os.urandom(100000))
            cancel = threading.Event()
            target = session.upload(local, target_dir, "myapp.war", True, self.lines.append, cancel, password,
                                    "tomcat:tomcat")
            session.upload(local, target_dir, "myapp.war", True, self.lines.append, cancel, password,
                           "tomcat:tomcat")
            self.lines.clear()
            session.run("stat -c '%%U:%%G %%a %%s' %s; ls -a %s; ls -ld /opt/teralink-ci; ls /tmp | grep -c teralink- || true"
                        % (target, target_dir), self.lines.append, cancel)
            text = "\n".join(self.lines)
            self.assertIn("tomcat:tomcat 644 100000", text)
            self.assertIn("myapp.war.bak-", text)
            self.assertNotIn(".part", text)
            self.assertTrue(text.strip().endswith("0"), "staging folders in /tmp are removed: %s" % text)
        finally:
            session.close()

    def test_runner_pipeline_reuses_one_session(self):
        web = os.path.join(self.data_dir, "web", "WEB-INF")
        os.makedirs(web)
        with open(os.path.join(web, "web.xml"), "w") as handle:
            handle.write("<web-app/>")
        task = Task(name="ci", confirm=False, steps=[
            Step(type=STEP_WAR, source_dir=os.path.dirname(web), output=os.path.join(self.data_dir, "a-${NOW}.war")),
            Step(type=STEP_UPLOAD, connection_id=self.connection.id, local="${ARTIFACT}",
                 remote_dir=self.remote_dir, remote_name="a.war"),
            Step(type=STEP_REMOTE, connection_id=self.connection.id,
                 command="unzip -l ${REMOTE_FILE} | grep -c WEB-INF/web.xml"),
        ])
        runner = tasks.Runner(AppData(connections=[self.connection]), self.lines.append, self.confirm)
        self.assertTrue(runner.run(task), "\n".join(self.lines))
        self.assertEqual(len(self.prompts), 1)


if __name__ == "__main__":
    unittest.main()
