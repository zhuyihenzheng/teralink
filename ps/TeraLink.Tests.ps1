# Checks for the platform-independent logic. Run on any machine:
#   pwsh -NoProfile -File ps/TeraLink.Tests.ps1
# DPAPI, named pipes and Tera Term itself are Windows-only and are NOT covered here.

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'TeraLink.Core.ps1')

$script:Passed = 0
$script:Failed = 0

function Test-Case {
    param([string]$Name, [scriptblock]$Body)
    try { & $Body; $script:Passed++; Write-Host "  ok   $Name" }
    catch { $script:Failed++; Write-Host "  FAIL $Name : $($_.Exception.Message)" }
}

function Assert-True { param([bool]$Condition, [string]$Message = 'assertion failed') if (-not $Condition) { throw $Message } }

function Assert-Equal {
    param($Expected, $Actual)
    if ("$Expected" -ne "$Actual") { throw "expected [$Expected] got [$Actual]" }
}

function Assert-Throws {
    param([scriptblock]$Body, [string]$Message = 'expected a throw')
    try { & $Body } catch { return }
    throw $Message
}

Write-Host 'host name'
Test-Case 'accepts a host name' { Assert-Equal 'example.com' (Test-TlHostName 'example.com') }
Test-Case 'accepts IPv4' { Assert-Equal '192.0.2.10' (Test-TlHostName '192.0.2.10') }
Test-Case 'accepts IPv6' { Assert-Equal '2001:db8::1' (Test-TlHostName '2001:db8::1') }
Test-Case 'rejects empty' { Assert-Throws { Test-TlHostName '' } }
Test-Case 'rejects a space' { Assert-Throws { Test-TlHostName 'a b' } }
Test-Case 'rejects a switch' { Assert-Throws { Test-TlHostName 'host /ssh' } }
Test-Case 'rejects userinfo' { Assert-Throws { Test-TlHostName 'user@host' } }
Test-Case 'rejects a quote' { Assert-Throws { Test-TlHostName 'host"x' } }
Test-Case 'rejects a backslash' { Assert-Throws { Test-TlHostName 'host\x' } }
Test-Case 'rejects a leading dash label' { Assert-Throws { Test-TlHostName '-bad.example' } }
Test-Case 'rejects an over-long name' { Assert-Throws { Test-TlHostName ('a' * 254) } }

Write-Host 'connect command'
Test-Case 'builds the documented shape' {
    Assert-Equal 'h.example /P=22 /ssh /2 /auth=password /user="me" /passwd="pw"' `
        (New-TlConnectCommand -HostName 'h.example' -Port 22 -UserName 'me' -Password 'pw')
}
Test-Case 'keeps a non-default port' {
    Assert-True (New-TlConnectCommand -HostName 'h.example' -Port 2222 -UserName 'me' -Password 'pw').Contains('/P=2222')
}
Test-Case 'doubles a quote in the user name so it cannot inject a switch' {
    Assert-Equal 'h.example /P=22 /ssh /2 /auth=password /user="a"" /passwd=""x" /passwd="pw"' `
        (New-TlConnectCommand -HostName 'h.example' -Port 22 -UserName 'a" /passwd="x' -Password 'pw')
}
Test-Case 'doubles a quote in the password' {
    Assert-True (New-TlConnectCommand -HostName 'h.example' -Port 22 -UserName 'me' -Password 'a"b').Contains('/passwd="a""b"')
}
Test-Case 'rejects a control character in the password' {
    Assert-Throws { New-TlConnectCommand -HostName 'h.example' -Port 22 -UserName 'me' -Password "a`nb" }
}
Test-Case 'rejects an empty password' {
    Assert-Throws { New-TlConnectCommand -HostName 'h.example' -Port 22 -UserName 'me' -Password '' }
}
Test-Case 'rejects port 0 and 65536' {
    Assert-Throws { New-TlConnectCommand -HostName 'h.example' -Port 0 -UserName 'me' -Password 'pw' }
    Assert-Throws { New-TlConnectCommand -HostName 'h.example' -Port 65536 -UserName 'me' -Password 'pw' }
}
Test-Case 'accepts exactly 511 UTF-8 bytes' {
    $fixed = 'h.example /P=22 /ssh /2 /auth=password /user="me" /passwd=""'
    $password = 'p' * (511 - [System.Text.Encoding]::UTF8.GetByteCount($fixed))
    $command = New-TlConnectCommand -HostName 'h.example' -Port 22 -UserName 'me' -Password $password
    Assert-Equal 511 ([System.Text.Encoding]::UTF8.GetByteCount($command))
}
Test-Case 'rejects 512 UTF-8 bytes' {
    $fixed = 'h.example /P=22 /ssh /2 /auth=password /user="me" /passwd=""'
    $password = 'p' * (512 - [System.Text.Encoding]::UTF8.GetByteCount($fixed))
    Assert-Throws { New-TlConnectCommand -HostName 'h.example' -Port 22 -UserName 'me' -Password $password }
}
Test-Case 'counts multi-byte characters as UTF-8 bytes' {
    $password = '密' * 200
    Assert-Throws { New-TlConnectCommand -HostName 'h.example' -Port 22 -UserName 'me' -Password $password }
}

