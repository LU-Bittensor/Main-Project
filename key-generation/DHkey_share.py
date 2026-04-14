# Need pip install web3 cryptography to run this code

#Usage:
#    Set environment variables before running:
#        RPC_URL             node URL (default: http://127.0.0.1:8545)
#        CONTRACT_ADDRESS    deployed DataAccessControl contract address
#        OWNER_PRIVATE_KEY   hex private key of the contract owner
#        USER_PRIVATE_KEY    hex private key of the accessing user
#                            (falls back to OWNER_PRIVATE_KEY if not set)
#    Then run:
#        python medilink_auth.py

import getpass
import os
from pathlib import Path
import subprocess
import sys
import re

from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.backends import default_backend
import secrets

from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
from eth_abi import encode as abi_encode

import boto3
from botocore.config import Config as BotocoreConfig
from botocore.exceptions import ClientError

# Config
SCRIPT_PATH = Path(__file__).parent / "linuxSetup.sh"
RPC_URL = os.getenv("RPC_URL", "http://127.0.0.1:8545") # http://127.0.0.1:8545 (local) or https://test.chain.opentensor.ai (testnet) or https://lite.chain.opentensor.ai (mainnet)
CHAIN_ID = int(os.getenv("CHAIN_ID", "945")) # 31337 (local Hardhat) | 945 (Subtensor testnet)
CONTRACT_ADDRESS = os.getenv("CONTRACT_ADDRESS") # deployed DataAccessControl contract address
OWNER_PRIVATE_KEY = os.getenv("OWNER_PRIVATE_KEY") # H160 EVM wallet private key — NOT your Bittensor coldkey
USER_PRIVATE_KEY = os.getenv("USER_PRIVATE_KEY") # H160 EVM wallet private key for the accessing user

# Hippius S3 config for storing encrypted medical records (not used in the DH exchange flow, but part of the overall MediLink architecture)
HIPPIUS_ACCESS_KEY = os.getenv("HIPPIUS_ACCESS_KEY") # from console.hippius.com/dashboard/settings — format: hip_xxxxx
HIPPIUS_SECRET_KEY = os.getenv("HIPPIUS_SECRET_KEY") # from console.hippius.com/dashboard/settings
HIPPIUS_BUCKET = os.getenv("HIPPIUS_BUCKET") # your Hippius bucket name
HIPPIUS_KEY = os.getenv("HIPPIUS_KEY") # object path within the bucket, e.g. patient-records/record-001.json
URL_EXPIRATION = os.getenv("URL_EXPIRATION", "300") # presigned URL lifetime in seconds (default 5 minutes)

#Contract ABI 
CONTRACT_ABI = [
    {
        "name": "registerCommitment",
        "type": "function",
        "inputs": [
            {"name": "_commitment", "type": "bytes32"},
        ],
        "outputs": [],
        "stateMutability": "nonpayable",
    },
    {
        "name": "requestChallenge",
        "type": "function",
        "inputs": [],
        "outputs": [
            {"name": "nonce", "type": "bytes32"},
        ],
        "stateMutability": "nonpayable",
    },
    {
        "name": "verifyAccess",
        "type": "function",
        "inputs": [
            {"name": "response", "type": "bytes32"},
        ],
        "outputs": [
            {"name": "granted", "type": "bool"},
        ],
        "stateMutability": "nonpayable",
    },
    {
        "name": "hasPendingChallenge",
        "type": "function",
        "inputs": [
            {"name": "user", "type": "address"},
        ],
        "outputs": [
            {"name": "pending", "type": "bool"},
        ],
        "stateMutability": "view",
    },
    {
        "name": "ChallengeIssued",
        "type": "event",
        "inputs": [
            {"name": "requester", "type": "address", "indexed": True},
            {"name": "nonce", "type": "bytes32", "indexed": False},
            {"name": "expiry", "type": "uint256", "indexed": False},
        ],
    },
    {
        "name": "AccessGranted",
        "type": "event",
        "inputs": [
            {"name": "requester", "type": "address", "indexed": True},
        ],
    },
    {
        "name": "AccessDenied",
        "type": "event",
        "inputs": [
            {"name": "requester", "type": "address", "indexed": True},
        ],
    }
]

