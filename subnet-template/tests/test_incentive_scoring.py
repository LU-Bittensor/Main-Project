"""Unit tests for MediLink validatorv1 incentive scoring (Part A2).

Exercises score_miner dimensions and EMA without connecting to Subtensor or EVM.
"""

from __future__ import annotations

import json
import pathlib
import sys
import unittest
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TESTS = pathlib.Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from incentive_test_helpers import ensure_import_env  # noqa: E402

ensure_import_env()

from protocol import MessageSynapse  # noqa: E402
from validatorv1 import (  # noqa: E402
    EMA_ALPHA,
    LATENCY_CUTOFF_MS,
    LATENCY_TARGET_MS,
    MerkleTree,
    ValidatorV1,
    compute_cid,
)


def _msg(payload: dict | None) -> MessageSynapse | None:
    if payload is None:
        return None
    return MessageSynapse(payload=payload)


def make_scoring_validator() -> ValidatorV1:
    """Build a ValidatorV1 without __init__ (no wallet / chain)."""
    v = object.__new__(ValidatorV1)
    v.actor_tips = {}
    v.batch_tips = {"global": {"tip_batch_cid": None, "batch_count": 0, "receipt_cids": []}}
    v.batch_receipt_cids = {}
    v.receipt_cache = {}
    v.merkle_tree_cache = {}
    v.score_ema = {}
    v.last_weight_block = 0
    v.my_uid = 0
    return v


def make_receipt(*, actor: str = "0xactor", data_cid: str = "bafydata", sig: str = "ab") -> dict:
    receipt = {
        "spec_version": "1.0",
        "tx_id": "tx-1",
        "timestamp_ns": 1,
        "actor_evm_addr": actor,
        "op_type": "WRITE",
        "data_cid": data_cid,
        "prev_cid": None,
        "hippius_status": "SUCCESS",
        "validator_hotkey": "5Validator",
        "validator_sig": sig,
    }
    return receipt


class TestLatencyScore(unittest.TestCase):
    def setUp(self):
        self.v = make_scoring_validator()

    def test_unavailable_is_zero(self):
        self.assertEqual(self.v._latency_score(False, 10.0), 0.0)

    def test_fast_is_one(self):
        self.assertEqual(self.v._latency_score(True, LATENCY_TARGET_MS), 1.0)
        self.assertEqual(self.v._latency_score(True, 1.0), 1.0)

    def test_cutoff_is_zero(self):
        self.assertEqual(self.v._latency_score(True, LATENCY_CUTOFF_MS), 0.0)

    def test_mid_range_between_zero_and_one(self):
        mid = (LATENCY_TARGET_MS + LATENCY_CUTOFF_MS) / 2.0
        score = self.v._latency_score(True, mid)
        self.assertGreater(score, 0.0)
        self.assertLess(score, 1.0)


