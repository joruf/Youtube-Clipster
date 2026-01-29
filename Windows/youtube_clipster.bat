@echo off
::chcp 65001 >nul	:: UTF-8
chcp 1252 >nul  :: Windows-1252
setlocal enabledelayedexpansion

:: Loresoft YouTube Clipster - Windows Edition
:: Original: Joachim Ruf, Loresoft.de
:: License: GPLv3 - The author's name must be credited upon publication and modification.

:: ========================================
:: CONFIGURATION
:: ========================================
set "LANG_CHOICE=EN"
set "SHOW_STARTUP_DIALOG=1"
set "ENABLE_AUTOSTART=0"
set "INTERVAL_TIME_SEC=2"
set "DOWNLOAD_DIR=%USERPROFILE%\Downloads"
set "INSTALL_DIR=%LOCALAPPDATA%\YoutubeClipster"
set "YTDLP_EXE=%INSTALL_DIR%\yt-dlp.exe"
set "FFMPEG_DIR=%INSTALL_DIR%\ffmpeg"
set "USER_AGENT=Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
set "LAST_CLIP="
set "CANCELED_CLIP="
set "LOCKFILE=%~dp0youtube-clipster.lock"
set "STATUS_FILE=%TEMP%\youtube_clipster_status.txt"
set "PROGRESS_FILE=%TEMP%\youtube_clipster_progress.txt"