# Run the linuxSetup.sh script to generate keys, and parse the output to get the paths of the generated private and public key files
def generate_keys(user_name: str, passphrase: str, overwrite: bool = False) -> tuple[Path, Path]:

    private_key_file = Path(f"{user_name}_private.pem")
    public_key_file = Path(f"{user_name}_public.pem")
    overwrite_answer = "y" if overwrite else "n"
    stdin_input = f"{passphrase}\n{passphrase}\n{overwrite_answer}\n"
    print(f"DEBUG stdin being sent: {repr(stdin_input)}")

    result = subprocess.run(["bash", str(SCRIPT_PATH)], input=stdin_input, capture_output=True, text=True, env={"PRIVATE_KEY":str(private_key_file), "PUBLIC_KEY":str(public_key_file), "PATH":"/usr/bin:/bin:/usr/local/bin"})

    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        raise RuntimeError(f"linuxSetup.sh failed with code {result.returncode}")
    
    print(result.stdout)

    # Parse paths from output lines of the script
    private_match = re.search(r"Private Key:\s+(\S+\.pem)", result.stdout)
    public_match = re.search(r"Public Key:\s+(\S+\.pem)", result.stdout)

    if not private_match or not public_match:
        raise RuntimeError(f"Failed to parse key paths from linuxSetup.sh output.\nOutput was:\n{result.stdout}")
    
    return Path(private_match.group(1)), Path(public_match.group(1))

# Representation of one party in the DH exchange
class DHUser:

    # Initialize from existing key files (used for both owner and user in the authentication flow)
    def __init__(self, name: str, private_key_path: Path, passphrase: str):
        self.name = name
        self.shared_secret: bytes | None = None

        private_pem = private_key_path.read_bytes()
        public_path = private_key_path.with_name(private_key_path.name.replace("private", "public"))
        public_pem = public_path.read_bytes()

        self.private_key = serialization.load_pem_private_key(private_pem, password=passphrase.encode(), backend=default_backend())
        self.public_key = serialization.load_pem_public_key(public_pem, backend=default_backend())

        print(f"{self.name} loaded private key from {private_key_path} and public key from {public_path}")
    
    # Collect user passphrase and generate keys using the linuxSetup.sh script, then initialize the DHUser with the generated keys
    @classmethod
    def from_script(cls, name: str, overwrite: bool = False):
        print(f"Generating keys for {name}...")
        passphrase = getpass.getpass(f"Enter passphrase for {name}'s private key: ")
        confirm_passphrase = getpass.getpass(f"Confirm passphrase for {name}'s private key: ")

        if passphrase != confirm_passphrase:
            raise ValueError("Passphrases do not match.")
        
        private_key_path, _ = generate_keys(name, passphrase, overwrite=overwrite)
        return cls(name, private_key_path, passphrase)
    
    # Serialize the public key to PEM format for sharing with the other party in the DH exchange
    def get_public_key_pem(self) -> bytes:
        return self.public_key.public_bytes(encoding=serialization.Encoding.PEM, format=serialization.PublicFormat.SubjectPublicKeyInfo)
    
    # Perform X25519 key exchange with peer's public key
    # Raw DH output processed through HKDF-SHA256 to derive a symmetric key for use as commitment seed
    # Both parties independently derive the same shared secret without it ever being transmitted, which is used to encrypt the challenge nonce for the authentication flow
    def derive_shared_secret(self, peer_public_key_pem: bytes) -> bytes:
        peer_pub = serialization.load_pem_public_key(peer_public_key_pem, backend=default_backend())
        raw_shared_secret = self.private_key.exchange(peer_pub)
        derived_key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"medi-link dh key exchange", backend=default_backend()).derive(raw_shared_secret)
        self.shared_secret = derived_key
        print(f"{self.name} derived shared secret: {derived_key.hex()}")
        print(f"Store this shared secret securely! It is not transmitted and is required to compute the on-chain commitment and respond to authentication challenges.")
        print(f"Note: The shared secret is derived from the DH exchange and processed through HKDF, so it cannot be reverse engineered to obtain the original private keys. However, if someone obtains the shared secret, they could impersonate you in the authentication flow, so keep it secure.")
        return derived_key


