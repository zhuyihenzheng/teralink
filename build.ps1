param(
    [ValidateSet('win-x64', 'win-arm64')][string]$Runtime = 'win-x64',
    [switch]$SelfContained
)
$ErrorActionPreference = 'Stop'
$env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'
$env:DOTNET_NOLOGO = '1'
Push-Location $PSScriptRoot
try {
    dotnet run --project tests/TeraLink.Checks -c Release
    if ($LASTEXITCODE -ne 0) { throw 'Checks failed.' }
    $package = if ($SelfContained) { "TeraLink-$Runtime" } else { "TeraLink-$Runtime-lite" }
    # A fresh staging folder prevents a lite build from retaining old bundled runtime files.
    $staging = Join-Path $PSScriptRoot ("artifacts/staging-" + [Guid]::NewGuid().ToString('N'))
    $target = Join-Path $staging $package
    $selfContainedValue = if ($SelfContained) { 'true' } else { 'false' }
    dotnet publish src/TeraLink.Windows -c Release -r $Runtime --self-contained $selfContainedValue -p:DebugType=None -o $target
    if ($LASTEXITCODE -ne 0) { throw 'Publish failed.' }
    Copy-Item README.md, QA.md, WINDOWS-ACCEPTANCE.md, THIRD-PARTY-NOTICES.md -Destination $target
    Copy-Item licenses -Destination $target -Recurse -Force
    $zip = Join-Path $PSScriptRoot "artifacts/$package.zip"
    Compress-Archive -Path $target -DestinationPath $zip -Force
    Remove-Item $staging -Recurse -Force
    Get-FileHash $zip -Algorithm SHA256
    Write-Host "Ready: $zip"
} finally { Pop-Location }
