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

## One-command local startup

Run `bash start_backend.sh` from `Main-Project/backend`. After Hardhat becomes ready,
the launcher deploys the faucet if needed, tops its balance up to 1 local ETH,
and supplies its address to the Python API. No separate faucet process is needed.
The operator is `FAUCET_OPERATOR_PRIVATE_KEY`, falling back to `OWNER_PRIVATE_KEY`
and the launcher's funded Hardhat test account. A custom operator must already
have local ETH to deploy and fund the contract.

An existing deployment is reused on the same Hardhat instance after checking its
code and operator. A reset creates a new deployment; local state is stored in
`.grant-supabase-local/local-faucet.json`. Public-network deployment files are not
modified. `LOCAL_FAUCET_BALANCE_WEI` overrides the target balance (default 1 ETH).
Drip/minimum/cooldown environment settings apply when deploying a new faucet.

## Deploy (Hardhat manually or remote EVM)

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

Local wallet sharing resolves AccessRegistry through the authenticated API (`POST /api/registry/resolve`). The API deploys or reuses its bytecode-validated registry; local frontend code does not trust `VITE_ACCESS_REGISTRY`, which may point to the faucet after a chain reset changes deployment order. Restart the API and refresh the frontend after updating this flow. Public-network configurations still use the explicitly configured registry address.

Wallet-signed document grants are reconstructed by `/api/grants` from AccessRegistry events and current contract state, then matched to enrolled account wallets and the document owner. Both the records page and access ledger consume this list. Document delivery checks the resource ID, CID/hash, enrolled owner, recipient wallet, and live `hasGrant` before decrypting. Out-of-band revocation takes effect on the next request. This prototype scans chain history in 2,000-block ranges; use a durable event indexer before scaling. Existing wallet grants are discoverable without re-sharing while the chain and account enrollment remain intact. A Hardhat reset erases grants and requires re-enrollment/sharing.
