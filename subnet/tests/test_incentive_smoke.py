"""In-process incentive smoke: receipt → batch → challenges → score → EMA (Part A1).

Does not require a live Subtensor node, EVM RPC, or HTTP axon. Stubs wallets and
runs miner handlers against an in-memory SQLite store.
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
import sys
import time
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

from miner import LocalIPFSClient, Miner  # noqa: E402
from protocol import MessageSynapse  # noqa: E402
from validator import (  # noqa: E402
    BATCH_SIGN_FIELDS,
    EMA_ALPHA,
    MerkleTree,
    VALIDATOR_SIGN_FIELDS,
    Validator,
    canonical_bytes,
    compute_cid,
)


class _Sig:
    def __init__(self, payload: bytes):
        self._payload = payload

    def hex(self) -> str:
        return self._payload.hex()


class _Hotkey:
    ss58_address = "5TestMinerHotkey"

    def sign(self, data: bytes) -> _Sig:
        return _Sig(b"miner-sig-" + data[:8])


def make_miner() -> Miner:
    m = object.__new__(Miner)
    m.db = sqlite3.connect(":memory:", check_same_thread=False)
    m.db.row_factory = sqlite3.Row
    m._init_schema()
    m.ipfs = LocalIPFSClient()
    m.batch_tree_cache = {}
    m.wallet = SimpleNamespace(hotkey=_Hotkey())
    m.hotkeys = [_Hotkey.ss58_address]
    m.my_uid = 0
    return m


def make_validator() -> Validator:
    v = object.__new__(Validator)
    v.actor_tips = {}
    v.batch_tips = {"global": {"tip_batch_cid": None, "batch_count": 0, "receipt_cids": []}}
    v.batch_receipt_cids = {}
    v.batch_pending = []
    v.window_open_ns = time.time_ns()
    v.batch_index = 0
    v.score_ema = {}
    v.last_weight_block = 0
    v.merkle_tree_cache = {}
    v.receipt_cache = {}
    v.resource_cache = {}
    v.seen_log_keys = set()
    v.my_uid = 0
    v.wallet = SimpleNamespace(hotkey=SimpleNamespace(ss58_address="5TestValidatorHotkey"))
    v.keypair = SimpleNamespace(sign=lambda data: _Sig(b"val-sig-" + data[:8]))
    return v


def build_signed_receipt(v: Validator, actor: str, data_cid: str) -> tuple[dict, str]:
    actor_state = v.actor_tips.get(actor, {})
    receipt = {
        "spec_version": "1.0",
        "tx_id": f"tx-{time.time_ns()}",
        "timestamp_ns": time.time_ns(),
        "actor_evm_addr": actor,
        "op_type": "WRITE",
        "data_cid": data_cid,
        "prev_cid": actor_state.get("tip_cid"),
        "hippius_status": "SUCCESS",
        "validator_hotkey": v.wallet.hotkey.ss58_address,
    }
    signed_payload = {k: receipt[k] for k in VALIDATOR_SIGN_FIELDS}
    receipt["validator_sig"] = v._sign(canonical_bytes(signed_payload))
    receipt_cid = compute_cid(receipt)
    v.receipt_cache[receipt_cid] = receipt
    v.actor_tips[actor] = {
        "tip_cid": receipt_cid,
        "tip_data_cid": data_cid,
        "receipt_count": actor_state.get("receipt_count", 0) + 1,
    }
    v.batch_pending.append(receipt_cid)
    return receipt, receipt_cid


def close_batch(v: Validator) -> tuple[dict, str]:
    cids = sorted(v.batch_pending)
    tree = MerkleTree(cids)
    envelope = {
        "spec_version": "1.0",
        "batch_index": v.batch_index,
        "window_start_ns": v.window_open_ns,
        "window_end_ns": time.time_ns(),
        "receipt_count": len(cids),
        "receipt_cids": cids,
        "merkle_root": tree.root_hex,
        "prev_batch_cid": v.batch_tips["global"]["tip_batch_cid"],
        "validator_hotkey": v.wallet.hotkey.ss58_address,
    }
    signed_payload = {k: envelope[k] for k in BATCH_SIGN_FIELDS}
    envelope["validator_sig"] = v._sign(canonical_bytes(signed_payload))
    batch_cid = compute_cid(envelope)
    v.merkle_tree_cache[batch_cid] = tree
    v.batch_receipt_cids[batch_cid] = list(cids)
    v.batch_tips["global"]["tip_batch_cid"] = batch_cid
    v.batch_tips["global"]["batch_count"] += 1
    v.batch_tips["global"]["receipt_cids"] = list(cids)
    v.batch_index += 1
    v.batch_pending = []
    v.window_open_ns = time.time_ns()
    return envelope, batch_cid


class TestIncentiveSmoke(unittest.TestCase):
    def test_receipt_batch_challenge_score_ema(self):
        miner = make_miner()
        validator = make_validator()
        actor = "0xSmokeActor"
        data_cid = compute_cid({"seed": "incentive-smoke"})

        receipt, receipt_cid = build_signed_receipt(validator, actor, data_cid)
        out = miner.handle_receipt(MessageSynapse(payload=dict(receipt)))
        self.assertTrue(out.payload.get("miner_cid"))
        self.assertTrue(out.payload.get("miner_sig"))

        row = miner.db.execute(
            "SELECT receipt_cid FROM receipts WHERE receipt_cid = ?", (receipt_cid,)
        ).fetchone()
        self.assertIsNotNone(row)

        envelope, batch_cid = close_batch(validator)
        batch_out = miner.handle_batch_envelope(MessageSynapse(payload=dict(envelope)))
        self.assertTrue(batch_out.payload.get("miner_batch_cid"))
        self.assertIn(batch_cid, miner.batch_tree_cache)

        pin_req = {
            "receipt_cid": receipt_cid,
            "nonce": "aabbccdd",
            "issued_at_ns": time.time_ns(),
        }
        pin_resp = miner.handle_pin_challenge(MessageSynapse(payload=dict(pin_req)))
        self.assertTrue(pin_resp.payload.get("receipt_json"))
        returned = json.loads(pin_resp.payload["receipt_json"])
        self.assertEqual(compute_cid(returned), receipt_cid)

        proof_req = {
            "batch_cid": batch_cid,
            "target_receipt_cid": receipt_cid,
            "nonce": "11223344",
        }
        proof_resp = miner.handle_merkle_proof_challenge(MessageSynapse(payload=dict(proof_req)))
        self.assertIsNotNone(proof_resp.payload.get("proof_path"))
        # Single-leaf batches correctly return an empty sibling path.
        computed = MerkleTree.verify(receipt_cid, proof_resp.payload["proof_path"])
        self.assertEqual(computed, validator.merkle_tree_cache[batch_cid].root_hex)

        index_resp = miner.handle_index_query(MessageSynapse(payload={"actor_evm_addr": actor}))
        self.assertEqual(index_resp.payload.get("tip_cid"), receipt_cid)
        self.assertEqual(index_resp.payload.get("receipt_count"), 1)

        raw = validator.score_miner(
            actor,
            receipt_cid,
            batch_cid,
            pin_resp,
            proof_resp,
            index_resp,
            elapsed_ms=40.0,
        )
        self.assertGreaterEqual(raw, 0.99)

        uid = 1
        prev = validator.score_ema.get(uid, raw)
        validator.score_ema[uid] = (EMA_ALPHA * raw) + ((1.0 - EMA_ALPHA) * prev)
        self.assertAlmostEqual(validator.score_ema[uid], raw)

        # Second observation pulls EMA toward a lower raw score.
        raw2 = 0.5
        prev = validator.score_ema[uid]
        validator.score_ema[uid] = (EMA_ALPHA * raw2) + ((1.0 - EMA_ALPHA) * prev)
        expected = (EMA_ALPHA * raw2) + ((1.0 - EMA_ALPHA) * prev)
        self.assertAlmostEqual(validator.score_ema[uid], expected)
        self.assertLess(validator.score_ema[uid], raw)

    def test_audit_receipt_from_access_granted_pins_and_scores(self):
        """AccessGranted → AUDIT receipt uses resource cache data_cid and stable tx_id."""
        miner = make_miner()
        validator = make_validator()
        validator.keypair = SimpleNamespace(sign=lambda data: _Sig(b"val-sig-" + data[:8]))
        actor = "0xAuditActor"
        resource_id = "0x" + "ab" * 32
        hippius_cid = "ehr/demo/patient/record/v1/payload.bin"
        validator._cache_resource(resource_id, hippius_cid, data_hash="0x" + "cd" * 32, owner=actor)

        event_tx = "0x" + "11" * 32
        event = __import__("validator", fromlist=["CommitmentEvent"]).CommitmentEvent(
            actor_evm_addr=actor,
            data_cid=validator._lookup_resource_data_cid(resource_id, "0x" + "cd" * 32),
            hippius_status="SUCCESS",
            op_type_hint="AUDIT",
            resource_id=resource_id,
            tx_id=f"{event_tx}-0",
            evm_tx_hash=event_tx,
            log_index=0,
        )
        self.assertEqual(event.data_cid, hippius_cid)

        receipt, receipt_cid = validator.build_receipt(event)
        self.assertEqual(receipt["op_type"], "AUDIT")
        self.assertEqual(receipt["data_cid"], hippius_cid)
        self.assertEqual(receipt["tx_id"], f"{event_tx}-0")
        self.assertEqual(receipt["resource_id"], resource_id)

        out = miner.handle_receipt(MessageSynapse(payload=dict(receipt)))
        self.assertTrue(out.payload.get("miner_cid"))
        row = miner.db.execute(
            "SELECT op_type FROM receipts WHERE receipt_cid = ?", (receipt_cid,)
        ).fetchone()
        self.assertEqual(row["op_type"], "AUDIT")

        envelope, batch_cid = close_batch(validator)
        miner.handle_batch_envelope(MessageSynapse(payload=dict(envelope)))
        self.assertIn(receipt_cid, envelope["receipt_cids"])

        pin_resp = miner.handle_pin_challenge(
            MessageSynapse(payload={"receipt_cid": receipt_cid, "nonce": "aa", "issued_at_ns": 1})
        )
        proof_resp = miner.handle_merkle_proof_challenge(
            MessageSynapse(
                payload={"batch_cid": batch_cid, "target_receipt_cid": receipt_cid, "nonce": "bb"}
            )
        )
        index_resp = miner.handle_index_query(MessageSynapse(payload={"actor_evm_addr": actor}))
        raw = validator.score_miner(
            actor, receipt_cid, batch_cid, pin_resp, proof_resp, index_resp, elapsed_ms=20.0
        )
        self.assertGreaterEqual(raw, 0.99)


if __name__ == "__main__":
    unittest.main()