:: ========================================
:: LANGUAGE STRINGS
:: ========================================
if /i "%LANG_CHOICE%"=="DE" (
    set "MSG_INSTALL_ERROR=Fehler bei der Installation von"
    set "MSG_STARTED=Loresoft Youtube Clipster gestartet. Youtube-Link kopieren um Download zu starten."
    set "MSG_LINK_RECEIVED=Youtube-Link erhalten, Prozess wird vorbereitet..."
    set "MSG_UNKNOWN_TITLE=Unbekannter Titel"
    set "MSG_DOWNLOAD_TITLE=YouTube Clipster - Download"
    set "MSG_SELECT_FORMAT=Downloadformat auswaehlen:"
    set "MSG_AUDIO_ONLY=MP3 - Nur Audio"
    set "MSG_VIDEO_AUDIO=MP4 - Video + Audio"
    set "MSG_DOWNLOAD_BUTTON=Download"
    set "MSG_NO_FORMAT_SELECTED=Kein Format ausgewaehlt. Download abgebrochen."
    set "MSG_STARTING_DOWNLOAD=Starte Download"
    set "MSG_DOWNLOAD_COMPLETE=Download abgeschlossen"
    set "MSG_DOWNLOAD_FAILED=Download fehlgeschlagen"
    set "MSG_LOCATION=Speicherort"
    set "MSG_CHECKING_DEPS=Ueberpruefe benoetigte Programme..."
    set "MSG_INIT_COMPLETE=Initialisierung abgeschlossen"
    set "MSG_READY=Bereit! Ueberwache Zwischenablage..."
    set "MSG_COPY_URL=Kopiere eine YouTube-URL um den Download zu starten"
    set "MSG_ORPHANED_LOCK=Verwaiste Lock-Datei gefunden. Entferne sie..."
    set "MSG_ONLY_ONE_INSTANCE=Das Programm laeuft bereits. Nur eine Instanz erlaubt."
    set "MSG_LOCK_CREATED=Lock-Datei erstellt."
    set "MSG_CLIP_ALREADY_CANCELED=Dieser Link wurde zuvor abgebrochen."
    set "MSG_TITLE=Titel"
    set "MSG_FORMAT=Format"
    set "MSG_DESTINATION=Ziel"
    set "MSG_AUTOSTART_CHECK=Pruefe Registry fuer YouTube Clipster..."
    set "MSG_AUTOSTART_EXISTS=Registry-Eintrag existiert bereits."
    set "MSG_AUTOSTART_ADD=Fuege aktuelle Datei zum Registry-Autostart hinzu..."
    set "MSG_AUTOSTART_SUCCESS=erfolgreich zum Windows-Autostart hinzugefuegt."
    set "MSG_AUTOSTART_ERROR=Fehler beim Aendern der Registry. Versuche als Administrator auszufuehren."
    set "MSG_STATUS_URL=URL erkannt"
    set "MSG_STATUS_FETCHING=Lade Informationen..."
    set "MSG_STATUS_FORMAT=Waehle Format..."
    set "MSG_STATUS_DOWNLOADING=Lade herunter..."
    set "MSG_STATUS_CONVERTING=Konvertiere zu MP3..."
    set "MSG_STATUS_SUCCESS=Erfolgreich abgeschlossen!"
    set "MSG_STATUS_ERROR=Fehler aufgetreten"
) else (
    set "MSG_INSTALL_ERROR=Error installing"
    set "MSG_STARTED=Loresoft Youtube Clipster started. Copy YouTube link to start download."
    set "MSG_LINK_RECEIVED=YouTube link received, process preparing..."
    set "MSG_UNKNOWN_TITLE=Unknown Title"
    set "MSG_DOWNLOAD_TITLE=YouTube Clipster - Download"
    set "MSG_SELECT_FORMAT=Select download format:"
    set "MSG_AUDIO_ONLY=MP3 - Audio only"
    set "MSG_VIDEO_AUDIO=MP4 - Video + Audio"
    set "MSG_DOWNLOAD_BUTTON=Download"
    set "MSG_NO_FORMAT_SELECTED=No format selected. Download canceled."
    set "MSG_STARTING_DOWNLOAD=Starting download"
    set "MSG_DOWNLOAD_COMPLETE=Download completed successfully"
    set "MSG_DOWNLOAD_FAILED=Download failed"
    set "MSG_LOCATION=Location"
    set "MSG_CHECKING_DEPS=Checking required programs..."
    set "MSG_INIT_COMPLETE=Initialization complete"
    set "MSG_READY=Ready! Monitoring clipboard..."
    set "MSG_COPY_URL=Copy a YouTube URL to start downloading"
    set "MSG_ORPHANED_LOCK=Orphaned lock file found. Removing it..."
    set "MSG_ONLY_ONE_INSTANCE=Program is already running. Only one instance allowed."
    set "MSG_LOCK_CREATED=Lock file created."
    set "MSG_CLIP_ALREADY_CANCELED=This link was previously canceled."
    set "MSG_TITLE=Title"
    set "MSG_FORMAT=Format"
    set "MSG_DESTINATION=Destination"
    set "MSG_AUTOSTART_CHECK=Checking Registry for YouTube Clipster..."
    set "MSG_AUTOSTART_EXISTS=Registry entry already exists."
    set "MSG_AUTOSTART_ADD=Adding current file to Registry autostart..."
    set "MSG_AUTOSTART_SUCCESS=successfully added to Windows startup via Registry."
    set "MSG_AUTOSTART_ERROR=Failed to modify Registry. Try running as Administrator."
    set "MSG_STATUS_URL=URL detected"
    set "MSG_STATUS_FETCHING=Fetching information..."
    set "MSG_STATUS_FORMAT=Select format..."
    set "MSG_STATUS_DOWNLOADING=Downloading..."
    set "MSG_STATUS_CONVERTING=Converting to MP3..."
    set "MSG_STATUS_SUCCESS=Successfully completed!"
    set "MSG_STATUS_ERROR=Error occurred"
)

if not exist "%DOWNLOAD_DIR%" mkdir "%DOWNLOAD_DIR%"
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"

echo YouTube Clipster v1.02
echo Author: Joachim Ruf, Loresoft.de
echo License: GPLv3 - The author's name must be credited upon publication and modification.
echo ========================================
echo.

:: ========================================
:: LOCK FILE CHECK
:: ========================================
echo [INFO] Checking for running instances...

