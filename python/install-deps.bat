@echo off
rem Installs paramiko (SSH/SFTP for upload and server commands) for the current user only.
rem Offline: put the .whl files in .\wheels\ first (see download-wheels.bat).
setlocal
cd /d "%~dp0"
if exist "%~dp0python\python.exe" (
    set "PY="%~dp0python\python.exe""
) else (
    where py >nul 2>nul && (set "PY=py -3") || (set "PY=python")
)
if exist "%~dp0wheels\*.whl" (
    echo Installing from .\wheels\ ^(offline^)...
    %PY% -m pip install --user --no-index --find-links "%~dp0wheels" -r requirements.txt
) else (
    echo Installing from PyPI. If your company needs a proxy, first run:
    echo     set HTTPS_PROXY=http://proxy.example.com:8080
    %PY% -m pip install --user -r requirements.txt
)
if errorlevel 1 (
    echo.
    echo [TeraLink] Install failed. Connections still work; uploads need paramiko or Windows OpenSSH with SSH keys.
) else (
    %PY% -c "import paramiko; print('paramiko', paramiko.__version__, 'OK')"
)
pause
