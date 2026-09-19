#Requires -Version 5.1
<#
    TeraLink — Tera Term SSH 快捷登录（无 EXE 版本）

    没有编译产物，所以不会被 SmartScreen 拦截，也不需要安装 .NET 运行时。
    密码用 DPAPI 绑定当前 Windows 账户加密，通过仅本账户可读的命名管道交给本次宏进程，
    并核对接收端 PID；密码不写入启动参数、宏文件、命令文件、日志或剪贴板。
#>
param(
    [string]$Name,
    [switch]$List,
    [switch]$Add,
    [string]$Edit,
    [string]$Remove,
    [switch]$Import,
    [string]$SetTeraTermPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'TeraLink.Core.ps1')
Add-Type -AssemblyName System.Security
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$script:DataDirectory = Join-Path $env:LOCALAPPDATA 'TeraLink'
$script:StorePath = Join-Path $script:DataDirectory 'ssh.json'
$script:LegacyStorePath = Join-Path $script:DataDirectory 'connections.json'
$script:StoreVersion = 3

Add-Type -Namespace TeraLink -Name Native -UsingNamespace @('System.Runtime.InteropServices') -MemberDefinition @'
[DllImport("kernel32.dll", SetLastError = true)]
[return: MarshalAs(UnmanagedType.Bool)]
public static extern bool GetNamedPipeClientProcessId(IntPtr pipe, out uint clientProcessId);
'@

# ---------- 密码 ----------
# DPAPI 当前用户范围，与旧版 C# 的密文格式相同，因此旧数据可直接导入。

function Protect-TlPassword {
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Password)
    if ($Password.Length -lt 1 -or $Password.Length -gt 1024 -or $Password.Contains([char]0)) {
        throw '密码不能为空，不能包含空字符，最多 1024 字。'
    }
    $raw = [System.Text.Encoding]::UTF8.GetBytes($Password)
    try { return [Convert]::ToBase64String([System.Security.Cryptography.ProtectedData]::Protect($raw, $null, 'CurrentUser')) }
    finally { [Array]::Clear($raw, 0, $raw.Length) }
}

function Unprotect-TlPassword {
    param([Parameter(Mandatory)][string]$Ciphertext)
    $raw = $null
    try {
        $raw = [System.Security.Cryptography.ProtectedData]::Unprotect([Convert]::FromBase64String($Ciphertext), $null, 'CurrentUser')
        return [System.Text.Encoding]::UTF8.GetString($raw)
    } catch [System.Security.Cryptography.CryptographicException] {
        throw '无法解密密码，请使用保存时的 Windows 账户，或用 -Edit 重新输入密码。'
    } finally { if ($null -ne $raw) { [Array]::Clear($raw, 0, $raw.Length) } }
}

function Read-TlPassword {
    param([Parameter(Mandatory)][string]$Prompt)
    $secure = Read-Host -Prompt $Prompt -AsSecureString
    $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToGlobalAllocUnicode($secure)
    try { return [System.Runtime.InteropServices.Marshal]::PtrToStringUni($bstr) }
    finally {
        [System.Runtime.InteropServices.Marshal]::ZeroFreeGlobalAllocUnicode($bstr)
        $secure.Dispose()
    }
}

# ---------- 存储 ----------

function Get-TlField {
    param($Object, [string]$FieldName, $Default)
    if ($null -eq $Object) { return $Default }
    $property = $Object.PSObject.Properties[$FieldName]
    if ($null -eq $property -or $null -eq $property.Value) { return $Default }
    return $property.Value
}

function New-TlConnection {
    param($Source)
    $connection = [pscustomobject][ordered]@{
        Id                = [string](Get-TlField $Source 'Id' ([Guid]::NewGuid().ToString()))
        Name              = [string](Get-TlField $Source 'Name' '')
        HostName          = [string](Get-TlField $Source 'HostName' (Get-TlField $Source 'Host' ''))
        Port              = [int](Get-TlField $Source 'Port' 22)
        UserName          = [string](Get-TlField $Source 'UserName' (Get-TlField $Source 'Username' ''))
        Prompt            = [string](Get-TlField $Source 'Prompt' '')
        WaitSeconds       = [int](Get-TlField $Source 'WaitSeconds' 30)
        Commands          = [string[]]@(Get-TlField $Source 'Commands' @())
        ProtectedPassword = [string](Get-TlField $Source 'ProtectedPassword' '')
    }
    return $connection
}