class TestIntegrityAndConsistency(unittest.TestCase):
    def setUp(self):
        self.v = make_scoring_validator()
        self.actor = "0xabc"
        self.receipt = make_receipt(actor=self.actor)
        self.receipt_cid = compute_cid(self.receipt)
        self.tree = MerkleTree([self.receipt_cid])
        self.batch_cid = "batch-1"
        self.v.merkle_tree_cache[self.batch_cid] = self.tree
        self.v.actor_tips[self.actor] = {
            "tip_cid": self.receipt_cid,
            "tip_data_cid": self.receipt["data_cid"],
            "receipt_count": 1,
        }
        self.v.batch_tips = {
            "global": {
                "tip_batch_cid": self.batch_cid,
                "batch_count": 1,
                "receipt_cids": [self.receipt_cid],
            }
        }

    def _good_pin(self) -> MessageSynapse:
        from validatorv1 import canonical_bytes

        return _msg(
            {
                "receipt_cid": self.receipt_cid,
                "receipt_json": canonical_bytes(self.receipt).decode("utf-8"),
            }
        )

    def _good_proof(self) -> MessageSynapse:
        return _msg(
            {
                "batch_cid": self.batch_cid,
                "target_receipt_cid": self.receipt_cid,
                "proof_path": self.tree.generate_proof(self.receipt_cid),
            }
        )

    def _good_index(self, *, tip: str | None = None, count: int | None = None, batch: str | None = None) -> MessageSynapse:
        return _msg(
            {
                "actor_evm_addr": self.actor,
                "tip_cid": tip if tip is not None else self.receipt_cid,
                "receipt_count": count if count is not None else 1,
                "tip_batch_cid": batch if batch is not None else self.batch_cid,
            }
        )

    def test_integrity_full_credit(self):
        score = self.v._integrity_score(
            self._good_pin(), self._good_proof(), self.receipt_cid, self.batch_cid
        )
        self.assertAlmostEqual(score, 1.0, places=5)

    def test_integrity_wrong_cid_with_merkle(self):
        bad_pin = _msg(
            {
                "receipt_json": json.dumps(
                    make_receipt(actor=self.actor, data_cid="other"),
                    sort_keys=True,
                    separators=(",", ":"),
                )
            }
        )
        score = self.v._integrity_score(bad_pin, self._good_proof(), self.receipt_cid, self.batch_cid)
        # sig 0.40 + merkle 0.30; CID fail — merkle ok so no fail-closed cap
        self.assertAlmostEqual(score, 0.70, places=5)

    def test_integrity_bad_merkle_is_fail_closed(self):
        bad_proof = _msg({"proof_path": ["00" * 32]})
        score = self.v._integrity_score(
            self._good_pin(), bad_proof, self.receipt_cid, self.batch_cid
        )
        # sig+cid would be 0.70 but merkle fail caps integrity at 0.40
        self.assertAlmostEqual(score, 0.40, places=5)

    def test_integrity_missing_proof_key_fail_closed(self):
        score = self.v._integrity_score(
            self._good_pin(), _msg({}), self.receipt_cid, self.batch_cid
        )
        self.assertAlmostEqual(score, 0.40, places=5)

    def test_consistency_perfect(self):
        self.assertAlmostEqual(self.v._consistency_score(self._good_index(), self.actor), 1.0)

    def test_consistency_tip_mismatch(self):
        score = self.v._consistency_score(self._good_index(tip="bafywrong"), self.actor)
        # tip fail 0; batch still matches → 0.3
        self.assertAlmostEqual(score, 0.3, places=5)

    def test_consistency_count_drift_ignored_when_tip_matches(self):
        # Durable miner count vs ephemeral validator count must not tank score.
        score = self.v._consistency_score(self._good_index(count=999), self.actor)
        self.assertAlmostEqual(score, 1.0, places=5)

    def test_consistency_batch_mismatch(self):
        score = self.v._consistency_score(self._good_index(batch="bafyotherbatch"), self.actor)
        # tip ok 0.7, batch fail 0
        self.assertAlmostEqual(score, 0.7, places=5)

    def test_consistency_empty_payload(self):
        self.assertEqual(self.v._consistency_score(None, self.actor), 0.0)
        self.assertEqual(self.v._consistency_score(_msg({}), self.actor), 0.0)


class TestScoreMinerComposite(unittest.TestCase):
    def setUp(self):
        self.v = make_scoring_validator()
        self.actor = "0xabc"
        self.receipt = make_receipt(actor=self.actor)
        self.receipt_cid = compute_cid(self.receipt)
        self.tree = MerkleTree([self.receipt_cid])
        self.batch_cid = "batch-1"
        self.v.merkle_tree_cache[self.batch_cid] = self.tree
        self.v.actor_tips[self.actor] = {
            "tip_cid": self.receipt_cid,
            "tip_data_cid": self.receipt["data_cid"],
            "receipt_count": 1,
        }
        self.v.batch_tips = {
            "global": {
                "tip_batch_cid": self.batch_cid,
                "batch_count": 1,
                "receipt_cids": [self.receipt_cid],
            }
        }

    def _responses(self, *, with_pin: bool = True):
        from validatorv1 import canonical_bytes

        pin = (
            _msg({"receipt_json": canonical_bytes(self.receipt).decode("utf-8")})
            if with_pin
            else _msg({"receipt_json": None})
        )
        proof = _msg(
            {
                "proof_path": self.tree.generate_proof(self.receipt_cid),
            }
        )
        index = _msg(
            {
                "tip_cid": self.receipt_cid,
                "receipt_count": 1,
                "tip_batch_cid": self.batch_cid,
            }
        )
        return pin, proof, index

    def test_good_path_near_one(self):
        pin, proof, index = self._responses()
        score = self.v.score_miner(
            self.actor, self.receipt_cid, self.batch_cid, pin, proof, index, elapsed_ms=50.0
        )
        self.assertGreaterEqual(score, 0.99)
        self.assertLessEqual(score, 1.0)

    def test_missing_pin_collapses_availability_and_latency(self):
        pin, proof, index = self._responses(with_pin=False)
        score = self.v.score_miner(
            self.actor, self.receipt_cid, self.batch_cid, pin, proof, index, elapsed_ms=50.0
        )
        # avail 0, latency 0; integrity may still get merkle 0.30; consistency 1.0
        # composite = 0.50*0.30 + 0.30*1.0 = 0.15 + 0.30 = 0.45
        self.assertAlmostEqual(score, 0.45, places=5)

    def test_empty_pin_synapse(self):
        score = self.v.score_miner(
            self.actor, self.receipt_cid, self.batch_cid, None, None, None, elapsed_ms=50.0
        )
        self.assertEqual(score, 0.0)

    def test_merkle_failure_is_heavily_penalized(self):
        pin, _proof, index = self._responses()
        bad_proof = _msg({"proof_path": None})
        score = self.v.score_miner(
            self.actor, self.receipt_cid, self.batch_cid, pin, bad_proof, index, elapsed_ms=50.0
        )
        # avail 1, latency 1, integrity capped 0.40, consistency 1
        # 0.15 + 0.05 + 0.50*0.40 + 0.30 = 0.15+0.05+0.20+0.30 = 0.70
        self.assertAlmostEqual(score, 0.70, places=5)


