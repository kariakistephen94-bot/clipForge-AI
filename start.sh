#!/usr/bin/env bash
# ClipForge AI launcher (macOS / Linux).
#   ./start.sh          install/verify, build the UI, serve everything at http://127.0.0.1:8765
#   ./start.sh --dev    backend + Vite dev server with hot reload at http://127.0.0.1:5173
#   ./start.sh --check  only verify dependencies
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
MODE="${1:-}"
PORT="${CLIPFORGE_PORT:-8765}"
VENV="$ROOT/backend/.venv"
YUNET="$ROOT/backend/models/face_detection_yunet_2023mar.onnx"
YUNET_URL="https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
YUNET_SHA="8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"

step() { printf "\033[1;35m▸\033[0m %s\n" "$*"; }
ok()   { printf "  \033[1;32m✓\033[0m %s\n" "$*"; }
warn() { printf "  \033[1;33m!\033[0m %s\n" "$*"; }
die()  { printf "\033[1;31m✗ %s\033[0m\n" "$*" >&2; exit 1; }

sha256() { if command -v shasum >/dev/null; then shasum -a 256 "$1" | cut -d' ' -f1; else sha256sum "$1" | cut -d' ' -f1; fi; }

# ---------------------------------------------------------------- Python 3.12 / 3.13
step "Checking Python"
PY=""
for c in python3.12 python3.13 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if (3,12) <= sys.version_info[:2] <= (3,13) else 1)' 2>/dev/null; then
    PY="$(command -v "$c")"; break
  fi
done
if [ -z "$PY" ] && command -v uv >/dev/null 2>&1; then
  step "Installing Python 3.12 with uv (isolated, does not touch your system Python)"
  uv python install 3.12 >/dev/null
  PY="$(uv python find 3.12)"
fi
[ -n "$PY" ] || die "Python 3.12 or 3.13 is required (ML wheels lag behind newer versions).
  macOS:  brew install python@3.12     (or: brew install uv && uv python install 3.12)
  Linux:  sudo apt install python3.12 python3.12-venv"
ok "$("$PY" --version) ($PY)"

# ---------------------------------------------------------------- FFmpeg
step "Checking FFmpeg"
if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
  case "$(uname -s)" in
    Darwin) HINT="brew install ffmpeg" ;;
    Linux)  HINT="sudo apt install ffmpeg   (Fedora: sudo dnf install ffmpeg)" ;;
    *)      HINT="see https://ffmpeg.org/download.html" ;;
  esac
  die "FFmpeg and ffprobe are required and free. Install with:  $HINT"
fi
ok "$(ffmpeg -hide_banner -version | head -1)"

# ---------------------------------------------------------------- Node
step "Checking Node.js"
NODE_OK=1
if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
  ok "node $(node --version), npm $(npm --version)"
else
  NODE_OK=0
  warn "Node.js not found. Needed only to build the dashboard (https://nodejs.org or: brew install node)."
fi

[ "$MODE" = "--check" ] && { ok "Dependency check complete"; exit 0; }

# ---------------------------------------------------------------- backend env
step "Preparing Python environment"
if [ ! -x "$VENV/bin/python" ]; then
  if command -v uv >/dev/null 2>&1; then uv venv --python "$PY" "$VENV" >/dev/null; else "$PY" -m venv "$VENV"; fi
fi
REQ_HASH="$(sha256 backend/requirements.txt)"
if [ "$(cat "$VENV/.req-hash" 2>/dev/null || true)" != "$REQ_HASH" ]; then
  step "Installing backend dependencies (first run can take a few minutes)"
  if command -v uv >/dev/null 2>&1; then
    uv pip install --python "$VENV/bin/python" -r backend/requirements.txt
  else
    "$VENV/bin/python" -m pip install --upgrade pip >/dev/null
    "$VENV/bin/python" -m pip install -r backend/requirements.txt
  fi
  echo "$REQ_HASH" > "$VENV/.req-hash"
fi
ok "backend dependencies ready"

# ---------------------------------------------------------------- face model (free, Apache-2.0, OpenCV Zoo)
if [ ! -f "$YUNET" ] || [ "$(sha256 "$YUNET")" != "$YUNET_SHA" ]; then
  step "Downloading OpenCV YuNet face model (~230 KB)"
  mkdir -p "$(dirname "$YUNET")"
  if curl -fsSL -o "$YUNET.tmp" "$YUNET_URL" && [ "$(sha256 "$YUNET.tmp")" = "$YUNET_SHA" ]; then
    mv "$YUNET.tmp" "$YUNET"; ok "face model installed"
  else
    rm -f "$YUNET.tmp"; warn "Could not download the face model; smart framing will fall back to center crop."
  fi
fi

# ---------------------------------------------------------------- config
if [ ! -f .env ]; then
  cp .env.example .env
  warn "Created .env from .env.example. Add GEMINI_API_KEY there for AI analysis (Demo mode works without it)."
fi
mkdir -p workspace/broll workspace/music logs

# ---------------------------------------------------------------- frontend
if [ "$NODE_OK" = 1 ]; then
  if [ ! -d frontend/node_modules ] || [ frontend/package.json -nt frontend/node_modules ]; then
    step "Installing frontend dependencies"
    npm --prefix frontend install --no-fund --no-audit
  fi
  if [ "$MODE" != "--dev" ]; then
    step "Building dashboard"
    npm --prefix frontend run build >/dev/null
    ok "frontend/dist built"
  fi
elif [ ! -f frontend/dist/index.html ]; then
  die "Node.js is required to build the dashboard the first time."
fi

# ---------------------------------------------------------------- run
PIDS=()
cleanup() { for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done; }
trap cleanup EXIT INT TERM

step "Starting backend on http://127.0.0.1:$PORT"
"$VENV/bin/python" -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port "$PORT" &
PIDS+=($!)

for _ in $(seq 1 60); do
  curl -fs "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1 && break
  sleep 0.5
done
curl -fs "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1 || die "Backend did not start. See logs/app.log"

URL="http://127.0.0.1:$PORT"
if [ "$MODE" = "--dev" ]; then
  npm --prefix frontend run dev -- --host 127.0.0.1 &
  PIDS+=($!)
  URL="http://127.0.0.1:5173"
  sleep 2
fi

printf "\n\033[1;32m  ClipForge AI is running:  %s\033[0m\n  Press Ctrl+C to stop.\n\n" "$URL"
if command -v open >/dev/null 2>&1; then open "$URL"; elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL" >/dev/null 2>&1 || true; fi
wait
