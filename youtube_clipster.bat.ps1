param(
    [string]$StatusFile,
    [string]$DialogTitle
)

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

# Create form
$form = New-Object System.Windows.Forms.Form
$form.Text = $DialogTitle
$form.Width = 500
$form.Height = 280
$form.StartPosition = "CenterScreen"
$form.TopMost = $true
$form.FormBorderStyle = "FixedDialog"
$form.MaximizeBox = $false
$form.MinimizeBox = $false

# Icon
$iconBox = New-Object System.Windows.Forms.PictureBox
$iconBox.Width = 48
$iconBox.Height = 48
$iconBox.Location = New-Object System.Drawing.Point(20, 20)
$iconBox.Image = [System.Drawing.SystemIcons]::Information.ToBitmap()
$iconBox.SizeMode = "StretchImage"
$form.Controls.Add($iconBox)

# URL Label
$labelURL = New-Object System.Windows.Forms.Label
$labelURL.Location = New-Object System.Drawing.Point(80, 20)
$labelURL.Size = New-Object System.Drawing.Size(390, 30)
$labelURL.Font = New-Object System.Drawing.Font("Segoe UI", 8)
$labelURL.ForeColor = [System.Drawing.Color]::Gray
$labelURL.Text = "..."
$form.Controls.Add($labelURL)

# Title Label
$labelTitle = New-Object System.Windows.Forms.Label
$labelTitle.Location = New-Object System.Drawing.Point(80, 50)
$labelTitle.Size = New-Object System.Drawing.Size(390, 50)
$labelTitle.Font = New-Object System.Drawing.Font("Segoe UI", 10, [System.Drawing.FontStyle]::Bold)
$labelTitle.Text = "..."
$form.Controls.Add($labelTitle)

# Status Label
$labelStatus = New-Object System.Windows.Forms.Label
$labelStatus.Location = New-Object System.Drawing.Point(80, 110)
$labelStatus.Size = New-Object System.Drawing.Size(390, 25)
$labelStatus.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$labelStatus.Text = "Initializing..."
$form.Controls.Add($labelStatus)

# Progress Bar
$progressBar = New-Object System.Windows.Forms.ProgressBar
$progressBar.Location = New-Object System.Drawing.Point(80, 145)
$progressBar.Size = New-Object System.Drawing.Size(390, 25)
$progressBar.Style = "Continuous"
$progressBar.Minimum = 0
$progressBar.Maximum = 100
$progressBar.Value = 0
$form.Controls.Add($progressBar)

# Percent Label
$labelPercent = New-Object System.Windows.Forms.Label
$labelPercent.Location = New-Object System.Drawing.Point(80, 175)
$labelPercent.Size = New-Object System.Drawing.Size(390, 20)
$labelPercent.Font = New-Object System.Drawing.Font("Segoe UI", 8)
$labelPercent.ForeColor = [System.Drawing.Color]::Gray
$labelPercent.Text = "0%"
$form.Controls.Add($labelPercent)

# Close button (initially hidden)
$buttonClose = New-Object System.Windows.Forms.Button
$buttonClose.Text = "Close"
$buttonClose.Location = New-Object System.Drawing.Point(200, 205)
$buttonClose.Size = New-Object System.Drawing.Size(100, 30)
$buttonClose.Visible = $false
$buttonClose.Add_Click({ $form.Close() })
$form.Controls.Add($buttonClose)

# Timer to update status
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 500  # Update every 500ms

$script:autoCloseCountdown = 0
$script:isComplete = $false

$timer.Add_Tick({
    if (Test-Path $StatusFile) {
        try {
            $content = Get-Content $StatusFile -ErrorAction SilentlyContinue
            
            $status = ""
            $title = ""
            $progress = 0
            $url = ""
            $error = ""
            
            foreach ($line in $content) {
                if ($line -match "^STATUS=(.+)") {
                    $status = $matches[1]
                }
                elseif ($line -match "^TITLE=(.+)") {
                    $title = $matches[1]
                }
                elseif ($line -match "^PROGRESS=(\d+)") {
                    $progress = [int]$matches[1]
                }
                elseif ($line -match "^URL=(.+)") {
                    $url = $matches[1]
                }
                elseif ($line -match "^ERROR=(.+)") {
                    $error = $matches[1]
                }
            }
            
            # Update UI
            if ($url) { $labelURL.Text = $url }
            if ($title) { $labelTitle.Text = $title }
            if ($status) { $labelStatus.Text = $status }
            
            if ($progress -ge 0 -and $progress -le 100) {
                $progressBar.Value = $progress
                $labelPercent.Text = "$progress%"
            }
            
            # Check if complete or error
            if ($status -match "Erfolgreich|Successfully|completed" -or $status -match "Fehler|Error|CANCELED") {
                if (-not $script:isComplete) {
                    $script:isComplete = $true
                    $script:autoCloseCountdown = 3
                    
                    if ($status -match "Erfolgreich|Successfully|completed") {
                        $iconBox.Image = [System.Drawing.SystemIcons]::Information.ToBitmap()
                        $progressBar.Value = 100
                        $labelPercent.Text = "100%"
                    }
                    elseif ($status -match "Fehler|Error") {
                        $iconBox.Image = [System.Drawing.SystemIcons]::Error.ToBitmap()
                        if ($error) {
                            $labelStatus.Text = "$status - $error"
                        }
                    }
                    else {
                        $iconBox.Image = [System.Drawing.SystemIcons]::Warning.ToBitmap()
                    }
                    
                    $buttonClose.Visible = $true
                }
            }
            
            # Auto-close countdown
            if ($script:isComplete -and $script:autoCloseCountdown -gt 0) {
                $script:autoCloseCountdown -= 0.5
                $buttonClose.Text = "Close ($([Math]::Ceiling($script:autoCloseCountdown))s)"
                
                if ($script:autoCloseCountdown -le 0) {
                    $timer.Stop()
                    $form.Close()
                }
            }
        }
        catch {
            # Ignore read errors
        }
    }
    else {
        # Status file deleted = close dialog
        if ($script:isComplete) {
            $timer.Stop()
            $form.Close()
        }
    }
})

$timer.Start()

# Show form
$form.Add_FormClosing({
    $timer.Stop()
    $timer.Dispose()
})

$form.ShowDialog() | Out-Null
$form.Dispose()
