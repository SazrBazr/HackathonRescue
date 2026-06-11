#!/usr/bin/env bash
# =====================================================================
#  run.sh  —  ONE COMMAND to launch the whole RescueGrid demo.
#
#  Usage:   ./run.sh
#  Stop:    press Ctrl+C  (this cleanly kills everything)
#
#  It starts, in order:
#    1. Python triage API   (the brain)        -> port 8000
#    2. C++ localization core (compiles first) -> UDP 5005
#    3. Mock hardware generator                -> feeds the system
#    4. Static web server for the dashboards   -> port 8080
#  Then it prints the links to open in a browser.
# =====================================================================

set -u
cd "$(dirname "$0")"   # always run from the folder this script lives in

# --- Keep track of every process we start, so we can kill them on exit ---
PIDS=()

cleanup() {
    echo ""
    echo "Shutting down all services..."
    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null
    done
    # Belt-and-suspenders: free the ports in case anything lingered.
    pkill -f "uvicorn triage_api" 2>/dev/null
    pkill -f "ingestion_core"      2>/dev/null
    pkill -f "mock_generator.py"   2>/dev/null
    pkill -f "http.server 8080"    2>/dev/null
    echo "Done. All services stopped."
    exit 0
}
trap cleanup INT TERM

echo "=================================================="
echo "   RescueGrid — starting all services"
echo "=================================================="

# --- 0. Make sure old runs aren't still holding the ports ---
pkill -f "uvicorn triage_api" 2>/dev/null
pkill -f "ingestion_core"      2>/dev/null
pkill -f "mock_generator.py"   2>/dev/null
pkill -f "http.server 8080"    2>/dev/null
sleep 1

# --- 1. Triage API (the brain) ---
echo "[1/4] Starting triage API on http://127.0.0.1:8000 ..."
uvicorn triage_api:app --host 127.0.0.1 --port 8000 > triage.log 2>&1 &
PIDS+=($!)
sleep 2

# --- 2. C++ localization core (compile if the binary is missing/older) ---
if [ ! -f ./ingestion_core ] || [ ingestion_core.cpp -nt ./ingestion_core ]; then
    echo "[2/4] Compiling C++ localization core..."
    if ! g++ ingestion_core.cpp -o ingestion_core -Ilib -lcurl -pthread; then
        echo "  ERROR: C++ failed to compile. Is libcurl installed?"
        echo "  Run: sudo apt install libcurl4-openssl-dev"
        cleanup
    fi
fi
echo "[2/4] Starting C++ localization core (UDP 5005)..."
./ingestion_core > core.log 2>&1 &
PIDS+=($!)
sleep 1

# --- 3. Mock hardware generator ---
echo "[3/4] Starting mock hardware feed..."
python3 mock_generator.py > mock.log 2>&1 &
PIDS+=($!)
sleep 1

# --- 4. Static web server for the dashboards ---
echo "[4/4] Starting dashboard web server on http://localhost:8080 ..."
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
echo "   Logs are in: triage.log, core.log, mock.log, web.log"
echo "   Press Ctrl+C here to stop EVERYTHING."
echo "=================================================="

# Try to auto-open the commander dashboard in a browser (best effort).
( command -v explorer.exe >/dev/null 2>&1 && explorer.exe "http://localhost:8080/Commander.html" ) 2>/dev/null \
  || ( command -v xdg-open >/dev/null 2>&1 && xdg-open "http://localhost:8080/Commander.html" ) 2>/dev/null \
  || true

# --- Wait forever (until Ctrl+C) so the background jobs keep running ---
wait
