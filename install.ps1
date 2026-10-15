# Dictate installer for Windows.
# Run:  powershell -ExecutionPolicy Bypass -File install.ps1
$ErrorActionPreference = "Stop"
$Dir = $PSScriptRoot

Write-Host "Dictate installer"
Write-Host "================="
Write-Host

Write-Host "[1/2] Installing Python packages..."
# Core deps: GUI, audio capture, hotkey listener + typing, arrays.
# Local-STT deps: sherpa-onnx runs Parakeet on CPU offline; huggingface_hub
# downloads the model on demand. No evdev/xdotool/ydotool: pynput does both
# hotkey and typing on Windows.
python -m pip install --user `
    PyQt6 sounddevice pynput numpy `
    sherpa-onnx soundfile huggingface_hub
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

Write-Host
Write-Host "[2/2] Creating Start menu shortcut..."
# pythonw.exe starts the tray app without a console window.
$Python = python -c "import sys; print(sys.executable)"
$Pythonw = Join-Path (Split-Path $Python) "pythonw.exe"
if (-not (Test-Path $Pythonw)) { $Pythonw = $Python }
# Shortcuts need an .ico; let Qt convert the PNG. Without it the shortcut
# just shows the Python icon.
$Ico = Join-Path $Dir "icon.ico"
python -c "import sys; from PyQt6.QtGui import QImage; sys.exit(0 if QImage(sys.argv[1]).scaled(256, 256).save(sys.argv[2]) else 1)" (Join-Path $Dir "icon.png") $Ico
$Lnk = Join-Path ([Environment]::GetFolderPath("Programs")) "Dictate.lnk"
$Shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($Lnk)
$Shortcut.TargetPath = $Pythonw
$Shortcut.Arguments = "`"$(Join-Path $Dir 'gui.py')`""
$Shortcut.WorkingDirectory = $Dir
$Shortcut.Description = "Voice dictation to keyboard (offline, Parakeet)"
if (Test-Path $Ico) { $Shortcut.IconLocation = $Ico }
$Shortcut.Save()

Write-Host
Write-Host "Installed."
Write-Host
Write-Host "Launch:"
Write-Host "  - From Start menu: 'Dictate'"
Write-Host "  - From shell:      python $(Join-Path $Dir 'gui.py')"
Write-Host
Write-Host "On first launch: open Settings and click 'Download model' (~640 MB, one-time)."
