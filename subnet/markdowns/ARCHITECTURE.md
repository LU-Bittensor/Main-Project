# MediLink Architecture

How the subnet turns AccessRegistry events into an auditable incentive ledger.

**Code:** [`validator.py`](../validator.py), [`miner.py`](../miner.py), [`protocol.py`](../protocol.py), [`../key-generation/AccessRegistry.sol`](../../key-generation/AccessRegistry.sol)  
**Run / prove:** [`OPERATOR_GUIDE.md`](OPERATOR_GUIDE.md) · **Epic / Hippius:** [`INTEGRATIONS.md`](INTEGRATIONS.md)

---

## 1. What MediLink is

MediLink is a Bittensor subnet that stores **signed audit receipts** for healthcare storage and access events. It does **not** store plaintext EHR data.

| Layer | Stores | Role |
|-------|--------|------|
| **AccessRegistry** (Subtensor EVM) | Commitments, resource metadata, access events | Authorization + canonical access log |
| **Validator** | In-memory tips, resource cache, EMA scores | Polls EVM, builds/signs receipts & batches, challenges miners, SetWeights |
| **Miner** | Receipts/batches in SQLite + local CID store | Pins ledger objects; answers pin / Merkle / index challenges |
| **Hippius / off-chain store** | Encrypted payloads + DEK wrap sidecars | Bytes; never MediLink receipts |
| **Gateway / app** | — | **Fail-closed** download only after `AccessGranted` |

The subnet **audits**; the gateway **gates** bytes.

---

## 2. End-to-end flow

```
Owner ↔ User          off-chain X25519 DH → shared_secret
Owner → storage       PutObject ciphertext + wraps/{grantee}.bin
Owner → Registry      registerResource → ResourceRegistered
Owner → Registry      grantAccess(commitment) → AccessGrantSet (no receipt by default)
User  → Registry      requestChallenge → ChallengeIssued (no receipt)
User  → Registry      verifyAccess → AccessGranted / AccessDenied
User  → Gateway       fetch payload + own wrap only if AccessGranted

every ~12s:
Validator → EVM       eth_getLogs(ACCESS_REGISTRY)
Validator             CommitmentEvent → signed Receipt (WRITE / AUDIT / …)
Validator → Miners    HTTP POST /receipt, then /batch when window closes
Validator → Miners    /challenge/pin + /challenge/merkle + /index → EMA → SetWeights
```

**One contract** indexes many resources. Validators poll a **single** address. Off-chain DH produces a per-grantee secret; only `keccak256(secret)` is on-chain at `commitments[resourceId][grantee]`.

---

## 3. Roles

| Role | Responsibility |
|------|----------------|
| **Owner** | DH with each user, upload ciphertext + wraps, `registerResource` / `grantAccess` / `revokeAccess` |
| **User (grantee)** | DH, then `requestChallenge` + `verifyAccess` |
| **AccessRegistry** | Multi-grantee commitments; emits registry events |
| **Gateway** | Presign payload + that user’s wrap only after confirmed `AccessGranted` |
| **Validator** | Sole receipt constructor/signer; challenges; weights |
| **Miner** | Persist receipts/batches; answer challenges |

---

## 4. Event → receipt mapping

| Event | Receipt? | `op_type` | Actor | `data_cid` |
|-------|----------|-----------|-------|------------|
| `ResourceRegistered` | Yes | `WRITE` (first tip) / `UPDATE` | owner | event `dataCid` (preferred) |
| `AccessGranted` | Yes | `AUDIT` | requester | resource cache → `resources()` eth_call → hash-derived fallback |
| `AccessDenied` | Skip by default (`skip_deny_receipts`) | — | — | — |
| `AccessGrantSet` / `AccessRevoked` | Skip by default | — | — | — |
| `ChallengeIssued` | **Never** | — | — | — |

**`tx_id`:** `{transactionHash}-{logIndex}` (lowercase). Mock mode may use a UUID.  
**`timestamp_ns`:** validator wall clock at ingest (not block time).  
**Dedup:** `seen_log_keys` across polls/reorg re-reads.  
**Resource cache:** `resourceId → {data_cid, data_hash, owner}` filled from `ResourceRegistered`.

### Receipt (validator-signed core)

Important fields: `spec_version`, `tx_id`, `timestamp_ns`, `actor_evm_addr` (or SS58 when association exists), `op_type`, `data_cid`, `prev_cid`, `hippius_status`, `validator_hotkey`, `validator_sig`. Miner adds `miner_cid` / `miner_hotkey` / `miner_sig` after pin.

`op_type` values: `WRITE` · `UPDATE` · `DELETE_REQ` · `AUDIT`.

### Batch envelope

