# MediLink Subnet — Node Specification
**Version:** 1.0  
**Status:** Reference Definition  
**Scope:** Validator, Miner, and Shared Protocol Contracts

---

## Table of Contents

1. [Overview](#overview)
2. [Shared Contracts](#shared-contracts)
   - [Receipt Wire Format](#receipt-wire-format)
   - [Batch Envelope Wire Format](#batch-envelope-wire-format)
   - [Synapse Types](#synapse-types)
   - [Canonical Serialisation Rule](#canonical-serialisation-rule)
   - [Signing Scopes](#signing-scopes)
   - [MMR vs Batch Merkle Tree](#mmr-vs-batch-merkle-tree)
3. [Validator](#validator)
   - [Role Definition](#validator-role-definition)
   - [Durable State](#validator-durable-state)
   - [Process 1 — EVM Log Ingestion](#validator-process-1--evm-log-ingestion)
   - [Process 2 — Receipt Construction and Signing](#validator-process-2--receipt-construction-and-signing)
   - [Process 3 — Merkle Batch Construction](#validator-process-3--merkle-batch-construction)
   - [Process 4 — Miner Challenge and Scoring](#validator-process-4--miner-challenge-and-scoring)
   - [Process 5 — On-Chain Weight Submission](#validator-process-5--on-chain-weight-submission)
   - [Validator Function Reference](#validator-function-reference)
4. [Miner](#miner)
   - [Role Definition](#miner-role-definition)
   - [Durable State](#miner-durable-state)
   - [Process 1 — Receipt Ingestion and Pinning](#miner-process-1--receipt-ingestion-and-pinning)
   - [Process 2 — Batch Envelope Ingestion](#miner-process-2--batch-envelope-ingestion)
   - [Process 3 — Merkle Mountain Range Maintenance](#miner-process-3--merkle-mountain-range-maintenance)
   - [Process 4 — Responding to Validator Challenges](#miner-process-4--responding-to-validator-challenges)
   - [Miner Function Reference](#miner-function-reference)
5. [Scoring Function](#scoring-function)
6. [Operational Constants](#operational-constants)

---

## Overview

MediLink is a Bittensor subnet (netuid TBD) that provides a HIPAA-aligned,
distributed, immutable audit ledger for healthcare storage operations. The
subnet does not store healthcare data directly. It stores cryptographically
signed receipts that record the metadata of every storage operation performed
against Hippius SN75 by authorised actors.

**Architecture summary:**

```
EVM AccessRegistry  (single contract — Subtensor EVM, Chain ID 945/964)
        |
        | eth_getLogs (polled every 12s by validator; one address)
        | topics: ResourceRegistered | AccessGranted  (+ optional AccessDenied)
        v
Validator           (sole receipt constructor and signer)
        |
        | bt.dendrite.forward() — Receipt and BatchEnvelope synapses
        v
Miners              (persistent IPFS storage, MMR maintenance, challenge response)
        |
        | stored receipts reference
        v
Hippius SN75        (stores encrypted health payloads, NOT receipts)
```

**What miners store:** Signed receipt objects (~1 KB each) and batch envelope
objects (~4 KB each). Never plaintext health data.

**What Hippius stores:** Encrypted health payloads. The data_cid in every
receipt points here.

**What the EVM registry stores:** Per-`(resourceId, grantee)` commitments
(`keccak256(sharedSecret)`), resource metadata (`dataHash` / `dataCid` =
Hippius payload key), and access events (`AccessGranted` / `AccessDenied`) as
the canonical log of authorised operations. Grant/revoke events exist but are
skipped for receipts by default. See [`access-registry-wiring.md`](access-registry-wiring.md)
and [`../Hippius_EHR_Storage.md`](../Hippius_EHR_Storage.md).

---

## Shared Contracts

These definitions apply to both validators and miners. Any deviation from
these contracts by either party is treated as a protocol violation.

---

### Receipt Wire Format

Version: `spec_version = "1.0"`

A receipt is the fundamental unit of the MediLink audit ledger. One receipt
is created per storage operation. Receipts are content-addressed IPFS objects.
Their CID is derived from their content, making them self-verifying.

```json
{
  "spec_version":     "1.0",
  "tx_id":            "<uuid-v4-lowercase-hyphenated>",
  "timestamp_ns":     1712680000000000000,
  "actor_ss58":       "5Grwva...",
  "op_type":          "WRITE",
  "data_cid":         "bafyrei...",
  "prev_cid":         "bafyrei..." ,
  "hippius_status":   "SUCCESS",
  "validator_hotkey": "5Dxqr...",
  "validator_sig":    "0xabcdef...",
  "miner_cid":        "bafyrei...",
  "miner_hotkey":     "5FHne...",
  "miner_sig":        "0xfedcba..."
}
```

**Field definitions:**

| Field | Type | Source | Rule |
|---|---|---|---|
| `spec_version` | string | validator | Semver. Bump minor on additive changes, major on breaking changes. |
| `tx_id` | string | validator | RFC 4122 UUID v4, lowercase, hyphenated. One per operation. |
| `timestamp_ns` | int | validator | UTC Unix nanoseconds at the moment the validator's eth_getLogs call received this event. Not the EVM block timestamp. Not the miner's clock. |
| `actor_ss58` | string | validator | SS58 address with network prefix 42 (Bittensor mainnet). Resolved from H160 via SubtensorModule.EVMAssociation. |
| `op_type` | string | validator | Enum: `WRITE`, `UPDATE`, `DELETE_REQ`, `AUDIT`. No aliases. No lowercase. |
| `data_cid` | string | validator | CIDv1 base32 encoding of the Hippius IPFS object. Never CIDv0. Never base58. |
| `prev_cid` | string or null | validator | CIDv1 base32 of the previous receipt for this actor. `null` only for the genesis receipt (first operation by this actor). |
| `hippius_status` | string | validator | Enum: `SUCCESS`, `FAIL`, `PARTIAL`. No aliases. |
| `validator_hotkey` | string | validator | SS58 of the signing validator. Must be registered in the metagraph. |
| `validator_sig` | string | validator | Hex-encoded Ed25519 signature. Signs over canonical_bytes(receipt minus miner fields). |
| `miner_cid` | string | miner | CIDv1 base32 of the receipt as pinned by this miner. Added after pinning. |
| `miner_hotkey` | string | miner | SS58 of the pinning miner. |
| `miner_sig` | string | miner | Hex-encoded Ed25519 signature. Signs over miner_cid only (not the full receipt). |

**op_type semantics:**

- `WRITE` — first write of a record for an actor-resource pair. data_cid points to new content.
- `UPDATE` — overwrite of an existing record. data_cid points to new content. prev_cid links to the prior WRITE or UPDATE receipt.
- `DELETE_REQ` — records a deletion request. The payload CID is preserved for audit. The decryption key is revoked at the application layer. IPFS content is not deleted (content-addressed systems cannot guarantee deletion).
- `AUDIT` — records an authorised read event for compliance tracking.

---

### Batch Envelope Wire Format

Version: `spec_version = "1.0"`

A batch envelope is a content-addressed IPFS object that commits a time-window
of receipts into a Merkle tree and chains to the prior batch. One batch
envelope is created per closed window.

```json
{
  "spec_version":     "1.0",
  "batch_index":      42,
  "window_start_ns":  1712680000000000000,
  "window_end_ns":    1712680060000000000,
  "receipt_count":    128,
  "receipt_cids":     ["bafyrei...", "bafyrei...", "..."],
  "merkle_root":      "a1b2c3d4e5f6...",
  "prev_batch_cid":   "bafyrei...",
  "validator_hotkey": "5Dxqr...",
  "validator_sig":    "0xabcdef..."
}
```

**Field definitions:**

| Field | Type | Rule |
|---|---|---|
| `spec_version` | string | Same versioning policy as receipt. |
| `batch_index` | int | Monotonically increasing integer. Reset to 0 only at subnet genesis. |
| `window_start_ns` | int | UTC nanoseconds of the first receipt in this batch. |
| `window_end_ns` | int | UTC nanoseconds at which this window was closed. |
| `receipt_count` | int | Count of entries in receipt_cids. Must match len(receipt_cids). |
| `receipt_cids` | array of string | CIDv1 base32. Sorted lexicographically ascending. No duplicates. |
| `merkle_root` | string | 64-character lowercase hex SHA-256 root of the Merkle tree over receipt_cids. |
| `prev_batch_cid` | string or null | CIDv1 base32 of the prior batch. `null` for the genesis batch. |
| `validator_hotkey` | string | SS58 of signing validator. |
| `validator_sig` | string | Hex Ed25519 sig over canonical_bytes(all above fields). |

---

### Synapse Types

Three synapse types are used for validator-miner communication. All extend
`bt.Synapse`.

**Receipt (bt.Synapse subclass)**
Used to deliver a newly constructed receipt to miners for pinning.
Direction: validator → miner.
Miner populates `miner_cid`, `miner_hotkey`, `miner_sig` before returning.

**BatchEnvelope (bt.Synapse subclass)**
Used to deliver a closed batch envelope to miners for pinning.
Direction: validator → miner.
Miner returns acknowledgement with `miner_batch_cid` and `miner_sig` over
`miner_batch_cid`.

**PinChallenge (bt.Synapse subclass)**
Used by validators to challenge miners to prove they hold a specific receipt.

Fields sent by validator:
- `receipt_cid: str` — the CID to retrieve
- `nonce: str` — 128-bit random hex, single-use
- `issued_at_ns: int` — validator's timestamp at issue

Fields returned by miner:
- `receipt_json: str` — full canonical JSON of the pinned receipt
- `retrieved_at_ns: int` — miner's timestamp at retrieval
- `miner_sig: str` — hex Ed25519 sig over `(receipt_cid + nonce)` concatenated

**MerkleProofChallenge (bt.Synapse subclass)**
Used by validators to challenge miners to prove a receipt is a member of a
specific batch.

Fields sent by validator:
- `batch_cid: str` — the batch to prove membership in
- `target_receipt_cid: str` — the receipt whose membership is being proven
- `nonce: str` — 128-bit random hex, single-use

Fields returned by miner:
- `proof_path: list[str]` — array of 64-char hex sibling hashes from leaf to root
- `miner_sig: str` — hex Ed25519 sig over `(batch_cid + target_receipt_cid + nonce)` concatenated

**IndexQuery (bt.Synapse subclass)**
Used by validators to query a miner's current chain state for consistency
scoring.

Fields sent by validator:
- `actor_ss58: str` — the actor whose chain state to report

Fields returned by miner:
- `tip_cid: str | None` — receipt chain tip for this actor
- `receipt_count: int` — total receipts held for this actor
- `oldest_cid: str | None` — genesis receipt CID for this actor
- `tip_batch_cid: str | None` — batch chain tip
- `batch_count: int` — total batches held

---

### Canonical Serialisation Rule

All signing and CID computation uses the same deterministic serialisation:

```python
import json

def canonical_bytes(obj: dict) -> bytes:
    """
    Produce deterministic bytes from a dict.
    - Keys sorted lexicographically (sort_keys=True)
    - No whitespace (separators=(',', ':'))
    - UTF-8 encoded
    This is the ONLY valid serialisation for signing and CID computation.
    """
    return json.dumps(obj, sort_keys=True, separators=(',', ':')).encode('utf-8')
```

CID computation:

```python
import hashlib
import base64

def compute_cid(obj: dict) -> str:
    """
    Compute a CIDv1 base32 identifier for a dict object.
    In production, use the ipfshttpclient library and pin the object;
    the IPFS daemon returns the canonical CID. This function is used
    for local computation and test verification only.
    """
    raw = canonical_bytes(obj)
    digest = hashlib.sha256(raw).digest()
    # CIDv1 multihash prefix: 0x12 (sha2-256) + 0x20 (32 bytes)
    multihash = bytes([0x12, 0x20]) + digest
    # CIDv1: version=1, codec=0x55 (raw), multihash
    cidv1_bytes = bytes([0x01, 0x55]) + multihash
    # Base32 lower, no padding, with 'b' multibase prefix
    return 'b' + base64.b32encode(cidv1_bytes).decode('utf-8').lower().rstrip('=')
```

---

### Signing Scopes

There are exactly two signing scopes in the protocol. Never mix them.

**Scope 1 — Validator signs receipt (excludes miner fields):**

```python
VALIDATOR_SIGN_FIELDS = [
    'spec_version', 'tx_id', 'timestamp_ns', 'actor_ss58',
    'op_type', 'data_cid', 'prev_cid', 'hippius_status', 'validator_hotkey'
]

def validator_canonical(receipt: dict) -> bytes:
    payload = {k: receipt[k] for k in VALIDATOR_SIGN_FIELDS}
    return canonical_bytes(payload)
```

**Scope 2 — Miner signs its own miner_cid only:**

```python
def miner_canonical(miner_cid: str) -> bytes:
    return miner_cid.encode('utf-8')
```

**Scope 3 — Validator signs batch envelope (all envelope fields):**

```python
BATCH_SIGN_FIELDS = [
    'spec_version', 'batch_index', 'window_start_ns', 'window_end_ns',
    'receipt_count', 'receipt_cids', 'merkle_root', 'prev_batch_cid',
    'validator_hotkey'
]

def batch_canonical(envelope: dict) -> bytes:
    payload = {k: envelope[k] for k in BATCH_SIGN_FIELDS}
    return canonical_bytes(payload)
```

---

### MMR vs Batch Merkle Tree

Two distinct cryptographic structures are maintained in the system.

**Batch Merkle tree (held by both validator and miner):**
- Built once when a batch window closes.
- Fixed membership — all receipts from one time window.
- Leaves are SHA-256 hashes of receipt CIDs, sorted lexicographically.
- Used to generate and verify Merkle inclusion proofs during challenges.
- Validator retains trees for the proof_retention_period (default 30 days).
- Miner retains trees indefinitely (they are the permanent storage layer).

```
Build algorithm:
  leaves = sorted([sha256(cid.encode()) for cid in receipt_cids])
  If len(leaves) is odd: append leaves[-1] (duplicate last leaf)
  While len(leaves) > 1:
      parents = []
      For i in range(0, len(leaves), 2):
          parent = sha256(leaves[i] + leaves[i+1])
          parents.append(parent)
      leaves = parents
  root = leaves[0]
```

**Miner MMR — Merkle Mountain Range (held by miner only):**
- Append-only across the miner's entire lifetime.
- Every receipt the miner pins is appended as a new leaf in arrival order.
- Supports O(log n) inclusion proofs for any receipt ever pinned.
- The MMR peaks change with every append — the validator does not track
  the miner's MMR directly. It uses batch roots as checkpoints.
- Purpose: miner self-integrity. A miner can prove its complete receipt
  history without the validator needing to enumerate all receipts.

The MMR and the batch Merkle tree are independent structures serving
orthogonal purposes. A batch Merkle tree proves "this receipt is in
this time window." The MMR proves "this receipt exists in my complete
pinned history."

---

## Validator

---

### Validator Role Definition

The validator is the sole authority that:
1. Reads EVM commitment events from the Subtensor chain
2. Constructs and cryptographically signs receipt objects
3. Distributes receipts and batch envelopes to miners
4. Challenges miners to prove correct storage
5. Scores miners and submits weights to the Bittensor metagraph

The validator never stores receipts permanently. It maintains an ephemeral
ground-truth index used only for challenge verification and scoring. Long-term
receipt storage is exclusively the responsibility of miners.

**The validator is the trust anchor for the entire receipt chain.** A receipt
without a valid validator signature is invalid and must be rejected by miners.
No other node has authority to construct receipts.

---

### Validator Durable State

The following state must survive process restarts (persist to disk):

```
last_evm_block:     int       — last EVM block number successfully processed
actor_tips:         dict      — {actor_ss58: {tip_cid, receipt_count}}
batch_tips:         dict      — {actor_ss58: {tip_batch_cid, batch_count}}  (global if not per-actor)
batch_pending:      list      — receipt_cids accumulated in the current open window
window_open_ns:     int       — timestamp when current window opened
batch_index:        int       — monotonically increasing batch counter
score_ema:          dict      — {uid: float} EMA scores per miner UID
last_weight_block:  int       — last Bittensor block at which weights were submitted
merkle_tree_cache:  dict      — {batch_cid: MerkleTree} for proof generation (30-day TTL)
```

---

### Validator Process 1 — EVM Log Ingestion

**Trigger:** Timer, every 12 seconds (one Bittensor block).

**Contract:** Single `AccessRegistry` address (`ACCESS_REGISTRY`). Do **not**
poll one contract per resource. Do **not** create receipts for
`ChallengeIssued` (forensic only).

**Function: `poll_evm_commitments() -> list[CommitmentEvent]`**

```
1. Call eth_getLogs with filter:
      fromBlock = last_evm_block + 1
      toBlock   = max(fromBlock, latest - REORG_DEPTH)   # default REORG_DEPTH=6
      address   = ACCESS_REGISTRY
      topics    = OR of:
        keccak256("ResourceRegistered(bytes32,address,bytes32,string)")
        keccak256("AccessGranted(bytes32,address,bytes32,uint256)")
        # optional:
        keccak256("AccessDenied(bytes32,address,uint256)")
        # NEVER receipt:
        # keccak256("ChallengeIssued(bytes32,address,bytes32,uint256)")

2. For each log entry returned:
   a. Decode by event type:

      ResourceRegistered:
        actor_h160   = owner
        data_hash    = dataHash
        data_cid     = dataCid (prefer) or cidv1_base32(dataHash)
        op_type_hint = None  → derive WRITE/UPDATE from actor tip

      AccessGranted:
        actor_h160   = requester (topics indexed)
        data_hash    = dataHash (non-indexed data)
        data_cid     = cidv1_base32(dataHash)
        op_type_hint = AUDIT

      AccessDenied (if SKIP_DENY_RECEIPTS=false):
        actor_h160   = requester
        hippius_status = FAIL
        op_type_hint = AUDIT

   b. Resolve actor_h160 → actor_ss58:
        Query SubtensorModule.EVMAssociation / AssociatedEvmAddress with actor_h160.
        If no mapping found:
          - production (--require-evm-association): DISCARD
          - local/testnet default: keep checksum H160 as actor id and warn

   c. Verify CID is reachable on Hippius (when client configured):
        Call hippius_client.exists(data_cid).
        If unreachable: set hippius_status = "FAIL".
        If reachable:   set hippius_status = "SUCCESS".

3. Update last_evm_block = max block number processed (or toBlock if empty).

4. Return list of CommitmentEvent(actor, data_cid, hippius_status,
                                   op_type_hint, resource_id).
```

**Error handling:**
- RPC timeout: retry up to 3 times with exponential backoff, then skip this
  poll cycle. Do not advance last_evm_block on failure.
- Reorg detected (log count drops): roll back last_evm_block by 6 blocks and
  reprocess. Mark any receipts built from reorged blocks as INVALIDATED in
  local state.

**Reference:** [`access-registry-wiring.md`](access-registry-wiring.md),
[`../key-generation/AccessRegistry.sol`](../key-generation/AccessRegistry.sol).

---

### Validator Process 2 — Receipt Construction and Signing

**Trigger:** Called for each CommitmentEvent returned by poll_evm_commitments().

**Function: `build_receipt(event: CommitmentEvent) -> tuple[Receipt, str]`**

Returns `(receipt_object, receipt_cid)`.

```
1. Assemble receipt fields:
     receipt = {
       "spec_version":     "1.0",
       "tx_id":            str(uuid.uuid4()),
       "timestamp_ns":     time.time_ns(),   # NOW — not the EVM block timestamp
       "actor_ss58":       event.actor_ss58,
       "op_type":          derive_op_type(event),  # see op_type derivation below
       "data_cid":         event.data_cid,
       "prev_cid":         actor_tips.get(event.actor_ss58, {}).get("tip_cid"),
       "hippius_status":   event.hippius_status,
       "validator_hotkey": self.wallet.hotkey_str,
     }

2. Compute canonical bytes (validator scope — excludes miner fields):
     canon = canonical_bytes({k: receipt[k] for k in VALIDATOR_SIGN_FIELDS})

3. Sign with validator hotkey:
     receipt["validator_sig"] = self.keypair.sign(canon).hex()

4. Compute receipt_cid:
     receipt_cid = compute_cid(receipt)  # CIDv1 of the complete signed object

5. Advance actor chain state:
     actor_tips[actor_ss58]["tip_cid"]       = receipt_cid
     actor_tips[actor_ss58]["receipt_count"] += 1

6. Append to batch_pending:
     batch_pending.append(receipt_cid)

7. Return (receipt, receipt_cid).
```

**op_type derivation:**

```
derive_op_type(event) -> str:
  If event.op_type_hint is set (e.g. AccessGranted → "AUDIT"):
      return event.op_type_hint
  If actor_tips.get(actor_ss58) is None:
      return "WRITE"         # First ever operation for this actor (ResourceRegistered)
  If event.data_cid == actor_tips[actor_ss58]["tip_data_cid"]:
      return "AUDIT"         # Same content, read event
  Return "UPDATE"            # Different content, overwrite
  # DELETE_REQ and other op_types are set by the EVM contract event field
  # directly if the contract encodes op_type in the log data.
```

---

### Validator Process 3 — Merkle Batch Construction

**Trigger:** Either condition met:
- `time.time_ns() - window_open_ns >= BATCH_WINDOW_NS` (default 60,000,000,000 ns = 60 seconds)
- `len(batch_pending) >= MAX_BATCH_SIZE` (default 256 receipts)

An empty batch_pending list produces no batch. Skip silently.

**Function: `close_batch() -> tuple[BatchEnvelope, str]`**

Returns `(envelope_object, batch_cid)`.

```
1. Capture the current window:
     cids           = sorted(batch_pending)    # lexicographic sort
     window_end_ns  = time.time_ns()

2. Build the Merkle tree:
     leaves = [sha256(cid.encode('utf-8')) for cid in cids]
     If len(leaves) is odd: leaves.append(leaves[-1])
     tree   = MerkleTree(leaves)               # stores all internal nodes
     root   = tree.root.hex()                  # 64-char lowercase hex

3. Store tree in merkle_tree_cache[batch_cid_placeholder]:
     (stored after batch_cid is computed in step 5)

4. Assemble envelope:
     envelope = {
       "spec_version":     "1.0",
       "batch_index":      batch_index,
       "window_start_ns":  window_open_ns,
       "window_end_ns":    window_end_ns,
       "receipt_count":    len(cids),
       "receipt_cids":     cids,
       "merkle_root":      root,
       "prev_batch_cid":   batch_tips.get("global", {}).get("tip_batch_cid"),
       "validator_hotkey": self.wallet.hotkey_str,
     }

5. Sign and compute batch_cid:
     canon                = canonical_bytes({k: envelope[k] for k in BATCH_SIGN_FIELDS})
     envelope["validator_sig"] = self.keypair.sign(canon).hex()
     batch_cid            = compute_cid(envelope)

6. Store tree: merkle_tree_cache[batch_cid] = tree

7. Advance batch state:
     batch_tips["global"]["tip_batch_cid"] = batch_cid
     batch_tips["global"]["batch_count"]  += 1
     batch_index                          += 1
     batch_pending                         = []
     window_open_ns                        = time.time_ns()

8. Broadcast envelope to all active miners via bt.dendrite.forward()
   (fire-and-forget, same pattern as receipt broadcast).

9. Return (envelope, batch_cid).
```

---

### Validator Process 4 — Miner Challenge and Scoring

**Trigger:** Timer, every CHALLENGE_INTERVAL_S seconds (default 60).

**Function: `run_challenge_round() -> dict[int, float]`**

Returns `{uid: raw_score}` for all active miners.

#### Step 1 — Sync metagraph and select active miners

```
metagraph.sync()
active_miners = [uid for uid, axon in enumerate(metagraph.axons)
                 if axon.port > 0 and uid != own_uid]
```

#### Step 2 — Select challenge targets per miner

```
select_challenge_target(uid: int) -> tuple[str|None, str|None, str|None]:
  # Returns (actor_ss58, receipt_cid, batch_cid)
  # Rotate through actors by uid to distribute load.
  actors = sorted(actor_tips.keys())
  if not actors: return (None, None, None)
  actor  = actors[uid % len(actors)]
  tip    = actor_tips[actor]["tip_cid"]
  batch  = batch_tips.get("global", {}).get("tip_batch_cid")
  return (actor, tip, batch)
```

#### Step 3 — Issue three synapses per miner concurrently

For each active miner, issue in parallel:
- `PinChallenge(receipt_cid, nonce=secrets.token_hex(16), issued_at_ns)`
- `MerkleProofChallenge(batch_cid, target_receipt_cid, nonce=secrets.token_hex(16))`
- `IndexQuery(actor_ss58)`

All three use `bt.dendrite.forward()` with `timeout=CHALLENGE_TIMEOUT_S` (default 10).

#### Step 4 — Score each miner

**Function: `score_miner(uid, pin_resp, proof_resp, index_resp) -> float`**

```
WEIGHTS = {
    "availability": 0.40,
    "latency":      0.25,
    "integrity":    0.25,
    "consistency":  0.10,
}

availability_score(pin_resp):
  Return 1.0 if pin_resp is not None and pin_resp.receipt_json is not None
  Return 0.0 otherwise

latency_score(pin_resp, elapsed_ms):
  If availability_score == 0.0: return 0.0
  TARGET_MS  = 500.0
  CUTOFF_MS  = 5000.0
  STEEPNESS  = 3.0
  If elapsed_ms <= TARGET_MS: return 1.0
  If elapsed_ms >= CUTOFF_MS: return 0.0
  x = (elapsed_ms - TARGET_MS) / (CUTOFF_MS - TARGET_MS)  # normalise to [0,1]
  return 1.0 / (1.0 + exp(STEEPNESS * x * 10 - 5))        # sigmoid decay

integrity_score(pin_resp, proof_resp):
  # Sub-weights within integrity dimension
  W_SIG     = 0.40   # validator sig on returned receipt is valid
  W_CONTENT = 0.30   # content hash of returned receipt matches known hash
  W_PROOF   = 0.30   # Merkle proof verifies against known batch root

  score = 0.0

  If pin_resp and pin_resp.receipt_json:
    returned = json.loads(pin_resp.receipt_json)

    # Check validator sig on returned content
    Try:
      kp = Keypair(ss58_address=returned["validator_hotkey"])
      canon = canonical_bytes({k: returned[k] for k in VALIDATOR_SIGN_FIELDS})
      If kp.verify(canon, bytes.fromhex(returned["validator_sig"])):
          score += W_SIG
    Except: pass

    # Check content hash matches validator's own record
    returned_cid = compute_cid(returned)
    If returned_cid == expected_receipt_cid:
        score += W_CONTENT

  If proof_resp and proof_resp.proof_path:
    Try:
      leaf          = sha256(target_receipt_cid.encode('utf-8'))
      computed_root = merkle_verify(leaf, proof_resp.proof_path)
      known_root    = merkle_tree_cache[batch_cid].root
      If computed_root == known_root:
          score += W_PROOF
    Except: pass

  Return score

consistency_score(index_resp, actor_ss58):
  If index_resp is None: return 0.0

  known_tip   = actor_tips[actor_ss58]["tip_cid"]
  known_count = actor_tips[actor_ss58]["receipt_count"]

  tip_score = 1.0 if index_resp.tip_cid == known_tip else 0.0

  If known_count == 0:
      count_score = 1.0 if index_resp.receipt_count == 0 else 0.0
  Else:
      drift = abs(index_resp.receipt_count - known_count)
      DRIFT_TOLERANCE = 2
      If drift <= DRIFT_TOLERANCE:
          count_score = 1.0
      Else:
          count_score = max(0.0, 1.0 - (drift - DRIFT_TOLERANCE) / known_count)

  Return (tip_score * 0.6) + (count_score * 0.4)

composite = (
    WEIGHTS["availability"] * availability_score  +
    WEIGHTS["latency"]      * latency_score       +
    WEIGHTS["integrity"]    * integrity_score      +
    WEIGHTS["consistency"]  * consistency_score
)
Return max(0.0, min(1.0, composite))
```

#### Step 5 — Update EMA and normalise

```
EMA_ALPHA = 0.3

For each uid, raw_score:
  prev = score_ema.get(uid, raw_score)
  score_ema[uid] = EMA_ALPHA * raw_score + (1 - EMA_ALPHA) * prev

# Normalise so top miner = 1.0
max_score = max(score_ema.values()) or 1.0
normalised = {uid: s / max_score for uid, s in score_ema.items()}
```

---

### Validator Process 5 — On-Chain Weight Submission

**Trigger:** Called after each scoring round. Executes only if rate limit
has elapsed.

**Function: `maybe_set_weights()`**

```
current_block = subtensor.get_current_block()
If current_block - last_weight_block < WEIGHTS_RATE_LIMIT_BLOCKS (default 100):
    Log "cooldown: N/100 blocks elapsed" and return.

n = len(metagraph.hotkeys)
weights = torch.zeros(n, dtype=torch.float32)

For uid, ema_score in score_ema.items():
    If uid < n and uid != own_uid:
        weights[uid] = float(ema_score)

total = weights.sum()
If total == 0: Log warning and return.
weights = weights / total   # normalise to sum = 1.0

result = subtensor.set_weights(
    wallet               = self.wallet,
    netuid               = NETUID,
    uids                 = list(range(n)),
    weights              = weights.tolist(),
    wait_for_inclusion   = True,
    wait_for_finalization = False,  # do not block — finalization can take minutes
)

If result:
    last_weight_block = current_block
    Log "weights set at block {current_block}"
Else:
    Log warning "set_weights returned False"
```

---

### Validator Function Reference

```
poll_evm_commitments()           -> list[CommitmentEvent]
  Polls eth_getLogs, decodes events, resolves H160→SS58,
  verifies Hippius CID presence.

build_receipt(event)             -> (Receipt, receipt_cid: str)
  Constructs, signs, and CID-computes a receipt.
  Advances actor_tips and batch_pending.

broadcast_receipt(receipt)       -> None   [async, fire-and-forget]
  Sends Receipt synapse to all active miner axons.

close_batch()                    -> (BatchEnvelope, batch_cid: str)
  Builds Merkle tree, constructs and signs batch envelope,
  stores tree in cache, broadcasts to miners.

run_challenge_round()            -> dict[int, float]
  Selects targets, issues three synapses per miner,
  scores all miners, updates EMA.

score_miner(uid, responses...)   -> float
  Computes composite score across four dimensions.

maybe_set_weights()              -> None
  Checks rate limit, normalises EMA scores,
  calls subtensor.set_weights().

resolve_h160_to_ss58(h160)       -> str | None
  Queries SubtensorModule.EVMAssociation on-chain.

verify_validator_sig(receipt)    -> bool
  Recomputes canonical bytes, verifies Ed25519 sig.

merkle_verify(leaf, proof_path)  -> bytes
  Recomputes Merkle root from a leaf and sibling path.
  Returns root bytes for comparison against known root.
```

---

## Miner

---

### Miner Role Definition

The miner is a persistent IPFS storage node. Its complete function is:
1. Receive validator-signed receipt objects and pin them to local IPFS
2. Receive validator-signed batch envelope objects and pin them to local IPFS
3. Maintain a local receipt index for O(1) lookup
4. Maintain a local Merkle mountain range over all pinned receipts
5. Respond to PinChallenge, MerkleProofChallenge, and IndexQuery synapses
6. Never construct receipts independently
7. Never access the EVM chain
8. Never store or access plaintext health data

**A miner that constructs its own receipts or modifies received receipts is
acting fraudulently.** The validator's signature on every receipt makes
tampering detectable — any modified receipt will fail signature verification
at challenge time, resulting in an integrity score of 0 for that round and
eventual deregistration.

---

### Miner Durable State

The following state must survive process restarts:

**SQLite database — `miner_store.db`**

```sql
CREATE TABLE receipts (
    receipt_cid   TEXT PRIMARY KEY,     -- CIDv1 as assigned by validator
    miner_cid     TEXT NOT NULL,        -- CIDv1 as assigned by local IPFS
    actor_ss58    TEXT NOT NULL,
    timestamp_ns  INTEGER NOT NULL,
    prev_cid      TEXT,                 -- NULL for genesis
    op_type       TEXT NOT NULL,
    hippius_status TEXT NOT NULL,
    batch_id      TEXT,                 -- batch_cid of enclosing batch (NULL if not yet batched)
    pinned_at_ns  INTEGER NOT NULL,
    FOREIGN KEY (batch_id) REFERENCES batches(batch_cid)
);

CREATE TABLE actor_tips (
    actor_ss58     TEXT PRIMARY KEY,
    tip_cid        TEXT NOT NULL,
    receipt_count  INTEGER NOT NULL DEFAULT 0,
    oldest_cid     TEXT
);

CREATE TABLE batches (
    batch_cid      TEXT PRIMARY KEY,
    batch_index    INTEGER NOT NULL,
    window_start_ns INTEGER NOT NULL,
    window_end_ns   INTEGER NOT NULL,
    receipt_count   INTEGER NOT NULL,
    merkle_root     TEXT NOT NULL,
    prev_batch_cid  TEXT,
    miner_batch_cid TEXT,               -- CIDv1 as assigned by local IPFS
    pinned_at_ns    INTEGER NOT NULL
);

CREATE TABLE batch_tips (
    scope          TEXT PRIMARY KEY,    -- "global" or actor_ss58 if per-actor batches
    tip_batch_cid  TEXT NOT NULL,
    batch_count    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE mmr_nodes (
    position       INTEGER PRIMARY KEY, -- leaf index in arrival order
    node_hash      TEXT NOT NULL,       -- 64-char hex SHA-256
    node_type      TEXT NOT NULL        -- "leaf" or "internal"
);

CREATE TABLE fail_log (
    tx_id          TEXT PRIMARY KEY,
    actor_ss58     TEXT NOT NULL,
    op_type        TEXT NOT NULL,
    data_cid       TEXT NOT NULL,
    logged_at_ns   INTEGER NOT NULL,
    reason         TEXT
);

CREATE INDEX idx_receipts_actor ON receipts(actor_ss58);
CREATE INDEX idx_receipts_batch ON receipts(batch_id);
```

**IPFS local node:** All receipt and batch envelope objects are pinned
recursively. The local IPFS node must have sufficient storage for the
lifetime of the subnet.

---

### Miner Process 1 — Receipt Ingestion and Pinning

**Trigger:** Incoming Receipt synapse on the miner's axon.

**Function: `handle_receipt(receipt: Receipt) -> Receipt`**

```
1. BLACKLIST CHECK (axon middleware — runs before this handler):
   If receipt.dendrite.hotkey not in metagraph.hotkeys:
       Reject with 403. Return immediately.

2. VALIDATOR SIGNATURE VERIFICATION:
   canon = canonical_bytes({k: receipt[k] for k in VALIDATOR_SIGN_FIELDS})
   kp    = Keypair(ss58_address=receipt.validator_hotkey)
   Try:
       valid = kp.verify(canon, bytes.fromhex(receipt.validator_sig))
   Except:
       valid = False
   If not valid:
       Log "REJECT: invalid validator sig on tx_id={receipt.tx_id}"
       Return receipt with miner fields unset.

3. FIELD VALIDATION:
   Required non-empty: tx_id, data_cid, actor_ss58, validator_hotkey,
                       hippius_status, spec_version, op_type
   If any required field is empty or missing:
       Log "REJECT: missing fields on tx_id={receipt.tx_id}"
       Return receipt with miner fields unset.

4. FAIL STATUS HANDLING:
   If receipt.hippius_status == "FAIL":
       INSERT INTO fail_log VALUES (receipt.tx_id, receipt.actor_ss58,
           receipt.op_type, receipt.data_cid, time.time_ns(), "hippius_fail")
       Return receipt with miner fields unset.
       # FAIL receipts are not pinned, not indexed, not chained.

5. DUPLICATE CHECK:
   If SELECT COUNT(*) FROM receipts WHERE receipt_cid = receipt_cid > 0:
       Log "SKIP: already pinned receipt_cid={receipt_cid}"
       Populate miner fields from existing record and return.

6. PIN TO IPFS:
   receipt_json = canonical_bytes(receipt_as_full_dict).decode('utf-8')
   Try:
       miner_cid = ipfs_client.add_str(receipt_json)
       ipfs_client.pin.add(miner_cid)
   Except IPFSError as e:
       Log "IPFS pin failed: {e}"
       Return receipt with miner fields unset.

7. UPDATE RECEIPT INDEX:
   receipt_cid = compute_cid(receipt_as_dict)
   INSERT INTO receipts VALUES (receipt_cid, miner_cid, receipt.actor_ss58,
       receipt.timestamp_ns, receipt.prev_cid, receipt.op_type,
       receipt.hippius_status, NULL, time.time_ns())

   INSERT OR REPLACE INTO actor_tips VALUES (
       receipt.actor_ss58,
       receipt_cid,
       (SELECT receipt_count FROM actor_tips WHERE actor_ss58 = receipt.actor_ss58
        UNION SELECT 0 LIMIT 1) + 1,
       COALESCE((SELECT oldest_cid FROM actor_tips
                  WHERE actor_ss58 = receipt.actor_ss58), receipt_cid)
   )

8. APPEND TO MMR:
   mmr_append(receipt_cid)   # see Process 3

9. SIGN AND RETURN:
   receipt.miner_cid    = miner_cid
   receipt.miner_hotkey = self.wallet.hotkey_str
   receipt.miner_sig    = self.keypair.sign(miner_cid.encode('utf-8')).hex()
   Return receipt
```

---

### Miner Process 2 — Batch Envelope Ingestion

**Trigger:** Incoming BatchEnvelope synapse on the miner's axon.

**Function: `handle_batch_envelope(envelope: BatchEnvelope) -> BatchEnvelope`**

```
1. BLACKLIST CHECK (same as receipt handler).

2. VALIDATOR SIGNATURE VERIFICATION:
   canon = canonical_bytes({k: envelope[k] for k in BATCH_SIGN_FIELDS})
   kp    = Keypair(ss58_address=envelope.validator_hotkey)
   valid = kp.verify(canon, bytes.fromhex(envelope.validator_sig))
   If not valid:
       Log "REJECT: invalid validator sig on batch_index={envelope.batch_index}"
       Return envelope with miner fields unset.

3. LOCAL INTEGRITY CHECK:
   missing = []
   For cid in envelope.receipt_cids:
       If SELECT COUNT(*) FROM receipts WHERE receipt_cid = cid == 0:
           missing.append(cid)
   If missing:
       Log "GAP: {len(missing)} receipts missing from batch {envelope.batch_index}"
       # Still proceed with pinning. Score will reflect gap via consistency check.
       # Optionally: issue gap-fill requests to validator for missing CIDs.

4. VERIFY MERKLE ROOT:
   leaves = [sha256(cid.encode('utf-8')) for cid in sorted(envelope.receipt_cids)]
   If len(leaves) % 2 == 1: leaves.append(leaves[-1])
   computed_root = build_merkle_tree(leaves).root.hex()
   If computed_root != envelope.merkle_root:
       Log "REJECT: merkle root mismatch on batch_index={envelope.batch_index}"
       Return envelope with miner fields unset.

5. STORE MERKLE TREE:
   tree = build_merkle_tree(leaves)    # full node array for proof generation
   batch_tree_cache[batch_cid] = tree  # in-memory; also serialise to disk

6. PIN ENVELOPE TO IPFS:
   envelope_json = canonical_bytes(envelope_as_full_dict).decode('utf-8')
   miner_batch_cid = ipfs_client.add_str(envelope_json)
   ipfs_client.pin.add(miner_batch_cid)

7. UPDATE BATCH INDEX:
   batch_cid = compute_cid(envelope_as_dict)
   INSERT INTO batches VALUES (batch_cid, envelope.batch_index,
       envelope.window_start_ns, envelope.window_end_ns,
       envelope.receipt_count, envelope.merkle_root,
       envelope.prev_batch_cid, miner_batch_cid, time.time_ns())

   INSERT OR REPLACE INTO batch_tips VALUES (
       "global", batch_cid,
       (SELECT batch_count FROM batch_tips WHERE scope = "global"
        UNION SELECT 0 LIMIT 1) + 1
   )

   UPDATE receipts SET batch_id = batch_cid
   WHERE receipt_cid IN ({','.join('?' for _ in envelope.receipt_cids)})

8. SIGN AND RETURN:
   envelope.miner_batch_cid = miner_batch_cid
   envelope.miner_hotkey    = self.wallet.hotkey_str
   envelope.miner_sig       = self.keypair.sign(
       miner_batch_cid.encode('utf-8')).hex()
   Return envelope
```

---

### Miner Process 3 — Merkle Mountain Range Maintenance

The MMR is an append-only authenticated data structure. Leaves are appended
in receipt arrival order. The structure enables O(log n) inclusion proofs
for any receipt without rebuilding the entire tree.

**Function: `mmr_append(receipt_cid: str)`**

```
A Merkle Mountain Range is maintained as a list of "peaks" — the roots of
complete binary trees of sizes 2^k for various k.

leaf_hash = sha256(receipt_cid.encode('utf-8'))
position  = len(mmr_nodes)  # next position index

INSERT INTO mmr_nodes VALUES (position, leaf_hash.hex(), "leaf")
position += 1

# Merge peaks: while the last two peaks can be combined into a larger tree:
While len(peaks) >= 2 and peaks[-2].size == peaks[-1].size:
    left  = peaks.pop()
    right = peaks.pop()
    parent_hash = sha256(left.hash + right.hash)
    INSERT INTO mmr_nodes VALUES (position, parent_hash.hex(), "internal")
    peaks.append(Peak(hash=parent_hash, size=left.size * 2))
    position += 1

peaks.append(Peak(hash=leaf_hash, size=1))
```

**Function: `mmr_generate_proof(receipt_cid: str) -> list[str]`**

```
Returns the sibling hash path from the leaf to the MMR peaks.
Used to prove a receipt is in the miner's complete history.
This is NOT the same as a batch Merkle proof — it proves membership
in the miner's full append history, not a specific batch window.

leaf_hash = sha256(receipt_cid.encode('utf-8'))
position  = find_leaf_position(leaf_hash)   # query mmr_nodes table
Return compute_sibling_path(position, mmr_nodes)
```

**Function: `mmr_peaks() -> list[str]`**

```
Returns the list of current peak hashes as hex strings.
The peaks change with every append. The validator uses batch roots
(not MMR peaks) for challenge purposes, but the MMR peaks can be
used as a compact summary of the miner's complete history.
```

---

### Miner Process 4 — Responding to Validator Challenges

#### Handle PinChallenge

**Function: `handle_pin_challenge(challenge: PinChallenge) -> PinChallenge`**

```
1. BLACKLIST CHECK.

2. RECEIPT LOOKUP:
   row = SELECT miner_cid FROM receipts WHERE receipt_cid = challenge.receipt_cid
   If no row:
       Log "MISS: pin challenge for unknown cid={challenge.receipt_cid}"
       Return challenge with receipt_json = None.

3. RETRIEVE FROM IPFS:
   Try:
       content = ipfs_client.cat(row.miner_cid)
   Except:
       Log "IPFS retrieval failed for miner_cid={row.miner_cid}"
       Return challenge with receipt_json = None.

4. SIGN FOR FRESHNESS:
   msg = (challenge.receipt_cid + challenge.nonce).encode('utf-8')
   sig = self.keypair.sign(msg).hex()

5. RETURN:
   challenge.receipt_json     = content.decode('utf-8')
   challenge.retrieved_at_ns  = time.time_ns()
   challenge.miner_sig        = sig
   Return challenge
```

**Performance requirement:** Step 3 must complete within 8 seconds. Local
IPFS node must be configured with sufficient memory for hot CID caching.
The most recently pinned receipts are accessed most frequently — ensure
IPFS datastore is on NVMe storage.

#### Handle MerkleProofChallenge

**Function: `handle_merkle_proof_challenge(challenge: MerkleProofChallenge) -> MerkleProofChallenge`**

```
1. BLACKLIST CHECK.

2. BATCH LOOKUP:
   If challenge.batch_cid not in batch_tree_cache:
       Try to load tree from disk serialisation.
       If not found:
           Log "MISS: no tree for batch_cid={challenge.batch_cid}"
           Return challenge with proof_path = None.

3. FIND LEAF:
   leaf = sha256(challenge.target_receipt_cid.encode('utf-8'))
   tree = batch_tree_cache[challenge.batch_cid]

4. GENERATE PROOF PATH:
   Try:
       proof_path = tree.generate_proof(leaf)
       # proof_path: list of 64-char hex sibling hashes, leaf → root
   Except LeafNotFound:
       Log "MISS: target not in batch tree"
       Return challenge with proof_path = None.

5. SIGN FOR FRESHNESS:
   msg = (challenge.batch_cid + challenge.target_receipt_cid + challenge.nonce
          ).encode('utf-8')
   sig = self.keypair.sign(msg).hex()

6. RETURN:
   challenge.proof_path = [h.hex() for h in proof_path]
   challenge.miner_sig  = sig
   Return challenge
```

#### Handle IndexQuery

**Function: `handle_index_query(query: IndexQuery) -> IndexQuery`**

```
1. BLACKLIST CHECK.

2. QUERY RECEIPT CHAIN STATE:
   tip_row = SELECT tip_cid, receipt_count, oldest_cid
             FROM actor_tips WHERE actor_ss58 = query.actor_ss58

3. QUERY BATCH CHAIN STATE:
   batch_row = SELECT tip_batch_cid, batch_count
               FROM batch_tips WHERE scope = "global"

4. RETURN:
   query.tip_cid       = tip_row.tip_cid if tip_row else None
   query.receipt_count = tip_row.receipt_count if tip_row else 0
   query.oldest_cid    = tip_row.oldest_cid if tip_row else None
   query.tip_batch_cid = batch_row.tip_batch_cid if batch_row else None
   query.batch_count   = batch_row.batch_count if batch_row else 0
   Return query
```

---

### Miner Function Reference

```
handle_receipt(receipt)                    -> Receipt
  Validates sig, pins to IPFS, updates receipt index,
  appends to MMR, returns signed acknowledgement.

handle_batch_envelope(envelope)            -> BatchEnvelope
  Validates sig, verifies Merkle root, pins to IPFS,
  stores Merkle tree, updates batch index.

handle_pin_challenge(challenge)            -> PinChallenge
  Retrieves receipt from IPFS by CID, signs (cid+nonce),
  returns receipt content.

handle_merkle_proof_challenge(challenge)   -> MerkleProofChallenge
  Generates sibling hash path for target receipt within
  specified batch, signs (batch_cid+receipt_cid+nonce).

handle_index_query(query)                  -> IndexQuery
  Returns actor receipt chain tip, count, oldest CID,
  and batch chain tip from local SQLite index.

mmr_append(receipt_cid)                    -> None
  Appends leaf to the Merkle mountain range, merges peaks.

mmr_generate_proof(receipt_cid)            -> list[str]
  Returns sibling hash path proving membership in full history.

mmr_peaks()                                -> list[str]
  Returns current list of MMR peak hashes.

verify_validator_sig(obj, sig_field,
                     signed_fields)        -> bool
  Generic validator signature verification used by all handlers.

blacklist_fn(synapse)                      -> (bool, str)
  Returns (True, reason) if caller not in metagraph.

priority_fn(synapse)                       -> float
  Returns caller's stake weight for queue prioritisation.
```

---

## Scoring Function

Complete scoring reference for use in `score_miner()`.

```
Inputs:
  uid:         int
  pin_resp:    PinChallenge response (may be None)
  proof_resp:  MerkleProofChallenge response (may be None)
  index_resp:  IndexQuery response (may be None)
  elapsed_ms:  float  (latency of pin_resp)
  expected:    dict   (validator's ground truth for this miner's target)
    {
      "receipt_cid":    str,
      "batch_cid":      str,
      "batch_root":     str,  (64-char hex)
      "actor_ss58":     str,
      "known_tip_cid":  str,
      "known_count":    int,
    }

Output: float in [0.0, 1.0]

Dimension weights (must sum to 1.0):
  AVAILABILITY = 0.40
  LATENCY      = 0.25
  INTEGRITY    = 0.25
  CONSISTENCY  = 0.10

Availability (binary):
  1.0 if pin_resp is not None and pin_resp.receipt_json is not None
  0.0 otherwise

Latency (sigmoid decay):
  TARGET_MS  = 500
  CUTOFF_MS  = 5000
  STEEPNESS  = 3.0
  If availability == 0:        0.0
  If ms <= TARGET_MS:          1.0
  If ms >= CUTOFF_MS:          0.0
  Else: 1 / (1 + e^(STEEPNESS * ((ms - TARGET) / (CUTOFF - TARGET)) * 10 - 5))

Integrity (three sub-components, weights: sig=0.40, content=0.30, proof=0.30):
  sig_valid:
    Parse pin_resp.receipt_json
    Recompute canonical validator bytes
    Verify validator_sig with validator's public key from metagraph
    1.0 if valid, 0.0 if invalid or exception

  content_valid:
    Recompute CID of returned receipt
    Compare to expected.receipt_cid
    1.0 if match, 0.0 otherwise

  proof_valid:
    leaf          = sha256(expected.receipt_cid.encode())
    computed_root = merkle_verify(leaf, proof_resp.proof_path)
    1.0 if computed_root.hex() == expected.batch_root, 0.0 otherwise
    0.0 if proof_resp is None

  integrity = (0.40 * sig_valid) + (0.30 * content_valid) + (0.30 * proof_valid)

Consistency:
  If index_resp is None: 0.0
  tip_match    = 1.0 if index_resp.tip_cid == expected.known_tip_cid else 0.0
  count_drift  = abs(index_resp.receipt_count - expected.known_count)
  TOLERANCE    = 2
  count_score  = 1.0 if count_drift <= TOLERANCE else
                 max(0.0, 1.0 - (count_drift - TOLERANCE) / max(known_count, 1))
  consistency  = (0.60 * tip_match) + (0.40 * count_score)

Composite:
  score = (AVAILABILITY * availability)
        + (LATENCY      * latency)
        + (INTEGRITY    * integrity)
        + (CONSISTENCY  * consistency)
  Return max(0.0, min(1.0, score))
```

---

## Operational Constants

```python
# Network
NETUID                    = 0       # Set at subnet registration
NETWORK                   = "finney"  # "test" for testnet

# EVM — single AccessRegistry (not one contract per resource)
ACCESS_REGISTRY           = ""      # Set after AccessRegistry.sol deployment
# Legacy alias (same address):
COMMITMENT_CONTRACT_ADDRESS = ACCESS_REGISTRY
EVM_RPC_URL               = "https://test.chain.opentensor.ai"  # chain id 945
# Mainnet EVM: https://lite.chain.opentensor.ai (964)
EVM_CHAIN_ID              = 945
EVM_POLL_INTERVAL_S       = 12.0    # One Bittensor block
REORG_DEPTH               = 6
SKIP_DENY_RECEIPTS        = True    # Do not receipt AccessDenied by default

# Batch windows
BATCH_WINDOW_NS           = 60_000_000_000   # 60 seconds in nanoseconds
MAX_BATCH_SIZE            = 256              # receipts per batch

# Challenge timing
CHALLENGE_INTERVAL_S      = 60.0    # Run challenge round every 60 seconds
CHALLENGE_TIMEOUT_S       = 10.0    # Per-miner response deadline

# Weight submission
WEIGHTS_RATE_LIMIT_BLOCKS = 100     # ~20 minutes at 12s/block

# EMA scoring
EMA_ALPHA                 = 0.3     # Smoothing factor. Higher = faster adaptation.

# Latency scoring
LATENCY_TARGET_MS         = 500.0   # Full score below this
LATENCY_CUTOFF_MS         = 5000.0  # Zero score above this
LATENCY_STEEPNESS         = 3.0     # Sigmoid curve steepness

# Consistency scoring
DRIFT_TOLERANCE           = 2       # Receipts of count drift before penalising

# Validator proof retention
PROOF_RETENTION_DAYS      = 30      # How long to keep Merkle trees for challenges

# Miner IPFS
IPFS_API_URL              = "/ip4/127.0.0.1/tcp/5001"
AXON_PORT                 = 8091

# Spec versioning
RECEIPT_SPEC_VERSION      = "1.0"
BATCH_SPEC_VERSION        = "1.0"
```

---

*End of MediLink Subnet Specification v1.0*