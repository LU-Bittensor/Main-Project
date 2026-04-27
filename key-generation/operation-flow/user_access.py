"""
Run by user machine.
Fetches the owner's public key, derives the shared secret.
Performs challenge-response to get a presigned URL
"""

import requests
import os
import asyncio
import aiohttp
from pathlib import Path
from DHkey_share import DHUser, request_access


KEY_SERVER = os.getenv("KEY_SERVER_URL", "http://key-server:5000")
RECORD_ID = os.getenv("RECORD_ID")  # Unique identifier for the medical record

USER_NAME = os.getenv("USER_NAME", "user")
USER_PRIVATE_KEY = os.getenv("USER_PRIVATE_KEY")

PRIVATE_KEY_PATH = Path(f"{USER_NAME}_private.pem")
PUBLIC_KEY_PATH = Path(f"{USER_NAME}_public.pem")

# First access if user's local keys don't exist yet
def is_first_access() -> bool:
    keys_exist = PRIVATE_KEY_PATH.exists() and PUBLIC_KEY_PATH.exists()
    if not keys_exist:
        return True
    else:
        return False
    
"""
Full first-access flow:
    1. Generate key pair
    2. Publish public key to broker so owner can complete their DH side
    3. Wait for owner to publish their public key
    4. Derive shared secret
    5. Perform challenge-response against the contract
"""
async def first_access():
    print("[User] First access - Generating Key pair...")
    # Step 1: Generate user keypair or load existing one
    user = DHUser.from_script(USER_NAME, overwrite=False)

    # Step 2: Publish user's public key so the owner can complete their side
    requests.post(f"{KEY_SERVER}/publish_key", json={
        "record_id": f"{RECORD_ID}-{USER_NAME}",
        "public_key": user.get_public_key_pem().decode(),
    })
    print(f"[User] Public key published to broker for record: {RECORD_ID}")

    # Step 3: Fetch owner's public key from key server
    try:
        owner_pub_pem = await wait_for_owner_key(KEY_SERVER, RECORD_ID)
        print(f"Successfully retrieved owner key: {owner_pub_pem}")
    except RuntimeError as e:
        print(f"Error: {e}")

    # Step 4: Derive shared secret
    # Should produce same secret the owner derived in owner_setup.py
    user_secret = user.derive_shared_secret(owner_pub_pem.encode())
    print(f"Store this shared secret in a secure spot, it serves as your access to the data. If lost it cannot be re-created\nKey: {user_secret}")

    # Step 5: Perform challenge response 
    _do_challenge_response(user_secret)

"""
Subsequent access flow — keys and shared secret already exist:
    1. Load existing private key
    2. Perform challenge-response against the contract
No key generation, no broker interaction, no owner involvement needed.
"""
async def subsequent_access():
    print("[User] Subsequent access - loading existing keys...")
    passphrase = __import__("getpass").getpass(f"Enter passphrase for {USER_NAME}'s private key: ")

    user = DHUser(USER_NAME, PRIVATE_KEY_PATH, passphrase)

    user_secret = __import__("getpass").getpass(f"Enter the shared secret for the file you are accessing: ")

    if user_secret is None:
        raise RuntimeError("Error: No shared secret entered")
    
    _do_challenge_response(user_secret)

# async polling server for owner's public key
async def wait_for_owner_key(key_server_url, record_id):
    print("[User] Waiting for owner to publish their key...")
    owner_pub_pem = None

    async with aiohttp.ClientSession() as session:
        for attempt in range(30):
            async with session.get(f"{key_server_url}/get_key/{record_id}") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    owner_pub_pem = data["public_key"].encode()
                    break
            
            await asyncio.sleep(10)
    
    if owner_pub_pem is None:
        raise RuntimeError(f"Owner's public key not available after 5 minutes.\nEnsure owner has run access setup for you")

# Perform challenge-response against smart contract and print result
def _do_challenge_response(shared_secret: bytes):
    print("[User] Performing challenge-response against smart contract...")
    url = request_access(shared_secret, USER_PRIVATE_KEY)

    if url:
        print(f"[User] Access Granted! Presigned URL: {url}")
    else:
        print(f"[User] Access Denied.")

def main():
    if is_first_access():
        print("[User] Detected first access for this record.")
        first_access()
    else:
        print("[User] Detected subsequent access — skipping key generation.")
        subsequent_access()

if __name__ == "__main__":
    asyncio.run(main())