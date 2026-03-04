from hippius_sdk import HippiusClient
from dotenv import load_dotenv
import asyncio
import os

# Load environment variables
load_dotenv()

async def main():
    # Create a client with IPFS node configuration
    client = HippiusClient(
        ipfs_api_url="http://localhost:5001"
    )

    # Test a simple operation
    print("Testing IPFS client...")
    try:
        result = await client.exists("QmZ4tDuvesekSs4qM5ZBKpXiZGun7S2CYtEZRB3DYXkjGx")
        print(f"Result: {result}")
    except Exception as e:
        print(f"Error: {e}")

asyncio.run(main())