#!/usr/bin/env bash
# Start the local blockchain and real-account API; Ctrl+C stops owned processes.
set -euo pipefail
BACKEND_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
API_DIR="$BACKEND_DIR/key-generation"
CHAIN_DIR="$API_DIR/testContractDeployment"
CHAIN_PID=""
API_PID=""

cleanup() {
  trap - EXIT INT TERM
  for pid in "$API_PID" "$CHAIN_PID"; do
    if [ -n "$pid" ]; then kill "$pid" 2>/dev/null || true; fi
  done
  for pid in "$API_PID" "$CHAIN_PID"; do
    if [ -n "$pid" ]; then wait "$pid" 2>/dev/null || true; fi
  done
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

command -v node >/dev/null || { echo "Install Node.js and npm first." >&2; exit 1; }
if [ ! -x "$API_DIR/.venv/bin/python" ]; then
  python3 -m venv "$API_DIR/.venv"
fi
PYTHON="$API_DIR/.venv/bin/python"
if ! "$PYTHON" -c 'import flask, requests, web3, cryptography, pytest' 2>/dev/null; then
  "$PYTHON" -m pip install -r "$API_DIR/requirements.txt"
fi
if [ ! -f "$CHAIN_DIR/node_modules/hardhat/internal/cli/cli.js" ]; then
  (cd "$CHAIN_DIR" && npm ci)
fi

# Match the API launcher's configuration without printing credentials.
if [ -f "$API_DIR/.env" ]; then
  set -a
  source "$API_DIR/.env"
  set +a
fi
case "${RPC_URL:-http://127.0.0.1:8545}" in
  http://127.0.0.1:8545|http://localhost:8545) ;;
  *) echo "This launcher uses local Hardhat on port 8545. For a custom RPC_URL, use key-generation/load_API_bc.sh directly." >&2; exit 1 ;;
esac

probe() {
  "$PYTHON" - "$1" <<'PY'
import json, socket, sys, urllib.request
mode = sys.argv[1]
try:
    if mode in ('port', 'api-port'):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 8545 if mode == 'port' else 5001))
    elif mode == 'chain':
        def rpc(method):
            payload = json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':[]}).encode()
            req = urllib.request.Request('http://127.0.0.1:8545', data=payload, headers={'Content-Type':'application/json'})
            with urllib.request.urlopen(req, timeout=1) as response:
                return json.load(response).get('result')
        assert rpc('eth_chainId') == '0x7a69'
        assert rpc('hardhat_metadata')['instanceId']
    else:
        with urllib.request.urlopen('http://127.0.0.1:5001/api/health', timeout=1) as response:
            health = json.load(response)
        assert health['mode'] == 'supabase' and health['chain'] is True
except Exception:
    sys.exit(1)
PY
}

if ! probe api-port; then
  echo "Port 5001 is already in use. Stop the existing API before starting this launcher." >&2
  exit 1
fi
(cd "$CHAIN_DIR" && node node_modules/hardhat/internal/cli/cli.js compile)
if probe chain; then
  echo "Reusing existing Hardhat node on port 8545 (it will be left running on exit)."
elif probe port; then
  echo "Starting Hardhat on http://127.0.0.1:8545"
  (cd "$CHAIN_DIR" && exec node node_modules/hardhat/internal/cli/cli.js node --hostname 127.0.0.1) &
  CHAIN_PID=$!
else
  echo "Port 8545 is occupied by a service that is not a compatible local Hardhat node." >&2
  exit 1
fi

wait_ready() {
  local service="$1" pid="$2" attempt
  for ((attempt=0; attempt<60; attempt++)); do
    if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
      echo "$service exited before becoming ready. See the output above." >&2
      return 1
    fi
    if probe "$service"; then return 0; fi
    sleep 1
  done
  echo "Timed out waiting for $service." >&2
  return 1
}
wait_ready chain "$CHAIN_PID"
echo "Starting Python API on http://127.0.0.1:5001"
bash "$API_DIR/load_API_bc.sh" &
API_PID=$!
wait_ready api "$API_PID"
echo "Backend ready. Start the frontend in another terminal. Press Ctrl+C to stop the backend."
while kill -0 "$API_PID" 2>/dev/null; do
  if [ -n "$CHAIN_PID" ] && ! kill -0 "$CHAIN_PID" 2>/dev/null; then
    echo "Hardhat stopped; shutting down the API." >&2
    exit 1
  fi
  sleep 1
done
wait "$API_PID"
