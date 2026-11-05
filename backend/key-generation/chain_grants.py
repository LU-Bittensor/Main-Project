"""Shared AccessRegistry adapter; the API operator owns registered resources.

Prototype: the public-commitment challenge is forgeable. API authentication and
record ownership checks remain mandatory; this is not a secure on-chain proof.
"""
import json
import os
from pathlib import Path
from threading import RLock

from web3 import Web3
from web3.exceptions import TransactionNotFound
from web3.logs import DISCARD
from web3.middleware import ExtraDataToPOAMiddleware

from DHkey_share import derive_commitment, compute_response


class ChainGrants:
    def __init__(self, rpc_url, operator_key, *, fund_local_wallets=False):
        self.w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 15}))
        self.chain_id = self.w3.eth.chain_id
        if self.chain_id in (945, 964):
            self.w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        path = Path(__file__).parent / "testContractDeployment/artifacts/contracts/AccessRegistry.sol/AccessRegistry.json"
        artifact = json.loads(path.read_text())
        self.abi = artifact["abi"]
        self.runtime = artifact["deployedBytecode"]
        self.registry_address = os.getenv("REGISTRY_ADDRESS") or None
        self.factory = self.w3.eth.contract(abi=self.abi, bytecode=artifact["bytecode"])
        self.account = self.w3.eth.account.from_key(operator_key)
        self.lock = RLock()
        self.fund_local_wallets = fund_local_wallets
        if fund_local_wallets and self.chain_id != 31337:
            raise RuntimeError("Automatic test funding is restricted to local Hardhat chain 31337")
        self.identity = [self.chain_id, self.w3.eth.get_block(0)["hash"].hex(),
                         self.account.address, Web3.keccak(hexstr=artifact["bytecode"]).hex(), self.registry_address]

    def contract(self, address):
        return self.w3.eth.contract(address=Web3.to_checksum_address(address), abi=self.abi)

    def transact(self, function, private_key, save_hash=lambda _hash: None):
        with self.lock:
            account = self.w3.eth.account.from_key(private_key)
            tx = function.build_transaction({
                "from": account.address,
                "nonce": self.w3.eth.get_transaction_count(account.address, "pending"),
                "chainId": self.chain_id,
            })
            signed = account.sign_transaction(tx)
            tx_hash = Web3.to_hex(self.w3.keccak(signed.raw_transaction))
            # Keep the hash before broadcast: receipt timeouts must not cause duplicate deployment.
            save_hash(tx_hash)
            self.w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=45)
            if receipt["status"] != 1:
                raise RuntimeError("Transaction reverted")
            return receipt

    def accepted_runtimes(self):
        """Hardhat artifact and py-solc-x deploys can differ; accept either known runtime."""
        runtimes = [bytes.fromhex(self.runtime.removeprefix("0x"))]
        deploy_path = Path(__file__).parent / "deployment.json"
        if deploy_path.is_file():
            try:
                payload = json.loads(deploy_path.read_text())
                raw = payload.get("deployedBytecode")
                if isinstance(raw, str) and raw.strip():
                    runtimes.append(bytes.fromhex(raw.removeprefix("0x")))
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                pass
        return runtimes

    def validate_registry(self, address):
        code = bytes(self.w3.eth.get_code(address))
        if not code:
            raise RuntimeError("No contract at registry address")
        if code not in self.accepted_runtimes():
            raise RuntimeError("Registry bytecode does not match AccessRegistry.sol; check chain/address")

    def deploy(self, save_hash):
        return self.transact(self.factory.constructor(), self.account.key, save_hash)

    def resource_matches(self, address, resource_id, data_hash, cid):
        owner, stored_hash, stored_cid, exists = self.contract(address).functions.resources(resource_id).call()
        if not exists:
            return False
        if owner != self.account.address or stored_hash != data_hash or stored_cid != cid:
            raise RuntimeError("Resource already registered with different ownership or metadata")
        return True

    def register_resource(self, address, resource_id, data_hash, cid, save_hash):
        return self.transact(self.contract(address).functions.registerResource(
            resource_id, data_hash, cid), self.account.key, save_hash)

    def register(self, address, resource_id, wallet, shared_secret, save_hash):
        return self.transact(self.contract(address).functions.grantAccess(
            resource_id, wallet, derive_commitment(shared_secret)), self.account.key, save_hash)

    def has_grant(self, address, resource_id, wallet):
        return self.contract(address).functions.hasGrant(resource_id, wallet).call()

    def revoke(self, address, resource_id, wallet, save_hash):
        return self.transact(self.contract(address).functions.revokeAccess(
            resource_id, wallet), self.account.key, save_hash)

    def receipt(self, tx_hash):
        try:
            return self.w3.eth.get_transaction_receipt(tx_hash)
        except TransactionNotFound:
            return None

    def matches(self, address, resource_id, wallet, shared_secret):
        return self.contract(address).functions.commitments(resource_id, wallet).call() == derive_commitment(shared_secret)

    def request_access(self, address, resource_id, shared_secret, recipient_key):
        # Serialize both transactions so another request cannot replace this wallet's challenge.
        with self.lock:
            account = self.w3.eth.account.from_key(recipient_key)
            if self.fund_local_wallets and self.w3.eth.get_balance(account.address) < self.w3.to_wei(0.1, "ether"):
                result = self.w3.provider.make_request("hardhat_setBalance", [account.address, hex(self.w3.to_wei(1, "ether"))])
                if "error" in result:
                    raise RuntimeError("Local wallet funding failed")
            contract = self.contract(address)
            if not contract.functions.hasGrant(resource_id, account.address).call():
                return {"allowed": False}
            challenge = self.transact(contract.functions.requestChallenge(resource_id), recipient_key)
            logs = contract.events.ChallengeIssued().process_receipt(challenge, errors=DISCARD)
            if len(logs) != 1 or (logs[0]["args"]["requester"] != account.address or logs[0]["args"]["resourceId"] != resource_id):
                raise RuntimeError("Missing recipient challenge event")
            response = compute_response(shared_secret, logs[0]["args"]["nonce"])
            verification = self.transact(contract.functions.verifyAccess(resource_id, response), recipient_key)
            granted = contract.events.AccessGranted().process_receipt(verification, errors=DISCARD)
            return {
                "allowed": any(log["args"]["requester"] == account.address and log["args"]["resourceId"] == resource_id for log in granted),
                "challenge_tx": Web3.to_hex(challenge["transactionHash"]),
                "verify_tx": Web3.to_hex(verification["transactionHash"]),
            }
