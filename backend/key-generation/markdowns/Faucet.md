# MediLink Faucet

Ops-funded EVM faucet that drips native gas to **enrolled vault H160s** so users can sign AccessRegistry transactions without holding a custodial server EVM key.

## Pieces

| Piece | Role |
|-------|------|
| [`backend/contracts/MediLinkFaucet.sol`](../../contracts/MediLinkFaucet.sol) | Holds ETH; `operator` calls `drip(to)` |
| [`deploy_medilink_faucet.py`](../deploy_medilink_faucet.py) | Deploy + write `FAUCET_ADDRESS` |
| [`faucet_client.py`](../faucet_client.py) | `needs_drip` / `ensure_funded` |
| [`grant_api.py`](../grant_api.py) | Enroll public vault address; `POST /api/faucet/ensure` |

Users **never** send private keys. The browser vault creates/unlocks the EVM key; the API only stores the public address and asks the faucet operator key to drip when balance &lt; `minBalance`.

Do **not** run a separate `wallet_api.py` — faucet and vault enroll live on the same grant API process (`:5001`).

## Deploy (Hardhat or remote EVM)

Prefer the combined script (AccessRegistry + faucet + optional fund):

```bash
# Terminal A — local chain only
cd Main-Project/backend/key-generation/testContractDeployment
npx hardhat node --hostname 127.0.0.1

# Terminal B
cd Main-Project/backend/key-generation
./deploy_evm_contracts.sh hardhat
# or: OWNER_PRIVATE_KEY=0x... ./deploy_evm_contracts.sh subtensor-testnet
source deployment.exports.sh
```

Individual scripts: `deploy_access_registry.py`, `deploy_medilink_faucet.py`.

## Run grant API with faucet

```bash
cd Main-Project/backend/key-generation
export FAUCET_ADDRESS=...   # from deploy
export FAUCET_OPERATOR_PRIVATE_KEY=$OWNER_PRIVATE_KEY
# Real accounts: bash load_API_bc.sh
# Demo fixtures: bash load_API_bc_demo.sh
# Or: .venv/bin/python grant_api.py --dev --port 5001
```

## Frontend env

```dotenv
VITE_GRANT_API_URL=http://127.0.0.1:5001
VITE_ACCESS_REGISTRY=0x...   # AccessRegistry from deploy
VITE_EVM_RPC_URL=http://127.0.0.1:8545
VITE_EVM_CHAIN_ID=31337
```

Never put `FAUCET_OPERATOR_PRIVATE_KEY` or `OWNER_PRIVATE_KEY` in `VITE_*`. Vite does not export env into the Python API process.

## Defaults

| Param | Default |
|-------|---------|
| `dripAmount` | 0.01 ETH |
| `minBalance` | 0.005 ETH |
| `cooldown` | 24h (use short values in tests) |

## Subtensor EVM (945)

Fund the **operator** H160 with `btcli evm fund` / `fund-evm-key`, deploy faucet, then deposit into the contract. End users only receive drips to their vault address — they do not use `btcli`.
