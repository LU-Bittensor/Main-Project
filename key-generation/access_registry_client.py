"""
Access Registry client: X25519 DH + multi-grantee on-chain access flow.

Usage:
    Set environment variables before running:
        RPC_URL              EVM node URL (default: http://127.0.0.1:8545)
        CHAIN_ID             31337 Hardhat | 945 Subtensor testnet | 964 mainnet
        CONTRACT_ADDRESS     deployed AccessRegistry address
        OWNER_PRIVATE_KEY    hex private key of the resource owner (H160)
        USER_PRIVATE_KEY     hex private key of the accessing user (grantee)
        RESOURCE_ID          optional 0x-prefixed bytes32 (default: derived from DATA_CID)
        DATA_CID             Hippius object key / payload path string
        DATA_HASH            optional 0x bytes32; default keccak256(DATA_CID)
        HIPPIUS_*            optional presigned URL helpers

    python key-generation/access_registry_client.py
    python key-generation/access_registry_client.py test
"""

from __future__ import annotations

import getpass
import os
import secrets
from pathlib import Path

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from eth_abi import encode as abi_encode
from web3 import Web3

try:
    from web3.middleware import ExtraDataToPOAMiddleware
except ImportError:  # web3<7
    from web3.middleware import geth_poa_middleware as ExtraDataToPOAMiddleware  # type: ignore

try:
    import boto3
    from botocore.config import Config as BotocoreConfig
    from botocore.exceptions import ClientError
except ImportError:  # pragma: no cover
    boto3 = None
    BotocoreConfig = None
    ClientError = Exception

DEPLOYMENT_JSON = Path(__file__).resolve().parent / "deployment.json"


