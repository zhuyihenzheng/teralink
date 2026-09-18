using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;

namespace TeraLink.Windows;

internal static class PasswordVault
{
    // No machine scope: DPAPI binds this ciphertext to the current Windows account.
    [StructLayout(LayoutKind.Sequential)]
    private struct Blob { public int Size; public IntPtr Data; }

    [DllImport("crypt32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CryptProtectData(ref Blob input, string description, IntPtr entropy, IntPtr reserved, IntPtr prompt, int flags, out Blob output);
    [DllImport("crypt32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CryptUnprotectData(ref Blob input, IntPtr description, IntPtr entropy, IntPtr reserved, IntPtr prompt, int flags, out Blob output);
    [DllImport("kernel32.dll")] private static extern IntPtr LocalFree(IntPtr memory);

    public static string Protect(string password)
    {
        if (password.Length is < 1 or > 1024 || password.Contains('\0'))
            throw new ArgumentException("密码不能为空，不能包含空字符，最多 1024 字。");
        var raw = Encoding.UTF8.GetBytes(password);
        try { return Convert.ToBase64String(Transform(raw, protect: true)); }
        finally { CryptographicOperations.ZeroMemory(raw); }
    }

    public static string Unprotect(string ciphertext)
    {
        var raw = Transform(Convert.FromBase64String(ciphertext), protect: false);
        try { return Encoding.UTF8.GetString(raw); }
        finally { CryptographicOperations.ZeroMemory(raw); }
    }

    public static string ToRdpPassword(string ciphertext)
    {
        string password = Unprotect(ciphertext);
        var raw = Encoding.Unicode.GetBytes(password);
        password = "";
        try { return Convert.ToHexString(Transform(raw, protect: true)); }
        finally { CryptographicOperations.ZeroMemory(raw); }
    }

    private static byte[] Transform(byte[] bytes, bool protect)
    {
        var input = new Blob { Size = bytes.Length, Data = Marshal.AllocHGlobal(bytes.Length) };
        Blob output = default;
        try
        {
            Marshal.Copy(bytes, 0, input.Data, bytes.Length);
            bool ok = protect
                ? CryptProtectData(ref input, "TeraLink", IntPtr.Zero, IntPtr.Zero, IntPtr.Zero, 1, out output)
                : CryptUnprotectData(ref input, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero, 1, out output);
            if (!ok) throw new Win32Exception(Marshal.GetLastWin32Error(), protect
                ? "Windows 无法加密密码。" : "无法解密密码，请使用保存时的 Windows 账户，或编辑连接重新输入密码。");
            var result = new byte[output.Size];
            Marshal.Copy(output.Data, result, 0, result.Length);
            return result;
        }
        finally
        {
            for (int i = 0; i < input.Size; i++) Marshal.WriteByte(input.Data, i, 0);
            Marshal.FreeHGlobal(input.Data);
            if (output.Data != IntPtr.Zero)
            {
                for (int i = 0; i < output.Size; i++) Marshal.WriteByte(output.Data, i, 0);
                LocalFree(output.Data);
            }
        }
    }
}
