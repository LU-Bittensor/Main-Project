#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
export RPC_URL=http://127.0.0.1:8545
export OWNER_PRIVATE_KEY=0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80
export FUND_LOCAL_WALLETS=1
# This is a parent directory: the API selects a child for this Hardhat instance/build.
export GRANT_DATA_DIR="${GRANT_DATA_DIR:-$PWD/.grant-pairs-local}"
exec .venv/bin/python grant_api.py --dev --chain --auto-data-dir
