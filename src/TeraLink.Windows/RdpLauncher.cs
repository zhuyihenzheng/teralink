using System.Diagnostics;
using System.Text;
using System.Security.Cryptography;
using TeraLink.Core;

namespace TeraLink.Windows;

internal static class RdpLauncher
{
    private static string DirectoryPath => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "TeraLink", "rdp");

    public static void Launch(Connection connection)
    {
        connection.Validate();
        string executable = Path.Combine(Environment.SystemDirectory, "mstsc.exe");
        if (!File.Exists(executable)) throw new FileNotFoundException("未找到 Windows 远程桌面客户端 mstsc.exe。");
        string? existing = connection.RdpFilePath.Length > 0 ? ReadProfile(connection.RdpFilePath) : null;
        if (existing is not null)
        {
            if (Fingerprint(existing) != connection.RdpFileHash)
                throw new IOException("原 RDP 文件的设置已改变，请编辑连接并重新选择文件后保存。");
            var target = RdpProfile.Inspect(existing);
            if (!target.Host.Equals(connection.Host, StringComparison.OrdinalIgnoreCase) || target.Port != connection.Port)
                throw new IOException("原 RDP 文件的目标地址已改变，请编辑连接并重新选择文件后保存。");
        }
        string profile = RdpProfile.Create(connection, PasswordVault.ToRdpPassword(connection.ProtectedPassword), existing);
        Directory.CreateDirectory(DirectoryPath);
        string path = Path.Combine(DirectoryPath, connection.Id.ToString("N") + ".rdp");
        string temporary = path + ".tmp";
        try
        {
            // Retain the encrypted profile so mstsc can read it after the launcher exits.
            // No shared TERMSRV credentials are written or replaced.
            File.WriteAllText(temporary, profile, Encoding.Unicode);
            File.Move(temporary, path, overwrite: true);
            var start = new ProcessStartInfo(executable) { UseShellExecute = false };
            start.ArgumentList.Add(path);
            using var process = Process.Start(start) ?? throw new IOException("未能打开远程桌面客户端。");
        }
        catch
        {
            try { File.Delete(path); } catch (IOException) { } catch (UnauthorizedAccessException) { }
            throw;
        }
        finally { if (File.Exists(temporary)) File.Delete(temporary); }
    }

    public static void Forget(Guid id)
    {
        string path = Path.Combine(DirectoryPath, id.ToString("N") + ".rdp");
        if (File.Exists(path)) File.Delete(path);
    }

    public static string ReadProfile(string path)
    {
        if (!Path.IsPathFullyQualified(path) || !path.EndsWith(".rdp", StringComparison.OrdinalIgnoreCase))
            throw new ArgumentException("请选择完整路径的 .rdp 文件。");
        if (!File.Exists(path)) throw new FileNotFoundException("原 RDP 文件不存在，请编辑连接重新选择。", path);
        if (new FileInfo(path).Length > 1024 * 1024) throw new IOException("RDP 文件过大。");
        return File.ReadAllText(path); // Detects UTF-16 BOM produced by mstsc, or UTF-8.
    }

    public static string Fingerprint(string profile) => Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(profile)));
}