def _read_deployment() -> dict:
    import json

    if not DEPLOYMENT_JSON.exists():
        return {}
    try:
        data = json.loads(DEPLOYMENT_JSON.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


_DEPLOYMENT = _read_deployment()

# Prefer explicit env; otherwise fall back to the latest Hardhat/testnet deploy.
RPC_URL = os.getenv("RPC_URL") or _DEPLOYMENT.get("rpc_url") or "http://127.0.0.1:8545"
CHAIN_ID = int(os.getenv("CHAIN_ID") or _DEPLOYMENT.get("chain_id") or "945")
CONTRACT_ADDRESS = (
    os.getenv("CONTRACT_ADDRESS")
    or os.getenv("ACCESS_REGISTRY")
    or _DEPLOYMENT.get("address")
)
OWNER_PRIVATE_KEY = os.getenv("OWNER_PRIVATE_KEY")
USER_PRIVATE_KEY = os.getenv("USER_PRIVATE_KEY")
DATA_CID = os.getenv("DATA_CID", "ehr/demo/patient/record/v1/payload.bin")
RESOURCE_ID = os.getenv("RESOURCE_ID")
DATA_HASH = os.getenv("DATA_HASH")

HIPPIUS_ACCESS_KEY = os.getenv("HIPPIUS_ACCESS_KEY")
HIPPIUS_SECRET_KEY = os.getenv("HIPPIUS_SECRET_KEY")
HIPPIUS_BUCKET = os.getenv("HIPPIUS_BUCKET")
HIPPIUS_KEY = os.getenv("HIPPIUS_KEY")
URL_EXPIRATION = os.getenv("URL_EXPIRATION", "300")

# Fallback ABI if deployment.json is missing. Events MUST include "anonymous".
_FALLBACK_ABI = [
    {
        "name": "registerResource",
        "type": "function",
        "inputs": [
            {"name": "resourceId", "type": "bytes32"},
            {"name": "dataHash", "type": "bytes32"},
            {"name": "dataCid", "type": "string"},
        ],
        "outputs": [],
        "stateMutability": "nonpayable",
    },
    {
        "name": "grantAccess",
        "type": "function",
        "inputs": [
            {"name": "resourceId", "type": "bytes32"},
            {"name": "grantee", "type": "address"},
            {"name": "commitment", "type": "bytes32"},
        ],
        "outputs": [],
        "stateMutability": "nonpayable",
    },
    {
        "name": "revokeAccess",
        "type": "function",
        "inputs": [
            {"name": "resourceId", "type": "bytes32"},
            {"name": "grantee", "type": "address"},
        ],
        "outputs": [],
        "stateMutability": "nonpayable",
    },
    {
        "name": "requestChallenge",
        "type": "function",
        "inputs": [{"name": "resourceId", "type": "bytes32"}],
        "outputs": [{"name": "nonce", "type": "bytes32"}],
        "stateMutability": "nonpayable",
    },
    {
        "name": "verifyAccess",
        "type": "function",
        "inputs": [
            {"name": "resourceId", "type": "bytes32"},
            {"name": "response", "type": "bytes32"},
        ],
        "outputs": [{"name": "granted", "type": "bool"}],
        "stateMutability": "nonpayable",
    },
    {
        "name": "hasPendingChallenge",
        "type": "function",
        "inputs": [
            {"name": "resourceId", "type": "bytes32"},
            {"name": "user", "type": "address"},
        ],
        "outputs": [{"name": "pending", "type": "bool"}],
        "stateMutability": "view",
    },
    {
        "name": "hasGrant",
        "type": "function",
        "inputs": [
            {"name": "resourceId", "type": "bytes32"},
            {"name": "grantee", "type": "address"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "view",
    },
    {
        "name": "commitments",
        "type": "function",
        "inputs": [
            {"name": "", "type": "bytes32"},
            {"name": "", "type": "address"},
        ],
        "outputs": [{"name": "", "type": "bytes32"}],
        "stateMutability": "view",
    },
    {
        "anonymous": False,
        "name": "ResourceRegistered",
        "type": "event",
        "inputs": [
            {"indexed": False, "name": "resourceId", "type": "bytes32"},
            {"indexed": False, "name": "owner", "type": "address"},
            {"indexed": False, "name": "dataHash", "type": "bytes32"},
            {"indexed": False, "name": "dataCid", "type": "string"},
        ],
    },
    {
        "anonymous": False,
        "name": "AccessGrantSet",
        "type": "event",
        "inputs": [
            {"indexed": True, "name": "resourceId", "type": "bytes32"},
            {"indexed": True, "name": "grantee", "type": "address"},
            {"indexed": False, "name": "dataHash", "type": "bytes32"},
        ],
    },
    {
        "anonymous": False,
        "name": "AccessRevoked",
        "type": "event",
        "inputs": [
            {"indexed": True, "name": "resourceId", "type": "bytes32"},
            {"indexed": True, "name": "grantee", "type": "address"},
        ],
    },
    {
        "anonymous": False,
        "name": "ChallengeIssued",
        "type": "event",
        "inputs": [
            {"indexed": True, "name": "resourceId", "type": "bytes32"},
            {"indexed": True, "name": "requester", "type": "address"},
            {"indexed": False, "name": "nonce", "type": "bytes32"},
            {"indexed": False, "name": "expiry", "type": "uint256"},
        ],
    },
    {
        "anonymous": False,
        "name": "AccessGranted",
        "type": "event",
        "inputs": [
            {"indexed": True, "name": "resourceId", "type": "bytes32"},
            {"indexed": True, "name": "requester", "type": "address"},
            {"indexed": False, "name": "dataHash", "type": "bytes32"},
            {"indexed": False, "name": "grantedAt", "type": "uint256"},
        ],
    },
    {
        "anonymous": False,
        "name": "AccessDenied",
        "type": "event",
        "inputs": [
            {"indexed": True, "name": "resourceId", "type": "bytes32"},
            {"indexed": True, "name": "requester", "type": "address"},
            {"indexed": False, "name": "deniedAt", "type": "uint256"},
        ],
    },
]


def _load_contract_abi() -> list:
    """Prefer solc ABI from deployment.json (includes anonymous); else fallback."""
    import json

    if DEPLOYMENT_JSON.exists():
        data = json.loads(DEPLOYMENT_JSON.read_text())
        abi = data.get("abi")
        if isinstance(abi, list) and abi:
            for entry in abi:
                if entry.get("type") == "event" and "anonymous" not in entry:
                    entry["anonymous"] = False
            print(f"[INFO] Loaded contract ABI from {DEPLOYMENT_JSON.name}")
            return abi
    return _FALLBACK_ABI


CONTRACT_ABI = _load_contract_abi()


def generate_keys(user_name: str, passphrase: str, overwrite: bool = False) -> tuple[Path, Path]:
    private_key_file = Path(f"{user_name}_private.pem")
    public_key_file = Path(f"{user_name}_public.pem")

    if (private_key_file.exists() or public_key_file.exists()) and not overwrite:
        print(f"Key files for {user_name} already exist. Skipping generation.")
        return private_key_file, public_key_file

    private_key = X25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.BestAvailableEncryption(passphrase.encode()),
    )
    private_key_file.write_bytes(private_pem)
    private_key_file.chmod(0o600)

    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    public_key_file.write_bytes(public_pem)
    print(f"[INFO] Wrote {private_key_file} / {public_key_file}")
    return private_key_file, public_key_file


class DHUser:
    def __init__(self, name: str, private_key_path: Path, passphrase: str):
        self.name = name
        self.shared_secret: bytes | None = None
        private_pem = private_key_path.read_bytes()
        public_path = private_key_path.with_name(private_key_path.name.replace("private", "public"))
        public_pem = public_path.read_bytes()
        self.private_key = serialization.load_pem_private_key(
            private_pem, password=passphrase.encode(), backend=default_backend()
        )
        self.public_key = serialization.load_pem_public_key(public_pem, backend=default_backend())

    @classmethod
    def from_script(cls, name: str, overwrite: bool = False, passphrase: str | None = None):
        if passphrase is None:
            passphrase = getpass.getpass(f"Enter passphrase for {name}'s private key: ")
            confirm = getpass.getpass(f"Confirm passphrase for {name}'s private key: ")
            if passphrase != confirm:
                raise ValueError("Passphrases do not match.")
        private_key_path, _ = generate_keys(name, passphrase, overwrite=overwrite)
        return cls(name, private_key_path, passphrase)

    def get_public_key_pem(self) -> bytes:
        return self.public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

    def derive_shared_secret(self, peer_public_key_pem: bytes) -> bytes:
        peer_pub = serialization.load_pem_public_key(peer_public_key_pem, backend=default_backend())
        raw = self.private_key.exchange(peer_pub)
        derived = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=b"medi-link dh key exchange",
            backend=default_backend(),
        ).derive(raw)
        self.shared_secret = derived
        print(f"{self.name} derived shared secret: {derived.hex()}")
        return derived


def derive_kek(shared_secret: bytes, resource_id: bytes, grantee: str) -> bytes:
    """KEK_u = HKDF(shared_secret, info=resourceId || grantee_h160)."""
    grantee_bytes = bytes.fromhex(Web3.to_checksum_address(grantee)[2:])
    info = resource_id + grantee_bytes
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=info,
        backend=default_backend(),
    ).derive(shared_secret)


