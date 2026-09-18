using System.Net;
using System.Text.Json;
using System.Text;
using System.Text.RegularExpressions;

namespace TeraLink.Core;

public enum ConnectionKind { Ssh, Rdp }

public sealed record Connection
{
    public Guid Id { get; init; } = Guid.NewGuid();
    public string Name { get; init; } = "";
    public string Host { get; init; } = "";
    public int Port { get; init; } = 22;
    public string Username { get; init; } = "";
    public string Group { get; init; } = "";
    public string Notes { get; init; } = "";
    public string ProtectedPassword { get; init; } = "";
    public ConnectionKind Kind { get; init; } = ConnectionKind.Ssh;
    public bool RdpFullScreen { get; init; }
    public string RdpFilePath { get; init; } = "";
    public string RdpFileHash { get; init; } = "";
    public bool Favorite { get; init; }
    public DateTimeOffset? LastLaunched { get; init; }

    public Connection Validate()
    {
        if (Id == Guid.Empty) throw new ArgumentException("连接 ID 无效。");
        if (!Enum.IsDefined(Kind)) throw new ArgumentException("连接类型无效。");
        if (RdpFilePath is null || RdpFilePath.Length > 4096 || RdpFilePath.Any(char.IsControl)
            || (RdpFilePath.Length > 0 && !RdpFilePath.EndsWith(".rdp", StringComparison.OrdinalIgnoreCase)))
            throw new ArgumentException("请选择有效的 .rdp 连接文件。");
        if (RdpFileHash is null || (RdpFilePath.Length > 0 && (RdpFileHash.Length != 64 || !RdpFileHash.All(Uri.IsHexDigit))))
            throw new ArgumentException("RDP 文件校验信息无效，请重新选择文件。");
        if (string.IsNullOrWhiteSpace(Name) || Name.Length > 100) throw new ArgumentException("连接名称必填，最多 100 字。");
        ValidateHost(Host);
        if (Port is < 1 or > 65535) throw new ArgumentException("端口必须在 1–65535 之间。");
        if (string.IsNullOrWhiteSpace(Username) || Username.Length > 255 || Username.Any(char.IsControl))
            throw new ArgumentException("请输入有效的用户名（最多 255 字）。");
        if (Group.Length > 100 || Notes.Length > 2000) throw new ArgumentException("分组最多 100 字，备注最多 2000 字。");
        if (string.IsNullOrEmpty(ProtectedPassword) || ProtectedPassword.Length > 32768)
            throw new ArgumentException("请保存登录密码。");
        try { Convert.FromBase64String(ProtectedPassword); }
        catch (FormatException) { throw new ArgumentException("保存的密码数据格式无效。"); }
        return this;
    }

    public static string ValidateHost(string host)
    {
        if (string.IsNullOrWhiteSpace(host) || host.Length > 253 || host != host.Trim())
            throw new ArgumentException("请输入主机名或 IP 地址，不要包含协议、端口或空格。");
        // Never allow Tera Term switches, URLs, userinfo or command-line quoting in a host.
        if (host.Any(c => char.IsWhiteSpace(c) || char.IsControl(c) || "/\\\"';@".Contains(c)))
            throw new ArgumentException("主机只填写主机名或 IP 地址，端口请单独填写。");
        if (IPAddress.TryParse(host, out _)) return host;
        if (!Regex.IsMatch(host, @"^(?=.{1,253}$)[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?\.?$"))
            throw new ArgumentException("主机名格式无效；国际域名请填写 punycode。");
        if (host.TrimEnd('.').Split('.').Any(label => label.Length is < 1 or > 63 || label.StartsWith('-') || label.EndsWith('-')))
            throw new ArgumentException("主机名格式无效。");
        return host;
    }

    // For the already-linked macro only. NEVER use as process arguments or write to disk.
    public string MacroConnectCommand(string password)
    {
        if (Kind != ConnectionKind.Ssh) throw new ArgumentException("只有 SSH 连接可使用 Tera Term 宏。");
        ValidateHost(Host);
        if (Port is < 1 or > 65535) throw new ArgumentException("端口无效。");
        if (string.IsNullOrWhiteSpace(Username) || Username.Any(char.IsControl) || string.IsNullOrEmpty(password) || password.Any(char.IsControl))
            throw new ArgumentException("用户名和密码不能为空，且不能包含换行或控制字符。");
        static string Quote(string value) => "\"" + value.Replace("\"", "\"\"") + "\"";
        string command = $"{Host} /P={Port} /ssh /2 /auth=password /user={Quote(Username)} /passwd={Quote(password)}";
        if (Encoding.UTF8.GetByteCount(command) > 511)
            throw new ArgumentException("连接信息过长：Tera Term 宏的连接参数最多 511 个 UTF-8 字节，请缩短主机名、用户名或密码。");
        return command;
    }
}

public sealed record AppData
{
    public int Version { get; init; } = 2;
    public string TeraTermPath { get; init; } = "";
    public bool ExitAfterLaunch { get; init; }
    public List<Connection> Connections { get; init; } = [];
}

public sealed class ConnectionStore : IDisposable
{
    private readonly string path;
    private readonly FileStream lease;
    private static readonly JsonSerializerOptions JsonOptions = new() { WriteIndented = true };

    public ConnectionStore(string directory)
    {
        Directory.CreateDirectory(directory);
        path = Path.Combine(directory, "connections.json");
        // Held throughout the process; blocks concurrent instances from losing each other's edits.
        lease = new FileStream(Path.Combine(directory, "connections.lock"), FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None);
    }

    public AppData Load()
    {
        if (!File.Exists(path)) return new();
        if (new FileInfo(path).Length > 16 * 1024 * 1024) throw new InvalidDataException("连接文件过大。");
        var data = JsonSerializer.Deserialize<AppData>(File.ReadAllText(path), JsonOptions)
            ?? throw new InvalidDataException("连接文件为空或损坏。");
        if (data.Version == 1) data = data with { Version = 2 };
        Validate(data);
        return data;
    }

    private static void Validate(AppData data)
    {
        if (data.Version != 2) throw new InvalidDataException("此连接文件来自不支持的版本，请升级工具。");
        if (data.Connections is null || data.TeraTermPath is null || data.Connections.Count > 10000)
            throw new InvalidDataException("连接文件结构无效。");
        if (data.Connections.Any(c => c is null || c.Name is null || c.Host is null || c.Username is null || c.Group is null || c.Notes is null || c.ProtectedPassword is null))
            throw new InvalidDataException("连接文件包含无效字段。");
        if (data.Connections.Select(c => c.Id).Distinct().Count() != data.Connections.Count)
            throw new InvalidDataException("连接文件包含重复 ID。");
        foreach (var connection in data.Connections) connection.Validate();
    }

    public void Save(AppData data)
    {
        Validate(data);
        var temporary = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
        try
        {
            using (var file = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None))
            {
                JsonSerializer.Serialize(file, data, JsonOptions);
                file.Flush(flushToDisk: true);
            }
            File.Move(temporary, path, overwrite: true);
        }
        finally { if (File.Exists(temporary)) File.Delete(temporary); }
    }

    public void Dispose() => lease.Dispose();
}