Closed when **~60s** window elapses or pending size hits cap (`MAX_BATCH_SIZE`, currently **4** in v1). Contains sorted `receipt_cids`, `merkle_root`, `prev_batch_cid`, validator signature.

---

## 5. EVM poller

- Interval ~**12s**; filter one `ACCESS_REGISTRY` address.
- Cursor: `evm.start_block` → `last_evm_block`; each tick scans `from_block = last + 1`.
- Stay **`REORG_DEPTH` (6)** blocks behind tip when tip is deep enough (Hardhat: mine past the cushion or events near tip wait).
- Chunk logs (`EVM_LOG_CHUNK = 128`) for public RPC limits; retry on failure **without** advancing cursor.
- **`mock_events: true`:** invent synthetic commitments (no `eth_getLogs`). **`false`:** live registry (required for real AccessGranted → AUDIT).

H160→SS58 via `EVMAssociation` when available; with `require_evm_association: false` (current testnet default), checksum H160 may be used as actor id.

---

## 6. Validator ↔ miner transport (HTTP)

Bittensor v11 has no Axon/Dendrite stack for this path.

| Side | Mechanism |
|------|-----------|
| Miner | FastAPI on `--axon.port`; `ServeAxon` publishes `ip:port` on metagraph |
| Validator | `_SynapseFanout` (`httpx.post`) to miner endpoints |
| Envelope | `MessageSynapse(payload={...})` JSON |

**Miner routes:** `POST /receipt`, `/batch`, `/challenge/pin`, `/challenge/merkle`, `/index` · `GET /health`

Same-host smoke: `LOCAL_MINER_AXON=127.0.0.1:8091` (loopback ServeAxon is rejected on-chain). Challenges fan out with a small thread pool (pin + merkle + index in parallel).

---

## 7. Miner storage

| Store | Durable? | Purpose |
|-------|----------|---------|
| SQLite `receipts` | Yes | Accepted receipts (`receipt_cid`, `miner_cid`, `op_type`, …) |
| SQLite `actor_tips` | Yes | Per-actor tip CID + count |
| SQLite `batches` | Yes | Batch envelopes + leaf lists |
| `LocalIPFSClient` | In-memory | CID → JSON content for pin responses |
| `batch_tree_cache` | In-memory | Fast Merkle proofs; **rebuilt from SQLite** on cache miss |

Miners never talk to EVM. They only persist what validators emit.

---

## 8. Challenges and scoring

Each challenge round (per active miner):

1. **Pin** — return receipt JSON for a `receipt_cid` from the **last closed batch** (not the live tip still in `batch_pending`).
2. **Merkle** — inclusion proof vs that batch’s root (empty path OK for single-leaf trees).
3. **Index** — actor tip / tip_batch consistency.

| Dimension | Weight | Meaning |
|-----------|--------|---------|
| Availability | 0.15 | Pin returns receipt JSON |
| Latency | 0.05 | vs ~500ms target / 5s cutoff |
| Integrity | 0.50 | sig 0.40 + CID 0.30 + merkle 0.30; **fail-closed:** bad/missing merkle caps integrity at 0.40 |
| Consistency | 0.30 | tip + tip_batch agreement (absolute count drift is diagnostic only) |

EMA α = **0.3**. Weights submitted via `maybe_set_weights` subject to ~**100-block** chain rate limit.

---

## 9. Fail-closed access (gateway policy)

```
IF AccessGranted(resourceId, requester) in confirmed tx
  THEN MAY PresignedGet payload + wraps/{requester}.bin only
ELSE
  DO NOT serve download
```

The audit subnet cannot stop a buggy gateway that skips `verifyAccess`.

---

## 10. Throughput notes

- ~2 EVM txs per interactive unlock (`requestChallenge` + `verifyAccess`).
- Receipt only terminal outcomes (`AccessGranted`); never `ChallengeIssued`.
- High QPS saturates EVM/gas and subnet fan-out; later: access-session TTL so UI clicks are not all on-chain.

---

## 11. Code map

| Artifact | Role |
|----------|------|
| [`AccessRegistry.sol`](../../key-generation/AccessRegistry.sol) | Multi-grantee registry |
| [`deploy_access_registry.py`](../../key-generation/deploy_access_registry.py) | Deploy → `deployment.json` |
| [`access_registry_client.py`](../../key-generation/access_registry_client.py) | DH + register/grant/verify |
| [`ehr_hippius.py`](../../key-generation/ehr_hippius.py) | Envelope encrypt + wraps demo |
| [`validator.config.yml`](../validator.config.yml) | netuid + EVM knobs |
| [`Hippius_EHR_Storage.md`](../../Hippius_EHR_Storage.md) | Ciphertext layout (Main-Project) |
