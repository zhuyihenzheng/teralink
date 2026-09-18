using System.Net;
using System.Net.Sockets;

namespace TeraLink.Core;

public static class RdpProfile
{
    // encryptedPasswordHex is DPAPI-protected UTF-16LE, not the vault's UTF-8 ciphertext.
    public static string Create(Connection connection, string encryptedPasswordHex, string? existingProfile = null)
    {
        connection.Validate();
        if (connection.Kind != ConnectionKind.Rdp) throw new ArgumentException("请选择 RDP 连接。");
        if (string.IsNullOrEmpty(encryptedPasswordHex) || encryptedPasswordHex.Length % 2 != 0
            || encryptedPasswordHex.Length > 65536 || !encryptedPasswordHex.All(Uri.IsHexDigit))
            throw new ArgumentException("RDP 加密密码格式无效。");
        if (existingProfile is not null)
        {
            var target = Inspect(existingProfile);
            if (!target.Host.Equals(connection.Host, StringComparison.OrdinalIgnoreCase) || target.Port != connection.Port)
                throw new ArgumentException("原 RDP 文件的目标地址和连接记录不一致，请重新选择文件。");
            var imported = ReadSettings(existingProfile);
            // Editing a signed RDP file invalidates its signature. The derived file is unsigned.
            imported.RemoveAll(line => new[] { "username", "password 51", "prompt for credentials", "signature", "signscope" }
                .Contains(Key(line), StringComparer.OrdinalIgnoreCase));
            if (connection.Username.Contains('\\') || connection.Username.Contains('@'))
                imported.RemoveAll(line => Key(line).Equals("domain", StringComparison.OrdinalIgnoreCase));
            imported.Add($"username:s:{connection.Username}");
            imported.Add($"password 51:b:{encryptedPasswordHex}");
            imported.Add("prompt for credentials:i:0");
            return string.Join("\r\n", imported) + "\r\n";
        }
        var host = connection.Host;
        if (IPAddress.TryParse(host, out var address) && address.AddressFamily == AddressFamily.InterNetworkV6)
            host = $"[{host}]";
        return string.Join("\r\n", new[]
        {
            $"full address:s:{host}:{connection.Port}",
            $"username:s:{connection.Username}",
            $"password 51:b:{encryptedPasswordHex}",
            "prompt for credentials:i:0",
            "enablecredsspsupport:i:1",
            "authentication level:i:2",
            "gatewayusagemethod:i:0",
            $"screen mode id:i:{(connection.RdpFullScreen ? 2 : 1)}",
            "desktopwidth:i:1280", "desktopheight:i:800",
            "session bpp:i:32", "compression:i:1",
            "disable wallpaper:i:1", "allow font smoothing:i:0",
            "allow desktop composition:i:0", "disable full window drag:i:1",
            "disable menu anims:i:1", "disable themes:i:1",
            "redirectclipboard:i:1", "redirectprinters:i:0",
            "redirectcomports:i:0", "redirectsmartcards:i:0",
            "drivestoredirect:s:", "audiomode:i:2", "audiocapturemode:i:0", ""
        });
    }

    private static string Key(string line) => line.Split(':', 2)[0].Trim();

    private static List<string> ReadSettings(string profile)
    {
        if (profile.Length > 1024 * 1024 || profile.Contains('\0'))
            throw new ArgumentException("RDP 文件过大或编码无效。");
        var lines = profile.Split(['\r', '\n'], StringSplitOptions.RemoveEmptyEntries).ToList();
        foreach (var line in lines)
        {
            var parts = line.Split(':', 3);
            if (parts.Length != 3 || parts[0].Length == 0 || parts[0] != parts[0].Trim()
                || parts[1] is not ("s" or "i" or "b") || line.Any(char.IsControl))
                throw new ArgumentException("RDP 文件中存在无效设置，请用远程桌面客户端重新另存为 .rdp 文件。");
            if (new[] { "full address", "username", "domain" }.Contains(parts[0], StringComparer.OrdinalIgnoreCase) && parts[1] != "s")
                throw new ArgumentException("RDP 地址或账户设置类型无效。");
        }
        if (lines.GroupBy(Key, StringComparer.OrdinalIgnoreCase).Any(group => group.Count() > 1))
            throw new ArgumentException("RDP 文件包含重复设置，请用远程桌面客户端重新另存为。");
        if (!lines.Any(line => Key(line).Equals("full address", StringComparison.OrdinalIgnoreCase)))
            throw new ArgumentException("RDP 文件缺少 full address 目标地址。");
        return lines;
    }

    public static (string Host, int Port, string Username) Inspect(string profile)
    {
        var lines = ReadSettings(profile);
        string Value(string key) => lines.FirstOrDefault(line => Key(line).Equals(key, StringComparison.OrdinalIgnoreCase))?.Split(':', 3)[2] ?? "";
        string endpoint = Value("full address");
        string host = endpoint;
        int port = 3389;
        if (endpoint.StartsWith('['))
        {
            int end = endpoint.IndexOf(']');
            if (end < 0) throw new ArgumentException("RDP IPv6 地址无效。");
            host = endpoint[1..end];
            if (endpoint.Length > end + 1)
            {
                if (endpoint[end + 1] != ':' || !int.TryParse(endpoint[(end + 2)..], out port))
                    throw new ArgumentException("RDP 端口无效。");
            }
        }
        else if (endpoint.Count(c => c == ':') == 1)
        {
            int separator = endpoint.LastIndexOf(':');
            host = endpoint[..separator];
            if (!int.TryParse(endpoint[(separator + 1)..], out port)) throw new ArgumentException("RDP 端口无效。");
        }
        Connection.ValidateHost(host);
        if (port is < 1 or > 65535) throw new ArgumentException("RDP 端口无效。");
        string username = Value("username"), domain = Value("domain");
        if (username.Length > 0 && domain.Length > 0 && !username.Contains('\\') && !username.Contains('@'))
            username = domain + "\\" + username;
        return (host, port, username);
    }
}
