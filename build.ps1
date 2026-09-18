param(
    [ValidateSet('win-x64', 'win-arm64')][string]$Runtime = 'win-x64',
    [switch]$SelfContained,
    [string]$CertificateThumbprint,
    [string]$TimestampUrl = 'http://timestamp.digicert.com'
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
    if ($CertificateThumbprint) {
        # Authenticode is the only thing that removes the SmartScreen prompt for every user;
        # unblocking a download only clears the mark of the web on one machine.
        $signtool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" -ErrorAction SilentlyContinue |
            Sort-Object FullName | Select-Object -Last 1
        if (-not $signtool) { throw 'signtool.exe not found. Install the Windows SDK signing tools.' }
        # Microsoft ships the runtime files already signed; sign only what we build.
        $sign = Get-ChildItem $target -Recurse -Include 'TeraLink.exe', 'TeraLink.dll', 'TeraLink.*.dll' |
            ForEach-Object FullName
        & $signtool.FullName sign /fd SHA256 /td SHA256 /tr $TimestampUrl /sha1 $CertificateThumbprint $sign
        if ($LASTEXITCODE -ne 0) { throw 'Signing failed.' }
        & $signtool.FullName verify /pa /all $sign
        if ($LASTEXITCODE -ne 0) { throw 'Signature verification failed.' }
    } else {
        Write-Warning 'Unsigned build: Windows SmartScreen will warn on download. Pass -CertificateThumbprint to sign.'
    }
    Copy-Item README.md, QA.md, WINDOWS-ACCEPTANCE.md, THIRD-PARTY-NOTICES.md -Destination $target
    Copy-Item licenses -Destination $target -Recurse -Force
    $zip = Join-Path $PSScriptRoot "artifacts/$package.zip"
    Compress-Archive -Path $target -DestinationPath $zip -Force
    Remove-Item $staging -Recurse -Force
    # Publish this alongside the release so users can check the download before unblocking it.
    $hash = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLowerInvariant()
    Set-Content -Path "$zip.sha256" -Value "$hash  $package.zip" -Encoding ascii
    Write-Host "SHA256: $hash"
    Write-Host "Ready: $zip"
} finally { Pop-Location }
