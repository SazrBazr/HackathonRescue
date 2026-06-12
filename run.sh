#!/usr/bin/env bash
# =====================================================================
#  run.sh  —  ONE COMMAND to launch the whole RescueGrid demo.
#
#  Usage:   ./run.sh
#  Stop:    press Ctrl+C  (cleanly kills everything)
# =====================================================================

set -u
cd "$(dirname "$0")"

PIDS=()

cleanup() {
    echo ""
    echo "Shutting down all services..."
    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null
    done
    pkill -f "uvicorn triage_api" 2>/dev/null
    pkill -f "ingestion_core"      2>/dev/null
    pkill -f "mock_generator.py"   2>/dev/null
    pkill -f "http.server 8080"    2>/dev/null
    echo "Done. All services stopped."
    exit 0
}
trap cleanup INT TERM

echo "=================================================="
echo "   RESCUE GRID: PRE-FLIGHT CHECK"
echo "=================================================="


# -----------------------------------------------------------------------
# Helper: check a TCP port is free before we try to bind it.
# -----------------------------------------------------------------------
check_port_free() {
    local port=$1
    if ss -tlnp 2>/dev/null | grep -q ":${port} " || \
       netstat -tlnp 2>/dev/null | grep -q ":${port} "; then
        echo "  -> ERROR: port ${port} is already in use. Kill the process on it and retry."
        cleanup
    fi
}

# -----------------------------------------------------------------------
# 0a. System packages: g++, libcurl, python3-venv
# -----------------------------------------------------------------------
echo "[-] Checking system dependencies..."

NEED_APT=()
command -v g++      >/dev/null 2>&1 || NEED_APT+=("g++")
pkg-config --exists libcurl 2>/dev/null || \
    dpkg -l libcurl4-openssl-dev 2>/dev/null | grep -q "^ii" || \
    NEED_APT+=("libcurl4-openssl-dev")
python3 -c "import venv" 2>/dev/null || NEED_APT+=("python3-venv")

if [ "${#NEED_APT[@]}" -gt 0 ]; then
    echo "[!] Missing system packages: ${NEED_APT[*]}"
    if command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update -qq
        sudo apt-get install -y "${NEED_APT[@]}"
    elif command -v brew >/dev/null 2>&1; then
        # macOS fallback (maps apt names to brew names)
        for pkg in "${NEED_APT[@]}"; do
            case "$pkg" in
                g++)                  brew install gcc ;;
                libcurl4-openssl-dev) brew install curl ;;
                python3-venv)         brew install python3 ;;
            esac
        done
    else
        echo "  -> ERROR: No supported package manager found (apt-get / brew)."
        echo "     Install manually: ${NEED_APT[*]}"
        exit 1
    fi
fi
echo "  -> System dependencies OK."


# -----------------------------------------------------------------------
# 0b. nlohmann/json single-header (lib/json.hpp) — download if missing
# -----------------------------------------------------------------------
if [ ! -f "lib/json.hpp" ]; then
    echo "[-] lib/json.hpp not found. Downloading nlohmann/json..."
    mkdir -p lib
    if command -v curl >/dev/null 2>&1; then
        curl -sSL "https://github.com/nlohmann/json/releases/latest/download/json.hpp" \
             -o lib/json.hpp
    elif command -v wget >/dev/null 2>&1; then
        wget -q "https://github.com/nlohmann/json/releases/latest/download/json.hpp" \
             -O lib/json.hpp
    else
        echo "  -> ERROR: Neither curl nor wget found. Cannot download json.hpp."
        exit 1
    fi
    echo "  -> lib/json.hpp downloaded."
fi


# -----------------------------------------------------------------------
# 0c. Python virtual environment + pip packages (WSL/Ubuntu Safe)
# -----------------------------------------------------------------------
echo "[-] Setting up Python virtual environment..."

# 1. Clean up broken venv from previous failed attempts (like missing ensurepip)
if [ -d ".venv" ] && [ ! -f ".venv/bin/activate" ]; then
    echo "[!] Cleaning up broken virtual environment..."
    rm -rf .venv