function Test-TlConnection {
    param($Connection)
    if ([string]::IsNullOrWhiteSpace($Connection.Name) -or $Connection.Name.Length -gt 100) {
        throw '连接名称必填，最多 100 字。'
    }
    [void](Test-TlHostName $Connection.HostName)
    if ($Connection.Port -lt 1 -or $Connection.Port -gt 65535) { throw '端口必须在 1–65535 之间。' }
    if ([string]::IsNullOrWhiteSpace($Connection.UserName) -or $Connection.UserName.Length -gt 255) {
        throw '请输入有效的用户名（最多 255 字）。'
    }
    [void](Test-TlCommandLine $Connection.Prompt)
    if ($Connection.WaitSeconds -lt 1 -or $Connection.WaitSeconds -gt 600) { throw '等待提示符的秒数必须在 1–600 之间。' }
    if ($Connection.Commands.Count -gt 100) { throw '一个连接最多 100 条命令。' }
    foreach ($command in $Connection.Commands) { [void](Test-TlCommandLine $command) }
    if ([string]::IsNullOrEmpty($Connection.ProtectedPassword)) { throw '请保存登录密码。' }
    [void][Convert]::FromBase64String($Connection.ProtectedPassword)
    return $Connection
}

function Read-TlStore {
    if (-not (Test-Path -LiteralPath $script:StorePath)) {
        return [pscustomobject]@{ Version = $script:StoreVersion; TeraTermPath = ''; Connections = @() }
    }
    if ((Get-Item -LiteralPath $script:StorePath).Length -gt 16MB) { throw '连接文件过大。' }
    $raw = Get-Content -LiteralPath $script:StorePath -Raw -Encoding UTF8
    $data = $raw | ConvertFrom-Json
    $version = [int](Get-TlField $data 'Version' 0)
    if ($version -ne $script:StoreVersion) { throw "连接文件版本 $version 不受支持，请升级工具。" }
    $connections = @()
    foreach ($item in @(Get-TlField $data 'Connections' @())) { $connections += (Test-TlConnection (New-TlConnection $item)) }
    $ids = @($connections | ForEach-Object { $_.Id })
    if (@($ids | Select-Object -Unique).Count -ne $connections.Count) { throw '连接文件包含重复 ID。' }
    return [pscustomobject]@{
        Version      = $script:StoreVersion
        TeraTermPath = [string](Get-TlField $data 'TeraTermPath' '')
        Connections  = $connections
    }
}

function Write-TlStore {
    param($Data)
    foreach ($connection in $Data.Connections) { [void](Test-TlConnection $connection) }
    [void](New-Item -ItemType Directory -Force -Path $script:DataDirectory)
    $temporary = "$script:StorePath.$([Guid]::NewGuid().ToString('N')).tmp"
    try {
        # Keep a one-element list serialized as a JSON array.
        $Data.Connections = [object[]]@($Data.Connections)
        $json = $Data | ConvertTo-Json -Depth 6
        [System.IO.File]::WriteAllText($temporary, $json, (New-Object System.Text.UTF8Encoding $false))
        if (Test-Path -LiteralPath $script:StorePath) {
            [System.IO.File]::Replace($temporary, $script:StorePath, $null)
        } else {
            [System.IO.File]::Move($temporary, $script:StorePath)
        }
    } finally { if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force } }
}

function Import-TlLegacyStore {
    # Reads the v2 file written by the old C# launcher. SSH only; the DPAPI ciphertext
    # carries over unchanged. The old file is never modified.
    if (-not (Test-Path -LiteralPath $script:LegacyStorePath)) { throw "没有找到旧版数据：$script:LegacyStorePath" }
    $legacy = (Get-Content -LiteralPath $script:LegacyStorePath -Raw -Encoding UTF8) | ConvertFrom-Json
    $data = Read-TlStore
    $existing = @($data.Connections | ForEach-Object { $_.Id })
    $imported = 0
    $skipped = 0
    foreach ($item in @(Get-TlField $legacy 'Connections' @())) {
        $kind = Get-TlField $item 'Kind' 0
        if ("$kind" -ne '0' -and "$kind" -ne 'Ssh') { $skipped++; continue }
        $connection = New-TlConnection $item
        if ($existing -contains $connection.Id) { continue }
        $data.Connections = @($data.Connections) + @(Test-TlConnection $connection)
        $imported++
    }
    Write-TlStore $data
    Write-Host "已导入 $imported 条 SSH 连接，跳过 $skipped 条 RDP 连接。旧文件未改动。"
}

# ---------- Tera Term ----------