class TestEmaUpdate(unittest.TestCase):
    def test_ema_formula(self):
        v = make_scoring_validator()
        uid = 1
        raw = 1.0
        prev = 0.0
        v.score_ema[uid] = prev
        # Mirror run_challenge_round EMA step
        v.score_ema[uid] = (EMA_ALPHA * raw) + ((1.0 - EMA_ALPHA) * v.score_ema.get(uid, raw))
        self.assertAlmostEqual(v.score_ema[uid], EMA_ALPHA * raw)

        prev = v.score_ema[uid]
        raw2 = 0.5
        v.score_ema[uid] = (EMA_ALPHA * raw2) + ((1.0 - EMA_ALPHA) * prev)
        expected = (EMA_ALPHA * raw2) + ((1.0 - EMA_ALPHA) * prev)
        self.assertAlmostEqual(v.score_ema[uid], expected)

    def test_first_score_seeds_from_raw_when_missing(self):
        v = make_scoring_validator()
        uid = 2
        raw = 0.8
        prev = v.score_ema.get(uid, raw)
        v.score_ema[uid] = (EMA_ALPHA * raw) + ((1.0 - EMA_ALPHA) * prev)
        self.assertAlmostEqual(v.score_ema[uid], raw)


class TestSelectChallengeTarget(unittest.TestCase):
    def test_picks_leaf_from_last_closed_batch_not_live_tip(self):
        v = make_scoring_validator()
        batched = ["bafyleaf_a", "bafyleaf_b"]
        live_tip = "bafy_not_in_batch_yet"
        batch_cid = "bafybatch1"
        v.batch_tips = {
            "global": {
                "tip_batch_cid": batch_cid,
                "batch_count": 1,
                "receipt_cids": batched,
            }
        }
        v.batch_receipt_cids[batch_cid] = batched
        v.receipt_cache = {
            "bafyleaf_a": {"actor_evm_addr": "0xaaa"},
            "bafyleaf_b": {"actor_evm_addr": "0xbbb"},
        }
        v.actor_tips = {
            "0xaaa": {"tip_cid": live_tip, "tip_data_cid": "x", "receipt_count": 3},
        }

        actor, receipt_cid, out_batch = v.select_challenge_target(uid=0)
        self.assertEqual(out_batch, batch_cid)
        self.assertIn(receipt_cid, batched)
        self.assertNotEqual(receipt_cid, live_tip)
        self.assertEqual(actor, v.receipt_cache[receipt_cid]["actor_evm_addr"])

        actor1, receipt1, _ = v.select_challenge_target(uid=1)
        self.assertEqual(receipt1, batched[1])
        self.assertEqual(actor1, "0xbbb")

    def test_no_batch_yet_skips(self):
        v = make_scoring_validator()
        v.actor_tips = {"0xaaa": {"tip_cid": "bafytip", "tip_data_cid": "x", "receipt_count": 1}}
        self.assertEqual(v.select_challenge_target(0), (None, None, None))


class TestMaybeSetWeightsRateLimit(unittest.TestCase):
    def test_rate_limit_skips_submission(self):
        v = make_scoring_validator()
        v.last_weight_block = 100
        v.score_ema = {1: 1.0}
        v.metagraph = SimpleNamespace(hotkeys=["self", "miner"])
        v.subtensor = SimpleNamespace(block=150)  # delta 50 < 100
        called = {"n": 0}

        def _execute(*_a, **_k):
            called["n"] += 1
            return SimpleNamespace(success=True)

        v.subtensor.execute = _execute
        v.wallet = SimpleNamespace()
        v.config = SimpleNamespace(netuid=573)
        v.maybe_set_weights()
        self.assertEqual(called["n"], 0)


if __name__ == "__main__":
    unittest.main()
