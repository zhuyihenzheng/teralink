using System.Diagnostics;
using System.IO.Pipes;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using Microsoft.Win32.SafeHandles;
using TeraLink.Core;

namespace TeraLink.Windows;

internal static class TeraTermLauncher
{
    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetNamedPipeClientProcessId(SafePipeHandle pipe, out uint clientProcessId);

    public static string? FindExecutable()
    {
        var roots = new[] { Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86) };
        return roots.SelectMany(root => new[] { Path.Combine(root, "teraterm5", "ttermpro.exe"), Path.Combine(root, "teraterm", "ttermpro.exe"), Path.Combine(root, "Tera Term", "ttermpro.exe") })
            .FirstOrDefault(File.Exists);
    }

    public static void ValidateExecutable(string path)
    {
        if (!Path.IsPathFullyQualified(path) || !File.Exists(path) || !Path.GetFileName(path).Equals("ttermpro.exe", StringComparison.OrdinalIgnoreCase))
            throw new ArgumentException("请在「Tera Term 路径」选择已安装的 ttermpro.exe。");
        if (path.StartsWith(@"\\")) throw new ArgumentException("请选择本机磁盘上的 Tera Term 程序。");
        var version = FileVersionInfo.GetVersionInfo(path);
        if (!(version.ProductName ?? "").Contains("Tera Term", StringComparison.OrdinalIgnoreCase) || version.FileMajorPart < 5)
            throw new ArgumentException("第一版需要官方 Tera Term 5.x，请选择对应的 ttermpro.exe。");
        if (!File.Exists(Path.Combine(Path.GetDirectoryName(path)!, "ttpmacro.exe")))
            throw new ArgumentException("同一目录下缺少 ttpmacro.exe，请完整安装或解压 Tera Term。");
    }

    public static async Task LaunchAsync(string executable, Connection connection, CancellationToken cancellation)
    {
        ValidateExecutable(executable);
        connection.Validate();
        string password = PasswordVault.Unprotect(connection.ProtectedPassword);
        byte[] payload;
        try { payload = Encoding.UTF8.GetBytes(connection.MacroConnectCommand(password) + "\r\n"); }
        finally { password = ""; } // Immutable .NET strings cannot promise immediate memory erasure.
        string session = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "TeraLink", "sessions", Guid.NewGuid().ToString("N"));
        Process? macro = null;
        using var deadline = CancellationTokenSource.CreateLinkedTokenSource(cancellation);
        deadline.CancelAfter(TimeSpan.FromSeconds(120));
        try
        {
            cancellation.ThrowIfCancellationRequested();
            Directory.CreateDirectory(session);
            string pipeName = "TeraLink-" + Guid.NewGuid().ToString("N");
            string script = Path.Combine(session, "connect.ttl");
            string report = Path.Combine(session, "result.txt");
            await File.WriteAllTextAsync(script, CreateMacro(pipeName, report), new UTF8Encoding(false), deadline.Token);
            // Windows account ACL plus peer PID verification: only our helper receives the payload.
            using var pipe = new NamedPipeServerStream(pipeName, PipeDirection.Out, 1, PipeTransmissionMode.Byte,
                PipeOptions.Asynchronous | PipeOptions.CurrentUserOnly);
            var info = new ProcessStartInfo(Path.Combine(Path.GetDirectoryName(executable)!, "ttpmacro.exe"))
                { UseShellExecute = false, WorkingDirectory = Path.GetDirectoryName(executable)! };
            info.ArgumentList.Add("/V"); info.ArgumentList.Add(script);
            macro = Process.Start(info) ?? throw new IOException("未能启动 Tera Term 宏。");
            var connected = pipe.WaitForConnectionAsync(deadline.Token);
            var exited = macro.WaitForExitAsync(deadline.Token);
            var first = await Task.WhenAny(connected, exited);
            deadline.Token.ThrowIfCancellationRequested();
            if (first == exited && !connected.IsCompletedSuccessfully)
                throw new IOException("Tera Term 宏在接收连接信息前退出，请检查安装是否完整。");
            await connected;
            if (!GetNamedPipeClientProcessId(pipe.SafePipeHandle, out uint client) || client != (uint)macro.Id)
                throw new IOException("接收端不是本次启动的 Tera Term 宏，已停止传递密码。");
            await pipe.WriteAsync(payload, deadline.Token);
            await pipe.FlushAsync(deadline.Token);
            CryptographicOperations.ZeroMemory(payload);
            await exited;
            string result = File.Exists(report) ? (await File.ReadAllTextAsync(report, deadline.Token)).Trim() : "";
            if (result != "connected")
                throw new IOException("Tera Term 未完成自动连接。请在终端查看网络、认证或主机指纹提示；错误密码不会被自动重复提交。");
        }
        catch (OperationCanceledException) when (!cancellation.IsCancellationRequested)
        {
            throw new TimeoutException("等待 Tera Term 超时。请检查网络及主机指纹提示；已打开的终端可继续手动使用。");
        }
        finally
        {
            CryptographicOperations.ZeroMemory(payload);
            // Stop only our helper; leave the user's terminal/session open.
            if (macro is not null)
            {
                try { if (!macro.HasExited) macro.Kill(entireProcessTree: false); }
                catch (InvalidOperationException) { }
                catch (System.ComponentModel.Win32Exception) { }
                macro.Dispose();
            }
            try { if (Directory.Exists(session)) Directory.Delete(session, recursive: true); }
            catch (IOException) { } // Residue contains only paths and status, never credentials.
            catch (UnauthorizedAccessException) { }
        }
    }

    internal static string CreateMacro(string pipeName, string reportPath)
    {
        // Paths cannot contain double quotes on Windows. TTL does not interpret backslashes as escapes.
        if (reportPath.Contains('"') || reportPath.Any(char.IsControl) || !System.Text.RegularExpressions.Regex.IsMatch(pipeName, @"^TeraLink-[a-f0-9]{32}$"))
            throw new ArgumentException("宏路径无效。");
        return $$"""
            ; TeraLink transport. This file contains NO password or connection credentials.
            ; Attach before receiving secrets: connect will use DDE, not process arguments.
            connect '/DS'
            testlink
            if result <> 1 goto failed
            fileopen channel '\\.\pipe\{{pipeName}}' 0 1
            if channel = -1 goto failed
            filereadln channel command
            received = result
            fileclose channel
            if received <> 0 goto failed
            strlen command
            if result = 0 goto failed
            ; Recheck after the pipe wait: never launch a fresh process with the secret.
            testlink
            if result <> 1 goto failed
            connect command
            command = ''
            if result <> 2 goto failed
            fileopen report "{{reportPath}}" 0
            if report = -1 end
            filewriteln report 'connected'
            fileclose report
            end
            :failed
            command = ''
            fileopen report "{{reportPath}}" 0
            if report = -1 end
            filewriteln report 'failed'
            fileclose report
            end
            """;
    }
}
