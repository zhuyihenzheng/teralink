@echo off
rem Same as TeraLink.bat but keeps a console open, so start-up errors stay visible.
setlocal
cd /d "%~dp0"
if exist "%~dp0python\python.exe" (
    "%~dp0python\python.exe" -m teralink
    goto :done
)
where py >nul 2>nul
if not errorlevel 1 (
    py -3 -m teralink
    goto :done
)
python -m teralink
:done
echo.
echo Exit code: %errorlevel%
pause
