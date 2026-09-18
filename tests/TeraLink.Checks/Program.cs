using System.Text;
using System.Text.Json;
using TeraLink.Core;
using TeraLink.Windows;

int passed = 0;
void Check(string name, Action action) { action(); passed++; Console.WriteLine($"PASS {name}"); }
void Assert(bool value) { if (!value) throw new Exception("Assertion failed"); }
void Reject(Action action)
{
    bool rejected = false;
    try { action(); } catch (Exception e) when (e is ArgumentException or InvalidDataException or JsonException or IOException) { rejected = true; }
    Assert(rejected);
}
var connection = new Connection { Name = "开发服务器", Host = "dev.example.com", Username = "user", ProtectedPassword = Convert.ToBase64String([1, 2, 3]) };
Check("validate normal profile", () => connection.Validate());
Check("accept DNS / IPv4 / IPv6", () => { foreach (var h in new[] { "localhost", "a", "192.168.0.1", "::1", "2001:db8::1", "dev-box.example.com", "server.example.com." }) Connection.ValidateHost(h); });
Check("reject host switch injection and malformed domains", () =>
{
    foreach (var h in new[] { "/passwd=leak", "x /passwd=leak", "ssh://user@host", "x\" /m=evil", "host;command", "host\n/m=evil", "host:22", "-host", "host..example", "host.-bad", "abc\\file", "", " host" })
        Reject(() => Connection.ValidateHost(h));
});
Check("port boundaries", () => { foreach (int p in new[] { 0, -1, 65536 }) Reject(() => (connection with { Port = p }).Validate()); (connection with { Port = 65535 }).Validate(); });
Check("required fields and control characters", () =>
{
    Reject(() => (connection with { Name = " " }).Validate()); Reject(() => (connection with { Username = "bad\0name" }).Validate());
    Reject(() => (connection with { ProtectedPassword = "not-base64" }).Validate()); Reject(() => (connection with { ProtectedPassword = "" }).Validate());
});
Check("macro command quotes special credentials and keeps host verification", () =>
{
    string command = (connection with { Username = "domain\\user name" }).MacroConnectCommand("a \"b\"; /m=evil");
    Assert(command == "dev.example.com /P=22 /ssh /2 /auth=password /user=\"domain\\user name\" /passwd=\"a \"\"b\"\"; /m=evil\"");
    Assert(!command.Contains("/nosecuritywarning"));
});
Check("reject line framing attacks and overlong UTF-8 payloads", () =>
{
    foreach (var password in new[] { "", "line\nbreak", "line\rbreak", "null\0byte", "tab\there", new string('密', 200) })
        Reject(() => connection.MacroConnectCommand(password));
    int overhead = Encoding.UTF8.GetByteCount(connection.MacroConnectCommand("x")) - 1;
    Assert(Encoding.UTF8.GetByteCount(connection.MacroConnectCommand(new string('a', 511 - overhead))) == 511);
    Reject(() => connection.MacroConnectCommand(new string('a', 512 - overhead)));
});
var rdp = connection with { Kind = ConnectionKind.Rdp, Port = 3389, Username = @"DOMAIN\user" };
Check("RDP target, credentials, full screen and security settings", () =>
{
    string profile = RdpProfile.Create(rdp with { RdpFullScreen = true }, "AABB01");
    Assert(profile.Contains("full address:s:dev.example.com:3389\r\n"));
    Assert(profile.Contains(@"username:s:DOMAIN\user"));
    Assert(profile.Contains("screen mode id:i:2\r\n"));
    Assert(profile.Contains("enablecredsspsupport:i:1\r\n"));
    Assert(profile.Contains("authentication level:i:2\r\n"));
    Assert(profile.Contains("gatewayusagemethod:i:0\r\n"));
    Assert(profile.Contains("password 51:b:AABB01\r\n"));
    Assert(!profile.Contains(connection.ProtectedPassword));
});
Check("RDP IPv6 brackets and nonstandard port", () =>
{
    Assert(RdpProfile.Create(rdp with { Host = "2001:db8::1", Port = 3390 }, "00")
        .StartsWith("full address:s:[2001:db8::1]:3390\r\n"));
});
Check("RDP rejects injected fields, invalid ciphertext and wrong protocol", () =>
{
    Reject(() => RdpProfile.Create(rdp with { Username = "user\r\nauthentication level:i:0" }, "00"));
    Reject(() => RdpProfile.Create(rdp with { Host = "host\r\nusername:s:attacker" }, "00"));
    foreach (string blob in new[] { "", "0", "xyz", "00\r\nredirectclipboard:i:1" })
        Reject(() => RdpProfile.Create(rdp, blob));
    Reject(() => RdpProfile.Create(connection, "00"));
    Reject(() => rdp.MacroConnectCommand("test"));
    Reject(() => (connection with { Kind = (ConnectionKind)99 }).Validate());
});
Check("existing RDP preserves gateway, routing and display; original remains unchanged", () =>
{
    const string source = "full address:s:dev.example.com:3389\r\nusername:s:old\r\ndomain:s:OLD\r\npassword 51:b:CAFE\r\nprompt for credentials:i:1\r\ngatewayhostname:s:gateway.example.com\r\ngatewayusagemethod:i:1\r\npromptcredentialonce:i:0\r\nloadbalanceinfo:s:tsv://MS Terminal Services Plugin.1.pool\r\nscreen mode id:i:2\r\nsignature:s:oldsignature\r\nsignscope:s:Full Address\r\n";
    string updated = RdpProfile.Create(rdp, "ABCD", source);
    foreach (string line in new[] { "gatewayhostname:s:gateway.example.com", "gatewayusagemethod:i:1", "promptcredentialonce:i:0", "screen mode id:i:2", "loadbalanceinfo:s:tsv://MS Terminal Services Plugin.1.pool" })
        Assert(updated.Contains(line + "\r\n"));
    Assert(!updated.Contains("CAFE") && !updated.Contains("oldsignature") && !updated.Contains("domain:s:OLD"));
    Assert(updated.Contains(@"username:s:DOMAIN\user") && updated.Contains("password 51:b:ABCD"));
    Assert(source.Contains("username:s:old"));
});
Check("RDP import reads existing target and domain", () =>
{
    var target = RdpProfile.Inspect("full address:s:[::1]:3391\r\nusername:s:user\r\ndomain:s:DOMAIN\r\n");
    Assert(target.Host == "::1" && target.Port == 3391 && target.Username == @"DOMAIN\user");
    Assert(RdpProfile.Inspect("full address:s:localhost\n").Port == 3389);
    Assert(RdpProfile.Inspect("full address:s:2001:db8::1\n").Host == "2001:db8::1");
});
Check("RDP import rejects ambiguity and changed target", () =>
{
    foreach (var source in new[] { "username:s:user", "full address:s:host\nfull address:s:other", "full address:s:host:bad", "full address:s:host:0", "full address:s:[::1]bad", "full address:i:host", "full address:s:host\0" })
        Reject(() => RdpProfile.Inspect(source));
    Reject(() => RdpProfile.Create(rdp, "00", "full address:s:other.example.com"));
    Reject(() => (rdp with { RdpFilePath = "existing.rdp", RdpFileHash = "" }).Validate());
});
string directory = Path.Combine(Path.GetTempPath(), "teralink-checks-" + Guid.NewGuid());
try
{
    using (var store = new ConnectionStore(directory))
    {
        Check("first launch is empty", () => Assert(store.Load().Connections.Count == 0));
        Check("roundtrip Chinese, Unicode and favorites", () =>
        {
            store.Save(new AppData { TeraTermPath = @"C:\Program Files\teraterm5\ttermpro.exe", Connections = [connection with { Notes = "日本語 / 中文 🚀", Favorite = true }] });
            var restored = store.Load(); Assert(restored.Connections[0].Notes == "日本語 / 中文 🚀"); Assert(restored.Connections[0].Favorite);
        });
        Check("RDP and exit preference roundtrip", () =>
        {
            store.Save(new AppData { Connections = [rdp], ExitAfterLaunch = true });
            var restored = store.Load();
            Assert(restored.Connections[0].Kind == ConnectionKind.Rdp);
            Assert(restored.Connections[0].Port == 3389 && restored.ExitAfterLaunch);
        });
        Check("v1 SSH profiles migrate without changing credentials", () =>
        {
            string path = Path.Combine(directory, "connections.json");
            File.WriteAllText(path, JsonSerializer.Serialize(new { Version = 1, Connections = new[] {
                new { connection.Id, connection.Name, connection.Host, connection.Port, connection.Username, connection.ProtectedPassword }
            }}));
            var restored = store.Load();
            Assert(restored.Version == 2 && restored.Connections[0].Kind == ConnectionKind.Ssh);
            Assert(restored.Connections[0].ProtectedPassword == connection.ProtectedPassword);
            store.Save(restored);
            Assert(File.ReadAllText(path).Contains("\"Version\": 2"));
        });
        Check("reject duplicates without replacing saved file", () =>
        {
            Reject(() => store.Save(new AppData { Connections = [connection, connection] })); Assert(store.Load().Connections.Count == 1);
        });
        Check("update and deletion persist", () =>
        {
            store.Save(new AppData { Connections = [connection with { Name = "新名称", LastLaunched = DateTimeOffset.UtcNow }] });
            Assert(store.Load().Connections[0].Name == "新名称");
            store.Save(new AppData()); Assert(store.Load().Connections.Count == 0);
        });
        Check("single writer", () => Reject(() => { using var other = new ConnectionStore(directory); }));
        Check("no temporary files after save", () => Assert(Directory.GetFiles(directory, "*.tmp").Length == 0));
        Check("corrupt JSON is never reset", () =>
        {
            string path = Path.Combine(directory, "connections.json"); File.WriteAllText(path, "{broken");
            Reject(() => store.Load()); Assert(File.ReadAllText(path) == "{broken");
        });
        Check("reject unknown schema and null fields", () =>
        {
            string path = Path.Combine(directory, "connections.json");
            foreach (var json in new[] { "{\"Version\":99}", "{\"Connections\":null}", "{\"Connections\":[null]}" })
            { File.WriteAllText(path, json); Reject(() => store.Load()); }
        });
    }
    Check("lease released on close", () => { using var reopened = new ConnectionStore(directory); });
}
finally { Directory.Delete(directory, recursive: true); }