Write-Host 'post-login commands'
Test-Case 'accepts a plain command' { Assert-Equal 'ls -la' (Test-TlCommandLine 'ls -la') }
Test-Case 'rejects a newline in a command' { Assert-Throws { Test-TlCommandLine "a`nb" } }
Test-Case 'rejects a carriage return in a command' { Assert-Throws { Test-TlCommandLine "a`rb" } }
Test-Case 'rejects an over-long command' { Assert-Throws { Test-TlCommandLine ('x' * 1025) } }
Test-Case 'puts the prompt on line 1 and one command per line' {
    $text = New-TlCommandFileText -Prompt '$' -Commands @('cd /var/log', 'ls -la')
    Assert-Equal "`$`r`ncd /var/log`r`nls -la`r`n" $text
}
Test-Case 'drops blank commands' {
    $text = New-TlCommandFileText -Prompt '#' -Commands @('a', '', '   ', 'b')
    Assert-Equal "#`r`na`r`nb`r`n" $text
}
Test-Case 'allows an empty prompt with no commands' {
    Assert-Equal "`r`n" (New-TlCommandFileText -Prompt '' -Commands @())
}

Write-Host 'macro'
$pipe = 'TeraLink-' + ('a' * 32)
Test-Case 'embeds the pipe and both paths' {
    $macro = New-TlMacro -PipeName $pipe -ReportPath 'C:\t\result.txt' -CommandPath 'C:\t\commands.txt' -WaitSeconds 30
    Assert-True $macro.Contains("fileopen channel '\\.\pipe\$pipe' 0 1")
    Assert-True $macro.Contains('fileopen report "C:\t\result.txt" 0')
    Assert-True $macro.Contains('fileopen cmdfile "C:\t\commands.txt" 0 1')
    Assert-True $macro.Contains('timeout = 30')
}
Test-Case 'carries no password and links before reading the pipe' {
    $macro = New-TlMacro -PipeName $pipe -ReportPath 'C:\t\r.txt' -CommandPath 'C:\t\c.txt' -WaitSeconds 5
    Assert-True (-not $macro.Contains('/passwd='))
    Assert-True ($macro.IndexOf('testlink') -lt $macro.IndexOf('fileopen channel'))
    Assert-True ($macro.IndexOf('fileopen channel') -lt $macro.IndexOf('connect command'))
}
Test-Case 'clears the command variable on both paths' {
    $macro = New-TlMacro -PipeName $pipe -ReportPath 'C:\t\r.txt' -CommandPath 'C:\t\c.txt' -WaitSeconds 5
    Assert-True (([regex]::Matches($macro, "command = ''")).Count -ge 2)
}
Test-Case 'rejects a forged pipe name' {
    Assert-Throws { New-TlMacro -PipeName 'evil' -ReportPath 'C:\t\r.txt' -CommandPath 'C:\t\c.txt' -WaitSeconds 5 }
}
Test-Case 'rejects a quote in a path' {
    Assert-Throws { New-TlMacro -PipeName $pipe -ReportPath 'C:\t\"r.txt' -CommandPath 'C:\t\c.txt' -WaitSeconds 5 }
    Assert-Throws { New-TlMacro -PipeName $pipe -ReportPath 'C:\t\r.txt' -CommandPath 'C:\t\"c.txt' -WaitSeconds 5 }
}
Test-Case 'rejects an out-of-range wait' {
    Assert-Throws { New-TlMacro -PipeName $pipe -ReportPath 'C:\t\r.txt' -CommandPath 'C:\t\c.txt' -WaitSeconds 0 }
    Assert-Throws { New-TlMacro -PipeName $pipe -ReportPath 'C:\t\r.txt' -CommandPath 'C:\t\c.txt' -WaitSeconds 601 }
}

Write-Host ''
Write-Host "$script:Passed 项通过，$script:Failed 项失败。"
if ($script:Failed -gt 0) { exit 1 }