if exist "%LOCKFILE%" (
    echo [DEBUG] Lock file found at: %LOCKFILE%
    
    set /p OLDPID=<"%LOCKFILE%"
    
    tasklist /FI "PID eq !OLDPID!" 2>nul | find "!OLDPID!" >nul
    if !errorlevel! equ 0 (
        echo [ERROR] !MSG_ONLY_ONE_INSTANCE! PID: !OLDPID!
        echo.
        powershell -Command "[System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms') | Out-Null; [System.Windows.Forms.MessageBox]::Show('!MSG_ONLY_ONE_INSTANCE!`n`nPID: !OLDPID!', 'YouTube Clipster', 'OK', 'Error')" >nul
        exit /b 1
    ) else (
        echo [WARNING] !MSG_ORPHANED_LOCK!
        del "%LOCKFILE%" 2>nul
    )
)

for /f "tokens=2" %%a in ('tasklist /FI "IMAGENAME eq cmd.exe" /FO LIST ^| findstr /i "PID:"') do (
    set "CURRENT_PID=%%a"
    goto :pid_found
)
:pid_found

echo !CURRENT_PID! > "%LOCKFILE%"
echo [DEBUG] !MSG_LOCK_CREATED! PID: !CURRENT_PID!
echo.

set "PS_CLEANUP=%TEMP%\youtube_clipster_monitor_%CURRENT_PID%.ps1"
(
echo $lockFile = "%LOCKFILE%"
echo $processId = !CURRENT_PID!
echo.
echo while ^($true^) {
echo     if ^(-not ^(Get-Process -Id $processId -ErrorAction SilentlyContinue^)^) {
echo         if ^(Test-Path $lockFile^) {
echo             Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
echo         }
echo         break
echo     }
echo     Start-Sleep -Seconds 1
echo }
) > "%PS_CLEANUP%"

start /B powershell -WindowStyle Hidden -ExecutionPolicy Bypass -File "%PS_CLEANUP%" >nul 2>&1

:: ========================================
:: DEPENDENCY CHECK
:: ========================================
call :check_ytdlp
call :check_ffmpeg

if "%ENABLE_AUTOSTART%"=="1" (
    call :check_autostart
)

echo [INFO] !MSG_INIT_COMPLETE!
echo.

:: ========================================
:: IGNORE INITIAL CLIPBOARD
:: ========================================
echo [DEBUG] Reading initial clipboard to ignore old content...
for /f "usebackq delims=" %%a in (`powershell -Command "Get-Clipboard 2>$null | Select-Object -First 1"`) do set "LAST_CLIP=%%a"
if defined LAST_CLIP (
    echo [DEBUG] Initial clipboard ignored: !LAST_CLIP!
) else (
    echo [DEBUG] Clipboard is empty
)

echo.
echo [INFO] !MSG_READY!
echo [INFO] !MSG_COPY_URL!
echo ========================================
echo.

if "%SHOW_STARTUP_DIALOG%"=="1" (
    powershell -Command "Add-Type -AssemblyName System.Windows.Forms; $notify = New-Object System.Windows.Forms.NotifyIcon; $notify.Icon = [System.Drawing.SystemIcons]::Information; $notify.Visible = $true; $notify.ShowBalloonTip(3000, 'YouTube Clipster', '!MSG_STARTED!', [System.Windows.Forms.ToolTipIcon]::Info); Start-Sleep -Seconds 3; $notify.Dispose()" >nul 2>&1
)

:: ========================================
:: MAIN LOOP
:: ========================================
:loop
timeout /t %INTERVAL_TIME_SEC% /nobreak >nul

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
                    echo [DEBUG] Calling download for: !CLIP!
                    call :download "!CLIP!"
                    
                    :: Always set LAST_CLIP after download attempt to prevent loop
                    set "LAST_CLIP=!CLIP!"
                    echo [DEBUG] LAST_CLIP set to: !LAST_CLIP!
                ) else (
                    echo [DEBUG] Not a valid HTTP URL, ignoring
                )
            )
            
            del "!T!" 2>nul
        ) else (
            echo [DEBUG] !MSG_CLIP_ALREADY_CANCELED!
        )
    )
)

goto loop

:: ========================================
:: DOWNLOAD FUNCTION
:: ========================================
:download
set "URL=%~1"
echo [DEBUG] Full URL: !URL!