if (OperatingSystem.IsWindows())
{
    Check("DPAPI special-character password roundtrip", () =>
    {
        string password = "测试 パス word \"'\\;!@#$%^&*()\r\n🔑";
        string encrypted = PasswordVault.Protect(password);
        Assert(PasswordVault.Unprotect(encrypted) == password);
        Assert(!Encoding.UTF8.GetString(Convert.FromBase64String(encrypted)).Contains(password));
        Assert(PasswordVault.Protect(password) != encrypted);
    });
    Check("RDP password is DPAPI protected UTF-16LE", () =>
    {
        const string password = "测试 Japanese パス word \" \\ 🔑";
        var encrypted = Convert.FromHexString(PasswordVault.ToRdpPassword(PasswordVault.Protect(password)));
        // Unprotect returns UTF-8 text; direct byte comparison uses the OS DPAPI API below.
        byte[] raw = (byte[])typeof(PasswordVault).GetMethod("Transform", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Static)!.Invoke(null, [encrypted, false])!;
        Assert(Encoding.Unicode.GetString(raw) == password);
        System.Security.Cryptography.CryptographicOperations.ZeroMemory(raw);
    });
    Check("DPAPI rejects tampered ciphertext", () =>
    {
        var blob = Convert.FromBase64String(PasswordVault.Protect("test-only-password")); blob[^1] ^= 0xff;
        bool rejected = false; try { PasswordVault.Unprotect(Convert.ToBase64String(blob)); } catch (System.ComponentModel.Win32Exception) { rejected = true; }
        Assert(rejected);
    });
}
else Console.WriteLine("SKIP DPAPI runtime checks (requires Windows)");
Console.WriteLine($"{passed} checks passed.");