fi

# 2. Attempt to create it; if it fails, install the exact version of venv needed
if [ ! -d ".venv" ]; then
    if ! python3 -m venv .venv >/dev/null 2>&1; then
        echo "[!] venv missing ensurepip. Installing system dependencies..."
        if command -v apt-get >/dev/null 2>&1; then
            # Dynamically grab the Python version (e.g., "3.10") to install the exact matching package
            PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
            sudo apt-get update -qq && sudo apt-get install -y "python${PY_VER}-venv"
        elif command -v brew >/dev/null 2>&1; then
            brew install python3
        fi
        
        # Retry creation
        python3 -m venv .venv
    fi
fi

# 3. Hard abort if it STILL failed so the script doesn't blindly continue
if [ ! -f ".venv/bin/activate" ]; then
    echo "  -> ERROR: Virtual environment creation failed. Cannot proceed."
    exit 1
fi

# 4. Activate and install (removed --quiet so you can see if it fails)
source .venv/bin/activate
echo "[-] Installing Python packages (uvicorn, fastapi, pydantic)..."
pip install -r requirements.txt
echo "  -> Python packages OK."


# -----------------------------------------------------------------------
# 0d. Compile C++ localization core
# -----------------------------------------------------------------------
echo "[-] Compiling C++ localization core..."
if ! g++ ingestion_core.cpp -o ingestion_core -Ilib -lcurl -pthread -O2; then
    echo "  -> ERROR: C++ compilation failed. Check the output above."
    cleanup
fi
echo "  -> C++ core compiled successfully."
echo ""


# -----------------------------------------------------------------------
# Pre-flight port checks
# -----------------------------------------------------------------------
check_port_free 8000
check_port_free 8080


# -----------------------------------------------------------------------
# 1. Python triage API
# -----------------------------------------------------------------------
echo "[1/4] Starting Python triage API (port 8000)..."
python3 -m uvicorn triage_api:app --host 0.0.0.0 --port 8000 > triage.log 2>&1 &
PIDS+=($!)

# Wait until uvicorn is actually accepting connections (up to 10s)
echo "      Waiting for API to come up..."
for i in $(seq 1 10); do
    sleep 1
    if curl -sf http://127.0.0.1:8000/state > /dev/null 2>&1; then
        echo "      -> API up (${i}s)."
        break
    fi
    if [ "$i" -eq 10 ]; then
        echo "  -> ERROR: triage API didn't come up in 10s. Check triage.log."
        cleanup
    fi
done


# -----------------------------------------------------------------------
# 2. C++ localization core
# -----------------------------------------------------------------------
echo "[2/4] Starting C++ localization core (UDP 5005)..."
./ingestion_core > core.log 2>&1 &
PIDS+=($!)
sleep 1


# -----------------------------------------------------------------------
# 3. Mock hardware generator
# -----------------------------------------------------------------------
echo "[3/4] Starting mock hardware feed..."
python3 mock_generator.py > mock.log 2>&1 &
PIDS+=($!)
sleep 1


# -----------------------------------------------------------------------
# 4. Static web server for dashboards
# -----------------------------------------------------------------------
echo "[4/4] Starting dashboard web server (port 8080)..."
python3 -m http.server 8080 > web.log 2>&1 &
PIDS+=($!)
sleep 1


echo ""
echo "=================================================="
echo "   ALL SERVICES RUNNING"
echo "=================================================="
echo "   Commander dashboard:  http://localhost:8080/Commander.html"
echo "   Team (field) app:     http://localhost:8080/team.html"
echo ""
echo "   Logs:  triage.log  |  core.log  |  mock.log  |  web.log"
echo "   Press Ctrl+C to stop EVERYTHING."
echo "=================================================="

# Best-effort: auto-open browser
if command -v open >/dev/null 2>&1; then
    open "http://localhost:8080/Commander.html"
elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "http://localhost:8080/Commander.html" &
fi

wait
