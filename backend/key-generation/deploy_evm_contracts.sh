#!/usr/bin/env bash
# Deploy AccessRegistry + MediLinkFaucet to a chosen EVM network.
#
# Usage:
#   ./deploy_evm_contracts.sh hardhat
#   ./deploy_evm_contracts.sh subtensor-testnet
#   ./deploy_evm_contracts.sh subtensor-mainnet
#   ./deploy_evm_contracts.sh custom          # requires RPC_URL, CHAIN_ID, OWNER_PRIVATE_KEY
#
# Optional env:
#   OWNER_PRIVATE_KEY   deployer H160 key (required for non-hardhat; hardhat has a default)
#   FAUCET_FUND_ETH     native amount to deposit into the faucet after deploy (default: 1 on hardhat, 0 elsewhere)
#   FAUCET_DRIP_AMOUNT / FAUCET_MIN_BALANCE / FAUCET_COOLDOWN  (wei / seconds)
#   SKIP_REGISTRY=1     only deploy faucet
#   SKIP_FAUCET=1       only deploy AccessRegistry
#   PYTHON              python interpreter (default: .venv/bin/python or python3)
#
# Writes (gitignored):
#   deployment.json, faucet.deployment.json, deployment.exports.sh
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PROFILE="${1:-}"
if [[ -z "$PROFILE" || "$PROFILE" == "-h" || "$PROFILE" == "--help" ]]; then
  awk 'NR==1{next} /^#/{sub(/^# ?/,""); print; next} {exit}' "$0"
  exit 0
fi

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

case "$PROFILE" in
  hardhat|local)
    export RPC_URL="${RPC_URL:-http://127.0.0.1:8545}"
    export CHAIN_ID="${CHAIN_ID:-31337}"
    export OWNER_PRIVATE_KEY="${OWNER_PRIVATE_KEY:-0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80}"
    FAUCET_FUND_ETH="${FAUCET_FUND_ETH:-1}"
    ;;
  subtensor-testnet|testnet|945)
    export RPC_URL="${RPC_URL:-https://test.chain.opentensor.ai}"
    export CHAIN_ID="${CHAIN_ID:-945}"
    FAUCET_FUND_ETH="${FAUCET_FUND_ETH:-0}"
    ;;
  subtensor-mainnet|mainnet|964)
    export RPC_URL="${RPC_URL:-https://lite.chain.opentensor.ai}"
    export CHAIN_ID="${CHAIN_ID:-964}"
    FAUCET_FUND_ETH="${FAUCET_FUND_ETH:-0}"
    ;;
  custom)
    FAUCET_FUND_ETH="${FAUCET_FUND_ETH:-0}"
    ;;
  *)
    echo "Unknown profile: $PROFILE" >&2
    echo "Use: hardhat | subtensor-testnet | subtensor-mainnet | custom" >&2
    exit 1
    ;;
esac

if [[ -z "${RPC_URL:-}" || -z "${CHAIN_ID:-}" ]]; then
  echo "RPC_URL and CHAIN_ID are required." >&2
  exit 1
fi
if [[ -z "${OWNER_PRIVATE_KEY:-}" ]]; then
  echo "OWNER_PRIVATE_KEY is required (funded H160 key on chain $CHAIN_ID)." >&2
  exit 1
fi

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
elif [[ -n "${VIRTUAL_ENV:-}" && -x "${VIRTUAL_ENV}/bin/python" ]]; then
  PYTHON="${PYTHON:-$VIRTUAL_ENV/bin/python}"
else
  PYTHON="${PYTHON:-python3}"
fi

if ! "$PYTHON" -c 'import web3, solcx' 2>/dev/null; then
  echo "Missing Python deps (web3, py-solc-x). From $ROOT run:" >&2
  echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

echo "==> Profile: $PROFILE"
echo "    RPC_URL=$RPC_URL"
echo "    CHAIN_ID=$CHAIN_ID"
echo "    Python: $PYTHON"

if [[ "${SKIP_REGISTRY:-0}" != "1" ]]; then
  echo "==> Deploying AccessRegistry"
  "$PYTHON" deploy_access_registry.py
