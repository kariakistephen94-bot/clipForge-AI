# ClipForge AI launcher for Windows (PowerShell). Untested on Windows so far -- see DEVELOPMENT_STATUS.md.
#   powershell -ExecutionPolicy Bypass -File start.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$Port = if ($env:CLIPFORGE_PORT) { $env:CLIPFORGE_PORT } else { "8765" }
$Venv = Join-Path $PSScriptRoot "backend\.venv"
$VenvPy = Join-Path $Venv "Scripts\python.exe"

function Step($m) { Write-Host "> $m" -ForegroundColor Magenta }
function Die($m) { Write-Host "X $m" -ForegroundColor Red; exit 1 }

Step "Checking Python 3.12/3.13"
$Py = $null
foreach ($v in @("3.12", "3.13")) {
  try { & py "-$v" -c "import sys" 2>$null; if ($LASTEXITCODE -eq 0) { $Py = @("py", "-$v"); break } } catch {}
}
if (-not $Py) { Die "Python 3.12 is required: winget install Python.Python.3.12" }

Step "Checking FFmpeg"
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue) -or -not (Get-Command ffprobe -ErrorAction SilentlyContinue)) {
  Die "FFmpeg is required (free): winget install Gyan.FFmpeg   then open a new terminal"
}

Step "Checking Node.js"
$NodeOk = [bool](Get-Command npm -ErrorAction SilentlyContinue)
if (-not $NodeOk) { Write-Host "! Node.js not found (needed to build the UI): winget install OpenJS.NodeJS.LTS" -ForegroundColor Yellow }

if (-not (Test-Path $VenvPy)) {
  Step "Creating virtual environment"
  & $Py[0] $Py[1] -m venv $Venv
}
Step "Installing backend dependencies"
& $VenvPy -m pip install --upgrade pip | Out-Null
& $VenvPy -m pip install -r backend\requirements.txt

$Yunet = "backend\models\face_detection_yunet_2023mar.onnx"
if (-not (Test-Path $Yunet)) {
  Step "Downloading OpenCV YuNet face model"
  New-Item -ItemType Directory -Force -Path "backend\models" | Out-Null
  try {
    Invoke-WebRequest -Uri "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx" -OutFile $Yunet
  } catch { Write-Host "! Face model download failed; center-crop fallback will be used." -ForegroundColor Yellow }
}

if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env"; Write-Host "! Created .env - add GEMINI_API_KEY for AI analysis" -ForegroundColor Yellow }
New-Item -ItemType Directory -Force -Path "workspace\broll", "workspace\music", "logs" | Out-Null

if ($NodeOk) {
  Step "Building dashboard"
  if (-not (Test-Path "frontend\node_modules")) { npm --prefix frontend install --no-fund --no-audit }
  npm --prefix frontend run build
} elseif (-not (Test-Path "frontend\dist\index.html")) { Die "Node.js is required to build the dashboard the first time." }

$Url = "http://127.0.0.1:$Port"
Step "Starting ClipForge AI at $Url"
Start-Process $Url
& $VenvPy -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port $Port