:: Initialize status file
echo STATUS=!MSG_STATUS_URL! > "%STATUS_FILE%"
echo TITLE=... >> "%STATUS_FILE%"
echo PROGRESS=0 >> "%STATUS_FILE%"
echo URL=!URL! >> "%STATUS_FILE%"

:: Start progress dialog in background
start "" powershell -WindowStyle Hidden -ExecutionPolicy Bypass -File "%~dp0youtube_clipster.bat.ps1" "%STATUS_FILE%" "%MSG_DOWNLOAD_TITLE%"

:: Wait a moment for dialog to appear
timeout /t 1 /nobreak >nul

:: Update: Fetching title
echo STATUS=!MSG_STATUS_FETCHING! > "%STATUS_FILE%"
echo TITLE=... >> "%STATUS_FILE%"
echo PROGRESS=10 >> "%STATUS_FILE%"
echo URL=!URL! >> "%STATUS_FILE%"

echo [DEBUG] Fetching video title...
set "TEMP_BAT=%TEMP%\ytdlp_title.bat"
(
echo @echo off
echo "%YTDLP_EXE%" --no-playlist --skip-download --no-warnings --get-title "!URL!" 2^>nul
) > "%TEMP_BAT%"

for /f "usebackq delims=" %%t in (`"%TEMP_BAT%"`) do set "TITLE=%%t"
del "%TEMP_BAT%" 2>nul

if not defined TITLE (
    echo [WARNING] Could not fetch title
    set "TITLE=!MSG_UNKNOWN_TITLE!"
)

echo [DEBUG] Title: !TITLE!

:: Update: Show title, format selection
echo STATUS=!MSG_STATUS_FORMAT! > "%STATUS_FILE%"
echo TITLE=!TITLE! >> "%STATUS_FILE%"
echo PROGRESS=20 >> "%STATUS_FILE%"
echo URL=!URL! >> "%STATUS_FILE%"

:: Format dialog
echo [DEBUG] Creating format selection dialog...
set "PS=%TEMP%\fmt.ps1"

set "TITLE_CLEAN=!TITLE!"
set "TITLE_CLEAN=!TITLE_CLEAN:'=''!"

(
echo Add-Type -AssemblyName System.Windows.Forms
echo $f=New-Object System.Windows.Forms.Form
echo $f.Text="!MSG_DOWNLOAD_TITLE!"
echo $f.Width=400
echo $f.Height=240
echo $f.StartPosition="CenterScreen"
echo $f.TopMost=$true
echo $f.FormBorderStyle="FixedDialog"
echo $f.MaximizeBox=$false
echo.
echo $lTitle=New-Object System.Windows.Forms.Label
echo $lTitle.Text='!TITLE_CLEAN!'
echo $lTitle.Location="10,10"
echo $lTitle.Size="360,40"
echo $lTitle.Font=New-Object System.Drawing.Font^("Segoe UI",9,[System.Drawing.FontStyle]::Bold^)
echo $f.Controls.Add^($lTitle^)
echo.
echo $l=New-Object System.Windows.Forms.Label
echo $l.Text="!MSG_SELECT_FORMAT!"
echo $l.Location="20,60"
echo $l.AutoSize=$true
echo $f.Controls.Add^($l^)
echo.
echo $r1=New-Object System.Windows.Forms.RadioButton
echo $r1.Text="!MSG_AUDIO_ONLY!"
echo $r1.Location="30,90"
echo $r1.Width=300
echo $r1.Checked=$true
echo $f.Controls.Add^($r1^)
echo.
echo $r2=New-Object System.Windows.Forms.RadioButton
echo $r2.Text="!MSG_VIDEO_AUDIO!"
echo $r2.Location="30,120"
echo $r2.Width=300
echo $f.Controls.Add^($r2^)
echo.
echo $b=New-Object System.Windows.Forms.Button
echo $b.Text="!MSG_DOWNLOAD_BUTTON!"
echo $b.Location="150,160"
echo $b.Width=100
echo $b.Height=35
echo $b.DialogResult="OK"
echo $f.Controls.Add^($b^)
echo $f.AcceptButton=$b
echo.
echo $result=$f.ShowDialog^(^)
echo if^($result -eq "OK"^){
echo     if^($r1.Checked^){"mp3"}else{"mp4"}
echo }elseif^($result -eq "Cancel"^){
echo     Write-Output "CANCELED"
echo }
) > "%PS%"

