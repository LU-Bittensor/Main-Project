# MinerV1 Storage Map and Data Flow

This document explains every place `minerv1.py` stores data, why each store exists, and how data moves into, out of, and between those stores.

## Scope

Code source: `Main-Project/subnet-template/minerv1.py`

Miner storage surfaces:
- In-memory Python state
- Local sqlite database (`miner_store_v1.db` by default)
- Local IPFS-like content store (`LocalIPFSClient`)
- Bittensor Axon request/response payloads (transit boundary)

---

## 1) In-Memory Storage

## `self.batch_tree_cache: Dict[str, MerkleTree]`

Purpose:
- Keeps Merkle trees for known batches in memory.
- Enables fast Merkle proof generation during challenge handling.

Keys and values:
- Key: `batch_cid`
- Value: `MerkleTree` built from the envelope `receipt_cids`

Written by:
- `handle_batch_envelope()` after validating batch envelope and root.

Read by:
- `handle_merkle_proof_challenge()` when validator asks for proof for a receipt in a specific batch.

Flow notes:
- This cache is ephemeral (lost on process restart).
- If cache is missing a `batch_cid`, proof challenge returns `proof_path = None`.

## `LocalIPFSClient._store: Dict[str, str]`

Purpose:
- Stores serialized receipt/batch payload content by deterministic content ID.
- Acts as local stand-in for real IPFS pinning/retrieval.

Keys and values:
- Key: `cid` (derived from SHA-256 of content)
- Value: JSON string content

Written by:
- `ipfs.add_str(receipt_json)` in `handle_receipt()`
- `ipfs.add_str(envelope_json)` in `handle_batch_envelope()`

Read by:
- `handle_pin_challenge()` via `ipfs.cat(miner_cid)` to return receipt JSON.

Flow notes:
- Content is addressable via `miner_cid` / `miner_batch_cid`.
- This store is in-memory only in current implementation.

## `LocalIPFSClient._pins: set[str]`

Purpose:
- Tracks which local CIDs are marked pinned.

Written by:
- `ipfs.pin_add(miner_cid)` and `ipfs.pin_add(miner_batch_cid)`.

Read by:
- Not currently used for scoring logic directly.

Flow notes:
- Logical signal only in this local implementation.

---

## 2) Persistent Storage (sqlite)

Database path:
- CLI arg `--db-path` (default `./miner_store_v1.db`)

The miner initializes these tables in `_init_schema()`.

## `receipts` table

Schema fields:
- `receipt_cid` (PK)
- `miner_cid`
- `actor_ss58`
- `timestamp_ns`
- `prev_cid`
- `op_type`
- `hippius_status`
- `batch_id` (nullable link to batch)
- `pinned_at_ns`

Purpose:
- Canonical per-receipt persistence.
- Source of truth for what receipts miner accepted/stored.

Written by:
- `handle_receipt()` inserts new rows for valid non-FAIL receipts.
- `handle_batch_envelope()` updates `batch_id` for receipt CIDs included in a batch.

Read by:
- `handle_receipt()` dedupe lookup (`SELECT miner_cid WHERE receipt_cid=?`).
- `handle_pin_challenge()` to map `receipt_cid -> miner_cid`.

Flow notes:
- If receipt already exists, no new row is inserted; miner reuses existing `miner_cid`.
- Batch linkage is backfilled later via `batch_id`.

## `actor_tips` table

Schema fields:
- `actor_ss58` (PK)
- `tip_cid`
- `receipt_count`
- `oldest_cid`

Purpose:
- Tracks per-actor chain/index summary from miner perspective.

Written by:
- `handle_receipt()` updates actor tip and receipt count each accepted receipt.

Read by:
- `handle_receipt()` to compute next `receipt_count` and preserve `oldest_cid`.
- `handle_index_query()` to answer validator consistency checks.

Flow notes:
- Supports validator's drift/tip consistency scoring.

## `batches` table

Schema fields:
- `batch_cid` (PK)
- `batch_index`
- `window_start_ns`
- `window_end_ns`
- `receipt_count`
- `merkle_root`
- `prev_batch_cid`
- `miner_batch_cid`
- `pinned_at_ns`

Purpose:
- Stores validated batch envelope metadata and miner's content CID.

Written by:
- `handle_batch_envelope()` after signature-field check and Merkle root recomputation.

Read by:
- Not directly queried in challenge handlers right now (proofs use in-memory cache).

Flow notes:
- Persistent record exists even if `batch_tree_cache` is later lost (after restart).

## `batch_tips` table

Schema fields:
- `scope` (PK, uses `"global"`)
- `tip_batch_cid`
- `batch_count`

Purpose:
- Stores miner's global batch tip and count summary.

