"""A tiny SSH server (paramiko) with a scripted shell, for end-to-end tests of the Tera Term macro.

Prompt is "$ "; a line starting with "sudo" asks "[sudo] password for <user>: " and, on the right password,
switches to "# ". Everything received is recorded so tests can check what was typed and when.
"""
import socket
import threading

import paramiko


class _Server(paramiko.ServerInterface):
    def __init__(self, owner):
        self.owner = owner

    def get_allowed_auths(self, username):
        return "password"

    def check_auth_password(self, username, password):
        self.owner.events.append(("auth", username, password == self.owner.password))
        if username == self.owner.username and password == self.owner.password:
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_pty_request(self, *args):
        return True

    def check_channel_shell_request(self, channel):
        return True

    def check_channel_window_change_request(self, *args):
        return True


class FakeSshServer:
    def __init__(self, username="deploy", password="Ci-Pass-123"):
        self.username, self.password = username, password
        self.key = paramiko.RSAKey.generate(2048)
        self.events = []      # ("auth", user, ok) / ("cmd", line, prompt_before) / ("password", ok)
        self.done = threading.Event()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self._stop = False
        threading.Thread(target=self._accept, daemon=True).start()

    def known_hosts_line(self):
        return "[127.0.0.1]:%d %s %s" % (self.port, self.key.get_name(), self.key.get_base64())

    def commands(self):
        return [event[1] for event in self.events if event[0] == "cmd"]

    def close(self):
        self._stop = True
        try:
            self.sock.close()
        except OSError:
            pass

    def _accept(self):
        while not self._stop:
            try:
                client, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(client,), daemon=True).start()

    def _serve(self, client):
        transport = paramiko.Transport(client)
        transport.add_server_key(self.key)
        try:
            transport.start_server(server=_Server(self))
            channel = transport.accept(30)
            if channel is None:
                return
            self._shell(channel)
        except Exception as error:  # surfaced through events for the test to report
            self.events.append(("error", repr(error)))
        finally:
            self.done.set()

    def _shell(self, channel):
        prompt, mode, line = "$ ", "shell", b""
        channel.send(b"Welcome to the fake server\r\n" + prompt.encode())
        while True:
            data = channel.recv(1024)
            if not data:
                return
            for byte in data:
                char = bytes([byte])
                if char in (b"\r", b"\n"):
                    if char == b"\n" and not line:
                        continue  # CR LF: the LF after a CR
                    text = line.decode("utf-8", "replace")
                    line = b""
                    if mode == "password":
                        ok = text == self.password
                        self.events.append(("password", ok))
                        prompt = "# " if ok else "$ "
                        channel.send(("\r\n" if ok else "\r\nSorry, try again.\r\n").encode() + prompt.encode())
                        mode = "shell"
                        continue
                    self.events.append(("cmd", text, prompt))
                    channel.send(b"\r\n")
                    if text.startswith("sudo"):
                        channel.send(("[sudo] password for %s: " % self.username).encode())
                        mode = "password"
                    elif text == "exit":
                        channel.close()
                        return
                    else:
                        channel.send(("output of %s\r\n%s" % (text, prompt)).encode())
                elif mode == "password":
                    line += char  # not echoed, like a real password prompt
                else:
                    line += char
                    channel.send(char)
