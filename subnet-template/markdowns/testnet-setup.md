# MediLink testnet setup (netuid 573)

Run these in **your own terminal** (so you can type coldkey passwords). Activate the venv first:

```bash
source /Users/aidanosullivan/YumaBittensor/bt_venv/bin/activate
cd /Users/aidanosullivan/YumaBittensor/Main-Project
```

**Balances (as of last check):** AidanColdKey ≈ τ4 free; `test-validator` / `test-miner` = 0.  
**Registration burn on 573:** ≈ τ0.95 each (re-check with `btcli subnets burn-cost 573 -n test`).

---

## 1. Name the subnet `medilink` + start emissions

```bash
btcli sudo set-identity \
  --netuid 573 \
  --name medilink \
  --description "MediLink EHR access registry subnet" \
  -w AidanColdKey -H AidanHotKey -n test -y --no-mev-shield

btcli sudo start --netuid 573 -w AidanColdKey -n test -y --no-mev-shield

btcli sudo get-identity 573 -n test
btcli subnets show 573 -n test
```

---



## 2. Fund neuron wallets from AidanColdKey

Leave a little on Aidan for fees. With ~τ4 and ~τ0.95 burn:

```bash
btcli wallet transfer -w AidanColdKey --dest test-miner --amount-tao 1.05 -n test -y --no-mev-shield
btcli wallet transfer -w AidanColdKey --dest test-validator --amount-tao 2.55 -n test -y --no-mev-shield

btcli wallet balance -w AidanColdKey -n test
btcli wallet balance -w test-miner -n test
btcli wallet balance -w test-validator -n test
```

Adjust amounts if burn has moved.

---



## 3. Register miner + validator, then stake

```bash
btcli subnets register --netuid 573 -w test-miner -H default -n test -y
btcli subnets register --netuid 573 -w test-validator -H default -n test -y

# Stake most of remaining validator free balance (keep a little for fees)
btcli stake add --netuid 573 -w test-validator -H default \
  --amount-tao 1.2 --no-slippage-protection -n test -y --no-mev-shield

btcli subnets metagraph 573 -n test
btcli wallet overview -w test-validator -n test
```

---



## 4. Deploy AccessRegistry (Subtensor EVM chain 945)

Use free substrate TAO on your coldkey to fund an EVM key, then deploy.
Full concepts: [`../key-generation/Subtensor_EVM_Deploy.md`](../key-generation/Subtensor_EVM_Deploy.md).

```bash
btcli evm key new --name access-registry -w AidanColdKey
btcli evm fund --evm-key access-registry --amount-tao 0.2 -w AidanColdKey -n test -y

btcli evm key export --name access-registry -w AidanColdKey --private-key   # copy 0x…
export OWNER_PRIVATE_KEY=0x...
export RPC_URL=https://test.chain.opentensor.ai
export CHAIN_ID=945

python key-generation/deploy_access_registry.py
source key-generation/deployment.exports.sh

# Point validator at live registry
# Edit subnet-template/validator.config.yml:
#   evm.access_registry: "$ACCESS_REGISTRY"
#   evm.mock_events: false
```

Or: `make deploy-registry OWNER_PRIVATE_KEY=0x...`

---



## 5. Run miner then validator

Bittensor v11 has no Axon/Dendrite stack. `minerv1.py` serves FastAPI on `--axon.port`
and publishes with `ServeAxon` (must use a **non-loopback** IP — `127.0.0.1` is rejected
on-chain). For same-machine smoke tests, set `LOCAL_MINER_AXON=127.0.0.1:8091` on the
validator so it can reach the miner without a successful ServeAxon.

```bash
cd subnet-template

# Terminal A — miner (auto-detects public IP, or pass --axon.ip explicitly)
python minerv1.py \
  --netuid 573 \
  --wallet.name test-miner \
  --wallet.hotkey default \
  --subtensor.network test \
  --axon.port 8091 \
  --axon.ip auto \
  --logging.info

# Confirm on-chain axon (should show your public IP:8091 for the miner uid):
# btcli subnets metagraph 573 -n test

# If ServeAxon failed, publish manually:
# btcli axon set --netuid 573 --ip YOUR_PUBLIC_IP --port 8091 -w test-miner -H default -n test -y
#
# Home NAT: forward TCP 8091 on the router to this machine so external validators can connect.
# Terminal B — validator (live registry)
LOCAL_MINER_AXON=127.0.0.1:8091 \
python validatorv1.py \
  --config validator.config.yml \
  --netuid 573 \
  --network test \
  -w test-validator \
  -H default \
  --no-mock-events \
  --access-registry "$ACCESS_REGISTRY"
```

---



## Role mapping


| Role         | Wallet           | Hotkey        |
| ------------ | ---------------- | ------------- |
| Subnet owner | `AidanColdKey`   | `AidanHotKey` |
| Validator    | `test-validator` | `default`     |
| Miner        | `test-miner`     | `default`     |


