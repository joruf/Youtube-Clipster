@echo off
setlocal enabledelayedexpansion

:: Loresoft YouTube Clipster - Windows Edition
:: Original: Joachim Ruf, Loresoft.de

set "DOWNLOAD_DIR=%USERPROFILE%\Downloads"
set "INSTALL_DIR=%LOCALAPPDATA%\YoutubeClipster"
set "YTDLP_EXE=%INSTALL_DIR%\yt-dlp.exe"
set "FFMPEG_DIR=%INSTALL_DIR%\ffmpeg"
set "USER_AGENT=Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
set "LAST_CLIP="
set "CANCELED_CLIP="

if not exist "%DOWNLOAD_DIR%" mkdir "%DOWNLOAD_DIR%"
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"

echo YouTube Clipster v1.0
echo Author: Joachim Ruf, Loresoft.de
echo License: GPLv3 - The author's name must be credited upon publication and modification.
echo ========================================
echo.

call :check_ytdlp
call :check_ffmpeg
:: call :check_autostart



echo [INFO] Initialization complete
echo.

:: Ignore initial clipboard content
echo [DEBUG] Reading initial clipboard to ignore old content...
for /f "usebackq delims=" %%a in (`powershell -Command "Get-Clipboard 2>$null | Select-Object -First 1"`) do set "LAST_CLIP=%%a"
if defined LAST_CLIP (
    echo [DEBUG] Initial clipboard ignored: !LAST_CLIP!
) else (
    echo [DEBUG] Clipboard is empty
)

echo.
echo [INFO] Ready! Monitoring clipboard...
echo [INFO] Copy a YouTube URL to start downloading
echo ========================================
echo.

:loop
timeout /t 2 /nobreak >nul

for /f "usebackq delims=" %%a in (`powershell -Command "Get-Clipboard 2>$null | Select-Object -First 1"`) do set "CLIP=%%a"

if defined CLIP (
    if not "!CLIP!"=="!LAST_CLIP!" (
        if not "!CLIP!"=="!CANCELED_CLIP!" (            
            set "T=%TEMP%\ytcheck.txt"
            > "!T!" echo !CLIP!
            
            findstr /i "youtube.com youtu.be" "!T!" >nul
            if !errorlevel! equ 0 (
                echo [DEBUG] YouTube URL detected
                findstr /bi "http" "!T!" >nul
                if !errorlevel! equ 0 (
                    echo [DEBUG] Valid HTTP/HTTPS URL confirmed
                    call :download "!CLIP!"
                ) else (
                    echo [DEBUG] Not a valid HTTP URL, ignoring
                )
            )
            
            del "!T!" 2>nul
        )
    )
)

goto loop

:download
set "URL=%~1"
echo [DEBUG] Full URL: !URL!

:: Get title
echo [DEBUG] Fetching video title...
echo [DEBUG] Command: "%YTDLP_EXE%" --no-playlist --skip-download --no-warnings --get-title "!URL!"

:: Use temp batch file to avoid & parsing issues in URL
set "TEMP_BAT=%TEMP%\ytdlp_title.bat"
(
echo @echo off
echo "%YTDLP_EXE%" --no-playlist --skip-download --no-warnings --get-title "!URL!" 2^>nul
) > "%TEMP_BAT%"

for /f "usebackq delims=" %%t in (`"%TEMP_BAT%"`) do set "TITLE=%%t"
del "%TEMP_BAT%" 2>nul

if not defined TITLE (
    echo [WARNING] Could not fetch title
    set "TITLE=Unknown Title"
) else (
    echo [DEBUG] Title fetched successfully: !TITLE!
)


:: Format dialog
echo [DEBUG] Creating format selection dialog...
set "PS=%TEMP%\fmt.ps1"

:: Escape special characters in title for PowerShell
set "TITLE_CLEAN=!TITLE!"
set "TITLE_CLEAN=!TITLE_CLEAN:'=''!"

(
echo Add-Type -AssemblyName System.Windows.Forms
echo $f=New-Object System.Windows.Forms.Form
echo $f.Text="YouTube Clipster - Download"
echo $f.Width=400
echo $f.Height=240
echo $f.StartPosition="CenterScreen"
echo $f.TopMost=$true
echo $f.FormBorderStyle="FixedDialog"
echo $f.MaximizeBox=$false
echo.
echo # Title label
echo $lTitle=New-Object System.Windows.Forms.Label
echo $lTitle.Text='!TITLE_CLEAN!'
echo $lTitle.Location="10,10"
echo $lTitle.Size="360,40"
echo $lTitle.Font=New-Object System.Drawing.Font^("Segoe UI",9,[System.Drawing.FontStyle]::Bold^)
echo $f.Controls.Add^($lTitle^)
echo.
echo # Format label
echo $l=New-Object System.Windows.Forms.Label
echo $l.Text="Select download format:"
echo $l.Location="20,60"
echo $l.AutoSize=$true
echo $f.Controls.Add^($l^)
echo.
echo # MP3 radio button
echo $r1=New-Object System.Windows.Forms.RadioButton
echo $r1.Text="MP3 - Audio only"
echo $r1.Location="30,90"
echo $r1.Width=300
echo $r1.Checked=$true
echo $f.Controls.Add^($r1^)
echo.
echo # MP4 radio button
echo $r2=New-Object System.Windows.Forms.RadioButton
echo $r2.Text="MP4 - Video + Audio"
echo $r2.Location="30,120"
echo $r2.Width=300
echo $f.Controls.Add^($r2^)
echo.
echo # Download button
echo $b=New-Object System.Windows.Forms.Button
echo $b.Text="Download"
echo $b.Location="150,160"
echo $b.Width=100
echo $b.Height=35
echo $b.DialogResult="OK"
echo $f.Controls.Add^($b^)
echo $f.AcceptButton=$b
echo.
echo # Show dialog and return result
echo $result=$f.ShowDialog^(^)
echo if^($result -eq "OK"^){
echo     if^($r1.Checked^){"mp3"}else{"mp4"}
echo }elseif^($result -eq "Cancel"^){
echo     Write-Output "CANCELED"
echo }
) > "%PS%"

