# Pure logic, shared by the launcher and the checks. Nothing here touches DPAPI, pipes,
# processes or the registry, so the checks run on any platform; everything Windows-only
# lives in TeraLink.ps1.

Set-StrictMode -Version Latest

function ConvertTo-TlQuoted {
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Value)
    return '"' + $Value.Replace('"', '""') + '"'
}

function Test-TlHostName {
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value) -or $Value.Length -gt 253 -or $Value -ne $Value.Trim()) {
        throw '请输入主机名或 IP 地址，不要包含协议、端口或空格。'
    }
    # Never allow Tera Term switches, URLs, userinfo or command-line quoting in a host.
    foreach ($c in $Value.ToCharArray()) {
        if ([char]::IsWhiteSpace($c) -or [char]::IsControl($c) -or '/\"'';@'.Contains($c)) {
            throw '主机只填写主机名或 IP 地址，端口请单独填写。'
        }
    }
    $parsed = $null
    if ([System.Net.IPAddress]::TryParse($Value, [ref]$parsed)) { return $Value }
    if ($Value -notmatch '^(?=.{1,253}$)[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?\.?$') {
        throw '主机名格式无效；国际域名请填写 punycode。'
    }
    foreach ($label in $Value.TrimEnd('.').Split('.')) {
        if ($label.Length -lt 1 -or $label.Length -gt 63 -or $label.StartsWith('-') -or $label.EndsWith('-')) {
            throw '主机名格式无效。'
        }
    }
    return $Value
}

function Test-TlCommandLine {
    # Post-login commands are sent verbatim with sendln; one line each, no control characters.
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Value)
    if ($Value.Length -gt 1024) { throw '单条命令最多 1024 字。' }
    foreach ($c in $Value.ToCharArray()) {
        if ([char]::IsControl($c)) { throw '命令不能包含换行或控制字符，请每行写一条。' }
    }
    return $Value
}

# For the already-linked macro only. NEVER use as process arguments or write to disk.
function New-TlConnectCommand {
    param(
        [Parameter(Mandatory)][AllowEmptyString()][string]$HostName,
        [Parameter(Mandatory)][int]$Port,
        [Parameter(Mandatory)][AllowEmptyString()][string]$UserName,
        [Parameter(Mandatory)][AllowEmptyString()][string]$Password
    )
    [void](Test-TlHostName $HostName)
    if ($Port -lt 1 -or $Port -gt 65535) { throw '端口必须在 1–65535 之间。' }
    if ([string]::IsNullOrWhiteSpace($UserName) -or [string]::IsNullOrEmpty($Password)) {
        throw '用户名和密码不能为空。'
    }
    foreach ($c in ($UserName + $Password).ToCharArray()) {
        if ([char]::IsControl($c)) { throw '用户名和密码不能包含换行或控制字符。' }
    }
    $command = "$HostName /P=$Port /ssh /2 /auth=password /user=$(ConvertTo-TlQuoted $UserName) /passwd=$(ConvertTo-TlQuoted $Password)"
    if ([System.Text.Encoding]::UTF8.GetByteCount($command) -gt 511) {
        throw '连接信息过长：Tera Term 宏的连接参数最多 511 个 UTF-8 字节，请缩短主机名、用户名或密码。'
    }
    return $command
}

# The command file holds the prompt on line 1 and one command per line after it. Commands
# are not secrets, so the macro reads them from disk; the password never goes in here.
function New-TlCommandFileText {
    param(
        [Parameter(Mandatory)][AllowEmptyString()][string]$Prompt,
        [Parameter(Mandatory)][AllowEmptyCollection()][AllowEmptyString()][string[]]$Commands
    )
    [void](Test-TlCommandLine $Prompt)
    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add($Prompt)
    foreach ($command in $Commands) {
        if ([string]::IsNullOrWhiteSpace($command)) { continue }
        $lines.Add((Test-TlCommandLine $command))
    }
    return [string]::Join("`r`n", $lines) + "`r`n"
}

function New-TlMacro {
    param(
        [Parameter(Mandatory)][string]$PipeName,
        [Parameter(Mandatory)][string]$ReportPath,
        [Parameter(Mandatory)][string]$CommandPath,
        [Parameter(Mandatory)][int]$WaitSeconds
    )
    # Paths cannot contain double quotes on Windows. TTL does not interpret backslashes as escapes.
    foreach ($path in @($ReportPath, $CommandPath)) {
        if ($path.Contains('"')) { throw '宏路径无效。' }
        foreach ($c in $path.ToCharArray()) { if ([char]::IsControl($c)) { throw '宏路径无效。' } }
    }
    if ($PipeName -notmatch '^TeraLink-[a-f0-9]{32}$') { throw '宏路径无效。' }
    if ($WaitSeconds -lt 1 -or $WaitSeconds -gt 600) { throw '等待提示符的秒数必须在 1–600 之间。' }
    return @"
; TeraLink transport. This file contains NO password or connection credentials.
; Attach before receiving secrets: connect will use DDE, not process arguments.
connect '/DS'
testlink
if result <> 1 goto failed
fileopen channel '\\.\pipe\$PipeName' 0 1
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
; Logged in. Post-login commands are not secrets and come from a plain file on disk.
state = 'connected'
fileopen cmdfile "$CommandPath" 0 1
if cmdfile = -1 goto writestatus
filereadln cmdfile prompt
if result <> 0 goto closecmd
:nextcmd
filereadln cmdfile line
if result <> 0 goto closecmd
strlen line
if result = 0 goto nextcmd
strlen prompt
if result = 0 goto sendcmd
timeout = $WaitSeconds
wait prompt
if result = 0 goto cmdtimeout
:sendcmd
sendln line
goto nextcmd
:cmdtimeout
state = 'commands-timeout'
:closecmd
fileclose cmdfile
goto writestatus
:failed
state = 'failed'
:writestatus
command = ''
fileopen report "$ReportPath" 0
if report = -1 end
filewriteln report state
fileclose report
end
"@
}
