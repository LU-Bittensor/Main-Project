# Access Registry ↔ MediLink Wiring

How authorized data access on Subtensor EVM becomes an audited subnet ledger.

**Decision:** one `AccessRegistry` contract indexes many resources. Validators poll a single address. Off-chain X25519 Diffie–Hellman produces a **per-grantee** shared secret; only `keccak256(secret)` is stored on-chain at `commitments[resourceId][grantee]`. Hippius holds ciphertext + per-user DEK wrap sidecars ([`Hippius_EHR_Storage.md`](../Hippius_EHR_Storage.md)).

---

## 1. Who does what

| Role | Responsibility |
|---|---|
| **Owner** | Runs DH with each user, uploads ciphertext + wrap sidecar, `registerResource` then `grantAccess` |
| **User** | Completes DH, calls `requestChallenge` then `verifyAccess` (must already be granted) |
| **AccessRegistry** | Per-(resourceId, grantee) commitments; emits `ResourceRegistered`, `AccessGrantSet`, `AccessRevoked`, `AccessGranted`, `AccessDenied`, `ChallengeIssued` |
| **Gateway / app** | **Fail-closed:** PresignedGet of **payload + that user’s wrap** only after `AccessGranted` for `(resourceId, requester)` |
| **Validator** | Polls `eth_getLogs` on the registry, builds signed receipts, batches them, challenges miners, sets weights |
| **Miner** | Pins receipts/batches, answers pin / Merkle-proof / index challenges |
| **Hippius** | Stores encrypted payloads + wrap sidecars; never stores MediLink receipts |

The subnet **audits**; it does not gate bytes. Gating is the gateway’s job.

---

## 2. End-to-end flow

```
Owner ↔ User          off-chain X25519 DH → shared_secret
Owner → Hippius       PutObject payload + wraps/{grantee}.bin
Owner → Registry      registerResource(resourceId, dataHash, dataCid)
Owner → Registry      grantAccess(resourceId, grantee, commitment)  → AccessGrantSet (no receipt by default)
User  → Registry      requestChallenge(resourceId)  → ChallengeIssued  (no receipt)
User  → Registry      verifyAccess(resourceId, response)
Registry              AccessGranted / AccessDenied
User  → Gateway/Hippius  fetch payload + own wrap only if AccessGranted

every ~12s:
Validator → Registry  eth_getLogs(address=ACCESS_REGISTRY, fromBlock=cursor)
Validator             decode → CommitmentEvent → signed Receipt (AUDIT / WRITE / UPDATE)
Validator → Miners    dendrite broadcast Receipt
Validator → Miners    BatchEnvelope (Merkle root) when window/size hit
Validator → Miners    pin / proof / index challenges → EMA → set_weights
```

---

## 3. Poll loop (validator)

**Trigger:** every `EVM_POLL_INTERVAL_S` (default 12s).

**Filter (single contract):**

```
eth_getLogs({
  fromBlock: last_evm_block + 1,   // after reorg cushion: max(0, tip - REORG_DEPTH)
  toBlock:   "latest",
  address:   ACCESS_REGISTRY,      // one address only
  topics: [
    [
      keccak256("ResourceRegistered(bytes32,address,bytes32,string)"),
      keccak256("AccessGranted(bytes32,address,bytes32,uint256)"),
      // AccessDenied: optional; default SKIP_DENY_RECEIPTS=true
    ]
  ]
})
```

**Do not** create receipts for `ChallengeIssued` — that roughly doubles load with little audit value.

**Cursor / reorg:**

1. On success, set `last_evm_block = max(processed block numbers)`.
2. On suspected reorg (duplicate/missing logs), roll back `last_evm_block` by `REORG_DEPTH` (default 6) and reprocess.
3. On RPC failure: retry ≤3 with backoff; **do not** advance the cursor.

**H160 → SS58:** resolve requester/owner via `SubtensorModule.EVMAssociation`. If unmapped: log and **discard** (no receipt).

**CLI / env knobs:**

| Knob | Meaning | Default |
|---|---|---|
| `ACCESS_REGISTRY` / `--access-registry` | Registry contract address | required for live poll |
| `EVM_RPC_URL` / `--evm-rpc` | EVM HTTP RPC | `https://test.chain.opentensor.ai` |
| `EVM_CHAIN_ID` / `--evm-chain-id` | `945` testnet, `964` mainnet, `31337` Hardhat | `945` |
| `EVM_POLL_INTERVAL_S` | Sleep between poll cycles | `12` |
| `REORG_DEPTH` | Blocks to rewind on reorg | `6` |
| `SKIP_DENY_RECEIPTS` | Ignore `AccessDenied` for receipts | `true` |
| `--mock-events` | Synthetic events (local subnet without EVM) | on for local demos |

---

## 4. Metadata delivery (event → receipt)

