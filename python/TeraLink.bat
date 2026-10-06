@echo off
rem TeraLink (Python edition) launcher. Double-click to start; no console window stays open.
setlocal
cd /d "%~dp0"
call :find_python || goto :no_python
"%PYEXE%" %PYARG% -c "import sys, tkinter; sys.exit(0 if sys.version_info >= (3, 8) else 3)" >nul 2>nul
set "RC=%errorlevel%"
if "%RC%"=="3" goto :old_python
if not "%RC%"=="0" goto :no_tk
start "" "%PYWEXE%" %PYARG% -m teralink
exit /b 0

:find_python
rem 1) portable Python copied next to this file  2) py launcher  3) python on PATH
if exist "%~dp0python\pythonw.exe" (
    set "PYEXE=%~dp0python\python.exe"
    set "PYWEXE=%~dp0python\pythonw.exe"
    set "PYARG="
    exit /b 0
)
where py >nul 2>nul && where pyw >nul 2>nul && (
    set "PYEXE=py"
    set "PYWEXE=pyw"
    set "PYARG=-3"
    exit /b 0
)
where python >nul 2>nul && where pythonw >nul 2>nul && (
    set "PYEXE=python"
    set "PYWEXE=pythonw"
    set "PYARG="
    exit /b 0
)
exit /b 1

:no_python
echo [TeraLink] Python 3.8 or newer was not found.
echo Install Python for the current user (no admin needed), check "tcl/tk and IDLE",
echo or copy a full Python folder here as .\python\  -- see README.md.
pause
exit /b 1

:old_python
echo [TeraLink] Python 3.8 or newer is required. Found:
"%PYEXE%" %PYARG% --version
pause
exit /b 1

:no_tk
echo [TeraLink] This Python cannot load tkinter (Tcl/Tk), or "python" is only the Microsoft Store shortcut.
echo Re-run the Python installer, choose Modify, and check "tcl/tk and IDLE".
"%PYEXE%" %PYARG% -c "import tkinter"
pause
exit /b 1
