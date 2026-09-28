# cd Main-Project/key-generation
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
export RPC_URL=http://127.0.0.1:8545
export OWNER_PRIVATE_KEY=0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80
export FUND_LOCAL_WALLETS=1
export GRANT_DATA_DIR="$PWD/.grant-pairs-chain-v3"
.venv/bin/python grant_api.py --dev --chain