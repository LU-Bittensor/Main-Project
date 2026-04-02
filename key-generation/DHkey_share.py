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

# Config
SCRIPT_PATH = Path(__file__).parent / "linuxSetup.sh"
RPC_URL = os.getenv("RPC_URL", "http://127.0.0.1:8545")
CONTRACT_ADDRESS = os.getenv("CONTRACT_ADDRESS")
OWNER_PRIVATE_KEY = os.getenv("OWNER_PRIVATE_KEY")

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
            {"name": "requesrer", "type": "address", "indexed": True},
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
    stdin_input = f"{user_name}\n{passphrase}\n{passphrase}\n{overwrite_answer}\n"

    result = subprocess.run(["bash", str(SCRIPT_PATH)], input=stdin_input, capture_output=True, text=True, env={"PRIVATE_KEY":private_key_file, "PUBLIC_KEY":public_key_file, "PATH":"/usr/bin:/bin:/usr/local/bin"})

    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        raise RuntimeError("linuxSetup.sh failed with code {result.returncode}")
    
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
        raw_shared_seceret = self.private_key.exchange(peer_pub)
        derived_key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"medi-link dh key exchange", backend=default_backend()).derive(raw_shared_seceret)
        self.shared_secret = derived_key
        print(f"{self.name} derived shared secret: {derived_key.hex()}")
        return derived_key

# Connect to node and return (web3, contract) instances
def get_contract():
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    
    if not w3.isConnected():
        raise ConnectionError(f"Failed to connect to node at {RPC_URL}")