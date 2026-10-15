# Publishing AccessRegistry on Subtensor EVM

How MediLink’s Solidity contract gets onto Bittensor’s chain, how that relates to your
**testnet TAO**, and how validators read it afterward.

Official background: [Bittensor EVM guide](https://www.bittensor.com/docs/guides/evm),
`[fund-evm-key](https://www.bittensor.com/docs/tx/fund-evm-key)`.

---

## Two layers, one chain

Subtensor runs **native Substrate** and a full **EVM** on the same network. They share
TAO as the currency but use different account types and RPCs.


|                     | Substrate (subnet side)                             | EVM (contract side)                |
| ------------------- | --------------------------------------------------- | ---------------------------------- |
| Address shape       | SS58 (`5…`)                                         | H160 (`0x…`)                       |
| Signing             | sr25519 / ed25519 coldkey–hotkey                    | secp256k1 EVM key                  |
| What you use it for | Create subnet, register, stake, run miner/validator | Deploy / call `AccessRegistry`     |
| Testnet RPC         | `btcli … -n test` (WebSocket)                       | `https://test.chain.opentensor.ai` |
| Chain ID            | (substrate preset `test`)                           | **945** (mainnet EVM is **964**)   |


Your faucet / free balance on `AidanColdKey` is **substrate TAO**. Deploying a contract
needs **EVM gas** on an H160. Those are not the same wallet file — but you can move TAO
from the coldkey into an EVM key without a second faucet.

```
  SS58 coldkey ──btcli evm fund──► EVM key's ss58 "mirror"
                                         │
                                         ▼
                              H160 balance on chain 945
                                         │
                                         ▼
                              deploy AccessRegistry (0x…)
                                         │
                                         ▼
                     validatorv1 eth_getLogs → receipts → miners
```

---



## Why an H160 at all?

Smart contracts on Subtensor use the Ethereum transaction model:

- Deploy and writes are signed by a **secp256k1** private key.
- Gas is paid from that key’s **EVM** balance (shown with 18 decimals in MetaMask).
- Your Bittensor coldkey cannot sign those txs directly.

So the flow is always: **create (or import) an EVM key → fund it → deploy**.

The EVM key is stored next to the wallet as keystore V3 under
`~/.bittensor/wallets/<wallet>/evmkeys/`. It is **not** derived from the coldkey seed by
default (`btcli evm key new`), which keeps browser / dapp risk away from your SS58 seed.

---



## Funding EVM from your testnet TAO

Every H160 has a deterministic **ss58 mirror**
(`blake2_256("evm:" ++ h160)`). Native TAO sent to that mirror becomes the EVM account’s
balance.

```bash
# Create a named EVM key under your coldkey wallet
btcli evm key new --name access-registry -w AidanColdKey
btcli evm key list -w AidanColdKey

# Move substrate TAO → that EVM key (you unlock the coldkey)
btcli evm fund --evm-key access-registry --amount-tao 0.2 -w AidanColdKey -n test -y

# Confirm on the EVM JSON-RPC view
btcli evm balance access-registry -w AidanColdKey -n test
btcli evm doctor -n test
```

Notes:

- Transfers are **irreversible** into that EVM key; only its private key can spend afterward.
- Expect slightly less than you sent on the EVM side: the mirror keeps a tiny existential
deposit (~500 rao) that MetaMask / EVM balance views do not show.
- ~0.1–0.5 τ is usually enough to deploy `AccessRegistry` and run a few register/grant txs.
- Decimals trap: substrate uses 9 decimals (rao); EVM displays 18 (wei). Same TAO, different
UI scaling.

You can also fund an arbitrary `0x…` with `btcli tx fund-evm-key --evm-address 0x…`.

---



## Deploying AccessRegistry

This repo’s path: compile `AccessRegistry.sol` and send a create-tx via
`[deploy_access_registry.py](deploy_access_registry.py)`.

```bash
cd /path/to/Main-Project

# One-shot unlock to print hex (do not commit; do not paste into chat logs)
btcli evm key export access-registry -w AidanColdKey --private-key --show

export OWNER_PRIVATE_KEY=0x...          # from export
export RPC_URL=https://test.chain.opentensor.ai
export CHAIN_ID=945

python key-generation/deploy_access_registry.py
source key-generation/deployment.exports.sh
```

Outputs:

- `[deployment.json](deployment.json)` — address, ABI, `chain_id`, RPC
- `[deployment.exports.sh](deployment.exports.sh)` — `ACCESS_REGISTRY` / `CONTRACT_ADDRESS`

**Check** `chain_id` **is** `945`**.** A leftover Hardhat deploy (`31337`) will not be visible to
testnet validators.

Makefile shortcut (same env):

```bash
make deploy-registry OWNER_PRIVATE_KEY=0x...
```

Alternative (btcli-native): compile to a Hardhat/Foundry artifact, then
`btcli evm deploy ARTIFACT.json --evm-key access-registry -w AidanColdKey -n test`.
MediLink’s default remains the Python deployer above.

Solidity tip from Opentensor: compile with **≤ 0.8.24** and EVM target **Cancun** (this
project uses `0.8.20`).

---



## How the subnet validator uses the contract

Validators do **not** need the EVM private key to score miners. They only need:

1. EVM HTTP RPC (`https://test.chain.opentensor.ai`)
2. Chain id `945`
3. The deployed `access_registry` address
4. `mock_events: false` (live `eth_getLogs`)

In `[../subnet-template/validator.config.yml](../subnet-template/validator.config.yml)`:

```yaml
evm:
  rpc_url: https://test.chain.opentensor.ai
  chain_id: 945
  access_registry: "0xYOUR_DEPLOYED_ADDRESS"
  mock_events: false
```

Loop (see also [`../../subnet-template/markdowns/ARCHITECTURE.md`](../../subnet-template/markdowns/ARCHITECTURE.md)):

1. Owner/user txs call `registerResource` / `grantAccess` / … on the contract.
2. `validatorv1` polls logs (`ResourceRegistered`, `AccessGranted`, …).
3. Builds MediLink receipts and fans them out to miners.
4. Challenges / proofs → weights on the **substrate** subnet (e.g. netuid 573).

Miners never talk to the contract; they store and prove receipt payloads.

---



## H160 ↔ SS58 association (optional)

For stronger actor identity, map an EVM key to a subnet hotkey:

```bash
btcli evm associate --netuid 573 --evm-key access-registry \
  -w AidanColdKey -H AidanHotKey -n test -y
```

`validatorv1` can resolve H160 → SS58 via `SubtensorModule.EVMAssociation`. With
`require_evm_association: false` (current default), unmapped actors still produce receipts
using the checksum H160 as a fallback id.

---



## Hardhat vs testnet (don’t mix)


| Target                | RPC                                | Chain ID | Funding                       |
| --------------------- | ---------------------------------- | -------- | ----------------------------- |
| Local Hardhat         | `http://127.0.0.1:8545`            | `31337`  | Hardhat account keys          |
| Subtensor testnet EVM | `https://test.chain.opentensor.ai` | `945`    | `btcli evm fund` from coldkey |
| Subtensor mainnet EVM | `https://lite.chain.opentensor.ai` | `964`    | real TAO — care required      |


Pointing a testnet validator at a Hardhat address, or leaving `mock_events: true` after
deploy, means you never see live logs.

---



## Quick checklist

- [ ] Free TAO on coldkey (`btcli wallet balance -w AidanColdKey -n test`)
- [ ] EVM key created (`btcli evm key list -w AidanColdKey`)
- [ ] Funded (`btcli evm fund …`; `btcli evm balance` shows TAO)
- [ ] Deployed with `CHAIN_ID=945`; `deployment.json` matches
- [ ] Validator `access_registry` set; `mock_events: false`
- [ ] Optional: associate EVM key on netuid 573

Related files: [`../README.md`](../README.md), [`../AccessRegistry.sol`](../AccessRegistry.sol),
[`../deploy_access_registry.py`](../deploy_access_registry.py),
[`../../Contract_Logic.md`](../../Contract_Logic.md),
[`../../subnet-template/markdowns/OPERATOR_GUIDE.md`](../../subnet-template/markdowns/OPERATOR_GUIDE.md).