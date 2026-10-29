# Key generation / Access Registry / EHR Hippius

Deploy one multi-grantee `AccessRegistry` to Subtensor EVM (or Hardhat), store ciphertext on Hippius (or local demo backend), then point MediLink validators at that address.

## Files

| File | Role |
|---|---|
| [`AccessRegistry.sol`](AccessRegistry.sol) | Multi-resource, multi-grantee access contract |
| [`MediLinkFaucet.sol`](MediLinkFaucet.sol) | Ops-funded gas faucet for vault H160s |
| [`deploy_access_registry.py`](deploy_access_registry.py) | Compile + deploy → `deployment.json` |
| [`deploy_medilink_faucet.py`](deploy_medilink_faucet.py) | Deploy faucet → `FAUCET_ADDRESS` |
| [`faucet_client.py`](faucet_client.py) | `ensure_funded` / drip helpers |
| [`wallet_api.py`](wallet_api.py) | Enroll public vault keys + faucet HTTP API |
| [`access_registry_client.py`](access_registry_client.py) | CLI/demo X25519 DH + register / grant / challenge / verify (env keys) |
| [`ehr_hippius.py`](ehr_hippius.py) | Envelope encrypt, per-user wraps, store/unlock demo |
| [`.env.example`](.env.example) | Env vars template (copy secrets locally; do not commit) |
| [`Subtensor_EVM_Deploy.md`](markdowns/Subtensor_EVM_Deploy.md) | Concepts: SS58 TAO → EVM fund → deploy on chain 945 |
| [`Faucet.md`](markdowns/Faucet.md) | Vault wallets + faucet operator drip |

Docs: [`Faucet.md`](markdowns/Faucet.md), [`Subtensor_EVM_Deploy.md`](markdowns/Subtensor_EVM_Deploy.md), [`../markdowns/Hippius_EHR_Storage.md`](../markdowns/Hippius_EHR_Storage.md), [`../markdowns/Contract_Logic.md`](../markdowns/Contract_Logic.md), [`../subnet/markdowns/ARCHITECTURE.md`](../subnet/markdowns/ARCHITECTURE.md), [`../subnet/markdowns/OPERATOR_GUIDE.md`](../subnet/markdowns/OPERATOR_GUIDE.md)

**Product path:** browser vault EVM key signs AccessRegistry; MediLink faucet tops up gas.  
**CLI path:** `OWNER_PRIVATE_KEY` / `USER_PRIVATE_KEY` remain for ops demos only.

## 1. Install deps

```bash
source /path/to/bt_venv/bin/activate
pip install -r subnet/requirements.txt
```

## 2. Deploy (Hardhat example)

```bash
# Terminal A: npx hardhat node  (from key-generation/)
export RPC_URL=http://127.0.0.1:8545
export CHAIN_ID=31337
export OWNER_PRIVATE_KEY=0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80

python key-generation/deploy_access_registry.py
```

This writes `key-generation/deployment.json` and `deployment.exports.sh`. Then:

```bash
python key-generation/deploy_medilink_faucet.py
source key-generation/deployment.exports.sh
# Deposit ETH into FAUCET_ADDRESS (see markdowns/Faucet.md)
python key-generation/wallet_api.py --dev --port 5001
```

Or paste the printed `export CONTRACT_ADDRESS=...` lines. **Stale `CONTRACT_ADDRESS` after redeploy is the usual cause of `Transaction reverted without a reason string`.**

Testnet (945): use `https://test.chain.opentensor.ai` and a funded H160 key.

## 3. EHR store + grant + unlock demo

```bash
export USER_PRIVATE_KEY=0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d
# Use Account #1 key printed by `npx hardhat node` (keys can differ by Hardhat version)
# CONTRACT_ADDRESS from step 2
python key-generation/ehr_hippius.py demo
```

Without `HIPPIUS_*`, objects land under `key-generation/.ehr_local/`.

Offline crypto only: `python key-generation/ehr_hippius.py test`

## 4. Registry-only DH demo

```bash
python key-generation/access_registry_client.py
```

## 5. Poll with the validator (live)

Edit `subnet/validator.config.yml`:

```yaml
evm:
  access_registry: "0xYOUR_DEPLOYED_ADDRESS"
  mock_events: false
  rpc_url: http://127.0.0.1:8545   # or testnet URL
  chain_id: 31337
```

Validators receipt **`ResourceRegistered`** and **`AccessGranted`** only. `AccessGrantSet` / `AccessRevoked` / `ChallengeIssued` are skipped by default (see wiring doc).

## Makefile helpers

```bash
make deploy-registry
make validator-live
```