def get_contract():
    if not CONTRACT_ADDRESS:
        raise RuntimeError(
            "CONTRACT_ADDRESS is required (set env or run deploy_access_registry.py)"
        )
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    if CHAIN_ID in (945, 964):
        w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    if not w3.is_connected():
        raise ConnectionError(f"Failed to connect to EVM at {RPC_URL}")

    live_chain = int(w3.eth.chain_id)
    if live_chain != CHAIN_ID:
        raise RuntimeError(
            f"CHAIN_ID={CHAIN_ID} does not match node chain_id={live_chain} at {RPC_URL}. "
            f"For Hardhat use: export CHAIN_ID=31337 RPC_URL=http://127.0.0.1:8545"
        )

    address = Web3.to_checksum_address(CONTRACT_ADDRESS)
    dep_addr = _DEPLOYMENT.get("address")
    if dep_addr:
        dep_cs = Web3.to_checksum_address(dep_addr)
        if dep_cs != address:
            # Stale shell CONTRACT_ADDRESS after redeploy is a common Hardhat footgun.
            env_code = w3.eth.get_code(address)
            dep_code = w3.eth.get_code(dep_cs)
            if dep_code and len(dep_code) > 2:
                print(
                    f"[WARN] CONTRACT_ADDRESS={address} != deployment.json ({dep_cs}); "
                    "using deployment.json address."
                )
                address = dep_cs
            elif not env_code or env_code in (b"", b"\x00"):
                raise RuntimeError(
                    f"No contract code at env CONTRACT_ADDRESS={address}. Redeploy AccessRegistry."
                )

    code = w3.eth.get_code(address)
    if not code or code in (b"", b"\x00"):
        raise RuntimeError(
            f"No contract code at {address}. Stale CONTRACT_ADDRESS after Hardhat restart? "
            f"Redeploy and export CONTRACT_ADDRESS from {DEPLOYMENT_JSON.name}"
        )

    print(f"[INFO] Using AccessRegistry at {address} (chain {CHAIN_ID})")
    contract = w3.eth.contract(address=address, abi=CONTRACT_ABI)
    return w3, contract


