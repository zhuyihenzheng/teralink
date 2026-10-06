"""A one-shot outbound named pipe that only the current Windows user can open (ctypes, Windows only)."""
from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes
from typing import Optional

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

_INVALID_HANDLE = ctypes.c_void_p(-1).value
_PIPE_ACCESS_OUTBOUND = 0x00000002
_FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000
_PIPE_TYPE_BYTE = 0x0
_PIPE_WAIT = 0x0
_PIPE_REJECT_REMOTE_CLIENTS = 0x8
_ERROR_PIPE_CONNECTED = 535
_GENERIC_READ = 0x80000000
_OPEN_EXISTING = 3
_TOKEN_QUERY = 0x0008
_TOKEN_USER = 1


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [("nLength", wintypes.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p),
                ("bInheritHandle", wintypes.BOOL)]


_kernel32.GetCurrentProcess.restype = wintypes.HANDLE
_kernel32.CreateNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                       wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                       ctypes.POINTER(_SecurityAttributes)]
_kernel32.CreateNamedPipeW.restype = wintypes.HANDLE
_kernel32.ConnectNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
_kernel32.ConnectNamedPipe.restype = wintypes.BOOL
_kernel32.GetNamedPipeClientProcessId.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG)]
_kernel32.GetNamedPipeClientProcessId.restype = wintypes.BOOL
_kernel32.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
_kernel32.WriteFile.restype = wintypes.BOOL
_kernel32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
_kernel32.FlushFileBuffers.restype = wintypes.BOOL
_kernel32.DisconnectNamedPipe.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                  wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
_kernel32.CreateFileW.restype = wintypes.HANDLE
_kernel32.LocalFree.argtypes = [ctypes.c_void_p]
_kernel32.LocalFree.restype = ctypes.c_void_p
_advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
_advapi32.OpenProcessToken.restype = wintypes.BOOL
_advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                          ctypes.POINTER(wintypes.DWORD)]
_advapi32.GetTokenInformation.restype = wintypes.BOOL
_advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
_advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
_advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
_advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL


def _fail(message: str) -> OSError:
    return OSError(ctypes.get_last_error(), message)


def current_user_sid() -> str:
    token = wintypes.HANDLE()
    if not _advapi32.OpenProcessToken(_kernel32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)):
        raise _fail("无法读取当前 Windows 账户。")
    try:
        needed = wintypes.DWORD()
        _advapi32.GetTokenInformation(token, _TOKEN_USER, None, 0, ctypes.byref(needed))
        buffer = ctypes.create_string_buffer(needed.value)
        if not _advapi32.GetTokenInformation(token, _TOKEN_USER, buffer, needed, ctypes.byref(needed)):
            raise _fail("无法读取当前 Windows 账户。")
        sid_pointer = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]  # TOKEN_USER.User.Sid
        text = wintypes.LPWSTR()
        if not _advapi32.ConvertSidToStringSidW(sid_pointer, ctypes.byref(text)):
            raise _fail("无法读取当前 Windows 账户。")
        try:
            return text.value
        finally:
            _kernel32.LocalFree(text)
    finally:
        _kernel32.CloseHandle(token)


class SecureOutboundPipe:
    def __init__(self, name: str):
        self.path = "\\\\.\\pipe\\" + name
        self.connected = threading.Event()
        self.error: Optional[BaseException] = None
        self._closed = False
        descriptor = ctypes.c_void_p()
        # Protected DACL: full control for the current user only.
        sddl = "D:P(A;;GA;;;%s)" % current_user_sid()
        if not _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
            raise _fail("无法创建本机管道的访问控制。")
        try:
            attributes = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), descriptor, False)
            handle = _kernel32.CreateNamedPipeW(
                self.path, _PIPE_ACCESS_OUTBOUND | _FILE_FLAG_FIRST_PIPE_INSTANCE,
                _PIPE_TYPE_BYTE | _PIPE_WAIT | _PIPE_REJECT_REMOTE_CLIENTS, 1, 4096, 4096, 0,
                ctypes.byref(attributes))
        finally:
            _kernel32.LocalFree(descriptor)
        if handle in (None, _INVALID_HANDLE):
            raise _fail("无法创建本机管道。")
        self.handle = handle

    def start_accept(self) -> None:
        def accept():
            try:
                if not _kernel32.ConnectNamedPipe(self.handle, None):
                    code = ctypes.get_last_error()
                    if code != _ERROR_PIPE_CONNECTED:
                        raise OSError(code, "本机管道连接失败。")
            except BaseException as error:  # handed to the waiting thread
                self.error = error
            finally:
                self.connected.set()

        threading.Thread(target=accept, name="teralink-pipe", daemon=True).start()

    def client_pid(self) -> int:
        pid = wintypes.ULONG()
        if not _kernel32.GetNamedPipeClientProcessId(self.handle, ctypes.byref(pid)):
            raise _fail("无法确认管道接收端。")
        return pid.value

    def write(self, payload: bytearray) -> None:
        buffer = (ctypes.c_char * len(payload)).from_buffer(payload)
        written = wintypes.DWORD()
        if not _kernel32.WriteFile(self.handle, buffer, len(payload), ctypes.byref(written), None) \
                or written.value != len(payload):
            raise _fail("向 Tera Term 宏传递连接信息失败。")
        _kernel32.FlushFileBuffers(self.handle)  # returns once the macro has read the data

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if not self.connected.is_set():
            # Unblock the accept thread with our own client connection, then drop it.
            client = _kernel32.CreateFileW(self.path, _GENERIC_READ, 0, None, _OPEN_EXISTING, 0, None)
            if client not in (None, _INVALID_HANDLE):
                _kernel32.CloseHandle(client)
            self.connected.wait(2)
        _kernel32.DisconnectNamedPipe(self.handle)
        _kernel32.CloseHandle(self.handle)