for /f "usebackq delims=" %%f in (`powershell -EP Bypass -NoProfile -File "%PS%" 2^>nul`) do set "FMT=%%f"
del "%PS%" 2>nul

if not defined FMT (
    echo [INFO] !MSG_NO_FORMAT_SELECTED!
    echo STATUS=CANCELED > "%STATUS_FILE%"
    echo TITLE=!TITLE! >> "%STATUS_FILE%"
    echo PROGRESS=0 >> "%STATUS_FILE%"
    timeout /t 2 /nobreak >nul
    del "%STATUS_FILE%" 2>nul
    
    :: Set CANCELED_CLIP to prevent re-download
    set "CANCELED_CLIP=!URL!"
    goto :eof
)

if /i "!FMT!"=="CANCELED" (
    echo [INFO] !MSG_NO_FORMAT_SELECTED!
    echo STATUS=CANCELED > "%STATUS_FILE%"
    echo TITLE=!TITLE! >> "%STATUS_FILE%"
    echo PROGRESS=0 >> "%STATUS_FILE%"
    timeout /t 2 /nobreak >nul
    del "%STATUS_FILE%" 2>nul
    
    :: Set CANCELED_CLIP and clear LAST_CLIP
    set "CANCELED_CLIP=!URL!"
    set "LAST_CLIP="
    goto :eof
)

echo [DEBUG] User selected format: !FMT!

:: Change to download directory
cd /d "%DOWNLOAD_DIR%"
echo [DEBUG] Working directory: %CD%

:: Update: Starting download
echo STATUS=!MSG_STATUS_DOWNLOADING! > "%STATUS_FILE%"
echo TITLE=!TITLE! >> "%STATUS_FILE%"
echo PROGRESS=0 >> "%STATUS_FILE%"
echo URL=!URL! >> "%STATUS_FILE%"

echo.
echo [INFO] !MSG_STARTING_DOWNLOAD!
echo ========================================
echo [INFO] !MSG_TITLE!: !TITLE!
echo [INFO] !MSG_FORMAT!: !FMT!
echo [INFO] !MSG_DESTINATION!: %DOWNLOAD_DIR%
echo ========================================
echo.

set "DL_LOG=%TEMP%\ytdlp_download_%RANDOM%.log"

:: Show full yt-dlp and FFmpeg output (no suppression)
if /i "!FMT!"=="mp3" (
    echo [DEBUG] Executing MP3 download...
    echo [DEBUG] Command: "%YTDLP_EXE%" --user-agent "!USER_AGENT!" --no-playlist -x --audio-format mp3 --ffmpeg-location "%FFMPEG_DIR%\bin" -o "%%(title)s.%%(ext)s" "!URL!"
    echo.
    echo [OUTPUT] yt-dlp ^& FFmpeg output:
    echo ========================================
    
    :: Start background process to monitor progress
    start /B cmd /c "call :monitor_progress "!DL_LOG!" "%STATUS_FILE%" "!TITLE!" "!MSG_STATUS_DOWNLOADING!" "!MSG_STATUS_CONVERTING!""
    
    :: Run download with live output to console AND log file
    "%YTDLP_EXE%" --user-agent "!USER_AGENT!" --no-playlist -x --audio-format mp3 --ffmpeg-location "%FFMPEG_DIR%\bin" -o "%%(title)s.%%(ext)s" "!URL!" --newline 2>&1 | "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -Command "$input | ForEach-Object { Write-Host $_; Add-Content -Path '!DL_LOG!' -Value $_ -Encoding UTF8 }"
    
    echo ========================================
) else (
    echo [DEBUG] Executing MP4 download...
    echo [DEBUG] Command: "%YTDLP_EXE%" --user-agent "!USER_AGENT!" --no-playlist -f bestvideo+bestaudio --merge-output-format mp4 --ffmpeg-location "%FFMPEG_DIR%\bin" -o "%%(title)s.%%(ext)s" "!URL!"
    echo.
    echo [OUTPUT] yt-dlp ^& FFmpeg output:
    echo ========================================
    
    :: Start background process to monitor progress
    start /B cmd /c "call :monitor_progress "!DL_LOG!" "%STATUS_FILE%" "!TITLE!" "!MSG_STATUS_DOWNLOADING!" """
    
    :: Run download with live output to console AND log file
    "%YTDLP_EXE%" --user-agent "!USER_AGENT!" --no-playlist -f bestvideo+bestaudio --merge-output-format mp4 --ffmpeg-location "%FFMPEG_DIR%\bin" -o "%%(title)s.%%(ext)s" "!URL!" --newline 2>&1 | "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -Command "$input | ForEach-Object { Write-Host $_; Add-Content -Path '!DL_LOG!' -Value $_ -Encoding UTF8 }"
    
    echo ========================================
)