def derive_commitment(shared_secret: bytes) -> bytes:
    return Web3.solidity_keccak(["bytes"], [shared_secret])


def compute_response(shared_secret: bytes, nonce: bytes) -> bytes:
    commitment = derive_commitment(shared_secret)
    return Web3.solidity_keccak(["bytes32", "bytes32"], [commitment, nonce])


def resource_id_bytes(resource_id: str | None, data_cid: str) -> bytes:
    if resource_id:
        raw = resource_id[2:] if resource_id.startswith("0x") else resource_id
        return bytes.fromhex(raw.zfill(64))
    return Web3.solidity_keccak(["string"], [data_cid])


def data_hash_bytes(data_hash: str | None, data_cid: str) -> bytes:
    if data_hash:
        raw = data_hash[2:] if data_hash.startswith("0x") else data_hash
        return bytes.fromhex(raw.zfill(64))
    return Web3.solidity_keccak(["string"], [data_cid])


def send_transaction(w3: Web3, fn, private_key: str) -> dict:
    account = w3.eth.account.from_key(private_key)
    # Surface Solidity require() messages before paying for a raw send.
    try:
        fn.call({"from": account.address})
    except Exception as exc:
        raise RuntimeError(
            f"Contract call would revert at {fn.address}: {exc}. "
            "Check CONTRACT_ADDRESS matches the latest AccessRegistry deploy."
        ) from exc

    tx = fn.build_transaction(
        {
            "from": account.address,
            "nonce": w3.eth.get_transaction_count(account.address),
            "gas": 500000,
            "gasPrice": w3.eth.gas_price,
            "chainId": CHAIN_ID,
        }
    )
    signed = w3.eth.account.sign_transaction(tx, private_key)
    raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
    tx_hash = w3.eth.send_raw_transaction(raw)
    return w3.eth.wait_for_transaction_receipt(tx_hash)


def register_resource(resource_id: bytes, data_hash: bytes, data_cid: str) -> dict:
    """Register metadata only (no commitment). Owner = OWNER_PRIVATE_KEY."""
    if not OWNER_PRIVATE_KEY:
        raise RuntimeError("OWNER_PRIVATE_KEY is required")
    w3, contract = get_contract()
    print(f"Registering resourceId=0x{resource_id.hex()} dataCid={data_cid}")
    receipt = send_transaction(
        w3,
        contract.functions.registerResource(resource_id, data_hash, data_cid),
        OWNER_PRIVATE_KEY,
    )
    print(f"ResourceRegistered tx={receipt['transactionHash'].hex()}")
    return receipt


def grant_access(resource_id: bytes, grantee: str, shared_secret: bytes) -> dict:
    """Owner sets per-grantee commitment from DH shared secret."""
    if not OWNER_PRIVATE_KEY:
        raise RuntimeError("OWNER_PRIVATE_KEY is required")
    w3, contract = get_contract()
    commitment = derive_commitment(shared_secret)
    grantee_cs = Web3.to_checksum_address(grantee)
    print(f"grantAccess resourceId=0x{resource_id.hex()} grantee={grantee_cs}")
    receipt = send_transaction(
        w3,
        contract.functions.grantAccess(resource_id, grantee_cs, commitment),
        OWNER_PRIVATE_KEY,
    )
    print(f"AccessGrantSet tx={receipt['transactionHash'].hex()}")
    return receipt