def get_Hippius_client():
    return boto3.client(
        "s3",
        endpoint_url="https://s3.hippius.com",
        aws_access_key_id=HIPPIUS_ACCESS_KEY,
        aws_secret_access_key=HIPPIUS_SECRET_KEY,
        region_name="decentralized",
        config=BotocoreConfig(signature_version="s3v4", s3={"addressing_style": "path"})
    )

def generate_presigned_url(bucket: str, key: str, expiry: int = 300) -> str:
    try:
        return get_Hippius_client().generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expiry
        )
    except ClientError as e:
        raise RuntimeError(f"Failed to generate Hippius presigned URL: {e}")

# Connect to node and return (web3, contract) instances
def get_contract():
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    if CHAIN_ID in (945, 964): # Subtensor EVM chains require the POA middleware to handle the extraData field in block headers
        w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    
    if not w3.is_connected():
        raise ConnectionError(f"Failed to connect to Subtensor EVM at {RPC_URL}")
    
    contract = w3.eth.contract(address=Web3.to_checksum_address(CONTRACT_ADDRESS), abi=CONTRACT_ABI)

    return w3, contract

# Compute on-chain commitment as keccak256 hash of the shared secret derived from the DH exchange
def derive_commitment(shared_secret: bytes) -> bytes:
    return Web3.solidity_keccak(["bytes"], [shared_secret])

# Compute the challenege response: keccak256 hash of the commitment and the nonce provided by the contract, which can be verified on-chain to grant or deny access
def compute_response(shared_secret: bytes, nonce: bytes) -> bytes:
    commitment = derive_commitment(shared_secret)
    return Web3.solidity_keccak(["bytes32", "bytes32"], [commitment, nonce])

# Sign and broadcast a transaction to the contract, returning the transaction receipt
def send_transaction(w3: Web3, fn, private_key: str) -> dict:
    account = w3.eth.account.from_key(private_key)
    tx = fn.build_transaction({
        "from": account.address,
        "nonce": w3.eth.get_transaction_count(account.address),
        "gas": 200000,
        "gasPrice": w3.eth.gas_price,
        "chainId": CHAIN_ID,
    })
    signed_tx = w3.eth.account.sign_transaction(tx, private_key)
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    return receipt

# Derive keccak256(sharedSecret) and write it to the contract. Owner calls this after after both parties complete DH exchange.
# Raw shared_secret
def register_commitment(shared_secret: bytes):
    w3, contract = get_contract()
    commitment = derive_commitment(shared_secret)

    print(f"Registering commitment: 0x{commitment.hex()}")
    receipt = send_transaction(w3, contract.functions.registerCommitment(commitment), OWNER_PRIVATE_KEY)
    print(f"Commitment registered. Transaction hash: {receipt['transactionHash'].hex()}")