set "RET=!errorlevel!"
echo.
echo [DEBUG] Process exit code: !RET!


echo.

if !RET! equ 0 (
    echo STATUS=!MSG_STATUS_SUCCESS! > "%STATUS_FILE%"
    echo TITLE=!TITLE! >> "%STATUS_FILE%"
    echo PROGRESS=100 >> "%STATUS_FILE%"
    echo URL=!URL! >> "%STATUS_FILE%"
    
    echo.
    echo [SUCCESS] !MSG_DOWNLOAD_COMPLETE!!
    echo ========================================
    echo [INFO] !MSG_TITLE!: !TITLE!
    echo [INFO] !MSG_FORMAT!: !FMT!
    echo [INFO] !MSG_LOCATION!: %DOWNLOAD_DIR%
    echo ========================================
    echo.
    echo [DEBUG] Setting LAST_CLIP to prevent re-download
    set "LAST_CLIP=!URL!"
    echo [DEBUG] Resetting CANCELED_CLIP to allow new URLs
    set "CANCELED_CLIP="
    echo [DEBUG] Opening download folder...
    
    timeout /t 3 /nobreak >nul
    start "" "%DOWNLOAD_DIR%"
    
    :: Clean up
    del "%STATUS_FILE%" 2>nul
    del "!DL_LOG!" 2>nul
    
    echo.
    echo [DEBUG] Returning to clipboard monitoring...
    echo.
    goto :eof
) else (
    echo STATUS=!MSG_STATUS_ERROR! > "%STATUS_FILE%"
    echo TITLE=!TITLE! >> "%STATUS_FILE%"
    echo PROGRESS=0 >> "%STATUS_FILE%"
    echo ERROR=Exit code: !RET! >> "%STATUS_FILE%"
    
    echo.
    echo [FAILED] !MSG_DOWNLOAD_FAILED!!
    echo ========================================
    echo [ERROR] !MSG_TITLE!: !TITLE!
    echo [ERROR] !MSG_FORMAT!: !FMT!
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
    
    timeout /t 5 /nobreak >nul
    
    :: Clean up
    del "%STATUS_FILE%" 2>nul
    del "!DL_LOG!" 2>nul
)

echo.
echo [DEBUG] Returning to clipboard monitoring...
echo.
goto :eof

:: ========================================
:: MONITOR PROGRESS
:: ========================================
:monitor_progress
setlocal enabledelayedexpansion
set "LOG_FILE=%~1"
set "STATUS_FILE=%~2"
set "TITLE=%~3"
set "MSG_DL=%~4"
set "MSG_CONV=%~5"
set "LAST_PERCENT=0"
set "CONVERTING=0"

:monitor_loop
if not exist "%LOG_FILE%" (
    timeout /t 1 /nobreak >nul
    goto :monitor_loop
)