def revoke_access(resource_id: bytes, grantee: str) -> dict:
    if not OWNER_PRIVATE_KEY:
        raise RuntimeError("OWNER_PRIVATE_KEY is required")
    w3, contract = get_contract()
    grantee_cs = Web3.to_checksum_address(grantee)
    receipt = send_transaction(
        w3,
        contract.functions.revokeAccess(resource_id, grantee_cs),
        OWNER_PRIVATE_KEY,
    )
    print(f"AccessRevoked tx={receipt['transactionHash'].hex()}")
    return receipt


def request_access(shared_secret: bytes, resource_id: bytes, user_private_key: str) -> str | None:
    """Challenge–response prove; optional Hippius payload presign after AccessGranted."""
    w3, contract = get_contract()
    print("Requesting challenge...")
    receipt = send_transaction(w3, contract.functions.requestChallenge(resource_id), user_private_key)
    logs = contract.events.ChallengeIssued().process_receipt(receipt)
    if not logs:
        raise RuntimeError("No ChallengeIssued event found")
    nonce: bytes = logs[0]["args"]["nonce"]
    response = compute_response(shared_secret, nonce)
    receipt = send_transaction(
        w3, contract.functions.verifyAccess(resource_id, response), user_private_key
    )
    if contract.events.AccessGranted().process_receipt(receipt):
        print("AccessGranted")
        if HIPPIUS_BUCKET and HIPPIUS_KEY and boto3 is not None:
            return generate_presigned_url(HIPPIUS_BUCKET, HIPPIUS_KEY, int(URL_EXPIRATION))
        return "granted"
    if contract.events.AccessDenied().process_receipt(receipt):
        print("AccessDenied")
        return None
    raise RuntimeError("No AccessGranted/AccessDenied event")


def get_hippius_client():
    if boto3 is None:
        raise RuntimeError("boto3 is required for Hippius helpers")
    return boto3.client(
        "s3",
        endpoint_url="https://s3.hippius.com",
        aws_access_key_id=HIPPIUS_ACCESS_KEY,
        aws_secret_access_key=HIPPIUS_SECRET_KEY,
        region_name="decentralized",
        config=BotocoreConfig(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def generate_presigned_url(bucket: str, key: str, expiry: int = 300) -> str:
    try:
        return get_hippius_client().generate_presigned_url(
            "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=expiry
        )
    except ClientError as e:
        raise RuntimeError(f"Failed to generate Hippius presigned URL: {e}") from e


def run_demo():
    print("=== AccessRegistry multi-grantee DH + auth demo ===")
    owner = DHUser.from_script("owner", overwrite=True)
    user = DHUser.from_script("user", overwrite=True)
    owner_secret = owner.derive_shared_secret(user.get_public_key_pem())
    user_secret = user.derive_shared_secret(owner.get_public_key_pem())
    assert owner_secret == user_secret

    rid = resource_id_bytes(RESOURCE_ID, DATA_CID)
    dhash = data_hash_bytes(DATA_HASH, DATA_CID)
    register_resource(rid, dhash, DATA_CID)

    user_key = USER_PRIVATE_KEY or OWNER_PRIVATE_KEY
    if not user_key:
        raise RuntimeError("USER_PRIVATE_KEY or OWNER_PRIVATE_KEY required")
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    grantee = w3.eth.account.from_key(user_key).address
    grant_access(rid, grantee, owner_secret)

    url = request_access(owner_secret, rid, user_key)
    print(f"Result: {url}")


def run_tests():
    print("=== Offline crypto tests ===")
    alice = DHUser.from_script("alice", overwrite=True, passphrase="test-pass")
    bob = DHUser.from_script("bob", overwrite=True, passphrase="test-pass")
    a = alice.derive_shared_secret(bob.get_public_key_pem())
    b = bob.derive_shared_secret(alice.get_public_key_pem())
    assert a == b
    commitment = derive_commitment(a)
    assert commitment == Web3.solidity_keccak(["bytes"], [a])
    nonce = secrets.token_bytes(32)
    assert compute_response(a, nonce) == Web3.solidity_keccak(
        ["bytes32", "bytes32"], [commitment, nonce]
    )
    rid = resource_id_bytes(None, DATA_CID)
    grantee = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
    kek = derive_kek(a, rid, grantee)
    assert len(kek) == 32
    assert len(abi_encode(["bytes32"], [rid])) == 32
    print("All tests passed")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "test":
        run_tests()
    else:
        run_demo()
