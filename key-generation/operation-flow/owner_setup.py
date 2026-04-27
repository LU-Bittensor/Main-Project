"""
Data owner runs on machine.
Generates key pair, and publishes public key to key server.
Then registers commitment to chain.
"""

import requests
import os
from DHkey_share import DHUser, generate_keys, derive_commitment, register_commitment

KEY_SERVER = os.getenv("KEY_SERVER_URL", "http://key-server:5000")
RECORD_ID = os.getenv("RECORD_ID")  # Unique identifier for the medical record

def owner_setup():
    # Step 1: Generate key pair
    owner = DHUser.from_script("owner")  # Generate keys and initialize DHUser

    # Step 2: Publish the owner's PUBLIC key to the key server so users can find it
    # Private key never leaves the owner's machine
    requests.post(f"{KEY_SERVER}/publish_key", json={
        "record_id": RECORD_ID,
        "public_key": owner.get_public_key_pem().decode(),
    })
    print(f"[Owner] Public key published for record: {RECORD_ID}")

    # Step 3: Fetch the USER's public key from the key server to complete the exchange
    # Either wait for the user to publish their key also or use an out-of-band channel to communicate it
    user_pub_pem = requests.get(f"{KEY_SERVER}/get_key/{RECORD_ID}-user").json()["public_key"]

    # Step 4: Derive shared secret
    owner_secret = owner.derive_shared_secret(user_pub_pem.encode())

    # Step 5: Register commitment on-chain. Owner does this once per record
    register_commitment(owner_secret)
    print(f"[Owner] Commitment registered on-chain for record: {RECORD_ID}")

if __name__ == "__main__":
    owner_setup()