echo [DEBUG] Showing dialog...
for /f "usebackq delims=" %%f in (`powershell -EP Bypass -NoProfile -File "%PS%" 2^>nul`) do set "FMT=%%f"

echo [DEBUG] Dialog result: [!FMT!]
del "%PS%" 2>nul

:: Check if dialog was canceled
if not defined FMT (
    echo [INFO] No format selected - dialog was closed
    echo [DEBUG] Marking URL as canceled
    set "CANCELED_CLIP=!URL!"
    echo [DEBUG] Returning to monitoring
    echo.
    goto :eof
)

if /i "!FMT!"=="CANCELED" (
    echo [INFO] Download canceled by user
    echo [DEBUG] Marking URL as canceled
    set "CANCELED_CLIP=!URL!"
    echo [DEBUG] Returning to monitoring
    echo.
    set "LAST_CLIP="
    goto :eof
)

echo [DEBUG] User selected format: !FMT!

:: Change to download directory
cd /d "%DOWNLOAD_DIR%"
echo [DEBUG] Working directory: %CD%

echo.
echo [INFO] Starting download
echo ========================================
echo [INFO] Title: !TITLE!
echo [INFO] Format: !FMT!
echo [INFO] Destination: %DOWNLOAD_DIR%
echo ========================================
echo.

:: Show full yt-dlp and FFmpeg output (no suppression)
if /i "!FMT!"=="mp3" (
    echo [DEBUG] Executing MP3 download...
    echo [DEBUG] Command: "%YTDLP_EXE%" --user-agent "!USER_AGENT!" --no-playlist -x --audio-format mp3 --ffmpeg-location "%FFMPEG_DIR%\bin" -o "%%(title)s.%%(ext)s" "!URL!"
    echo.
    echo [OUTPUT] yt-dlp ^& FFmpeg output:
    echo ========================================
    "%YTDLP_EXE%" --user-agent "!USER_AGENT!" --no-playlist -x --audio-format mp3 --ffmpeg-location "%FFMPEG_DIR%\bin" -o "%%(title)s.%%(ext)s" "!URL!"
    echo ========================================
) else (
    echo [DEBUG] Executing MP4 download...
    echo [DEBUG] Command: "%YTDLP_EXE%" --user-agent "!USER_AGENT!" --no-playlist -f bestvideo+bestaudio --merge-output-format mp4 --ffmpeg-location "%FFMPEG_DIR%\bin" -o "%%(title)s.%%(ext)s" "!URL!"
    echo.
    echo [OUTPUT] yt-dlp ^& FFmpeg output:
    echo ========================================
    "%YTDLP_EXE%" --user-agent "!USER_AGENT!" --no-playlist -f bestvideo+bestaudio --merge-output-format mp4 --ffmpeg-location "%FFMPEG_DIR%\bin" -o "%%(title)s.%%(ext)s" "!URL!"
    echo ========================================
)

set "RET=!errorlevel!"
echo.
echo [DEBUG] Process exit code: !RET!

echo.
if !RET! equ 0 (
    echo [SUCCESS] Download completed successfully!
    echo ========================================
    echo [INFO] Title: !TITLE!
    echo [INFO] Format: !FMT!
    echo [INFO] Location: %DOWNLOAD_DIR%
    echo ========================================
    echo.
    echo [DEBUG] Setting LAST_CLIP to prevent re-download
    set "LAST_CLIP=!URL!"
    echo [DEBUG] Resetting CANCELED_CLIP to allow new URLs
    set "CANCELED_CLIP="
    echo [DEBUG] Opening download folder...
    start "" "%DOWNLOAD_DIR%"
) else (
    echo [FAILED] Download failed!
    echo ========================================
    echo [ERROR] Title: !TITLE!
    echo [ERROR] Format: !FMT!
    echo [ERROR] Exit code: !RET!
    echo ========================================
    echo.
    echo [TROUBLESHOOTING]
    echo Possible reasons:
    echo - Video unavailable, private, or deleted
    echo - Age-restricted content
    echo - Geographic restrictions
    echo - Network connection issues
    echo.
    echo [TIP] To force update yt-dlp.exe:
    echo       del "%YTDLP_EXE%"
    echo       Then restart this script
    echo.
    echo [DEBUG] NOT setting LAST_CLIP (failed download can be retried)
)
echo.
echo [DEBUG] Returning to clipboard monitoring...
echo.

