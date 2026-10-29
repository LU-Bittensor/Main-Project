"""
MediLinkFaucet client: check balances and operator-drip enrolled user H160s.

Env:
  RPC_URL / CHAIN_ID
  FAUCET_ADDRESS
  FAUCET_OPERATOR_PRIVATE_KEY  (defaults to OWNER_PRIVATE_KEY)
  FAUCET_MIN_BALANCE           (wei; default 0.005 ETH) — client-side gate before calling drip
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from web3 import Web3

try:
    from web3.middleware import ExtraDataToPOAMiddleware
except ImportError:  # web3<7
    from web3.middleware import geth_poa_middleware as ExtraDataToPOAMiddleware  # type: ignore

ROOT = Path(__file__).resolve().parent
# Deploy artifacts live next to the API; Solidity source is under backend/contracts/.
DEPLOY_SEARCH_PATHS = (
    ROOT / "faucet.deployment.json",
    ROOT / "deployment.json",
)

RPC_URL = os.getenv("RPC_URL", "http://127.0.0.1:8545")
CHAIN_ID = int(os.getenv("CHAIN_ID", os.getenv("EVM_CHAIN_ID", "31337")))
FAUCET_ADDRESS = os.getenv("FAUCET_ADDRESS", "")
OPERATOR_KEY = os.getenv("FAUCET_OPERATOR_PRIVATE_KEY") or os.getenv("OWNER_PRIVATE_KEY") or ""
# Client-side threshold (should match or be >= on-chain minBalance).
MIN_BALANCE = int(os.getenv("FAUCET_MIN_BALANCE", str(5 * 10**15)))

FAUCET_ABI = [
    {
        "inputs": [{"internalType": "address", "name": "to", "type": "address"}],
        "name": "drip",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "dripAmount",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "minBalance",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "cooldown",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "address", "name": "", "type": "address"}],
        "name": "lastDripTime",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "operator",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
]


def _load_abi() -> list:
    for path in DEPLOY_SEARCH_PATHS:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        if path.name == "faucet.deployment.json" and data.get("abi"):
            return data["abi"]
        if data.get("faucet_abi"):
            return data["faucet_abi"]
    return FAUCET_ABI


def get_w3() -> Web3:
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    if CHAIN_ID in (945, 964) and ExtraDataToPOAMiddleware is not None:
        w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    if not w3.is_connected():
        raise ConnectionError(f"Cannot connect to EVM RPC {RPC_URL}")
    return w3


def resolve_faucet_address() -> str:
    if FAUCET_ADDRESS:
        return Web3.to_checksum_address(FAUCET_ADDRESS)
    for path in DEPLOY_SEARCH_PATHS:
        if not path.exists():
            continue
        data = json.loads(path.read_text())
        addr = data.get("address") if data.get("contract") == "MediLinkFaucet" else data.get("faucet_address")
        if addr:
            return Web3.to_checksum_address(addr)
    raise RuntimeError("FAUCET_ADDRESS is not set and no faucet deployment.json was found")


def faucet_contract(w3: Web3 | None = None):
    w3 = w3 or get_w3()
    return w3.eth.contract(address=resolve_faucet_address(), abi=_load_abi())


def get_balance(address: str, w3: Web3 | None = None) -> int:
    w3 = w3 or get_w3()
    return int(w3.eth.get_balance(Web3.to_checksum_address(address)))


def on_chain_min_balance(w3: Web3 | None = None) -> int:
    w3 = w3 or get_w3()
    return int(faucet_contract(w3).functions.minBalance().call())


def needs_drip(address: str, w3: Web3 | None = None) -> bool:
    """True when the address balance is below the client/on-chain minBalance threshold."""
    w3 = w3 or get_w3()
    balance = get_balance(address, w3)
    try:
        threshold = on_chain_min_balance(w3)
    except Exception:
        threshold = MIN_BALANCE
    return balance < threshold


def drip(address: str, w3: Web3 | None = None) -> str:
    """Operator-signed drip(to). Returns tx hash hex."""
    if not OPERATOR_KEY:
        raise RuntimeError("FAUCET_OPERATOR_PRIVATE_KEY or OWNER_PRIVATE_KEY is required to drip")
    w3 = w3 or get_w3()
    account = w3.eth.account.from_key(OPERATOR_KEY)
    to = Web3.to_checksum_address(address)
    contract = faucet_contract(w3)
    tx = contract.functions.drip(to).build_transaction(
        {
            "from": account.address,
            "nonce": w3.eth.get_transaction_count(account.address),
            "gas": 200_000,
            "gasPrice": w3.eth.gas_price,
            "chainId": CHAIN_ID,
        }
    )
    signed = w3.eth.account.sign_transaction(tx, OPERATOR_KEY)
    raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
    tx_hash = w3.eth.send_raw_transaction(raw)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
    if receipt.status != 1:
        raise RuntimeError(f"Faucet drip failed: status={receipt.status} tx={tx_hash.hex()}")
    return tx_hash.hex()


def ensure_funded(address: str, w3: Web3 | None = None) -> dict[str, Any]:
    """
    If address needs gas, call faucet.drip. Returns status for API responses.
    """
    w3 = w3 or get_w3()
    to = Web3.to_checksum_address(address)
    balance = get_balance(to, w3)
    if not needs_drip(to, w3):
        return {"address": to, "balance": balance, "dripped": False, "txHash": None}
    tx_hash = drip(to, w3)
    return {
        "address": to,
        "balance": get_balance(to, w3),
        "dripped": True,
        "txHash": tx_hash,
    }


def faucet_status(w3: Web3 | None = None) -> dict[str, Any]:
    w3 = w3 or get_w3()
    contract = faucet_contract(w3)
    address = resolve_faucet_address()
    return {
        "address": address,
        "dripAmount": int(contract.functions.dripAmount().call()),
        "minBalance": int(contract.functions.minBalance().call()),
        "cooldown": int(contract.functions.cooldown().call()),
        "operator": contract.functions.operator().call(),
        "balance": int(w3.eth.get_balance(address)),
        "chainId": CHAIN_ID,
        "rpcUrl": RPC_URL,
    }


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print(json.dumps(faucet_status(), indent=2))
    elif sys.argv[1] == "ensure" and len(sys.argv) == 3:
        print(json.dumps(ensure_funded(sys.argv[2]), indent=2))
    else:
        print("Usage: python faucet_client.py [ensure 0xAddress]", file=sys.stderr)
        raise SystemExit(1)