function Find-TlTeraTerm {
    $roots = @($env:ProgramFiles, ${env:ProgramFiles(x86)}) | Where-Object { $_ }
    foreach ($root in $roots) {
        foreach ($folder in @('teraterm5', 'teraterm', 'Tera Term')) {
            $candidate = Join-Path (Join-Path $root $folder) 'ttermpro.exe'
            if (Test-Path -LiteralPath $candidate) { return $candidate }
        }
    }
    return ''
}

function Test-TlTeraTerm {
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path) -or -not [System.IO.Path]::IsPathRooted($Path) -or -not (Test-Path -LiteralPath $Path -PathType Leaf) `
        -or [System.IO.Path]::GetFileName($Path) -ne 'ttermpro.exe') {
        throw '请指定已安装的 ttermpro.exe（-SetTeraTermPath <完整路径>）。'
    }
    if ($Path.StartsWith('\\')) { throw '请选择本机磁盘上的 Tera Term 程序。' }
    $version = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($Path)
    if (-not "$($version.ProductName)".ToLowerInvariant().Contains('tera term') -or $version.FileMajorPart -lt 5) {
        throw '需要官方 Tera Term 5.x，请选择对应的 ttermpro.exe。'
    }
    $macro = Join-Path ([System.IO.Path]::GetDirectoryName($Path)) 'ttpmacro.exe'
    if (-not (Test-Path -LiteralPath $macro)) { throw '同一目录下缺少 ttpmacro.exe，请完整安装或解压 Tera Term。' }
    return $Path
}

function Resolve-TlTeraTerm {
    param($Data)
    $path = $Data.TeraTermPath
    if ([string]::IsNullOrWhiteSpace($path)) { $path = Find-TlTeraTerm }
    if ([string]::IsNullOrWhiteSpace($path)) {
        throw '没有找到 Tera Term。请用 -SetTeraTermPath <ttermpro.exe 的完整路径> 指定。'
    }
    return (Test-TlTeraTerm $path)
}

# ---------- 连接 ----------

function Invoke-TlConnect {
    param($Connection, [Parameter(Mandatory)][string]$Executable)

    [void](Test-TlConnection $Connection)
    $command = New-TlConnectCommand -HostName $Connection.HostName -Port $Connection.Port `
        -UserName $Connection.UserName -Password (Unprotect-TlPassword $Connection.ProtectedPassword)
    # .NET strings cannot promise immediate erasure; the byte payload below is what we wipe.
    $payload = [System.Text.Encoding]::UTF8.GetBytes($command + "`r`n")
    $command = ''

    $session = Join-Path (Join-Path $script:DataDirectory 'sessions') ([Guid]::NewGuid().ToString('N'))
    $pipeName = 'TeraLink-' + [Guid]::NewGuid().ToString('N')
    $scriptPath = Join-Path $session 'connect.ttl'
    $reportPath = Join-Path $session 'result.txt'
    $commandPath = Join-Path $session 'commands.txt'
    $utf8 = New-Object System.Text.UTF8Encoding $false
    $macro = $null
    $pipe = $null
    $timeoutMs = 120000

    try {
        [void](New-Item -ItemType Directory -Force -Path $session)
        [System.IO.File]::WriteAllText($scriptPath, (New-TlMacro -PipeName $pipeName -ReportPath $reportPath `
            -CommandPath $commandPath -WaitSeconds $Connection.WaitSeconds), $utf8)
        [System.IO.File]::WriteAllText($commandPath, (New-TlCommandFileText -Prompt $Connection.Prompt `
            -Commands $Connection.Commands), $utf8)

        # Windows account ACL plus peer PID verification: only our helper receives the payload.
        $security = New-Object System.IO.Pipes.PipeSecurity
        $me = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
        $security.SetOwner($me)
        $security.AddAccessRule((New-Object System.IO.Pipes.PipeAccessRule($me,
            [System.IO.Pipes.PipeAccessRights]::FullControl, [System.Security.AccessControl.AccessControlType]::Allow)))
        $pipe = New-Object System.IO.Pipes.NamedPipeServerStream($pipeName, [System.IO.Pipes.PipeDirection]::Out, 1,
            [System.IO.Pipes.PipeTransmissionMode]::Byte, [System.IO.Pipes.PipeOptions]::Asynchronous, 0, 0, $security)

        $teraTermDirectory = [System.IO.Path]::GetDirectoryName($Executable)
        $startInfo = New-Object System.Diagnostics.ProcessStartInfo
        $startInfo.FileName = Join-Path $teraTermDirectory 'ttpmacro.exe'
        # Windows paths cannot contain a double quote, so quoting the path is enough.
        $startInfo.Arguments = '/V "' + $scriptPath + '"'
        $startInfo.UseShellExecute = $false
        $startInfo.WorkingDirectory = $teraTermDirectory
        $macro = [System.Diagnostics.Process]::Start($startInfo)
        if ($null -eq $macro) { throw '未能启动 Tera Term 宏。' }

        # Wait on the pipe and on the helper at once, so a macro that dies early fails fast
        # instead of holding the full timeout.
        $connecting = $pipe.WaitForConnectionAsync()
        $macroExited = New-Object System.Threading.ManualResetEvent $false
        $macroExited.SafeWaitHandle = New-Object Microsoft.Win32.SafeHandles.SafeWaitHandle($macro.Handle, $false)
        $started = [DateTime]::UtcNow
        $signalled = [System.Threading.WaitHandle]::WaitAny(@($connecting.AsyncWaitHandle, $macroExited), $timeoutMs)
        if ($signalled -eq [System.Threading.WaitHandle]::WaitTimeout) {
            throw '等待 Tera Term 超时。请检查网络及主机指纹提示；已打开的终端可继续手动使用。'
        }
        if ($signalled -eq 1 -and -not $connecting.IsCompleted) {
            throw 'Tera Term 宏在接收连接信息前退出，请检查安装是否完整。'
        }
        $connecting.GetAwaiter().GetResult()

        [uint32]$client = 0
        if (-not [TeraLink.Native]::GetNamedPipeClientProcessId($pipe.SafePipeHandle.DangerousGetHandle(), [ref]$client)) {
            throw '无法确认接收端进程，已停止传递密码。'
        }
        if ($client -ne [uint32]$macro.Id) { throw '接收端不是本次启动的 Tera Term 宏，已停止传递密码。' }

        $pipe.Write($payload, 0, $payload.Length)
        $pipe.Flush()
        [Array]::Clear($payload, 0, $payload.Length)

        $remaining = $timeoutMs - [int]([DateTime]::UtcNow - $started).TotalMilliseconds
        if ($remaining -lt 1 -or -not $macro.WaitForExit($remaining)) {
            throw '等待 Tera Term 超时。请检查网络及主机指纹提示；已打开的终端可继续手动使用。'
        }

        $state = ''
        if (Test-Path -LiteralPath $reportPath) { $state = (Get-Content -LiteralPath $reportPath -Raw -Encoding UTF8).Trim() }
        switch ($state) {
            'connected' { Write-Host '已连接，命令已提交。' }
            'commands-timeout' {
                throw "已登录，但在 $($Connection.WaitSeconds) 秒内没有等到提示符「$($Connection.Prompt)」，后续命令未全部发送。终端仍可手动使用。"
            }
            default {
                throw 'Tera Term 未完成自动连接。请在终端查看网络、认证或主机指纹提示；错误密码不会被自动重复提交。'
            }
        }
    } finally {
        [Array]::Clear($payload, 0, $payload.Length)
        if ($null -ne $pipe) { $pipe.Dispose() }
        # Stop only our helper; leave the user's terminal/session open.
        if ($null -ne $macro) {
            try { if (-not $macro.HasExited) { $macro.Kill() } } catch { }
            $macro.Dispose()
        }
        # Residue contains only paths, commands and status, never credentials.
        try { if (Test-Path -LiteralPath $session) { Remove-Item -LiteralPath $session -Recurse -Force } } catch { }
    }
}

