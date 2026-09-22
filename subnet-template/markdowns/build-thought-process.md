# MediLink v1 Build Notes

## Goal
Implement a first-pass validator/miner pair from `configuration.md` that covers:
- validator receipt + batch construction,
- validator challenge and scoring loop,
- miner receipt/batch ingestion and persistence,
- miner challenge response endpoints,
- validator weight normalization and `set_weights` submission.

## How I translated the spec into code
- I kept validator and miner as separate runnable scripts (`validatorv1.py`, `minerv1.py`) matching the existing template layout.
- I implemented protocol-critical deterministic helpers in both files:
  - `canonical_bytes()` for signing/CID determinism.
  - `compute_cid()` as a deterministic local CID-like identifier for consistency tests.
  - SHA-256 Merkle tree logic for batch roots and proof generation.
- I treated the document as a strict behavior contract and prioritized:
  - state transitions,
  - request/response shape,
  - challenge scoring and EMA weighting,
  - persistence interfaces.

## Validator design choices
- Durable in-memory state was implemented for:
  - `actor_tips`,
  - `batch_tips`,
  - `batch_pending`,
  - `score_ema`,
  - `merkle_tree_cache`,
  - `receipt_cache`.
- Receipt build path follows the spec:
  - derive `op_type`,
  - fill required fields,
  - sign validator scope fields,
  - compute receipt CID,
  - advance actor tip and pending batch list.
- Batch close path follows the spec:
  - sorted receipt CID list,
  - Merkle root computation,
  - signed envelope,
  - cache tree by batch CID,
  - update batch pointers.
- Challenge/scoring round:
  - selects active miners and challenge targets,
  - issues pin challenge, proof challenge, and index query,
  - computes availability/latency/integrity/consistency composite,
  - updates EMA.
- Weight push path:
  - applies chain cooldown,
  - normalizes EMA to sum 1.0,
  - calls `subtensor.set_weights(...)`.

## Miner design choices
- SQLite schema mirrors the document’s core tables:
  - `receipts`,
  - `actor_tips`,
  - `batches`,
  - `batch_tips`,
  - `fail_log`.
- Receipt ingestion handler:
  - checks validator payload shape/signature presence,
  - handles FAIL receipts via `fail_log`,
  - deduplicates,
  - pins content to local store,
  - updates receipt and actor indexes,
  - returns miner CID + signature.
- Batch ingestion handler:
  - validates payload,
  - recomputes Merkle root,
  - stores envelope + tree,
  - updates batch tip and receipt batch mappings,
  - returns miner batch acknowledgement signature.
- Challenge handlers:
  - pin challenge returns canonical receipt JSON and freshness signature.
  - Merkle proof challenge returns proof path and freshness signature.
  - index query returns actor tip/count + global batch tip/count.

## Practical simplifications in this v1
- EVM ingestion is represented with a `--mock-events` path in validator (real `eth_getLogs` integration is left as production integration work).
- IPFS interactions are implemented with a deterministic local content-addressed store (`LocalIPFSClient`) so protocol behavior is testable without daemon dependencies.
- Validator signature verification on miner side is implemented as a lightweight structural check plus signature field presence; production should bind to metagraph public-key verification directly.
- Synapse payloads are passed as dictionaries to keep integration simple with the existing template.

## What is complete vs next
- Complete:
  - end-to-end query/response plumbing between validator and miner handlers,
  - miner storage + challenge responses,
  - validator scoring + EMA + set_weights loop,
  - deterministic receipt/batch/Merkle behavior.
- Recommended next steps:
  - introduce explicit typed synapse classes in `protocol.py`,
  - replace local IPFS mock with `ipfshttpclient`,
  - implement real EVM polling + H160 to SS58 resolution,
  - persist validator state to disk across restarts,
  - add integration tests for challenge scoring edge cases.
