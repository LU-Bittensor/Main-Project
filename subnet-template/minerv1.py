import argparse
import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import bittensor as bt
from bittensor.wallet import Wallet
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import uvicorn

from protocol import MessageSynapse
from logging_setup import configure_app_logging, silence_noisy_loggers

log = logging.getLogger("minerv1")


VALIDATOR_SIGN_FIELDS = [
    "spec_version",
    "tx_id",
    "timestamp_ns",
    "actor_evm_addr",
    "op_type",
    "data_cid",
    "prev_cid",
    "hippius_status",
    "validator_hotkey",
]

BATCH_SIGN_FIELDS = [
    "spec_version",
    "batch_index",
    "window_start_ns",
    "window_end_ns",
    "receipt_count",
    "receipt_cids",
    "merkle_root",
    "prev_batch_cid",
    "validator_hotkey",
]


def canonical_bytes(obj: Dict[str, Any]) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def compute_cid(obj: Dict[str, Any]) -> str:
    return f"bafy{hashlib.sha256(canonical_bytes(obj)).hexdigest()[:52]}"


def _sha(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


class LocalIPFSClient:
    # Minimal local content-addressed store to keep miner logic runnable.
    def __init__(self):
        self._store: Dict[str, str] = {}
        self._pins: set[str] = set()

    def add_str(self, data: str) -> str:
        cid = f"bafy{hashlib.sha256(data.encode('utf-8')).hexdigest()[:52]}"
        self._store[cid] = data
        return cid

    def pin_add(self, cid: str) -> None:
        if cid in self._store:
            self._pins.add(cid)

    def cat(self, cid: str) -> bytes:
        return self._store[cid].encode("utf-8")


class MerkleTree:
    def __init__(self, cids: List[str]):
        leaves = [_sha(cid.encode("utf-8")) for cid in sorted(cids)]
        if not leaves:
            self.layers = [[_sha(b"empty")]]
        else:
            self.layers = [leaves]
            while len(self.layers[-1]) > 1:
                current = self.layers[-1]
                if len(current) % 2 == 1:
                    current = current + [current[-1]]
                nxt = []
                for i in range(0, len(current), 2):
                    left, right = sorted([current[i], current[i + 1]])
                    nxt.append(_sha(left + right))
                self.layers.append(nxt)

    @property
    def root_hex(self) -> str:
        return self.layers[-1][0].hex()

    def generate_proof(self, target_cid: str) -> List[str]:
        target = _sha(target_cid.encode("utf-8"))
        if target not in self.layers[0]:
            raise ValueError("target not found")
        idx = self.layers[0].index(target)
        proof = []
        for layer in self.layers[:-1]:
            layer_for_pair = layer[:] + [layer[-1]] if len(layer) % 2 == 1 else layer[:]
            proof.append(layer_for_pair[idx ^ 1].hex())
            idx //= 2
        return proof


class MinerV1:
    def __init__(self):
        self.config = self.get_config()
        self.setup_logging()
        self.setup_bittensor_objects()
        self.db = sqlite3.connect(self.config.db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self._init_schema()
        self.ipfs = LocalIPFSClient()
        self.batch_tree_cache: Dict[str, MerkleTree] = {}

    def get_config(self):
        parser = argparse.ArgumentParser(description="MediLink miner v1 (bittensor v11 HTTP axon)")
        parser.add_argument("--netuid", type=int, default=2)
        parser.add_argument("--db-path", default="./miner_store_v1.db")
        parser.add_argument("--wallet.name", dest="wallet_name", default="test-miner")
        parser.add_argument("--wallet.hotkey", dest="wallet_hotkey", default="default")
        parser.add_argument("--wallet.path", dest="wallet_path", default="~/.bittensor/wallets")
        parser.add_argument("--subtensor.network", dest="network", default="test")
        parser.add_argument("--axon.port", dest="axon_port", type=int, default=8091)
        parser.add_argument(
            "--axon.ip",
            dest="axon_ip",
            default=os.getenv("AXON_IP", "auto"),
            help="Public IP to publish via ServeAxon. Use 'auto' to detect (default).",
        )
        parser.add_argument(
            "--logging.debug",
            dest="logging_debug",
            action="store_true",
            help="Enable DEBUG for minerv1 subnet logic (RPC libraries stay quiet).",
        )
        parser.add_argument(
            "--logging.info",
            dest="logging_info",
            action="store_true",
            help="Deprecated alias; INFO is the default.",
        )
        ns = parser.parse_args()
        ns.db_path = os.path.expanduser(ns.db_path)
        ns.wallet_path = os.path.expanduser(ns.wallet_path)
        return ns

    @staticmethod
    def _resolve_public_ip(configured: str) -> str:
        raw = (configured or "").strip()
        if raw and raw.lower() not in ("auto", "0.0.0.0", "127.0.0.1", "localhost"):
            return raw
        # Prefer explicit env, then public HTTP lookup.
        env_ip = os.getenv("EXTERNAL_IP") or os.getenv("PUBLIC_IP")
        if env_ip:
            return env_ip.strip()
        try:
            import urllib.request

            with urllib.request.urlopen("https://api.ipify.org", timeout=5) as resp:
                ip = resp.read().decode("utf-8").strip()
                if ip:
                    return ip
        except Exception as exc:
            log.warning("public IP lookup failed: %s", exc)
        raise RuntimeError(
            "Could not resolve a public axon IP. Pass --axon.ip <PUBLIC_IP> "
            "(127.0.0.1 is rejected by the chain)."
        )

    def setup_logging(self):
        # Default INFO for subnet logic; never flood with bittensor/websocket RPC DEBUG.
        if getattr(self.config, "logging_debug", False):
            level = logging.DEBUG
        else:
            level = logging.INFO
        configure_app_logging(("minerv1",), level=level)

    def setup_bittensor_objects(self):
        self.wallet = Wallet(
            name=self.config.wallet_name,
            hotkey=self.config.wallet_hotkey,
            path=self.config.wallet_path,
        )
        self.subtensor = bt.Subtensor(self.config.network)
        mg = self.subtensor.subnets.metagraph(self.config.netuid)
        self.metagraph = mg
        self.hotkeys = list(mg.hotkeys)
        log.info("metagraph hotkeys=%s", self.hotkeys)
        self.my_uid = self.hotkeys.index(self.wallet.hotkey.ss58_address)
        self.axon_ip = self._resolve_public_ip(self.config.axon_ip)
        self.axon_port = int(self.config.axon_port)
        log.info("axon will publish as %s:%s", self.axon_ip, self.axon_port)
        # Bittensor init often re-enables DEBUG on transport loggers.
        silence_noisy_loggers(logging.WARNING)

    def _init_schema(self):
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS receipts (
                receipt_cid TEXT PRIMARY KEY,
                miner_cid TEXT NOT NULL,
                actor_evm_addr TEXT NOT NULL,
                timestamp_ns INTEGER NOT NULL,
                prev_cid TEXT,
                op_type TEXT NOT NULL,
                hippius_status TEXT NOT NULL,
                batch_id TEXT,
                pinned_at_ns INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS actor_tips (
                actor_evm_addr TEXT PRIMARY KEY,
                tip_cid TEXT NOT NULL,
                receipt_count INTEGER NOT NULL DEFAULT 0,
                oldest_cid TEXT
            );
            CREATE TABLE IF NOT EXISTS batches (
                batch_cid TEXT PRIMARY KEY,
                batch_index INTEGER NOT NULL,
                window_start_ns INTEGER NOT NULL,
                window_end_ns INTEGER NOT NULL,
                receipt_count INTEGER NOT NULL,
                merkle_root TEXT NOT NULL,
                prev_batch_cid TEXT,
                miner_batch_cid TEXT,
                pinned_at_ns INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS batch_tips (
                scope TEXT PRIMARY KEY,
                tip_batch_cid TEXT NOT NULL,
                batch_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS fail_log (
                tx_id TEXT PRIMARY KEY,
                actor_evm_addr TEXT NOT NULL,
                op_type TEXT NOT NULL,
                data_cid TEXT NOT NULL,
                logged_at_ns INTEGER NOT NULL,
                reason TEXT
            );
            """
        )
        self.db.commit()

    @staticmethod
    def _payload(synapse: MessageSynapse) -> Dict[str, Any]:
        return dict(getattr(synapse, "payload", {}) or {})

    @staticmethod
    def _set_payload(synapse: MessageSynapse, payload: Dict[str, Any]) -> MessageSynapse:
        synapse.payload = payload
        return synapse

    def blacklist_fn(self, synapse: MessageSynapse) -> Tuple[bool, str]:
        caller = getattr(getattr(synapse, "dendrite", None), "hotkey", None)
        if caller is None:
            return False, "No dendrite caller in local test mode."
        if caller not in self.hotkeys:
            return True, "Unregistered caller."
        return False, "ok"

    def priority_fn(self, synapse: MessageSynapse) -> float:
        caller = getattr(getattr(synapse, "dendrite", None), "hotkey", None)
        if caller is None or caller not in self.hotkeys:
            return 0.0
        return 1.0

    def _dispatch_payload(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        synapse = MessageSynapse(payload=payload)
        if path == "/receipt":
            out = self.handle_receipt(synapse)
        elif path == "/batch":
            out = self.handle_batch_envelope(synapse)
        elif path == "/challenge/pin":
            out = self.handle_pin_challenge(synapse)
        elif path == "/challenge/merkle":
            out = self.handle_merkle_proof_challenge(synapse)
        elif path == "/index":
            out = self.handle_index_query(synapse)
        else:
            return {"error": f"unknown path {path}"}
        return dict(getattr(out, "payload", {}) or {})

    def setup_axon(self):
        app = FastAPI(title="MediLink miner v1")

        @app.get("/health")
        def health():
            return {"ok": True, "uid": self.my_uid, "hotkey": self.wallet.hotkey.ss58_address}

        async def _handle(request: Request, path: str):
            body = await request.json()
            payload = body.get("payload", body) if isinstance(body, dict) else {}
            try:
                result = self._dispatch_payload(path, dict(payload or {}))
            except Exception as exc:
                log.exception("handler failed path=%s", path)
                return JSONResponse({"error": str(exc)}, status_code=500)
            return {"payload": result}

        @app.post("/receipt")
        async def receipt(request: Request):
            return await _handle(request, "/receipt")

        @app.post("/batch")
        async def batch(request: Request):
            return await _handle(request, "/batch")

        @app.post("/challenge/pin")
        async def pin(request: Request):
            return await _handle(request, "/challenge/pin")

        @app.post("/challenge/merkle")
        async def merkle(request: Request):
            return await _handle(request, "/challenge/merkle")

        @app.post("/index")
        async def index(request: Request):
            return await _handle(request, "/index")

        self._app = app
        config = uvicorn.Config(
            app,
            host="0.0.0.0",
            port=self.axon_port,
            log_level="info",
        )
        self._server = uvicorn.Server(config)
        thread = threading.Thread(target=self._server.run, daemon=True)
        thread.start()
        log.info("HTTP axon listening on 0.0.0.0:%s (published ip=%s)", self.axon_port, self.axon_ip)

        try:
            result = self.subtensor.execute(
                bt.ServeAxon(netuid=self.config.netuid, ip=self.axon_ip, port=self.axon_port),
                self.wallet,
            )
            ok = bool(getattr(result, "success", False))
            log.info("ServeAxon result success=%s message=%s", ok, getattr(result, "message", result))
            if not ok:
                err = getattr(result, "error", None)
                log.warning("ServeAxon failed: %s", err or getattr(result, "message", result))
        except Exception as exc:
            log.warning("ServeAxon failed (HTTP server still running): %s", exc)

    def run(self):
        self.setup_axon()
        while True:
            try:
                mg = self.subtensor.subnets.metagraph(self.config.netuid)
                self.metagraph = mg
                self.hotkeys = list(mg.hotkeys)
                log.info("miner uid=%s running; hotkeys=%s", self.my_uid, len(self.hotkeys))
            except Exception as exc:
                log.warning("metagraph sync failed: %s", exc)
            time.sleep(15)

    def _verify_validator_sig(self, receipt: Dict[str, Any], fields: List[str]) -> bool:
        # Production implementation should verify with hotkey pubkey from metagraph.
        # Here we enforce required fields and presence of validator_sig.
        try:
            _ = canonical_bytes({k: receipt[k] for k in fields})
            return bool(receipt.get("validator_sig"))
        except Exception:
            return False

    def handle_receipt(self, synapse: MessageSynapse) -> MessageSynapse:
        receipt = self._payload(synapse)
        if not self._verify_validator_sig(receipt, VALIDATOR_SIGN_FIELDS):
            return synapse
        for required in [
            "tx_id",
            "data_cid",
            "actor_evm_addr",
            "validator_hotkey",
            "hippius_status",
            "spec_version",
            "op_type",
        ]:
            if not receipt.get(required):
                return synapse
        log.info(
            "handle_receipt op_type=%s tx_id=%s actor=%s data_cid=%s resource_id=%s",
            receipt.get("op_type"),
            receipt.get("tx_id"),
            receipt.get("actor_evm_addr"),
            receipt.get("data_cid"),
            receipt.get("resource_id"),
        )
        if receipt["hippius_status"] == "FAIL":
            self.db.execute(
                "INSERT OR REPLACE INTO fail_log VALUES (?, ?, ?, ?, ?, ?)",
                (
                    receipt["tx_id"],
                    receipt["actor_evm_addr"],
                    receipt["op_type"],
                    receipt["data_cid"],
                    time.time_ns(),
                    "hippius_fail",
                ),
            )
            self.db.commit()
            return synapse

        receipt_cid = compute_cid(receipt)
        row = self.db.execute("SELECT miner_cid FROM receipts WHERE receipt_cid = ?", (receipt_cid,)).fetchone()
        if row:
            receipt["miner_cid"] = row["miner_cid"]
            receipt["miner_hotkey"] = self.wallet.hotkey.ss58_address
            receipt["miner_sig"] = self.wallet.hotkey.sign(row["miner_cid"].encode("utf-8")).hex()
            return self._set_payload(synapse, receipt)

        receipt_json = canonical_bytes(receipt).decode("utf-8")
        miner_cid = self.ipfs.add_str(receipt_json)
        self.ipfs.pin_add(miner_cid)

        self.db.execute(
            "INSERT INTO receipts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                receipt_cid,
                miner_cid,
                receipt["actor_evm_addr"],
                int(receipt["timestamp_ns"]),
                receipt.get("prev_cid"),
                receipt["op_type"],
                receipt["hippius_status"],
                None,
                time.time_ns(),
            ),
        )
        actor_tip = self.db.execute(
            "SELECT tip_cid, receipt_count, oldest_cid FROM actor_tips WHERE actor_evm_addr = ?",
            (receipt["actor_evm_addr"],),
        ).fetchone()
        receipt_count = (actor_tip["receipt_count"] if actor_tip else 0) + 1
        oldest_cid = actor_tip["oldest_cid"] if actor_tip and actor_tip["oldest_cid"] else receipt_cid
        self.db.execute(
            "INSERT OR REPLACE INTO actor_tips VALUES (?, ?, ?, ?)",
            (receipt["actor_evm_addr"], receipt_cid, receipt_count, oldest_cid),
        )
        self.db.commit()

        receipt["miner_cid"] = miner_cid
        receipt["miner_hotkey"] = self.wallet.hotkey.ss58_address
        receipt["miner_sig"] = self.wallet.hotkey.sign(miner_cid.encode("utf-8")).hex()
        print(receipt)
        return self._set_payload(synapse, receipt)

    def handle_batch_envelope(self, synapse: MessageSynapse) -> MessageSynapse:
        envelope = self._payload(synapse)
        if not self._verify_validator_sig(envelope, BATCH_SIGN_FIELDS):
            return synapse
        cids = sorted(envelope.get("receipt_cids", []))
        tree = MerkleTree(cids)
        if tree.root_hex != envelope.get("merkle_root"):
            return synapse

        envelope_json = canonical_bytes(envelope).decode("utf-8")
        miner_batch_cid = self.ipfs.add_str(envelope_json)
        self.ipfs.pin_add(miner_batch_cid)
        batch_cid = compute_cid(envelope)
        self.batch_tree_cache[batch_cid] = tree

        self.db.execute(
            "INSERT OR REPLACE INTO batches VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                batch_cid,
                envelope["batch_index"],
                envelope["window_start_ns"],
                envelope["window_end_ns"],
                envelope["receipt_count"],
                envelope["merkle_root"],
                envelope.get("prev_batch_cid"),
                miner_batch_cid,
                time.time_ns(),
            ),
        )
        tip = self.db.execute("SELECT batch_count FROM batch_tips WHERE scope = 'global'").fetchone()
        batch_count = (tip["batch_count"] if tip else 0) + 1
        self.db.execute(
            "INSERT OR REPLACE INTO batch_tips VALUES (?, ?, ?)",
            ("global", batch_cid, batch_count),
        )
        if cids:
            placeholders = ",".join(["?"] * len(cids))
            self.db.execute(f"UPDATE receipts SET batch_id = ? WHERE receipt_cid IN ({placeholders})", [batch_cid] + cids)
        self.db.commit()

        envelope["miner_batch_cid"] = miner_batch_cid
        envelope["miner_hotkey"] = self.wallet.hotkey.ss58_address
        envelope["miner_sig"] = self.wallet.hotkey.sign(miner_batch_cid.encode("utf-8")).hex()
        return self._set_payload(synapse, envelope)

    def handle_pin_challenge(self, synapse: MessageSynapse) -> MessageSynapse:
        print("in handle_pin_challenge")
        challenge = self._payload(synapse)
        row = self.db.execute(
            "SELECT miner_cid FROM receipts WHERE receipt_cid = ?",
            (challenge.get("receipt_cid"),),
        ).fetchone()
        if not row:
            challenge["receipt_json"] = None
            return self._set_payload(synapse, challenge)
        content = self.ipfs.cat(row["miner_cid"]).decode("utf-8")
        msg = (challenge["receipt_cid"] + challenge["nonce"]).encode("utf-8")
        challenge["receipt_json"] = content
        challenge["retrieved_at_ns"] = time.time_ns()
        challenge["miner_sig"] = self.wallet.hotkey.sign(msg).hex()
        return self._set_payload(synapse, challenge)

    def handle_merkle_proof_challenge(self, synapse: MessageSynapse) -> MessageSynapse:
        challenge = self._payload(synapse)
        batch_cid = challenge.get("batch_cid")
        tree = self._get_or_rebuild_batch_tree(batch_cid)
        if not tree:
            log.warning(
                "merkle challenge: no tree for batch_cid=%s (cache miss + DB rebuild failed)",
                batch_cid,
            )
            challenge["proof_path"] = None
            return self._set_payload(synapse, challenge)
        try:
            proof = tree.generate_proof(challenge["target_receipt_cid"])
        except Exception as exc:
            log.warning(
                "merkle challenge: proof gen failed batch=%s target=%s err=%s",
                batch_cid,
                challenge.get("target_receipt_cid"),
                exc,
            )
            challenge["proof_path"] = None
            return self._set_payload(synapse, challenge)
        msg = (challenge["batch_cid"] + challenge["target_receipt_cid"] + challenge["nonce"]).encode("utf-8")
        challenge["proof_path"] = proof
        challenge["miner_sig"] = self.wallet.hotkey.sign(msg).hex()
        return self._set_payload(synapse, challenge)

    def _get_or_rebuild_batch_tree(self, batch_cid: Optional[str]) -> Optional[MerkleTree]:
        """Return cached tree, or rebuild from durable batch envelope (survives process restart)."""
        if not batch_cid:
            return None
        cached = self.batch_tree_cache.get(batch_cid)
        if cached is not None:
            return cached
        row = self.db.execute(
            "SELECT miner_batch_cid, merkle_root, receipt_count FROM batches WHERE batch_cid = ?",
            (batch_cid,),
        ).fetchone()
        if not row or not row["miner_batch_cid"]:
            return None
        try:
            envelope = json.loads(self.ipfs.cat(row["miner_batch_cid"]).decode("utf-8"))
        except Exception as exc:
            log.warning("merkle rebuild: ipfs cat failed batch=%s: %s", batch_cid, exc)
            return None
        cids = sorted(envelope.get("receipt_cids") or [])
        if not cids:
            return None
        tree = MerkleTree(cids)
        if tree.root_hex != (envelope.get("merkle_root") or row["merkle_root"]):
            log.warning(
                "merkle rebuild: root mismatch batch=%s rebuilt=%s stored=%s",
                batch_cid,
                tree.root_hex,
                envelope.get("merkle_root") or row["merkle_root"],
            )
            return None
        self.batch_tree_cache[batch_cid] = tree
        log.info("merkle rebuild: restored tree for batch_cid=%s leaves=%s", batch_cid, len(cids))
        return tree

    def handle_index_query(self, synapse: MessageSynapse) -> MessageSynapse:
        query = self._payload(synapse)
        tip = self.db.execute(
            "SELECT tip_cid, receipt_count, oldest_cid FROM actor_tips WHERE actor_evm_addr = ?",
            (query.get("actor_evm_addr"),),
        ).fetchone()
        batch = self.db.execute("SELECT tip_batch_cid, batch_count FROM batch_tips WHERE scope = 'global'").fetchone()
        query["tip_cid"] = tip["tip_cid"] if tip else None
        query["receipt_count"] = tip["receipt_count"] if tip else 0
        query["oldest_cid"] = tip["oldest_cid"] if tip else None
        query["tip_batch_cid"] = batch["tip_batch_cid"] if batch else None
        query["batch_count"] = batch["batch_count"] if batch else 0
        return self._set_payload(synapse, query)


if __name__ == "__main__":
    MinerV1().run()