# ---------- 命令行 ----------

function Show-TlList {
    param($Data)
    if ($Data.Connections.Count -eq 0) { Write-Host '还没有连接，用 -Add 新增，或用 -Import 从旧版数据导入。'; return }
    $index = 1
    foreach ($connection in $Data.Connections) {
        $commands = ''
        if ($connection.Commands.Count -gt 0) { $commands = "  [$($connection.Commands.Count) 条命令]" }
        Write-Host ("{0,3}. {1,-24} {2}@{3}:{4}{5}" -f $index, $connection.Name, $connection.UserName,
            $connection.HostName, $connection.Port, $commands)
        $index++
    }
}

function Read-TlConnectionInput {
    param($Existing)
    $connection = New-TlConnection $Existing
    $suffix = ''
    if ($connection.Name) { $suffix = "（回车保留：$($connection.Name)）" }
    $value = Read-Host "名称$suffix"
    if ($value) { $connection.Name = $value }

    $suffix = ''
    if ($connection.HostName) { $suffix = "（回车保留：$($connection.HostName)）" }
    $value = Read-Host "主机$suffix"
    if ($value) { $connection.HostName = $value }

    $value = Read-Host "端口（回车保留：$($connection.Port)）"
    if ($value) { $connection.Port = [int]$value }

    $suffix = ''
    if ($connection.UserName) { $suffix = "（回车保留：$($connection.UserName)）" }
    $value = Read-Host "用户名$suffix"
    if ($value) { $connection.UserName = $value }

    Write-Host '登录后等待的提示符，例如 $ 或 # ；留空表示不等待、也不发送命令。'
    $value = Read-Host "提示符（回车保留：$($connection.Prompt)）"
    if ($value) { $connection.Prompt = $value }

    $value = Read-Host "等待提示符的秒数（回车保留：$($connection.WaitSeconds)）"
    if ($value) { $connection.WaitSeconds = [int]$value }

    Write-Host "登录后自动执行的命令，一行一条，空行结束。当前 $($connection.Commands.Count) 条，直接空行表示保留。"
    Write-Host '注意：命令以明文保存，不要把密码写进去。'
    $commands = @()
    while ($true) {
        $value = Read-Host '命令'
        if (-not $value) { break }
        $commands += (Test-TlCommandLine $value)
    }
    if ($commands.Count -gt 0) { $connection.Commands = [string[]]$commands }

    $prompt = '密码'
    if ($connection.ProtectedPassword) { $prompt = '密码（回车保留原密码）' }
    $password = Read-TlPassword $prompt
    if ($password) { $connection.ProtectedPassword = Protect-TlPassword $password }
    $password = ''

    return (Test-TlConnection $connection)
}