### Registry events

| Event | Indexed | Data fields |
|---|---|---|
| `ResourceRegistered(bytes32 resourceId, address owner, bytes32 dataHash, string dataCid)` | — | metadata; `dataCid` = Hippius payload key |
| `AccessGrantSet(bytes32 indexed resourceId, address indexed grantee, bytes32 dataHash)` | resourceId, grantee | **skip** receipt by default |
| `AccessRevoked(bytes32 indexed resourceId, address indexed grantee)` | resourceId, grantee | skip or AUDIT |
| `AccessGranted(bytes32 indexed resourceId, address indexed requester, bytes32 dataHash, uint256 grantedAt)` | resourceId, requester | enough for AUDIT without `eth_call` |
| `AccessDenied(bytes32 indexed resourceId, address indexed requester, uint256 deniedAt)` | resourceId, requester | optional receipt |
| `ChallengeIssued(...)` | forensic only | **no receipt** |

### Event → `CommitmentEvent` → receipt

| Source | `op_type` | `actor_*` | `data_cid` | `hippius_status` |
|---|---|---|---|---|
| `ResourceRegistered` | `WRITE` if first tip for actor else `UPDATE` | owner H160 → SS58 | event `dataCid` (prefer) or CIDv1 from `dataHash` | Hippius exists check |
| `AccessGranted` | `AUDIT` | requester H160 → SS58 | CIDv1 from `dataHash` or registry-stored cid | usually `SUCCESS` if CID known |
| `AccessDenied` | skip (default) or `AUDIT` + deny flag | requester | from resource map if needed | `FAIL` if receipted |

Receipt fields follow [`configuration.md`](configuration.md) (`tx_id`, `timestamp_ns` = validator wall clock at ingest, `prev_cid` actor tip, validator signature, etc.).

Validators must **not** need a per-log `eth_call` for grant audits: `AccessGranted` carries `dataHash`.

---

## 5. Active monitoring (subnet)

After each poll cycle:

1. **Receipt construction** — sign validator-scope fields; compute receipt CID; advance actor tip; append to `batch_pending`.
2. **Broadcast** — `dendrite` / HTTP equivalent to miner axons with the receipt synapse.
3. **Batch close** — when `BATCH_WINDOW_NS` (60s) or `MAX_BATCH_SIZE` (256 prod / smaller in v1) hits: sort CIDs, Merkle root, signed `BatchEnvelope`, broadcast.
4. **Challenges** — pin challenge (return receipt JSON), Merkle proof challenge, index query (actor tip/count).
5. **Scoring** — availability / latency / integrity / consistency → EMA.
6. **Weights** — normalize EMA and `set_weights` subject to chain rate limit.

Miners never talk to EVM. They only persist what validators emit.

---

## 6. Fail-closed access rule

```
IF AccessGranted(resourceId, requester) in confirmed tx
  THEN gateway MAY mint short-lived PresignedGet for:
       - payload key (dataCid)
       - wraps/{requester_h160}.bin only
ELSE
  DO NOT serve plaintext or ciphertext download
```

The audit subnet can prove an access was recorded; it cannot stop a buggy gateway that skips `verifyAccess`.

See [`../Hippius_EHR_Storage.md`](../Hippius_EHR_Storage.md) for envelope layout and multi-grantee wraps.

---

## 7. Throughput / overcrowding

### Is it a problem?

**Yes at high interactive QPS; controllable with a single registry.** Per-resource contracts would make validator discovery/log polling fail first.

### Load surfaces

1. **EVM** — ~2 txs per access (`requestChallenge` + `verifyAccess`).
2. **Validator RPC** — one `eth_getLogs` on one address per poll (cheap).
3. **Subnet fan-out** — each receipt → all miners; batches and challenges scale with grant rate.
4. **Noise** — receipting `ChallengeIssued` ≈ 2× volume; do not.

### Rough capacity

- ~4 grants/s → ~256 receipts/min ≈ one full 256-batch/min (borderline).
- Tens of grants/s → batch backlog, challenge timeouts, stale weights.
- EVM gas/latency usually saturates before the subnet if every UI click is on-chain.

### Mitigations (implemented as policy)

- Single registry + indexed `resourceId` / `requester`.
- Receipts only on terminal outcomes (`AccessGranted`; denials skipped by default).
- No receipts for `ChallengeIssued`.
- Batch window + size caps; reorg depth cursor.
- Later: access-session TTL so repeated reads are not one EVM tx each.

---

## 8. Environment matrix (testnet / local)

Bittensor subnet RPC ≠ Subtensor EVM RPC.