goto :eof


:check_ytdlp
echo [INFO] Check Installation
echo ========================================
echo [1/2] Checking yt-dlp.exe...

if exist "%YTDLP_EXE%" (
    echo [DEBUG] yt-dlp.exe found at: %YTDLP_EXE%
    
    :: Check file age
    powershell -Command "$file=Get-Item '%YTDLP_EXE%';$age=(Get-Date)-$file.LastWriteTime;if($age.Days -gt 7){'UPDATE'}else{'OK'}" > "%TEMP%\ytdlp_check.txt"
    set /p YTDLP_STATUS=<"%TEMP%\ytdlp_check.txt"
    del "%TEMP%\ytdlp_check.txt" 2>nul
    
    if "!YTDLP_STATUS!"=="UPDATE" (
        echo [DEBUG] yt-dlp.exe is older than 7 days
        echo [UPDATE] Updating to latest version...
        del "%YTDLP_EXE%" 2>nul
        goto :download_ytdlp
    ) else (
        echo [OK] yt-dlp.exe is up-to-date
        goto :eof
    )
)


:download_ytdlp
echo [DOWNLOAD] Downloading latest yt-dlp.exe from GitHub...
echo [DEBUG] URL: https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe
echo [DEBUG] Destination: %YTDLP_EXE%
powershell -Command "$ProgressPreference='SilentlyContinue';Invoke-WebRequest -Uri 'https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe' -OutFile '%YTDLP_EXE%'"
if !errorlevel! neq 0 (
    echo [ERROR] Failed to download yt-dlp.exe
    echo [INFO] Please download manually from: https://github.com/yt-dlp/yt-dlp/releases
    pause
    exit /b 1
)
echo [OK] yt-dlp.exe downloaded successfully
:: Show version
for /f "usebackq delims=" %%v in (`"%YTDLP_EXE%" --version 2^>nul`) do echo [INFO] Version: %%v
goto :eof


:check_ffmpeg
echo [2/2] Checking FFmpeg...
if exist "%FFMPEG_DIR%\bin\ffmpeg.exe" (
    echo [DEBUG] FFmpeg found at: %FFMPEG_DIR%\bin\ffmpeg.exe
    :: Show version - fixed quotes
    echo [OK] FFmpeg found
    goto :eof
)

echo [DOWNLOAD] Downloading FFmpeg essentials...
echo [DEBUG] URL: https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip
echo [DEBUG] This may take a moment (60+ MB download)...
powershell -Command "$ProgressPreference='SilentlyContinue';Write-Host '[DEBUG] Starting download...';Invoke-WebRequest -Uri 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' -OutFile $env:TEMP\ffmpeg.zip;Write-Host '[DEBUG] Extracting...';Expand-Archive $env:TEMP\ffmpeg.zip $env:TEMP\ffmpeg_tmp -Force;Write-Host '[DEBUG] Done'"

if !errorlevel! neq 0 (
    echo [ERROR] Failed to download FFmpeg
    pause
    exit /b 1
)

echo [DEBUG] Moving files to installation directory...
for /d %%a in ("%TEMP%\ffmpeg_tmp\ffmpeg-*") do xcopy "%%a" "%FFMPEG_DIR%\" /E /I /Y >nul 2>&1

echo [DEBUG] Cleaning up temporary files...
del "%TEMP%\ffmpeg.zip" 2>nul
rd /s /q "%TEMP%\ffmpeg_tmp" 2>nul

if not exist "%FFMPEG_DIR%\bin\ffmpeg.exe" (
    echo [ERROR] FFmpeg installation failed
    echo [DEBUG] Expected location: %FFMPEG_DIR%\bin\ffmpeg.exe
    exit /b 1
)

echo [OK] FFmpeg installed successfully
for /f "usebackq tokens=3" %%v in (`"%FFMPEG_DIR%\bin\ffmpeg.exe" -version 2^>nul ^| findstr /i "ffmpeg version"`) do echo [INFO] Version: %%v
goto :eof


:check_autostart
echo [AUTOSTART] Checking Registry for YouTube Clipster...
set "REG_KEY=HKCU\Software\Microsoft\Windows\CurrentVersion\Run"

:: Check if the entry already exists
reg query "%REG_KEY%" /v "YouTubeClipster" >nul 2>&1
if %errorlevel% equ 0 (
    echo [AUTOSTART] Registry entry already exists.
    goto :eof
)

echo [AUTOSTART] Adding current file to Registry autostart...
:: %~f0 refers to the full path of the current batch file
reg add "%REG_KEY%" /v "YouTubeClipster" /t REG_SZ /d "\"%~f0\" --autostart" /f >nul 2>&1

if %errorlevel% equ 0 (
    echo [SUCCESS] %~nx0 added to Windows startup via Registry.
) else (
    echo [ERROR] Failed to modify Registry. Try running as Administrator.
)
goto :eof
