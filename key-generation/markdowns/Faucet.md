# MediLink Faucet

Ops-funded EVM faucet that drips native gas to **enrolled vault H160s** so users can sign AccessRegistry transactions without holding a custodial server wallet.

## Pieces

| Piece | Role |
|-------|------|
| [`MediLinkFaucet.sol`](../MediLinkFaucet.sol) | Holds ETH; `operator` calls `drip(to)` |
| [`deploy_medilink_faucet.py`](../deploy_medilink_faucet.py) | Deploy + write `FAUCET_ADDRESS` |
| [`faucet_client.py`](../faucet_client.py) | `needs_drip` / `ensure_funded` |
| [`wallet_api.py`](../wallet_api.py) | Enroll public vault address; `POST /api/faucet/ensure` |

Users **never** send private keys. The browser vault creates/unlocks the EVM key; the API only stores the public address and asks the faucet operator key to drip when balance &lt; `minBalance`.

## Deploy (Hardhat)

```bash
# Terminal A
cd key-generation && npx hardhat node

# Terminal B
export RPC_URL=http://127.0.0.1:8545
export CHAIN_ID=31337
export OWNER_PRIVATE_KEY=0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80
python deploy_access_registry.py
python deploy_medilink_faucet.py
source deployment.exports.sh

# Fund the faucet contract (1 ETH)
python - <<'PY'
import os
from web3 import Web3
w3 = Web3(Web3.HTTPProvider(os.environ["RPC_URL"]))
acct = w3.eth.account.from_key(os.environ["OWNER_PRIVATE_KEY"])
tx = {
    "from": acct.address,
    "to": os.environ["FAUCET_ADDRESS"],
    "value": w3.to_wei(1, "ether"),
    "nonce": w3.eth.get_transaction_count(acct.address),
    "gas": 100000,
    "gasPrice": w3.eth.gas_price,
    "chainId": int(os.environ["CHAIN_ID"]),
}
signed = w3.eth.account.sign_transaction(tx, os.environ["OWNER_PRIVATE_KEY"])
raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
print(w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(raw)))
PY
```

## Run wallet API

```bash
export FAUCET_ADDRESS=...   # from deploy
export FAUCET_OPERATOR_PRIVATE_KEY=$OWNER_PRIVATE_KEY
export GRANT_DATA_DIR=./.grant-wallets
python wallet_api.py --dev --port 5001
```

## Frontend env

```dotenv
VITE_GRANT_API_URL=http://127.0.0.1:5001
VITE_ACCESS_REGISTRY=0x...   # AccessRegistry from deploy
VITE_EVM_RPC_URL=http://127.0.0.1:8545
VITE_EVM_CHAIN_ID=31337
```

Never put `FAUCET_OPERATOR_PRIVATE_KEY` or `OWNER_PRIVATE_KEY` in `VITE_*`.

## Defaults

| Param | Default |
|-------|---------|
| `dripAmount` | 0.01 ETH |
| `minBalance` | 0.005 ETH |
| `cooldown` | 24h (use short values in tests) |

## Subtensor EVM (945)

Fund the **operator** H160 with `btcli evm fund` / `fund-evm-key`, deploy faucet, then deposit into the contract. End users only receive drips to their vault address — they do not use `btcli`.