function Select-TlConnection {
    param($Data, [string]$Wanted)
    if ($Data.Connections.Count -eq 0) { throw '还没有连接，用 -Add 新增，或用 -Import 从旧版数据导入。' }
    if ($Wanted) {
        $matched = @($Data.Connections | Where-Object { $_.Name -eq $Wanted })
        if ($matched.Count -eq 1) { return $matched[0] }
        $matched = @($Data.Connections | Where-Object { $_.Name -like "*$Wanted*" })
        if ($matched.Count -eq 1) { return $matched[0] }
        if ($matched.Count -eq 0) { throw "没有找到名称包含「$Wanted」的连接。" }
        throw "名称「$Wanted」匹配到多个连接，请写得更具体。"
    }
    Show-TlList $Data
    $choice = Read-Host '选择编号（回车退出）'
    if (-not $choice) { return $null }
    $number = 0
    if (-not [int]::TryParse($choice, [ref]$number) -or $number -lt 1 -or $number -gt $Data.Connections.Count) {
        throw '编号无效。'
    }
    return $Data.Connections[$number - 1]
}

try {
    if ($SetTeraTermPath) {
        $data = Read-TlStore
        $data.TeraTermPath = (Test-TlTeraTerm $SetTeraTermPath)
        Write-TlStore $data
        Write-Host "已保存 Tera Term 路径：$($data.TeraTermPath)"
        return
    }
    if ($Import) { Import-TlLegacyStore; return }
    if ($List) { Show-TlList (Read-TlStore); return }
    if ($Add) {
        $data = Read-TlStore
        $data.Connections = @($data.Connections) + @(Read-TlConnectionInput $null)
        Write-TlStore $data
        Write-Host '已保存。'
        return
    }
    if ($Edit) {
        $data = Read-TlStore
        $target = Select-TlConnection $data $Edit
        $updated = Read-TlConnectionInput $target
        $data.Connections = @($data.Connections | ForEach-Object { if ($_.Id -eq $updated.Id) { $updated } else { $_ } })
        Write-TlStore $data
        Write-Host '已保存。'
        return
    }
    if ($Remove) {
        $data = Read-TlStore
        $target = Select-TlConnection $data $Remove
        if ((Read-Host "删除「$($target.Name)」？输入 y 确认") -ne 'y') { Write-Host '已取消。'; return }
        $data.Connections = @($data.Connections | Where-Object { $_.Id -ne $target.Id })
        Write-TlStore $data
        Write-Host '已删除。'
        return
    }

    $data = Read-TlStore
    $target = Select-TlConnection $data $Name
    if ($null -eq $target) { return }
    $executable = Resolve-TlTeraTerm $data
    Write-Host "正在连接 $($target.Name) ..."
    Invoke-TlConnect -Connection $target -Executable $executable
} catch {
    Write-Host ''
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
