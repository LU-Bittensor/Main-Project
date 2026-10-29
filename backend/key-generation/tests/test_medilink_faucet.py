"""Compile MediLinkFaucet and exercise drip / cooldown / auth against a local EVM if available."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOL_PATH = ROOT.parent / "contracts" / "MediLinkFaucet.sol"

RPC_URL = os.getenv("RPC_URL", "http://127.0.0.1:8545")
# Hardhat account #0
OWNER_KEY = os.getenv(
    "OWNER_PRIVATE_KEY",
    "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
)
USER_KEY = os.getenv(
    "USER_PRIVATE_KEY",
    "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d",
)


def _compile():
    from solcx import compile_source, install_solc, set_solc_version

    install_solc("0.8.20")
    set_solc_version("0.8.20")
    compiled = compile_source(SOL_PATH.read_text(), output_values=["abi", "bin"], solc_version="0.8.20")
    _, interface = next((k, v) for k, v in compiled.items() if k.endswith(":MediLinkFaucet"))
    return interface["bin"], interface["abi"]


def _w3():
    from web3 import Web3

    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    if not w3.is_connected():
        pytest.skip(f"No EVM at {RPC_URL}; start `npx hardhat node` to run live faucet tests")
    return w3


@pytest.fixture(scope="module")
def faucet_setup():
    try:
        bytecode, abi = _compile()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"solc unavailable: {exc}")

    w3 = _w3()
    owner = w3.eth.account.from_key(OWNER_KEY)
    user = w3.eth.account.from_key(USER_KEY)
    stranger = w3.eth.account.create()

    drip_amount = w3.to_wei(0.01, "ether")
    min_balance = w3.to_wei(0.005, "ether")
    cooldown = 2  # short for tests

    contract = w3.eth.contract(abi=abi, bytecode=bytecode)
    tx = contract.constructor(drip_amount, min_balance, cooldown).build_transaction(
        {
            "from": owner.address,
            "nonce": w3.eth.get_transaction_count(owner.address),
            "gas": 1_500_000,
            "gasPrice": w3.eth.gas_price,
            "chainId": w3.eth.chain_id,
        }
    )
    signed = w3.eth.account.sign_transaction(tx, OWNER_KEY)
    raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
    receipt = w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(raw))
    assert receipt.status == 1
    address = receipt.contractAddress
    faucet = w3.eth.contract(address=address, abi=abi)

    # Fund faucet
    fund = {
        "from": owner.address,
        "to": address,
        "value": w3.to_wei(1, "ether"),
        "nonce": w3.eth.get_transaction_count(owner.address),
        "gas": 100_000,
        "gasPrice": w3.eth.gas_price,
        "chainId": w3.eth.chain_id,
    }
    signed_fund = w3.eth.account.sign_transaction(fund, OWNER_KEY)
    raw_fund = getattr(signed_fund, "raw_transaction", None) or signed_fund.rawTransaction
    w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(raw_fund))

    return {
        "w3": w3,
        "faucet": faucet,
        "owner": owner,
        "user": user,
        "stranger": stranger,
        "owner_key": OWNER_KEY,
        "drip_amount": drip_amount,
        "min_balance": min_balance,
        "cooldown": cooldown,
        "abi": abi,
    }


def _send(w3, tx, key):
    signed = w3.eth.account.sign_transaction(tx, key)
    raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
    return w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(raw))


def test_drip_funds_user(faucet_setup):
    s = faucet_setup
    w3, faucet, owner, user = s["w3"], s["faucet"], s["owner"], s["user"]
    before = w3.eth.get_balance(user.address)
    tx = faucet.functions.drip(user.address).build_transaction(
        {
            "from": owner.address,
            "nonce": w3.eth.get_transaction_count(owner.address),
            "gas": 200_000,
            "gasPrice": w3.eth.gas_price,
            "chainId": w3.eth.chain_id,
        }
    )
    receipt = _send(w3, tx, s["owner_key"])
    assert receipt.status == 1
    after = w3.eth.get_balance(user.address)
    assert after - before == s["drip_amount"]


def test_cooldown_and_threshold(faucet_setup):
    s = faucet_setup
    w3, faucet, owner, user = s["w3"], s["faucet"], s["owner"], s["user"]
    # User already dripped in previous test (module fixture order) — may be above minBalance.
    # Force a fresh recipient.
    recipient = w3.eth.account.create()
    tx = faucet.functions.drip(recipient.address).build_transaction(
        {
            "from": owner.address,
            "nonce": w3.eth.get_transaction_count(owner.address),
            "gas": 200_000,
            "gasPrice": w3.eth.gas_price,
            "chainId": w3.eth.chain_id,
        }
    )
    assert _send(w3, tx, s["owner_key"]).status == 1

    # Immediate second drip → cooldown
    tx2 = faucet.functions.drip(recipient.address).build_transaction(
        {
            "from": owner.address,
            "nonce": w3.eth.get_transaction_count(owner.address),
            "gas": 200_000,
            "gasPrice": w3.eth.gas_price,
            "chainId": w3.eth.chain_id,
        }
    )
    with pytest.raises(Exception):
        _send(w3, tx2, s["owner_key"])

    time.sleep(s["cooldown"] + 1)
    # Still above minBalance after first drip → BalanceAboveThreshold
    tx3 = faucet.functions.drip(recipient.address).build_transaction(
        {
            "from": owner.address,
            "nonce": w3.eth.get_transaction_count(owner.address),
            "gas": 200_000,
            "gasPrice": w3.eth.gas_price,
            "chainId": w3.eth.chain_id,
        }
    )
    with pytest.raises(Exception):
        _send(w3, tx3, s["owner_key"])


def test_non_operator_cannot_drip(faucet_setup):
    s = faucet_setup
    w3, faucet, stranger = s["w3"], s["faucet"], s["stranger"]
    # Fund stranger with gas from owner
    owner = s["owner"]
    fund = {
        "from": owner.address,
        "to": stranger.address,
        "value": w3.to_wei(0.05, "ether"),
        "nonce": w3.eth.get_transaction_count(owner.address),
        "gas": 100_000,
        "gasPrice": w3.eth.gas_price,
        "chainId": w3.eth.chain_id,
    }
    _send(w3, fund, s["owner_key"])

    target = w3.eth.account.create().address
    tx = faucet.functions.drip(target).build_transaction(
        {
            "from": stranger.address,
            "nonce": w3.eth.get_transaction_count(stranger.address),
            "gas": 200_000,
            "gasPrice": w3.eth.gas_price,
            "chainId": w3.eth.chain_id,
        }
    )
    with pytest.raises(Exception):
        _send(w3, tx, stranger.key.hex())
