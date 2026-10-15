# MediLink Operator Guide

Run and prove the incentive loop on **Bittensor testnet netuid 573** + **Subtensor EVM 945** (or local Hardhat for the registry only).

**Use only:** `miner.py` + `validator.py`

**Architecture:** [`ARCHITECTURE.md`](ARCHITECTURE.md) · **Epic / Hippius:** [`INTEGRATIONS.md`](INTEGRATIONS.md)  
**Config:** [`../validator.config.yml`](../validator.config.yml) · **EVM deploy concepts:** [`../../key-generation/markdowns/Subtensor_EVM_Deploy.md`](../../key-generation/markdowns/Subtensor_EVM_Deploy.md)

---

## Role mapping

| Role | Wallet | Hotkey |
|------|--------|--------|
| Subnet owner | `AidanColdKey` | `AidanHotKey` |
| Validator | `test-validator` | `default` |
| Miner | `test-miner` | `default` |

```bash
source /Users/aidanosullivan/YumaBittensor/bt_venv/bin/activate
cd /Users/aidanosullivan/YumaBittensor/Main-Project
```

Coldkey passwords must be typed in **your** terminal.

---

## 1. Subnet bootstrap (573)

Registration burn moves over time — re-check with `btcli subnets burn-cost 573 -n test`.

```bash
# Identity + start emissions
btcli sudo set-identity \
  --netuid 573 \
  --name medilink \
  --description "MediLink EHR access registry subnet" \
  -w AidanColdKey -H AidanHotKey -n test -y --no-mev-shield

btcli sudo start --netuid 573 -w AidanColdKey -n test -y --no-mev-shield
btcli sudo get-identity 573 -n test
btcli subnets show 573 -n test

# Fund neurons (adjust for current burn; leave owner fee buffer)
btcli wallet transfer -w AidanColdKey --dest test-miner --amount-tao 1.05 -n test -y --no-mev-shield
btcli wallet transfer -w AidanColdKey --dest test-validator --amount-tao 2.55 -n test -y --no-mev-shield

btcli subnets register --netuid 573 -w test-miner -H default -n test -y
btcli subnets register --netuid 573 -w test-validator -H default -n test -y

btcli stake add --netuid 573 -w test-validator -H default \
  --amount-tao 1.2 --no-slippage-protection -n test -y --no-mev-shield

btcli subnets metagraph 573 -n test
```

**Pass:** both UIDs on metagraph; validator stake > 0.

---

## 2. AccessRegistry on EVM

Bittensor WS ≠ EVM HTTP. Testnet EVM: `https://test.chain.opentensor.ai`, chain id **945**.

### 2a. Testnet 945

```bash
btcli evm key new --name access-registry -w AidanColdKey
btcli evm fund --evm-key access-registry --amount-tao 0.2 -w AidanColdKey -n test -y
btcli evm key export --name access-registry -w AidanColdKey --private-key   # copy 0x…

export OWNER_PRIVATE_KEY=0x...
export RPC_URL=https://test.chain.opentensor.ai
export CHAIN_ID=945

python key-generation/deploy_access_registry.py
source key-generation/deployment.exports.sh
```

Point [`validator.config.yml`](../validator.config.yml) at the address:

```yaml
evm:
  access_registry: "0x..."   # from deployment
  mock_events: false
  rpc_url: https://test.chain.opentensor.ai
  chain_id: 945
  start_block: <at or before deploy / events of interest>
```

### 2b. Local Hardhat (registry only)

Hardhat’s “10000 ETH” exists **only** on `http://127.0.0.1:8545`. Deploy defaults to testnet unless you set `RPC_URL`.

```bash
# Terminal H
cd key-generation && npx hardhat node

# Terminal D
export RPC_URL=http://127.0.0.1:8545 CHAIN_ID=31337
export OWNER_PRIVATE_KEY=0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80
python deploy_access_registry.py
source deployment.exports.sh
```

Validator overrides: `--evm-rpc http://127.0.0.1:8545 --evm-chain-id 31337 --evm-start-block 0 --no-mock-events --access-registry "$CONTRACT_ADDRESS"`.

**Hardhat tip:** poller stays ~6 blocks behind tip. Mine a few more txs after unlock so `AccessGranted` enters the confirmed window.

### Mock vs live

| Mode | Flag / config | Use when |
|------|---------------|----------|
| Mock | `--mock-events` / `mock_events: true` | Subnet smoke without contract |
| Live | `--no-mock-events` / `false` | Real WRITE/AUDIT from registry |

---

## 3. Unit tests (offline)

```bash
cd subnet
../../bt_venv/bin/python -m unittest tests.test_incentive_scoring tests.test_incentive_smoke -v
```

Covers scoring, EMA, AUDIT smoke, resource-cache / `tx_id`. No Subtensor/EVM required.

---

## 4. Run miner + validator

Bittensor v11: miner serves FastAPI; validator HTTP-POSTs. Loopback ServeAxon is rejected on-chain — use `LOCAL_MINER_AXON` for same-host smoke.

