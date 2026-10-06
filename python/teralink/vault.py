"""Password protection with Windows DPAPI (current-user scope), via ctypes.

The ciphertext format matches the C# TeraLink (DPAPI over UTF-8, base64), so legacy data can be imported.
On non-Windows systems the vault refuses to work unless TERALINK_INSECURE_TEST_VAULT=1 is set (tests only).
"""
from __future__ import annotations

import base64
import os

_TEST_PREFIX = b"TEST-ONLY:"


def _insecure_test_mode() -> bool:
    return os.name != "nt" and os.environ.get("TERALINK_INSECURE_TEST_VAULT") == "1"


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _crypt32.CryptProtectData.argtypes = [ctypes.POINTER(_Blob), wintypes.LPCWSTR, ctypes.c_void_p,
                                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
                                            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p
    _CRYPTPROTECT_UI_FORBIDDEN = 0x1

    def _transform(data: bytes, protect: bool) -> bytes:
        buffer = ctypes.create_string_buffer(data, len(data))
        source = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
        target = _Blob()
        try:
            if protect:
                ok = _crypt32.CryptProtectData(ctypes.byref(source), "TeraLink", None, None, None,
                                               _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(target))
            else:
                ok = _crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None,
                                                 _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(target))
            if not ok:
                code = ctypes.get_last_error()
                raise OSError(code, "Windows 无法加密密码。" if protect else
                              "无法解密密码，请使用保存时的 Windows 账户，或编辑连接重新输入密码。")
            return ctypes.string_at(target.pbData, target.cbData)
        finally:
            ctypes.memset(buffer, 0, len(data))
            if target.pbData:
                ctypes.memset(target.pbData, 0, target.cbData)
                _kernel32.LocalFree(target.pbData)


def protect(password: str) -> str:
    if not 1 <= len(password) <= 1024 or "\0" in password:
        raise ValueError("密码不能为空，不能包含空字符，最多 1024 字。")
    raw = password.encode("utf-8")
    if os.name == "nt":
        return base64.b64encode(_transform(raw, True)).decode("ascii")
    if _insecure_test_mode():
        return base64.b64encode(_TEST_PREFIX + raw).decode("ascii")
    raise OSError("密码加密只支持 Windows（DPAPI）。")


def unprotect(ciphertext: str) -> str:
    try:
        raw = base64.b64decode(ciphertext.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError):
        raise ValueError("保存的密码数据格式无效。")
    if os.name == "nt":
        return _transform(raw, False).decode("utf-8")
    if _insecure_test_mode() and raw.startswith(_TEST_PREFIX):
        return raw[len(_TEST_PREFIX):].decode("utf-8")
    raise OSError("密码解密只支持 Windows（DPAPI）。")


def to_rdp_password(ciphertext: str) -> str:
    """mstsc's `password 51:b:` value: DPAPI over UTF-16LE, upper-case hex."""
    password = unprotect(ciphertext)
    raw = password.encode("utf-16-le")
    if os.name == "nt":
        return _transform(raw, True).hex().upper()
    if _insecure_test_mode():
        return raw.hex().upper()
    raise OSError("RDP 密码只支持 Windows（DPAPI）。")
