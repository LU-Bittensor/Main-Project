# Scoring correctness notes (integrity / consistency)

## What `integrity=0.70` meant in your logs

Integrity is three additive checks (then fail-closed):

| Check | Points |
|---|---|
| pin has `validator_sig` | 0.40 |
| pin receipt CID matches expected | 0.30 |
| merkle proof verifies to batch root | 0.30 |

`0.70` = **sig + CID**, **merkle missing/failed**. Pin was fine; proof was not.

### Root cause (code, not machine)

Miner `handle_merkle_proof_challenge` only consulted in-memory `batch_tree_cache`. After restart (or if `/batch` was never applied to cache), it returned `proof_path: null` even though the batch envelope was durable in SQLite/IPFS. Validator then withheld the 0.30 merkle points.

**Fix:** rebuild the Merkle tree from the stored batch envelope on cache miss (`_get_or_rebuild_batch_tree`).

---

## What `consistency=0.60` meant

Old formula: `0.6 * tip_match + 0.4 * count_score`.

`0.60` = tip matched, **count_score = 0**.

### Root cause (code, not machine)

- Validator `actor_tips[].receipt_count` is **in-memory** (resets on restart).
- Miner `actor_tips.receipt_count` is **SQLite** (durable; your DB had ~100 for the mock actor).
- Tip CID can agree (same head) while absolute counts diverge → false negative.

**Fix:** consistency is now **tip + tip_batch agreement**. Absolute count drift is logged only (diagnostic), not used to zero the score when tips agree.

---

## Challenge targeting (merkle null race)

Challenges must pick a `receipt_cid` from the **last closed batch's** `receipt_cids`, not the live actor tip. The tip often sits in `batch_pending` and is not a leaf of `tip_batch_cid` yet — that produced intermittent `proof_path_null`. See `select_challenge_target` in `validatorv1.py`.

| Dimension | Old | New |
|---|---|---|
| Availability | 0.40 | **0.15** |
| Latency | 0.25 | **0.05** |
| Integrity | 0.25 | **0.50** |
| Consistency | 0.10 | **0.30** |

Integrity also **fail-closes**: if merkle is not OK, integrity is capped at **0.40** (cannot look “mostly correct” with a bad/missing proof).