```bash
cd subnet

# Terminal A — miner
python miner.py \
  --netuid 573 \
  --wallet.name test-miner \
  --wallet.hotkey default \
  --subtensor.network test \
  --axon.port 8091 \
  --axon.ip auto \
  --logging.info

# curl -s http://127.0.0.1:8091/health
# btcli subnets metagraph 573 -n test
# Optional public axon: btcli axon set --netuid 573 --ip YOUR_PUBLIC_IP --port 8091 \
#   -w test-miner -H default -n test -y
# Home NAT: forward TCP 8091

# Terminal B — validator (live registry)
export CONTRACT_ADDRESS=0x...   # or source ../key-generation/deployment.exports.sh
LOCAL_MINER_AXON=127.0.0.1:8091 \
python validator.py \
  --config validator.config.yml \
  --netuid 573 \
  --network test \
  -w test-validator \
  -H default \
  --no-mock-events \
  --access-registry "$CONTRACT_ADDRESS"
```

**Pass:** ticks with `mock_events=False`; `EVM poller ready` (or Hardhat RPC).

---

## 5. Emit AccessGranted

```bash
# Terminal C
cd /Users/aidanosullivan/YumaBittensor/Main-Project
export RPC_URL=... CHAIN_ID=... CONTRACT_ADDRESS=...
export OWNER_PRIVATE_KEY=0x... USER_PRIVATE_KEY=0x...   # distinct funded H160s

# Optional: reset DH PEMs if passphrase forgotten
# rm -f key-generation/owner_*.pem key-generation/user_*.pem

python key-generation/access_registry_client.py
```

Expect: DH → `registerResource` → `grantAccess` → `requestChallenge` + `verifyAccess` → **AccessGranted**.

EVM hex keys have **no** passphrase. PEM files (`owner_private.pem` / `user_private.pem`) use the passphrase you set at creation.

---

## 6. Prove checklist (AccessGranted → SetWeights)

### Definition of done

| Gate | Required |
|------|----------|
| A | AccessGranted mined |
| B | Validator AUDIT receipt + broadcast |
| C | Miner stored AUDIT |
| D | Batch + successful pin/merkle/index scores |
| E | SetWeights SUCCESS (eventually) + metagraph weight |

### Steps

1. **Validator logs (~12–60s):** `AccessGranted → AUDIT candidate` → `built receipt op_type=AUDIT` → `AUDIT receipt broadcast`. Often also WRITE from `ResourceRegistered`.
2. **Miner:** `handle_receipt op_type=AUDIT`;  
   `sqlite3 miner_store_v1.db "SELECT op_type, COUNT(*) FROM receipts GROUP BY op_type;"`
3. **Batch / challenge:** `closed batch_cid=… audit_leaves=…`; pin/merkle HTTP 200; EMA for miner UID.  
   Merkle null → tip not in closed batch yet; wait for `closed batch_cid`.
4. **Weights:** `maybe_set_weights: SUCCESS` or rate-limit wait (~100 blocks), then SUCCESS.  
   `btcli subnets metagraph 573 -n test`

### Troubleshooting

| Symptom | Check |
|---------|--------|
| No AUDIT logs | Still mock? Wrong `--access-registry`? |
| eth_getLogs errors | RPC / chunking; `start_block` vs tip |
| Event on chain, no receipt | Cursor ahead of event — lower `start_block`, restart |
| WRITE but no AUDIT | verifyAccess didn’t succeed |
| Hardhat events “missing” | Need tip ≥ event_block + ~6 |
| Balance 0 with Hardhat keys | Deploy hit testnet RPC — set `RPC_URL=http://127.0.0.1:8545` |

### `evm.start_block`

First block the poller will consider. Too high → miss events. Too low → more history. On Hardhat use `0` or `1`.

---

## 7. Multi-grantee (Part B)

Prove multiple users hold grants on one resource:

1. Three DH identities + three H160s (owner, A, B).
2. Encrypt once → `registerResource`.
3. Per grantee: DH → wrap → `grantAccess` with distinct `keccak256(secret)`.
4. Assert `hasGrant` for A and B; commitments differ.
5. A and B each `verifyAccess` → both `AccessGranted`.
6. Ungranted C → `"Not granted"`. Optional: revoke A; B still works.
7. Validator should emit **two AUDIT** receipts (distinct actors / tx ids).

---

## 8. After A–E pass

1. Drop `LOCAL_MINER_AXON` (public axon + NAT).  
2. Second miner / second validator.  
3. Fail-closed Presign gateway + UI on one AccessRegistry.  
4. `require_evm_association: true` when H160↔SS58 is ready.  
5. Then Finney / EVM 964.

---

## Quick three-terminal copy-paste (testnet)

```bash
# Shared
source /Users/aidanosullivan/YumaBittensor/bt_venv/bin/activate
export CONTRACT_ADDRESS=0xCA8Cb3EbD9D1Be4d555cA5800Edd116D789660E1   # or your deploy
export RPC_URL=https://test.chain.opentensor.ai CHAIN_ID=945
```

```bash
# A — miner
cd /Users/aidanosullivan/YumaBittensor/Main-Project/subnet
python miner.py --netuid 573 --wallet.name test-miner --wallet.hotkey default \
  --subtensor.network test --axon.port 8091 --axon.ip auto --logging.info
```

```bash
# B — validator
cd /Users/aidanosullivan/YumaBittensor/Main-Project/subnet
LOCAL_MINER_AXON=127.0.0.1:8091 python validator.py \
  --config validator.config.yml --netuid 573 --network test \
  -w test-validator -H default --no-mock-events \
  --access-registry "$CONTRACT_ADDRESS"
```

```bash
# C — unlock
cd /Users/aidanosullivan/YumaBittensor/Main-Project
export OWNER_PRIVATE_KEY=0x… USER_PRIVATE_KEY=0x…
python key-generation/access_registry_client.py
```