:: Read log file line by line
for /f "usebackq tokens=*" %%a in ("%LOG_FILE%") do (
    set "LINE=%%a"
    
    :: Check for download progress
    echo !LINE! | findstr /C:"[download]" >nul
    if !errorlevel! equ 0 (
        :: Extract percentage from download line
        for /f "tokens=2 delims= " %%p in ("!LINE!") do (
            set "PERCENT=%%p"
            :: Remove % sign
            set "PERCENT=!PERCENT:~0,-1!"
            
            :: Validate it's a number
            echo !PERCENT! | findstr /R "^[0-9][0-9]*\.[0-9]$" >nul
            if !errorlevel! equ 0 (
                :: Round to integer
                for /f "tokens=1 delims=." %%n in ("!PERCENT!") do set "PERCENT=%%n"
            )
            
            :: Check if it's a valid percentage
            if defined PERCENT (
                if !PERCENT! GEQ 0 if !PERCENT! LEQ 100 (
                    if not "!PERCENT!"=="!LAST_PERCENT!" (
                        echo STATUS=%MSG_DL% > "%STATUS_FILE%"
                        echo TITLE=%TITLE% >> "%STATUS_FILE%"
                        echo PROGRESS=!PERCENT! >> "%STATUS_FILE%"
                        set "LAST_PERCENT=!PERCENT!"
                        set "CONVERTING=0"
                    )
                )
            )
        )
    )
    
    :: Check for conversion/post-processing (MP3 conversion)
    if not "%MSG_CONV%"=="" (
        :: Check for various conversion indicators
        echo !LINE! | findstr /C:"ExtractAudio" /C:"Destination:" /C:"Deleting original file" >nul
        if !errorlevel! equ 0 (
            if !CONVERTING! equ 0 (
                echo STATUS=%MSG_CONV% > "%STATUS_FILE%"
                echo TITLE=%TITLE% >> "%STATUS_FILE%"
                echo PROGRESS=95 >> "%STATUS_FILE%"
                set "CONVERTING=1"
            )
        )
    )
)

:: Check if process is still running
if exist "%LOG_FILE%" (
    timeout /t 1 /nobreak >nul
    goto :monitor_loop
)

endlocal
goto :eof
:: ========================================
:: CHECK YT-DLP
:: ========================================
:check_ytdlp
echo [INFO] !MSG_CHECKING_DEPS!
echo ========================================
echo [1/2] Checking yt-dlp.exe...

if exist "%YTDLP_EXE%" (
    echo [DEBUG] yt-dlp.exe found at: %YTDLP_EXE%
    
    :: Get current installed version
    set "CURRENT_VERSION="
    for /f "usebackq delims=" %%v in (`"%YTDLP_EXE%" --version 2^>nul`) do set "CURRENT_VERSION=%%v"
    
    if defined CURRENT_VERSION (
        echo [INFO] Current version: !CURRENT_VERSION!
        
        :: Get latest version from GitHub API
        echo [DEBUG] Checking for latest version online...
        powershell -Command "$ProgressPreference='SilentlyContinue';try{$response=Invoke-RestMethod -Uri 'https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest' -TimeoutSec 10;$response.tag_name}catch{Write-Output 'ERROR'}" > "%TEMP%\ytdlp_latest.txt"
        
        set "LATEST_VERSION="
        set /p LATEST_VERSION=<"%TEMP%\ytdlp_latest.txt"
        del "%TEMP%\ytdlp_latest.txt" 2>nul
        
        if "!LATEST_VERSION!"=="ERROR" (
            echo [WARNING] Could not check for updates online
            echo [INFO] Using existing version: !CURRENT_VERSION!
            goto :eof
        )
        
        if defined LATEST_VERSION (
            echo [INFO] Latest version: !LATEST_VERSION!
            
            if not "!CURRENT_VERSION!"=="!LATEST_VERSION!" (
                echo [UPDATE] New version available: !CURRENT_VERSION! -^> !LATEST_VERSION!
                echo [UPDATE] Updating to latest version...
                
                :: Delete old version
                del "%YTDLP_EXE%" 2>nul
                timeout /t 1 /nobreak >nul
                
                goto :download_ytdlp
            ) else (
                echo [OK] yt-dlp.exe is up-to-date
                goto :eof
            )
        ) else (
            echo [WARNING] Could not retrieve latest version info
            echo [INFO] Using existing version: !CURRENT_VERSION!
            goto :eof
        )
    ) else (
        echo [WARNING] Could not get version from existing yt-dlp.exe
        echo [UPDATE] Re-downloading...
        del "%YTDLP_EXE%" 2>nul
        goto :download_ytdlp
    )
) else (
    echo [INFO] yt-dlp.exe not found, downloading...
)

