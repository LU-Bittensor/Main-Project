"""One permanently revocable DataAccessControl contract per recipient-record grant.

Prototype: the public-commitment challenge is forgeable. API authentication and
record ownership checks remain mandatory; this is not a secure on-chain proof.
"""
import json
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
        path = Path(__file__).parent / "testContractDeployment/artifacts/contracts/DataAccessControl.sol/DataAccessControl.json"
        artifact = json.loads(path.read_text())
        self.abi = artifact["abi"]
        self.factory = self.w3.eth.contract(abi=self.abi, bytecode=artifact["bytecode"])
        self.account = self.w3.eth.account.from_key(operator_key)
        self.lock = RLock()
        self.fund_local_wallets = fund_local_wallets
        if fund_local_wallets and self.chain_id != 31337:
            raise RuntimeError("Automatic test funding is restricted to local Hardhat chain 31337")
        self.identity = [self.chain_id, self.w3.eth.get_block(0)["hash"].hex(),
                         self.account.address, Web3.keccak(hexstr=artifact["bytecode"]).hex()]

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

    def deploy(self, identifier, save_hash):
        return self.transact(self.factory.constructor(identifier), self.account.key, save_hash)

    def register(self, address, shared_secret, save_hash):
        return self.transact(self.contract(address).functions.registerCommitment(
            derive_commitment(shared_secret)), self.account.key, save_hash)

    def is_killed(self, address):
        return self.contract(address).functions.isKilled().call()

    def kill(self, address, save_hash):
        return self.transact(self.contract(address).functions.killContract(), self.account.key, save_hash)

    def receipt(self, tx_hash):
        try:
            return self.w3.eth.get_transaction_receipt(tx_hash)
        except TransactionNotFound:
            return None

    def matches(self, address, identifier, shared_secret):
        contract = self.contract(address)
        return (contract.functions.owner().call() == self.account.address and
                contract.functions.dataIdentifier().call() == identifier and
                contract.functions.commitment().call() == derive_commitment(shared_secret))

    def request_access(self, address, shared_secret, recipient_key):
        # Serialize both transactions so another request cannot replace this wallet's challenge.
        with self.lock:
            account = self.w3.eth.account.from_key(recipient_key)
            if self.fund_local_wallets and self.w3.eth.get_balance(account.address) < self.w3.to_wei(0.1, "ether"):
                result = self.w3.provider.make_request("hardhat_setBalance", [account.address, hex(self.w3.to_wei(1, "ether"))])
                if "error" in result:
                    raise RuntimeError("Local wallet funding failed")
            contract = self.contract(address)
            if contract.functions.isKilled().call():
                return {"allowed": False}
            challenge = self.transact(contract.functions.requestChallenge(), recipient_key)
            logs = contract.events.ChallengeIssued().process_receipt(challenge, errors=DISCARD)
            if len(logs) != 1 or logs[0]["args"]["requester"] != account.address:
                raise RuntimeError("Missing recipient challenge event")
            response = compute_response(shared_secret, logs[0]["args"]["nonce"])
            verification = self.transact(contract.functions.verifyAccess(response), recipient_key)
            granted = contract.events.AccessGranted().process_receipt(verification, errors=DISCARD)
            return {
                "allowed": any(log["args"]["requester"] == account.address for log in granted),
                "challenge_tx": Web3.to_hex(challenge["transactionHash"]),
                "verify_tx": Web3.to_hex(verification["transactionHash"]),
            }