Written by:
- `handle_batch_envelope()` increments and updates global tip.

Read by:
- `handle_index_query()` to return batch indexing state.

Flow notes:
- Used by validator as part of consistency/index checks.

## `fail_log` table

Schema fields:
- `tx_id` (PK)
- `actor_ss58`
- `op_type`
- `data_cid`
- `logged_at_ns`
- `reason`

Purpose:
- Tracks receipts with `hippius_status == "FAIL"` rather than storing as normal receipts.

Written by:
- `handle_receipt()` on FAIL path (`INSERT OR REPLACE`).

Read by:
- Not currently read by challenge logic; useful for auditing/debugging.

---

## 3) Transit Boundary Storage (Synapse Payloads)

The miner uses request payload type by route/handler.

Inbound payload classes (conceptual):
- Receipt payload -> `handle_receipt()`
- Batch envelope payload -> `handle_batch_envelope()`
- Pin challenge -> `handle_pin_challenge()`
- Merkle proof challenge -> `handle_merkle_proof_challenge()`
- Index query -> `handle_index_query()`

Outbound payload additions:
- Receipt response: adds `miner_cid`, `miner_hotkey`, `miner_sig`
- Batch response: adds `miner_batch_cid`, `miner_hotkey`, `miner_sig`
- Pin challenge response: adds `receipt_json`, `retrieved_at_ns`, `miner_sig`
- Merkle challenge response: adds `proof_path`, `miner_sig`
- Index response: adds `tip_cid`, `receipt_count`, `oldest_cid`, `tip_batch_cid`, `batch_count`

Purpose:
- This is not durable storage, but it is the interface where data enters/exits miner storage.

---

## End-to-End Data Flow

## A) Receipt ingest flow

1. Validator sends receipt payload.
2. `handle_receipt()` validates required fields/signature presence.
3. If FAIL:
   - Write row to `fail_log`.
   - Return without writing `receipts`.
4. If SUCCESS:
   - Compute `receipt_cid`.
   - If dedupe hit in `receipts`: return existing `miner_cid`.
   - Else:
     - Serialize receipt and write to `LocalIPFSClient._store` (`miner_cid`).
     - Mark pin in `_pins`.
     - Insert row in `receipts`.
     - Update/replace row in `actor_tips`.
     - Commit.
     - Return payload enriched with miner fields.

## B) Batch ingest flow

1. Validator sends batch envelope with list of `receipt_cids` and `merkle_root`.
2. `handle_batch_envelope()` validates required fields/signature presence.
3. Rebuilds Merkle tree from sorted `receipt_cids`.
4. Rejects if computed root != envelope root.
5. On success:
   - Serialize envelope and write to local IPFS store (`miner_batch_cid`), pin it.
   - Compute `batch_cid`.
   - Store tree in `batch_tree_cache[batch_cid]`.
   - Upsert into `batches`.
   - Update `batch_tips` global row.
   - Update matching rows in `receipts` to set `batch_id=batch_cid`.
   - Commit.
   - Return payload with miner batch fields/signature.

## C) Validator challenge flows

### Pin challenge (`receipt_cid`)
- `handle_pin_challenge()`:
  - Lookup `receipts` for `miner_cid`.
  - Pull receipt JSON from local IPFS store.
  - Return `receipt_json` + signed challenge response.

### Merkle proof challenge (`batch_cid`, `target_receipt_cid`)
- `handle_merkle_proof_challenge()`:
  - Lookup in-memory `batch_tree_cache`.
  - Generate proof path for target leaf.
  - Return `proof_path` + signed challenge response.

### Index query (`actor_ss58`)
- `handle_index_query()`:
  - Read `actor_tips` and `batch_tips`.
  - Return current tips/counts.

---

## Inter-Store Relationships

- `receipts.receipt_cid` links request-level receipt identity to persistence.
- `receipts.miner_cid` links DB row -> local IPFS content blob.
- `receipts.batch_id` links receipt rows -> `batches.batch_cid`.
- `batch_tips.tip_batch_cid` points to latest `batches.batch_cid`.
- `batch_tree_cache[batch_cid]` mirrors proof-capable Merkle structure for that batch.

---

## Practical Notes / Current Constraints

- Signature verification is lightweight placeholder logic in current miner:
  - It checks payload field presence and non-empty `validator_sig`.
  - It does not verify signature cryptographically against validator pubkey yet.
- Local IPFS client is in-memory only:
  - Restarting miner loses `_store`, `_pins`, and `batch_tree_cache`.
  - sqlite data survives restart, so DB and in-memory stores can diverge after reboot.
- Merkle proof challenge depends on `batch_tree_cache`:
  - If process restarts, proofs for existing DB batches may fail until cache is rebuilt.

