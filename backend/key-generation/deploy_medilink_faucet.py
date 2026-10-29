#!/usr/bin/env python3
"""
Compile and deploy MediLinkFaucet.sol to Subtensor EVM (or local Hardhat).

Usage:
    export RPC_URL=http://127.0.0.1:8545
    export CHAIN_ID=31337
    export OWNER_PRIVATE_KEY=0x...
    # Optional tuning (wei):
    # export FAUCET_DRIP_AMOUNT=10000000000000000   # 0.01 ETH
    # export FAUCET_MIN_BALANCE=5000000000000000    # 0.005 ETH
    # export FAUCET_COOLDOWN=86400                  # 24h
    python deploy_medilink_faucet.py

Merges FAUCET_ADDRESS into deployment.json when present; also writes faucet.deployment.json.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from web3 import Web3

try:
    from web3.middleware import ExtraDataToPOAMiddleware
except ImportError:  # web3<7
    from web3.middleware import geth_poa_middleware as ExtraDataToPOAMiddleware  # type: ignore

ROOT = Path(__file__).resolve().parent
CONTRACTS = ROOT.parent / "contracts"
SOL_PATH = CONTRACTS / "MediLinkFaucet.sol"
OUT_PATH = ROOT / "faucet.deployment.json"
REGISTRY_OUT = ROOT / "deployment.json"

RPC_URL = os.getenv("RPC_URL", "https://test.chain.opentensor.ai")
CHAIN_ID = int(os.getenv("CHAIN_ID", "945"))
OWNER_PRIVATE_KEY = os.getenv("OWNER_PRIVATE_KEY")
SOLC_VERSION = os.getenv("SOLC_VERSION", "0.8.20")

# Defaults: drip 0.01 ETH when balance < 0.005 ETH; 24h cooldown.
DRIP_AMOUNT = int(os.getenv("FAUCET_DRIP_AMOUNT", str(10**16)))
MIN_BALANCE = int(os.getenv("FAUCET_MIN_BALANCE", str(5 * 10**15)))
COOLDOWN = int(os.getenv("FAUCET_COOLDOWN", "86400"))


def compile_contract() -> tuple[str, list]:
    try:
        from solcx import compile_source, install_solc, set_solc_version
    except ImportError as exc:
        raise SystemExit(
            "py-solc-x is required. Install with:\n"
            "  pip install 'py-solc-x>=2.0.0'\n"
            f"({exc})"
        ) from exc

    source = SOL_PATH.read_text()
    print(f"Compiling {SOL_PATH.name} with solc {SOLC_VERSION}...")
    install_solc(SOLC_VERSION)
    set_solc_version(SOLC_VERSION)
    compiled = compile_source(
        source,
        output_values=["abi", "bin"],
        solc_version=SOLC_VERSION,
    )
    contract_id, interface = next(
        (k, v) for k, v in compiled.items() if k.endswith(":MediLinkFaucet")
    )
    bytecode = interface["bin"]
    abi = interface["abi"]
    if not bytecode:
        raise RuntimeError(f"Empty bytecode for {contract_id}")
    print(f"Compiled {contract_id} ({len(bytecode) // 2} bytes bytecode)")
    return bytecode, abi


def get_w3() -> Web3:
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    if CHAIN_ID in (945, 964) and ExtraDataToPOAMiddleware is not None:
        w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    if not w3.is_connected():
        raise ConnectionError(f"Cannot connect to EVM RPC {RPC_URL}")
    return w3


def deploy(bytecode: str, abi: list) -> dict:
    if not OWNER_PRIVATE_KEY:
        raise SystemExit(
            "OWNER_PRIVATE_KEY is required (funded H160 private key on the target chain).\n"
            "Example: export OWNER_PRIVATE_KEY=0x..."
        )

    w3 = get_w3()
    account = w3.eth.account.from_key(OWNER_PRIVATE_KEY)
    balance = w3.eth.get_balance(account.address)
    print(f"Deployer: {account.address}")
    print(f"Balance:  {w3.from_wei(balance, 'ether')} ETH/TAO-EVM")
    print(f"RPC:      {RPC_URL}")
    print(f"Chain ID: {CHAIN_ID}")
    print(f"dripAmount={DRIP_AMOUNT} minBalance={MIN_BALANCE} cooldown={COOLDOWN}")

    if balance == 0:
        raise SystemExit(
            "Deployer balance is zero. Fund this address on the target EVM chain first.\n"
            f"  chain {CHAIN_ID} address {account.address}"
        )

    contract = w3.eth.contract(abi=abi, bytecode=bytecode)
    tx = contract.constructor(DRIP_AMOUNT, MIN_BALANCE, COOLDOWN).build_transaction(
        {
            "from": account.address,
            "nonce": w3.eth.get_transaction_count(account.address),
            "gas": 1_500_000,
            "gasPrice": w3.eth.gas_price,
            "chainId": CHAIN_ID,
        }
    )
    signed = w3.eth.account.sign_transaction(tx, OWNER_PRIVATE_KEY)
    raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
    tx_hash = w3.eth.send_raw_transaction(raw)
    print(f"Submitted deploy tx: {tx_hash.hex()}")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
    if receipt.status != 1:
        raise RuntimeError(f"Deploy failed: status={receipt.status} tx={tx_hash.hex()}")

    address = Web3.to_checksum_address(receipt.contractAddress)
    print(f"Deployed MediLinkFaucet at {address}")

    payload = {
        "contract": "MediLinkFaucet",
        "address": address,
        "chain_id": CHAIN_ID,
        "rpc_url": RPC_URL,
        "deployer": account.address,
        "tx_hash": tx_hash.hex(),
        "block_number": receipt.blockNumber,
        "drip_amount": DRIP_AMOUNT,
        "min_balance": MIN_BALANCE,
        "cooldown": COOLDOWN,
        "abi": abi,
    }
    OUT_PATH.write_text(json.dumps(payload, indent=2))
    print(f"Wrote {OUT_PATH}")

    if REGISTRY_OUT.exists():
        try:
            merged = json.loads(REGISTRY_OUT.read_text())
        except json.JSONDecodeError:
            merged = {}
        merged["faucet_address"] = address
        merged["faucet_abi"] = abi
        REGISTRY_OUT.write_text(json.dumps(merged, indent=2))
        print(f"Updated {REGISTRY_OUT} with faucet_address")

    exports_path = ROOT / "deployment.exports.sh"
    existing = exports_path.read_text() if exports_path.exists() else ""
    lines = [ln for ln in existing.splitlines() if not ln.startswith("export FAUCET_ADDRESS=")]
    lines.append(f"export FAUCET_ADDRESS={address}")
    if "export FAUCET_OPERATOR_PRIVATE_KEY=" not in existing:
        lines.append("# export FAUCET_OPERATOR_PRIVATE_KEY=...  # defaults to OWNER_PRIVATE_KEY")
    exports_path.write_text("\n".join(lines).rstrip() + "\n")
    print(f"Updated {exports_path}")
    print()
    print(f"export FAUCET_ADDRESS={address}")
    return payload


def main() -> int:
    if not SOL_PATH.exists():
        print(f"Missing {SOL_PATH}", file=sys.stderr)
        return 1
    bytecode, abi = compile_contract()
    deploy(bytecode, abi)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