else
  echo "==> Skipping AccessRegistry (SKIP_REGISTRY=1)"
fi

if [[ "${SKIP_FAUCET:-0}" != "1" ]]; then
  echo "==> Deploying MediLinkFaucet"
  "$PYTHON" deploy_medilink_faucet.py
else
  echo "==> Skipping MediLinkFaucet (SKIP_FAUCET=1)"
fi

# Merge faucet address into deployment.exports.sh if both exist.
if [[ -f deployment.exports.sh && -f faucet.deployment.json ]]; then
  FAUCET_ADDRESS="$("$PYTHON" -c 'import json; print(json.load(open("faucet.deployment.json"))["address"])')"
  export FAUCET_ADDRESS
  tmp="$(mktemp)"
  grep -Ev '^export FAUCET_(ADDRESS|OPERATOR_PRIVATE_KEY)=' deployment.exports.sh > "$tmp" || true
  {
    cat "$tmp"
    echo "export FAUCET_ADDRESS=$FAUCET_ADDRESS"
    echo 'export FAUCET_OPERATOR_PRIVATE_KEY=${FAUCET_OPERATOR_PRIVATE_KEY:-$OWNER_PRIVATE_KEY}'
  } > deployment.exports.sh
  rm -f "$tmp"
fi

fund_faucet() {
  local eth_amount="$1"
  [[ -n "${FAUCET_ADDRESS:-}" ]] || FAUCET_ADDRESS="$("$PYTHON" -c 'import json; print(json.load(open("faucet.deployment.json"))["address"])')"
  export FAUCET_ADDRESS
  echo "==> Funding faucet $FAUCET_ADDRESS with ${eth_amount} ETH"
  "$PYTHON" - <<'PY'
import os
from decimal import Decimal
from web3 import Web3

rpc = os.environ["RPC_URL"]
chain_id = int(os.environ["CHAIN_ID"])
key = os.environ["OWNER_PRIVATE_KEY"]
faucet = Web3.to_checksum_address(os.environ["FAUCET_ADDRESS"])
amount = Decimal(os.environ["FAUCET_FUND_ETH"])

w3 = Web3(Web3.HTTPProvider(rpc))
acct = w3.eth.account.from_key(key)
value = w3.to_wei(amount, "ether")
tx = {
    "from": acct.address,
    "to": faucet,
    "value": value,
    "nonce": w3.eth.get_transaction_count(acct.address),
    "gas": 100_000,
    "gasPrice": w3.eth.gas_price,
    "chainId": chain_id,
}
signed = w3.eth.account.sign_transaction(tx, key)
raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
receipt = w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(raw), timeout=180)
if receipt.status != 1:
    raise SystemExit(f"Faucet fund failed: status={receipt.status} tx={receipt.transactionHash.hex()}")
print(f"Funded faucet tx={receipt.transactionHash.hex()} balance={w3.eth.get_balance(faucet)}")
PY
}

if [[ "${SKIP_FAUCET:-0}" != "1" && "${FAUCET_FUND_ETH}" != "0" ]]; then
  export FAUCET_FUND_ETH
  fund_faucet "$FAUCET_FUND_ETH"
fi

echo
echo "==> Done. Source exports for this shell:"
echo "    source $ROOT/deployment.exports.sh"
echo
if [[ -f deployment.json ]]; then
  "$PYTHON" - <<'PY'
import json
from pathlib import Path
d = json.loads(Path("deployment.json").read_text())
print(f"ACCESS_REGISTRY={d.get('address')}")
print(f"FAUCET_ADDRESS={d.get('faucet_address') or ''}")
print(f"RPC_URL={d.get('rpc_url')}")
print(f"CHAIN_ID={d.get('chain_id')}")
print()
print("# Frontend (.env) hints:")
print(f"VITE_ACCESS_REGISTRY={d.get('address')}")
print(f"VITE_EVM_RPC_URL={d.get('rpc_url')}")
print(f"VITE_EVM_CHAIN_ID={d.get('chain_id')}")
PY
fi