# Challenge Response Flow
# 1. Call requestChallenge on the contract -> get nonce from event log
# 2. Compute response = keccak256(commitment ++ nonce)
# 3. Call verifyAccess(response) -> read AccessGranted or AccessDenied event
def request_access(shared_secret: bytes, user_private_key: str) -> str | None:
    w3, contract = get_contract()

    # Step 1: Request a challenge from the contract
    print("Requesting challenge from contract...")
    receipt = send_transaction(w3, contract.functions.requestChallenge(), user_private_key)

    # Parse nonce from ChallengeIssued event
    logs = contract.events.ChallengeIssued().process_receipt(receipt)
    if not logs:
        raise RuntimeError("No ChallengeIssued event found in transaction receipt.")
    nonce: bytes = logs[0]["args"]["nonce"] # 32 bytes nonce emitted by the contract
    print(f"Received Challenge nonce from contract: 0x{nonce.hex()[:16]}...")

    # Step 2: Compute the response off-chain using the shared secret and the nonce (secret never sent on chain)
    response = compute_response(shared_secret, nonce)
    print(f"Computed response to challenge: 0x{response.hex()[:16]}...")

    # Step 3: Submit the response
    receipt = send_transaction(w3, contract.functions.verifyAccess(response), user_private_key)

    # Read the outcome from the event log
    granted_logs = contract.events.AccessGranted().process_receipt(receipt)
    denied_logs = contract.events.AccessDenied().process_receipt(receipt)

    if granted_logs:
        print("AccessGranted event emitted - Access Granted!")
        url = generate_presigned_url(HIPPIUS_BUCKET, HIPPIUS_KEY, expiry=int(URL_EXPIRATION))
        print(f"Generated presigned URL for medical record access: {url} (expires in {URL_EXPIRATION} seconds)")
        return url
    elif denied_logs:
        print("AccessDenied event emitted - Access Denied!")
        return None
    else:
        raise RuntimeError("No AccessGranted or AccessDenied event found in transaction receipt.")
    

# End-to-end demo of the DH key exchange and authentication flow
def run_demo():
    print("=== MediLink DH Key Exchange and Authentication Demo ===")

    # Phase 1 + 2 - Owner and User generate their key pairs and derive the shared secret through DH exchange
    owner = DHUser.from_script("owner", overwrite=True)
    user = DHUser.from_script("user", overwrite=True)

    owner_secret = owner.derive_shared_secret(user.get_public_key_pem())
    user_secret = user.derive_shared_secret(owner.get_public_key_pem())

    assert owner_secret == user_secret, "Derived secrets do not match!"
    print("DH Key Exchange successful - both parties derived the same shared secret.")

    # Phase 3 - Owner registers the commitment on-chain
    register_commitment(owner_secret)

    # Phase 4 - User requests access by responding to the challenge issued by the contract
    user_key = os.getenv("USER_PRIVATE_KEY", OWNER_PRIVATE_KEY) # For demo purposes, allow using owner key if user key not set
    access_granted = request_access(owner_secret, user_key)
    if access_granted:
        print(f"User successfully authenticated and granted access to medical records!\nData available at presigned URL: {access_granted}")
    else:
        print("User authentication failed - access denied.")

def run_tests():
    print("=== Running tests ===")
    
    print("Test 1: Key Generation and DH Exchange")
    alice = DHUser.from_script("alice", overwrite=True)
    bob = DHUser.from_script("bob", overwrite=True)
    alice_secret = alice.derive_shared_secret(bob.get_public_key_pem())
    bob_secret = bob.derive_shared_secret(alice.get_public_key_pem())
    assert alice_secret == bob_secret, "Test 1 Failed: Derived secrets do not match"
    print("Test 1 Passed: Alice and Bob derived the same shared secret.")

    print("Test 2: Commitment Derivation")
    commitment = derive_commitment(alice_secret)
    expected_commitment = Web3.solidity_keccak(["bytes"], [alice_secret])
    assert commitment == expected_commitment, "Test 2 Failed: Commitment does not match expected value"
    print("Test 2 Passed: Commitment derived correctly from shared secret.")

    print("Test 3: Challenge Response Computation")
    nonce = secrets.token_bytes(32) # Simulate a random nonce from the contract
    response = compute_response(alice_secret, nonce)
    expected_response = Web3.solidity_keccak(["bytes32", "bytes32"], [derive_commitment(alice_secret), nonce])
    assert response == expected_response, "Test 3 Failed: Challenge response does not match expected value"
    print("Test 3 Passed: Challenge response computed correctly from commitment and nonce.")

    print("All tests passed!")

if __name__ == "__main__":
    run_demo()

if __name__ == "__test__":
    run_tests()