@echo off
rem Run on a PC WITH internet and the SAME Python version (e.g. 3.12, 64-bit) as the company PC.
rem Then copy the whole folder, including .\wheels\, and run install-deps.bat there.
setlocal
cd /d "%~dp0"
where py >nul 2>nul && (set "PY=py -3") || (set "PY=python")
%PY% -m pip download -r requirements.txt -d wheels --only-binary=:all:
pause
