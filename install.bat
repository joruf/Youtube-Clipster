@echo off
setlocal enabledelayedexpansion
title Loresoft YouTube Clipster

REM ---------------------------------------------------------------------
REM Loresoft YouTube Clipster - Windows starter.
REM
REM Finds a suitable Python 3, offers to install it via winget if missing,
REM and hands over to youtube-clipster.py, which installs every remaining
REM dependency (yt-dlp, FFmpeg) itself.
REM
REM Usage:  install.bat [--check] [--help] [any other option]
REM
REM Author:  Joachim Ruf, Loresoft.de
REM License: GPLv3
REM ---------------------------------------------------------------------

cd /d "%~dp0"
set "ENTRY=%~dp0youtube-clipster.py"
set "PYTHON="

if not exist "%ENTRY%" (
    echo [ERROR] youtube-clipster.py was not found in %~dp0
    pause
    exit /b 1
)

call :find_python
if not defined PYTHON call :install_python
if not defined PYTHON call :find_python

if not defined PYTHON (
    echo.
    echo [ERROR] Python 3.8 or newer is required but was not found.
    echo         Install it from https://www.python.org/downloads/ and make sure
    echo         "Add python.exe to PATH" is ticked, then start install.bat again.
    echo.
    pause
    exit /b 1
)

echo [INFO]  Using: %PYTHON%
echo.
%PYTHON% "%ENTRY%" %*
set "RET=%errorlevel%"

if not "%RET%"=="0" (
    echo.
    echo [ERROR] YouTube Clipster exited with code %RET%.
    pause
)
exit /b %RET%


REM ---------------------------------------------------------------------
REM Look for a usable interpreter: the py launcher, PATH, default locations.
REM ---------------------------------------------------------------------
:find_python
call :try_python py -3
if defined PYTHON goto :eof
call :try_python python
if defined PYTHON goto :eof
call :try_python python3
if defined PYTHON goto :eof
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
    if exist "%%~D\python.exe" (
        call :try_python "%%~D\python.exe"
        if defined PYTHON goto :eof
    )
)
for /d %%D in ("%ProgramFiles%\Python3*") do (
    if exist "%%~D\python.exe" (
        call :try_python "%%~D\python.exe"
        if defined PYTHON goto :eof
    )
)
goto :eof

REM Check one candidate command; sets PYTHON when it is new enough.
:try_python
set "CANDIDATE=%*"
%CANDIDATE% -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3, 8) else 1)" >nul 2>&1
if !errorlevel! equ 0 set "PYTHON=%CANDIDATE%"
goto :eof

REM Offer an automatic Python installation through winget.
:install_python
echo [WARN]  No Python 3.8 or newer was found.
echo.
where winget >nul 2>&1
if not !errorlevel! equ 0 (
    echo [INFO]  winget is not available - opening the Python download page.
    echo [INFO]  Important: tick "Add python.exe to PATH" during the installation.
    start "" "https://www.python.org/downloads/"
    goto :eof
)
choice /c YN /n /m "Install Python now via winget? [Y/N] "
if not !errorlevel! equ 1 goto :eof
echo [INFO]  Installing Python, this can take a few minutes...
winget install --exact --id Python.Python.3.12 --source winget --accept-package-agreements --accept-source-agreements
echo [INFO]  Installation finished.
goto :eof