:download_ytdlp
echo [DOWNLOAD] Downloading latest yt-dlp.exe from GitHub...
powershell -Command "$ProgressPreference='SilentlyContinue';Invoke-WebRequest -Uri 'https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe' -OutFile '%YTDLP_EXE%'"
if !errorlevel! neq 0 (
    echo [ERROR] !MSG_INSTALL_ERROR! yt-dlp.exe
    pause
    exit /b 1
)
echo [OK] yt-dlp.exe downloaded successfully

:: Verify downloaded version
set "NEW_VERSION="
for /f "usebackq delims=" %%v in (`"%YTDLP_EXE%" --version 2^>nul`) do set "NEW_VERSION=%%v"
if defined NEW_VERSION (
    echo [INFO] Installed version: !NEW_VERSION!
) else (
    echo [INFO] yt-dlp.exe installed
)
goto :eof

:: ========================================
:: CHECK FFMPEG
:: ========================================
:check_ffmpeg
echo [2/2] Checking FFmpeg...
if exist "%FFMPEG_DIR%\bin\ffmpeg.exe" (
    echo [DEBUG] FFmpeg found at: %FFMPEG_DIR%\bin\ffmpeg.exe
    echo [OK] FFmpeg found
    goto :eof
)

echo [DOWNLOAD] Downloading FFmpeg essentials...
powershell -Command "$ProgressPreference='SilentlyContinue';Write-Host '[DEBUG] Starting download...';Invoke-WebRequest -Uri 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' -OutFile $env:TEMP\ffmpeg.zip;Write-Host '[DEBUG] Extracting...';Expand-Archive $env:TEMP\ffmpeg.zip $env:TEMP\ffmpeg_tmp -Force;Write-Host '[DEBUG] Done'"

if !errorlevel! neq 0 (
    echo [ERROR] !MSG_INSTALL_ERROR! FFmpeg
    pause
    exit /b 1
)

for /d %%a in ("%TEMP%\ffmpeg_tmp\ffmpeg-*") do xcopy "%%a" "%FFMPEG_DIR%\" /E /I /Y >nul 2>&1

del "%TEMP%\ffmpeg.zip" 2>nul
rd /s /q "%TEMP%\ffmpeg_tmp" 2>nul

if not exist "%FFMPEG_DIR%\bin\ffmpeg.exe" (
    echo [ERROR] FFmpeg installation failed
    exit /b 1
)

echo [OK] FFmpeg installed successfully
for /f "usebackq tokens=3" %%v in (`"%FFMPEG_DIR%\bin\ffmpeg.exe" -version 2^>nul ^| findstr /i "ffmpeg version"`) do echo [INFO] Version: %%v
goto :eof

:: ========================================
:: CHECK AUTOSTART
:: ========================================
:check_autostart
echo [AUTOSTART] !MSG_AUTOSTART_CHECK!
set "REG_KEY=HKCU\Software\Microsoft\Windows\CurrentVersion\Run"

reg query "%REG_KEY%" /v "YouTubeClipster" >nul 2>&1
if !errorlevel! equ 0 (
    echo [AUTOSTART] !MSG_AUTOSTART_EXISTS!
    goto :eof
)

echo [AUTOSTART] !MSG_AUTOSTART_ADD!
reg add "%REG_KEY%" /v "YouTubeClipster" /t REG_SZ /d "\"%~f0\"" /f >nul 2>&1

if !errorlevel! equ 0 (
    echo [SUCCESS] %~nx0 !MSG_AUTOSTART_SUCCESS!
) else (
    echo [ERROR] !MSG_AUTOSTART_ERROR!
)
goto :eof