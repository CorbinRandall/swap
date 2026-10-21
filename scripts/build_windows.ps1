$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Python = $env:SWAP_PYTHON
if (-not $Python) {
  $Python = Join-Path $Root ".build-venv\Scripts\python.exe"
  if (-not (Test-Path $Python)) { $Python = "python" }
}

# SWAP_ONEFILE=1 builds a single portable Swap.exe for GitHub releases.
$Mode = if ($env:SWAP_ONEFILE -eq "1") { "--onefile" } else { "--onedir" }

& $Python -m PyInstaller `
  --noconfirm `
  --clean `
  --windowed `
  $Mode `
  --name Swap `
  --icon "$Root\assets\Swap.ico" `
  --collect-submodules swap.hidpp `
  --hidden-import hid `
  --hidden-import swap.app_win `
  --exclude-module swap.app_mac `
  --exclude-module gui `
  --exclude-module gcore `
  --exclude-module ghub_presets `
  "$Root\swap\app.py"

if ($Mode -eq "--onefile") { Write-Host "Built: $Root\dist\Swap.exe" }
else { Write-Host "Built: $Root\dist\Swap\Swap.exe" }
