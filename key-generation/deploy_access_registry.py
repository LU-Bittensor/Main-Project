#!/usr/bin/env python3
"""
Compile and deploy AccessRegistry.sol to Subtensor EVM (or local Hardhat).

Usage:
    export RPC_URL=https://test.chain.opentensor.ai
    export CHAIN_ID=945
    export OWNER_PRIVATE_KEY=0x...   # funded H160 key on that chain
    python key-generation/deploy_access_registry.py

Outputs:
    key-generation/deployment.json   — address, chain_id, rpc, abi, tx hash
    Prints shell exports for CONTRACT_ADDRESS / ACCESS_REGISTRY
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
SOL_PATH = ROOT / "AccessRegistry.sol"
OUT_PATH = ROOT / "deployment.json"

RPC_URL = os.getenv("RPC_URL", "https://test.chain.opentensor.ai")
CHAIN_ID = int(os.getenv("CHAIN_ID", "945"))
OWNER_PRIVATE_KEY = os.getenv("OWNER_PRIVATE_KEY")
SOLC_VERSION = os.getenv("SOLC_VERSION", "0.8.20")


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
    # Key looks like "<stdin>:AccessRegistry"
    contract_id, interface = next(
        (k, v) for k, v in compiled.items() if k.endswith(":AccessRegistry")
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

    if balance == 0:
        raise SystemExit(
            "Deployer balance is zero. Fund this address on the target EVM chain first.\n"
            f"  chain {CHAIN_ID} address {account.address}"
        )

    contract = w3.eth.contract(abi=abi, bytecode=bytecode)
    # AccessRegistry has an empty constructor
    tx = contract.constructor().build_transaction(
        {
            "from": account.address,
            "nonce": w3.eth.get_transaction_count(account.address),
            "gas": 2_500_000,
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

    address = receipt.contractAddress
    print(f"Deployed AccessRegistry at {address}")

    payload = {
        "contract": "AccessRegistry",
        "address": Web3.to_checksum_address(address),
        "chain_id": CHAIN_ID,
        "rpc_url": RPC_URL,
        "deployer": account.address,
        "tx_hash": tx_hash.hex(),
        "block_number": receipt.blockNumber,
        "abi": abi,
    }
    OUT_PATH.write_text(json.dumps(payload, indent=2))
    exports_path = ROOT / "deployment.exports.sh"
    exports_path.write_text(
        "\n".join(
            [
                f"export CONTRACT_ADDRESS={payload['address']}",
                f"export ACCESS_REGISTRY={payload['address']}",
                f"export EVM_RPC_URL={RPC_URL}",
                f"export RPC_URL={RPC_URL}",
                f"export EVM_CHAIN_ID={CHAIN_ID}",
                f"export CHAIN_ID={CHAIN_ID}",
                f"export EVM_START_BLOCK={max(0, receipt.blockNumber - 1)}",
                "",
            ]
        )
    )
    print(f"Wrote {OUT_PATH}")
    print(f"Wrote {exports_path} (run: source key-generation/deployment.exports.sh)")
    print()
    print("# Shell exports for client + validator:")
    print(f"export CONTRACT_ADDRESS={payload['address']}")
    print(f"export ACCESS_REGISTRY={payload['address']}")
    print(f"export EVM_RPC_URL={RPC_URL}")
    print(f"export RPC_URL={RPC_URL}")
    print(f"export EVM_CHAIN_ID={CHAIN_ID}")
    print(f"export CHAIN_ID={CHAIN_ID}")
    print(f"export EVM_START_BLOCK={max(0, receipt.blockNumber - 1)}")
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
