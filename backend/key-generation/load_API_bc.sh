#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
# Server-only keys may be kept in this untracked file. Never use a VITE_ prefix.
if [ -f .env ]; then
  set -a
  source .env
  set +a
fi
export SUPABASE_URL="${SUPABASE_URL:-https://cwyxwmzquatqlbufsdob.supabase.co}"
export SUPABASE_PUBLISHABLE_KEY="${SUPABASE_PUBLISHABLE_KEY:-sb_publishable_7NtcuZPUT618En1FAYVAVA_K0mBwNW9}"
export GRANT_DATA_DIR="$PWD/.grant-supabase-local"
# Generate once and retain this key across API restarts.
(umask 077; mkdir -p "$GRANT_DATA_DIR"; if [ ! -f "$GRANT_DATA_DIR/master.key" ]; then
  .venv/bin/python -c 'import os, pathlib; p = pathlib.Path(os.environ["GRANT_DATA_DIR"]) / "master.key"; p.write_bytes(os.urandom(32))'
fi)
export GRANT_MASTER_KEY="$(.venv/bin/python -c 'import os, pathlib; print((pathlib.Path(os.environ["GRANT_DATA_DIR"]) / "master.key").read_bytes().hex())')"
# Local Hardhat operator only; these public test credentials must never hold real funds.
export RPC_URL="${RPC_URL:-http://127.0.0.1:8545}"
export OWNER_PRIVATE_KEY="${OWNER_PRIVATE_KEY:-0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80}"
export FUND_LOCAL_WALLETS="${FUND_LOCAL_WALLETS:-1}"
# Optional: set FAUCET_ADDRESS after deploy_medilink_faucet.py (see markdowns/Faucet.md).
# Operator key defaults to OWNER_PRIVATE_KEY when FAUCET_OPERATOR_PRIVATE_KEY is unset.
if [ -f faucet.deployment.json ] && [ -z "${FAUCET_ADDRESS:-}" ]; then
  FAUCET_ADDRESS="$(.venv/bin/python -c 'import json; print(json.load(open("faucet.deployment.json")).get("address",""))')"
  export FAUCET_ADDRESS
fi
if [ -f deployment.json ] && [ -z "${ACCESS_REGISTRY:-}" ]; then
  ACCESS_REGISTRY="$(.venv/bin/python -c 'import json; print(json.load(open("deployment.json")).get("address",""))')"
  export ACCESS_REGISTRY
fi
if [ -n "${ACCESS_REGISTRY:-}" ]; then
  export CONTRACT_ADDRESS="${CONTRACT_ADDRESS:-$ACCESS_REGISTRY}"
  export REGISTRY_ADDRESS="${REGISTRY_ADDRESS:-$ACCESS_REGISTRY}"
fi
exec .venv/bin/python grant_api.py --chain --auto-data-dir