| Stage | Bittensor | EVM | Notes |
|---|---|---|---|
| **A** Subnet only | `make all` local (`ws://127.0.0.1:9945`) | `--mock-events` | Receipts/miners/challenges without contract |
| **B** Contract + DH | optional | Hardhat `31337` or EVM testnet **945** (`https://test.chain.opentensor.ai`) | Deploy `AccessRegistry`, run `key-generation/access_registry_client.py` |
| **C** Full public testnet | Bittensor testnet subnet | Registry on 945; validator `--evm-rpc` + `--access-registry` | Need funded EVM keys + EVMAssociation |
| **D** Local Docker full wire | local subnet | only if localnet exposes EVM | Makefile alone does **not** imply EVM on 9944/9945 |

### Stage C checklist

- [ ] Funded EVM keys on chain id **945** (owner + user)
- [ ] Deploy `AccessRegistry`; set `ACCESS_REGISTRY`
- [ ] Validator: `EVM_RPC_URL`, `EVM_CHAIN_ID=945`, POA middleware, live poll (`--mock-events` off)
- [ ] Map H160↔SS58 via EVMAssociation for actors you expect receipts for
- [ ] Confirm `AccessGranted` appears in validator logs → miner pin → batch

---

## 9. Dry-run checklist

### A — Local subnet (mock)

```bash
source /path/to/bt_venv/bin/activate
make all   # or attach to existing local_chain
# terminal 1
python subnet-template/minerv1.py --netuid 2 ...
# terminal 2
python subnet-template/validatorv1.py --netuid 2 --mock-events ...
```

Expect synthetic commitments → receipts printed → miner DB growth → challenges.

### B — Deploy registry + DH client (EVM 945)

```bash
pip install -r subnet-template/requirements.txt

export RPC_URL=https://test.chain.opentensor.ai
export CHAIN_ID=945
export OWNER_PRIVATE_KEY=0x...          # funded H160
python key-generation/deploy_access_registry.py
# → writes key-generation/deployment.json and prints exports

# optional: exercise register + AccessGranted
export CONTRACT_ADDRESS=0x...           # from deploy output
export ACCESS_REGISTRY=$CONTRACT_ADDRESS
export USER_PRIVATE_KEY=0x...
python key-generation/access_registry_client.py
```

Or: `make deploy-registry OWNER_PRIVATE_KEY=0x...`

### C — Hybrid / full wire (validator polls deployed registry)

```bash
# After deploy, either export ACCESS_REGISTRY or set in validator.config.yml:
#   evm.access_registry: "0x..."
#   evm.mock_events: false

export ACCESS_REGISTRY=0x...
export EVM_RPC_URL=https://test.chain.opentensor.ai
export EVM_CHAIN_ID=945

python subnet-template/validatorv1.py \
  --config subnet-template/validator.config.yml \
  --no-mock-events \
  --access-registry "$ACCESS_REGISTRY" \
  --evm-rpc "$EVM_RPC_URL" \
  --evm-chain-id 945 \
  --network ws://127.0.0.1:9945 \
  --netuid 2
```

Or: `make validator-live ACCESS_REGISTRY=0x...`

Trigger an access with the Python client; within ~12s the validator should emit an `AUDIT` / `WRITE` receipt.

---

## 10. Code map

| Artifact | Role |
|---|---|
| [`../key-generation/AccessRegistry.sol`](../key-generation/AccessRegistry.sol) | Multi-grantee registry (`grantAccess` / `revokeAccess`) |
| [`../key-generation/deploy_access_registry.py`](../key-generation/deploy_access_registry.py) | Compile + deploy → `deployment.json` |
| [`../key-generation/access_registry_client.py`](../key-generation/access_registry_client.py) | DH + register / grant / challenge / verify |
| [`../key-generation/ehr_hippius.py`](../key-generation/ehr_hippius.py) | Envelope encrypt, wrap sidecars, store/unlock demo |
| [`../Hippius_EHR_Storage.md`](../Hippius_EHR_Storage.md) | Ciphertext layout + threat model |
| [`validator.config.yml`](validator.config.yml) | Bittensor + EVM knobs (`evm.access_registry`, `mock_events`) |
| [`validatorv1.py`](validatorv1.py) | `poll_evm_commitments()` → receipts (`--config` / `--no-mock-events`); polls `ResourceRegistered` + `AccessGranted` only (skips `AccessGrantSet` / `AccessRevoked` by default) |
| [`protocol.py`](protocol.py) | Plain `MessageSynapse` envelope (v11 has no `bt.Synapse`) |
| [`configuration.md`](configuration.md) | Process 1 topic/filter spec |
| [`requirements.txt`](requirements.txt) | `bittensor>=11`, `web3`, `py-solc-x`, … |

**Note:** Miner fan-out uses a dendrite stub under bittensor v11 (no Axon/Dendrite). Receipt construction and EVM polling are live; replace `_SynapseFanout` with HTTP + `bt.http_auth` when serving real miner endpoints.
