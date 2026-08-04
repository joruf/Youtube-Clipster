@echo off
setlocal enabledelayedexpansion
title Loresoft YouTube Clipster

REM ---------------------------------------------------------------------
REM Loresoft YouTube Clipster - Windows starter.
REM
REM Starts the program through pythonw.exe, so there is no console window.
REM Everything the setup does is shown in a window instead (see
REM clipster\setup_ui.py): the first start downloads yt-dlp and FFmpeg, which
REM takes a few minutes, and must never look like a program that is broken.
REM As soon as every dependency is in place, YouTube Clipster starts itself.
REM
REM The console stays visible only while it is still needed: to find or install
REM Python, when tkinter is missing, and whenever options are passed on the
REM command line (their output belongs in the console).
REM
REM Usage:  run.bat                 start (silent, progress in a window)
REM         run.bat --check         only check dependencies, in the console
REM         run.bat --help          all options
REM
REM Author:  Joachim Ruf, Loresoft.de
REM License: GPLv3
REM ---------------------------------------------------------------------

cd /d "%~dp0"
set "ENTRY=%~dp0run.py"
set "PYTHON="
set "PYTHONW="

if not exist "%ENTRY%" (
    echo [ERROR] run.py was not found in %~dp0
    pause
    exit /b 1
)

REM Mirrors clipster.paths.install_dir plus venv_python.
set "CLIPSTER_HOME=%LOCALAPPDATA%\YoutubeClipster"
if defined YOUTUBE_CLIPSTER_HOME set "CLIPSTER_HOME=%YOUTUBE_CLIPSTER_HOME%"
set "VENV_PYTHONW=%CLIPSTER_HOME%\venv\Scripts\pythonw.exe"

REM Fast path: the environment already exists, so start it directly and return.
if "%~1"=="" (
    if exist "%VENV_PYTHONW%" (
        start "" "%VENV_PYTHONW%" "%ENTRY%"
        exit /b 0
    )
)

call :find_python
if not defined PYTHON call :install_python
if not defined PYTHON call :find_python

if not defined PYTHON (
    echo.
    echo [ERROR] Python 3.8 or newer is required but was not found.
    echo         Install it from https://www.python.org/downloads/ and make sure
    echo         "Add python.exe to PATH" is ticked, then start run.bat again.
    echo.
    pause
    exit /b 1
)

echo [INFO]  Using: %PYTHON%

REM Hand over to the windowless interpreter - but only when a window can
REM actually be drawn, and only without command line options.
if "%~1"=="" (
    %PYTHON% -c "import tkinter" >nul 2>&1
    if !errorlevel! equ 0 call :find_pythonw
)

if defined PYTHONW (
    echo [INFO]  Opening the setup window...
    start "" "%PYTHONW%" "%ENTRY%"
    exit /b 0
)

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

REM Locate pythonw.exe next to the interpreter found above.
:find_pythonw
for /f "delims=" %%P in ('%PYTHON% -c "import os, sys; print(os.path.join(os.path.dirname(sys.executable), 'pythonw.exe'))" 2^>nul') do set "PYTHONW=%%P"
if defined PYTHONW if not exist "!PYTHONW!" set "PYTHONW="
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
