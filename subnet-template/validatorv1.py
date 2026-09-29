import argparse
import hashlib
import json
import logging
import math
import os
import secrets
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import bittensor as bt
from bittensor.wallet import Wallet

from protocol import MessageSynapse
from logging_setup import configure_app_logging, silence_noisy_loggers

try:
    from web3 import Web3
except ImportError:  # pragma: no cover
    Web3 = None  # type: ignore

try:
    from web3.middleware import ExtraDataToPOAMiddleware
except ImportError:  # pragma: no cover
    try:
        from web3.middleware import geth_poa_middleware as ExtraDataToPOAMiddleware  # type: ignore
    except ImportError:
        ExtraDataToPOAMiddleware = None  # type: ignore


log = logging.getLogger("medilink.validator")


def _env_bool(name: str) -> Optional[bool]:
    raw = os.getenv(name)
    if raw is None:
        return None
    return raw.strip().lower() in ("1", "true", "yes", "on")


RECEIPT_SPEC_VERSION = "1.0"
BATCH_SPEC_VERSION = "1.0"
BATCH_WINDOW_NS = 60_000_000_000
MAX_BATCH_SIZE = 4
CHALLENGE_TIMEOUT_S = 10.0
WEIGHTS_RATE_LIMIT_BLOCKS = 100
EMA_ALPHA = 0.3
LATENCY_TARGET_MS = 500.0
LATENCY_CUTOFF_MS = 5000.0
LATENCY_STEEPNESS = 3.0
DRIFT_TOLERANCE = 2

# Scoring weights — correctness over speed (must sum to 1.0).
SCORE_W_AVAILABILITY = 0.15
SCORE_W_LATENCY = 0.05
SCORE_W_INTEGRITY = 0.50
SCORE_W_CONSISTENCY = 0.30
REORG_DEPTH = 6
EVM_POLL_RETRIES = 3
# Public Subtensor EVM RPCs often deny wide / OR-shaped eth_getLogs.
EVM_LOG_CHUNK = 128

# AccessRegistry event signatures (must match key-generation/AccessRegistry.sol).
# Poll ResourceRegistered + AccessGranted only. AccessGrantSet / AccessRevoked /
# ChallengeIssued are intentionally omitted (skip-by-default; see Hippius_EHR_Storage.md).
ACCESS_REGISTRY_ABI = [
    {
        "anonymous": False,
        "inputs": [
            {"indexed": False, "name": "resourceId", "type": "bytes32"},
            {"indexed": False, "name": "owner", "type": "address"},
            {"indexed": False, "name": "dataHash", "type": "bytes32"},
            {"indexed": False, "name": "dataCid", "type": "string"},
        ],
        "name": "ResourceRegistered",
        "type": "event",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "resourceId", "type": "bytes32"},
            {"indexed": True, "name": "requester", "type": "address"},
            {"indexed": False, "name": "dataHash", "type": "bytes32"},
            {"indexed": False, "name": "grantedAt", "type": "uint256"},
        ],
        "name": "AccessGranted",
        "type": "event",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "resourceId", "type": "bytes32"},
            {"indexed": True, "name": "requester", "type": "address"},
            {"indexed": False, "name": "deniedAt", "type": "uint256"},
        ],
        "name": "AccessDenied",
        "type": "event",
    },
]

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
    # Local deterministic CID-like id for validator/miner consistency checks.
    digest = hashlib.sha256(canonical_bytes(obj)).hexdigest()
    return f"bafy{digest[:52]}"


def _sha(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


class MerkleTree:
    def __init__(self, cids: List[str]):
        self.leaf_hashes = [_sha(cid.encode("utf-8")) for cid in sorted(cids)]
        if not self.leaf_hashes:
            self.layers = [[_sha(b"empty")]]
        else:
            self.layers = [self.leaf_hashes[:]]
            while len(self.layers[-1]) > 1:
                current = self.layers[-1]
                if len(current) % 2 == 1:
                    current = current + [current[-1]]
                parents = []
                for i in range(0, len(current), 2):
                    # Canonical ordering removes direction ambiguity in proof verification.
                    left, right = sorted([current[i], current[i + 1]])
                    parents.append(_sha(left + right))
                self.layers.append(parents)

    @property
    def root_hex(self) -> str:
        return self.layers[-1][0].hex()

    def generate_proof(self, target_cid: str) -> List[str]:
        target_hash = _sha(target_cid.encode("utf-8"))
        if target_hash not in self.layers[0]:
            raise ValueError("target leaf missing")
        idx = self.layers[0].index(target_hash)
        path: List[str] = []
        for layer in self.layers[:-1]:
            layer_for_pair = layer[:] + [layer[-1]] if len(layer) % 2 == 1 else layer[:]
            sibling_idx = idx ^ 1
            path.append(layer_for_pair[sibling_idx].hex())
            idx //= 2
        return path

    @staticmethod
    def verify(target_cid: str, proof_path: List[str]) -> str:
        node = _sha(target_cid.encode("utf-8"))
        for sibling_hex in proof_path:
            sibling = bytes.fromhex(sibling_hex)
            left, right = sorted([node, sibling])
            node = _sha(left + right)
        return node.hex()


@dataclass
class CommitmentEvent:
    actor_evm_addr: str
    data_cid: str
    hippius_status: str
    op_type_hint: Optional[str] = None  # AUDIT | WRITE | UPDATE | None → derive
    resource_id: Optional[str] = None


class _MetagraphView:
    """Thin adapter over bittensor v11 metagraph reads."""

    def __init__(self, subtensor: Any, netuid: int):
        self._sub = subtensor
        self.netuid = netuid
        self.hotkeys: List[str] = []
        self.axons: List[Any] = []
        self._raw = None

    def sync(self) -> None:
        mg = self._sub.subnets.metagraph(self.netuid)
        self._raw = mg
        self.hotkeys = list(getattr(mg, "hotkeys", []) or [n.hotkey for n in mg])
        axons = []
        for n in mg:
            endpoint = getattr(n, "axon", None)
            port = 0
            ip = "0.0.0.0"
            if isinstance(endpoint, str) and ":" in endpoint:
                ip, _, port_s = endpoint.rpartition(":")
                try:
                    port = int(port_s)
                except ValueError:
                    port = 0
            axons.append(SimpleNamespace(ip=ip, port=port, hotkey=n.hotkey))
        # Local smoke-test override: LOCAL_MINER_AXON=ip:port
        # - With LOCAL_MINER_HOTKEY: remap only that hotkey (even if already published).
        # - Without: remap every axon that already has a published endpoint (port > 0).
        #   Do not promote empty axons — that wrongly scores owner/empty uids on localhost
        #   while still timing out against the real public miner IP.
        override = os.getenv("LOCAL_MINER_AXON", "").strip()
        if override and ":" in override:
            o_ip, _, o_port_s = override.rpartition(":")
            try:
                o_port = int(o_port_s)
            except ValueError:
                o_port = 0
            target_hk = os.getenv("LOCAL_MINER_HOTKEY", "").strip()
            remapped = []
            for axon in axons:
                if o_port <= 0:
                    break
                if target_hk:
                    if axon.hotkey != target_hk:
                        continue
                elif axon.port <= 0:
                    continue
                axon.ip = o_ip
                axon.port = o_port
                remapped.append(axon.hotkey[:8] + "…")
            if remapped:
                log.info(
                    "LOCAL_MINER_AXON=%s applied to %s hotkey(s): %s",
                    override,
                    len(remapped),
                    remapped,
                )
        self.axons = axons


class _SynapseFanout:
    """
    HTTP fanout replacing v10 Dendrite. Posts JSON envelopes to miner axons
    published via ServeAxon (ip:port on the metagraph).
    """

    def __init__(self, wallet: Wallet):
        self.wallet = wallet
        try:
            import httpx

            self._httpx = httpx
        except ImportError:
            self._httpx = None
            log.warning("httpx not installed; miner fanout will be a no-op")

    @staticmethod
    def _path_for_payload(payload: Optional[Dict[str, Any]]) -> str:
        p = payload or {}
        if "merkle_root" in p and "receipt_cids" in p:
            return "/batch"
        if "target_receipt_cid" in p and "batch_cid" in p:
            return "/challenge/merkle"
        if "receipt_cid" in p and "nonce" in p and "op_type" not in p:
            return "/challenge/pin"
        if set(p.keys()) <= {"actor_evm_addr"} or (
            "actor_evm_addr" in p and "op_type" not in p and "receipt_cid" not in p and "merkle_root" not in p
        ):
            return "/index"
        return "/receipt"

    def _post_one(self, url: str, body: Dict[str, Any], timeout: float) -> Optional[MessageSynapse]:
        assert self._httpx is not None
        # Fail dead hosts quickly on connect; allow full timeout for slow handlers.
        to = self._httpx.Timeout(timeout, connect=min(2.0, timeout))
        try:
            resp = self._httpx.post(url, json=body, timeout=to)
            resp.raise_for_status()
            data = resp.json()
            result_payload = data.get("payload", data) if isinstance(data, dict) else {}
            return MessageSynapse(payload=dict(result_payload or {}))
        except Exception as exc:
            log.warning("fanout failed %s: %s", url, exc)
            return None

    def query(self, axons=None, synapse=None, timeout: float = 10.0):
        from concurrent.futures import ThreadPoolExecutor, as_completed

        axons = list(axons or [])
        payload = getattr(synapse, "payload", None) if synapse is not None else None
        if not axons:
            return [None]
        if self._httpx is None:
            log.debug("dendrite stub (no httpx): fanout to %s axons", len(axons))
            return [None] * len(axons)

        path = self._path_for_payload(payload if isinstance(payload, dict) else {})
        body = {"payload": payload or {}}
        targets: List[Tuple[int, str]] = []
        for i, axon in enumerate(axons):
            ip = getattr(axon, "ip", None) or "127.0.0.1"
            port = int(getattr(axon, "port", 0) or 0)
            if port <= 0 or ip in ("0.0.0.0", ""):
                continue
            targets.append((i, f"http://{ip}:{port}{path}"))

        out: List[Optional[MessageSynapse]] = [None] * len(axons)
        if not targets:
            return out or [None]

        # Parallel fanout so one dead public IP does not serialize behind localhost.
        workers = min(8, len(targets))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {
                pool.submit(self._post_one, url, body, timeout): idx for idx, url in targets
            }
            for fut in as_completed(futs):
                out[futs[fut]] = fut.result()
        return out

class ValidatorV1:
    def __init__(self):
        self.config = self.get_config()
        self.setup_logging()
        self.setup_bittensor_objects()
        self.my_uid = self.metagraph.hotkeys.index(self.wallet.hotkey.ss58_address)
        self.keypair = self.wallet.hotkey
        self.actor_tips: Dict[str, Dict[str, Any]] = {}
        self.batch_tips: Dict[str, Dict[str, Any]] = {
            "global": {"tip_batch_cid": None, "batch_count": 0, "receipt_cids": []}
        }
        self.batch_pending: List[str] = []
        self.window_open_ns = time.time_ns()
        self.batch_index = 0
        self.score_ema: Dict[int, float] = {}
        self.last_weight_block = 0
        self.merkle_tree_cache: Dict[str, MerkleTree] = {}
        self.batch_receipt_cids: Dict[str, List[str]] = {}
        self.receipt_cache: Dict[str, Dict[str, Any]] = {}
        self.last_evm_block: int = max(0, int(getattr(self.config, "evm_start_block", 0)) - 1)
        self._w3 = None
        self._registry = None
        self._setup_evm_client()
        # Align with on-chain LastUpdate so we do not spam SetWeights after restart.
        chain_last = self._chain_last_weight_block()
        if chain_last:
            self.last_weight_block = chain_last
            log.info(
                "Weight rate-limit cursor from chain LastUpdate[%s]=%s (limit=%s blocks)",
                self.my_uid,
                chain_last,
                WEIGHTS_RATE_LIMIT_BLOCKS,
            )

    def get_config(self):
        parser = argparse.ArgumentParser(description="MediLink validator v1 (Access Registry aware)")
        default_config = Path(__file__).resolve().parent / "validator.config.yml"
        parser.add_argument(
            "--config",
            default=os.getenv("VALIDATOR_CONFIG", str(default_config)),
            help="YAML config path (default: subnet-template/validator.config.yml).",
        )
        parser.add_argument("--netuid", type=int, default=None, help="Subnet UID.")
        parser.add_argument(
            "--mock-events",
            action=argparse.BooleanOptionalAction,
            default=None,
            help="Synthetic events. Use --no-mock-events for live eth_getLogs.",
        )
        parser.add_argument("--evm-rpc", default=None, help="Subtensor EVM HTTP RPC URL.")
        parser.add_argument("--evm-chain-id", type=int, default=None, help="EVM chain id.")
        parser.add_argument("--access-registry", default=None, help="AccessRegistry address.")
        parser.add_argument("--evm-start-block", type=int, default=None, help="eth_getLogs cursor.")
        parser.add_argument(
            "--skip-deny-receipts",
            action=argparse.BooleanOptionalAction,
            default=None,
            help="Skip AccessDenied receipts.",
        )
        parser.add_argument(
            "--require-evm-association",
            action=argparse.BooleanOptionalAction,
            default=None,
            help="Discard unmapped H160 actors.",
        )
        parser.add_argument("--wallet", "-w", default=None)
        parser.add_argument("--wallet-hotkey", "-H", default=None)
        parser.add_argument("--wallet-path", default=None)
        parser.add_argument("--network", "-n", default=None, help="Bittensor network or ws:// URL.")
        parser.add_argument("--logging-debug", action="store_true", default=False)
        ns = parser.parse_args()

        file_cfg = self._load_yaml_config(ns.config)
        evm = file_cfg.get("evm") or {}
        wallet_cfg = file_cfg.get("wallet") or {}
        subtensor_cfg = file_cfg.get("subtensor") or {}

        def pick(*values, default=None):
            for v in values:
                if v is None:
                    continue
                if isinstance(v, str) and v.strip() == "":
                    continue
                return v
            return default

        ns.netuid = int(pick(ns.netuid, file_cfg.get("netuid"), default=1))
        ns.wallet = pick(ns.wallet, os.getenv("BT_WALLET"), wallet_cfg.get("name"), default="test-validator")
        ns.wallet_hotkey = pick(
            ns.wallet_hotkey, os.getenv("BT_WALLET_HOTKEY"), wallet_cfg.get("hotkey"), default="default"
        )
        ns.wallet_path = pick(
            ns.wallet_path, os.getenv("BT_WALLET_PATH"), wallet_cfg.get("path"), default="~/.bittensor/wallets"
        )
        ns.network = pick(
            ns.network,
            os.getenv("BT_NETWORK"),
            subtensor_cfg.get("chain_endpoint"),
            subtensor_cfg.get("network"),
            default="local",
        )
        ns.evm_rpc = pick(
            ns.evm_rpc, os.getenv("EVM_RPC_URL"), evm.get("rpc_url"), default="https://test.chain.opentensor.ai"
        )
        ns.evm_chain_id = int(pick(ns.evm_chain_id, os.getenv("EVM_CHAIN_ID"), evm.get("chain_id"), default=945))
        ns.access_registry = pick(
            ns.access_registry,
            os.getenv("ACCESS_REGISTRY"),
            os.getenv("CONTRACT_ADDRESS"),
            evm.get("access_registry"),
            default="",
        )
        ns.evm_start_block = int(
            pick(ns.evm_start_block, os.getenv("EVM_START_BLOCK"), evm.get("start_block"), default=0)
        )
        ns.mock_events = pick(ns.mock_events, _env_bool("MOCK_EVENTS"), evm.get("mock_events"), default=True)
        ns.skip_deny_receipts = pick(
            ns.skip_deny_receipts, _env_bool("SKIP_DENY_RECEIPTS"), evm.get("skip_deny_receipts"), default=True
        )
        ns.require_evm_association = pick(
            ns.require_evm_association,
            _env_bool("REQUIRE_EVM_ASSOCIATION"),
            evm.get("require_evm_association"),
            default=False,
        )
        logging_cfg = file_cfg.get("logging") or {}
        ns.logging_debug = bool(ns.logging_debug or logging_cfg.get("debug", False))
        ns.subtensor = SimpleNamespace(network=ns.network, chain_endpoint=ns.network)
        return ns

    @staticmethod
    def _load_yaml_config(path: str) -> Dict[str, Any]:
        cfg_path = Path(os.path.expanduser(path))
        if not cfg_path.exists():
            log.warning("Config file not found: %s (using CLI/env defaults)", cfg_path)
            return {}
        try:
            import yaml
        except ImportError:
            log.warning("PyYAML not installed; ignoring %s", cfg_path)
            return {}
        with cfg_path.open() as fh:
            data = yaml.safe_load(fh) or {}
        return data if isinstance(data, dict) else {}

    def setup_logging(self):
        level = logging.DEBUG if getattr(self.config, "logging_debug", False) else logging.INFO
        # App logger at INFO/DEBUG; bittensor websocket/RPC libraries forced quiet.
        configure_app_logging(("medilink.validator", "medilink"), level=level)
        log.info("Validator v1 starting (bittensor %s).", getattr(bt, "__version__", "?"))
        log.info(
            "Config: netuid=%s network=%s mock_events=%s registry=%s evm_rpc=%s",
            self.config.netuid,
            self.config.network,
            self.config.mock_events,
            self.config.access_registry or "(none)",
            self.config.evm_rpc,
        )
    def setup_bittensor_objects(self):
        self.wallet = Wallet(
            name=self.config.wallet,
            hotkey=self.config.wallet_hotkey,
            path=os.path.expanduser(self.config.wallet_path),
        )
        network = self.config.network
        self.subtensor = bt.Subtensor(network)
        self.dendrite = _SynapseFanout(self.wallet)
        self.metagraph = _MetagraphView(self.subtensor, self.config.netuid)
        self.metagraph.sync()
        # Bittensor init often re-enables DEBUG on transport loggers.
        silence_noisy_loggers(logging.WARNING)

    def _setup_evm_client(self) -> None:
        if self.config.mock_events:
            log.info("EVM poller: mock-events enabled (no eth_getLogs).")
            return
        if not self.config.access_registry:
            log.warning("EVM poller: --access-registry unset; live poll returns no events.")
            return
        if Web3 is None:
            log.error("web3 is not installed; pip install web3")
            return
        self._w3 = Web3(Web3.HTTPProvider(self.config.evm_rpc))
        if self.config.evm_chain_id in (945, 964) and ExtraDataToPOAMiddleware is not None:
            self._w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        if not self._w3.is_connected():
            log.error("Cannot connect to EVM RPC %s", self.config.evm_rpc)
            self._w3 = None
            return
        self._registry = self._w3.eth.contract(
            address=Web3.to_checksum_address(self.config.access_registry),
            abi=ACCESS_REGISTRY_ABI,
        )
        log.info(
            "EVM poller ready: registry=%s rpc=%s chain_id=%s cursor=%s",
            self.config.access_registry,
            self.config.evm_rpc,
            self.config.evm_chain_id,
            self.last_evm_block,
        )

    def _sign(self, data: bytes) -> str:
        return self.keypair.sign(data).hex()

    @staticmethod
    def _msg(payload: Dict[str, Any]) -> MessageSynapse:
        return MessageSynapse(payload=payload)

    @staticmethod
    def _payload(synapse: Optional[MessageSynapse]) -> Optional[Dict[str, Any]]:
        if synapse is None:
            return None
        return dict(getattr(synapse, "payload", {}) or {})

    @staticmethod
    def _cid_from_data_hash(data_hash: bytes | str) -> str:
        if isinstance(data_hash, str):
            raw = data_hash[2:] if data_hash.startswith("0x") else data_hash
            digest = raw.lower()
        else:
            digest = data_hash.hex()
        return f"bafy{digest[:52]}"

    def resolve_evm_actor(self, h160: str) -> Optional[str]:
        """
        Resolve H160 → SS58 via SubtensorModule.EVMAssociation when available.
        Falls back to checksum H160 unless --require-evm-association is set.
        """
        address = Web3.to_checksum_address(h160) if Web3 else h160
        try:
            # Best-effort substrate storage query; shape varies by SDK/runtime.
            query = getattr(self.subtensor, "query_module", None) or getattr(self.subtensor, "query", None)
            if callable(query):
                result = None
                try:
                    result = query("SubtensorModule", "AssociatedEvmAddress", [address])
                except Exception:
                    try:
                        result = query("SubtensorModule", "EVMAssociation", [address])
                    except Exception:
                        result = None
                if result is not None:
                    value = getattr(result, "value", result)
                    if value:
                        return str(value)
        except Exception as exc:
            log.debug("EVMAssociation lookup failed for %s: %s", address, exc)

        if self.config.require_evm_association:
            log.warning("No EVMAssociation for %s; discarding event", address)
            return None
        return address

    def poll_evm_commitments(self) -> List[CommitmentEvent]:
        if self.config.mock_events:
            sample_actor = self.metagraph.hotkeys[self.my_uid]
            return [
                CommitmentEvent(
                    actor_evm_addr=sample_actor,
                    data_cid=compute_cid({"seed": str(time.time_ns())}),
                    hippius_status="SUCCESS",
                    op_type_hint=None,
                )
            ]
        return self._poll_access_registry_logs()

    def _fetch_registry_event_logs(self, from_block: int, to_block: int):
        """Fetch and decode registry logs; compatible with web3 v6/v7 get_logs APIs."""
        assert self._w3 is not None and self._registry is not None
        address = Web3.to_checksum_address(self.config.access_registry)
        specs = [
            ("ResourceRegistered", "ResourceRegistered(bytes32,address,bytes32,string)"),
            ("AccessGranted", "AccessGranted(bytes32,address,bytes32,uint256)"),
        ]
        if not self.config.skip_deny_receipts:
            specs.append(("AccessDenied", "AccessDenied(bytes32,address,uint256)"))

        registered, granted, denied = [], [], []
        for name, signature in specs:
            topic = self._w3.keccak(text=signature)
            raw_logs = self._w3.eth.get_logs(
                {
                    "fromBlock": from_block,
                    "toBlock": to_block,
                    "address": address,
                    "topics": [topic],
                }
            )
            for raw in raw_logs:
                try:
                    if name == "ResourceRegistered":
                        registered.append(self._registry.events.ResourceRegistered().process_log(raw))
                    elif name == "AccessGranted":
                        granted.append(self._registry.events.AccessGranted().process_log(raw))
                    else:
                        denied.append(self._registry.events.AccessDenied().process_log(raw))
                except Exception as exc:
                    log.warning("Failed to decode registry log (%s): %s", name, exc)
        return registered, granted, denied

    def _poll_access_registry_logs(self) -> List[CommitmentEvent]:
        if self._w3 is None or self._registry is None:
            return []

        latest = int(self._w3.eth.block_number)
        from_block = self.last_evm_block + 1
        if from_block > latest:
            return []
        # Stay behind tip by REORG_DEPTH to reduce reorg churn.
        tip = max(from_block, latest - REORG_DEPTH) if latest >= REORG_DEPTH else latest
        # Chunk so public RPCs accept the query shape/size.
        to_block = min(tip, from_block + EVM_LOG_CHUNK - 1)

        events: List[CommitmentEvent] = []
        last_error: Optional[Exception] = None
        registered = granted = denied = []
        for attempt in range(EVM_POLL_RETRIES):
            try:
                registered, granted, denied = self._fetch_registry_event_logs(from_block, to_block)
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                time.sleep(0.5 * (2**attempt))
        else:
            log.warning(
                "eth_getLogs failed after retries (blocks %s-%s): %s",
                from_block,
                to_block,
                last_error,
            )
            return []

        max_block = self.last_evm_block
        for log in registered:
            max_block = max(max_block, int(log["blockNumber"]))
            args = log["args"]
            actor = self.resolve_evm_actor(args["owner"])
            if not actor:
                continue
            data_cid = args.get("dataCid") or self._cid_from_data_hash(args["dataHash"])
            events.append(
                CommitmentEvent(
                    actor_evm_addr=actor,
                    data_cid=data_cid,
                    hippius_status="SUCCESS",
                    op_type_hint=None,
                    resource_id=Web3.to_hex(args["resourceId"]),
                )
            )

        for log in granted:
            max_block = max(max_block, int(log["blockNumber"]))
            args = log["args"]
            actor = self.resolve_evm_actor(args["requester"])
            if not actor:
                continue
            events.append(
                CommitmentEvent(
                    actor_evm_addr=actor,
                    data_cid=self._cid_from_data_hash(args["dataHash"]),
                    hippius_status="SUCCESS",
                    op_type_hint="AUDIT",
                    resource_id=Web3.to_hex(args["resourceId"]),
                )
            )

        for log in denied:
            max_block = max(max_block, int(log["blockNumber"]))
            args = log["args"]
            actor = self.resolve_evm_actor(args["requester"])
            if not actor:
                continue
            events.append(
                CommitmentEvent(
                    actor_evm_addr=actor,
                    data_cid=self._cid_from_data_hash(args["resourceId"]),
                    hippius_status="FAIL",
                    op_type_hint="AUDIT",
                    resource_id=Web3.to_hex(args["resourceId"]),
                )
            )

        if max_block > self.last_evm_block:
            self.last_evm_block = max_block
        elif to_block > self.last_evm_block:
            self.last_evm_block = to_block
        return events

    def derive_op_type(self, actor_evm_addr: str, data_cid: str, hint: Optional[str] = None) -> str:
        if hint in ("AUDIT", "WRITE", "UPDATE", "DELETE_REQ"):
            return hint
        actor_state = self.actor_tips.get(actor_evm_addr)
        if actor_state is None:
            return "WRITE"
        if actor_state.get("tip_data_cid") == data_cid:
            return "AUDIT"
        return "UPDATE"

    def build_receipt(self, event: CommitmentEvent) -> Tuple[Dict[str, Any], str]:
        actor_state = self.actor_tips.get(event.actor_evm_addr, {})
        receipt = {
            "spec_version": RECEIPT_SPEC_VERSION,
            "tx_id": str(uuid.uuid4()),
            "timestamp_ns": time.time_ns(),
            "actor_evm_addr": event.actor_evm_addr,
            "op_type": self.derive_op_type(event.actor_evm_addr, event.data_cid, event.op_type_hint),
            "data_cid": event.data_cid,
            "prev_cid": actor_state.get("tip_cid"),
            "hippius_status": event.hippius_status,
            "validator_hotkey": self.wallet.hotkey.ss58_address,
        }
        if event.resource_id:
            receipt["resource_id"] = event.resource_id
        signed_payload = {k: receipt[k] for k in VALIDATOR_SIGN_FIELDS}
        receipt["validator_sig"] = self._sign(canonical_bytes(signed_payload))
        print(receipt)
        receipt_cid = compute_cid(receipt)
        self.receipt_cache[receipt_cid] = receipt
        self.actor_tips[event.actor_evm_addr] = {
            "tip_cid": receipt_cid,
            "tip_data_cid": event.data_cid,
            "receipt_count": actor_state.get("receipt_count", 0) + 1,
        }
        self.batch_pending.append(receipt_cid)
        return receipt, receipt_cid

    def broadcast_receipt(self, receipt: Dict[str, Any]) -> None:
        self.dendrite.query(axons=self.metagraph.axons, synapse=self._msg(receipt), timeout=CHALLENGE_TIMEOUT_S)

    def close_batch(self) -> Optional[Tuple[Dict[str, Any], str]]:
        if not self.batch_pending:
            return None
        cids = sorted(self.batch_pending)
        tree = MerkleTree(cids)
        envelope = {
            "spec_version": BATCH_SPEC_VERSION,
            "batch_index": self.batch_index,
            "window_start_ns": self.window_open_ns,
            "window_end_ns": time.time_ns(),
            "receipt_count": len(cids),
            "receipt_cids": cids,
            "merkle_root": tree.root_hex,
            "prev_batch_cid": self.batch_tips["global"]["tip_batch_cid"],
            "validator_hotkey": self.wallet.hotkey.ss58_address,
        }
        signed_payload = {k: envelope[k] for k in BATCH_SIGN_FIELDS}
        envelope["validator_sig"] = self._sign(canonical_bytes(signed_payload))
        batch_cid = compute_cid(envelope)
        self.merkle_tree_cache[batch_cid] = tree
        self.batch_receipt_cids[batch_cid] = list(cids)
        self.batch_tips["global"]["tip_batch_cid"] = batch_cid
        self.batch_tips["global"]["batch_count"] += 1
        self.batch_tips["global"]["receipt_cids"] = list(cids)
        self.batch_index += 1
        self.batch_pending = []
        self.window_open_ns = time.time_ns()
        self.dendrite.query(axons=self.metagraph.axons, synapse=self._msg(envelope), timeout=CHALLENGE_TIMEOUT_S)
        log.info(
            "closed batch_cid=%s leaves=%s (challenges will target a leaf from this set)",
            batch_cid,
            len(cids),
        )
        return envelope, batch_cid

    def select_challenge_target(self, uid: int) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """Pick (actor, receipt_cid, batch_cid) where receipt_cid is in the last closed batch.

        Never challenge the live tip against tip_batch unless that tip is actually a leaf
        of tip_batch — otherwise merkle proofs return null for receipts still in batch_pending.
        """
        batch_cid = self.batch_tips["global"].get("tip_batch_cid")
        if not batch_cid:
            return None, None, None
        cids = self.batch_tips["global"].get("receipt_cids") or self.batch_receipt_cids.get(batch_cid) or []
        if not cids:
            return None, None, None

        receipt_cid = cids[uid % len(cids)]
        cached = self.receipt_cache.get(receipt_cid) or {}
        actor = cached.get("actor_evm_addr")
        if not actor:
            actors = sorted(self.actor_tips.keys())
            if not actors:
                return None, None, None
            actor = actors[uid % len(actors)]
        return actor, receipt_cid, batch_cid

    def _latency_score(self, available: bool, elapsed_ms: float) -> float:
        if not available:
            return 0.0
        if elapsed_ms <= LATENCY_TARGET_MS:
            return 1.0
        if elapsed_ms >= LATENCY_CUTOFF_MS:
            return 0.0
        x = (elapsed_ms - LATENCY_TARGET_MS) / (LATENCY_CUTOFF_MS - LATENCY_TARGET_MS)
        return 1.0 / (1.0 + math.exp(LATENCY_STEEPNESS * x * 10 - 5))

    def _integrity_breakdown(
        self,
        pin_resp: Optional[MessageSynapse],
        proof_resp: Optional[MessageSynapse],
        expected_receipt_cid: str,
        batch_cid: str,
    ) -> Dict[str, Any]:
        """Detailed integrity checks — mistakes here must not look like 'mostly fine'."""
        pin_payload = self._payload(pin_resp) or {}
        proof_payload = self._payload(proof_resp) or {}
        sig_ok = False
        cid_ok = False
        merkle_ok = False
        merkle_reason = "not_checked"

        if pin_payload.get("receipt_json"):
            try:
                returned = json.loads(pin_payload["receipt_json"])
                sig_ok = bool(returned.get("validator_sig"))
                cid_ok = compute_cid(returned) == expected_receipt_cid
            except Exception as exc:
                merkle_reason = f"pin_json_error:{exc}"
        else:
            merkle_reason = "no_pin_payload"

        if "proof_path" not in proof_payload:
            merkle_reason = "proof_path_missing_key"
        elif proof_payload["proof_path"] is None:
            merkle_reason = "proof_path_null"
        elif batch_cid not in self.merkle_tree_cache:
            merkle_reason = "validator_batch_cache_miss"
        else:
            try:
                computed = MerkleTree.verify(expected_receipt_cid, proof_payload["proof_path"])
                expected_root = self.merkle_tree_cache[batch_cid].root_hex
                merkle_ok = computed == expected_root
                merkle_reason = "ok" if merkle_ok else f"root_mismatch got={computed[:16]}…"
            except Exception as exc:
                merkle_reason = f"verify_error:{exc}"

        # Sub-scores (sum to 1.0 when all pass). Merkle is required for full credit.
        score = 0.0
        if sig_ok:
            score += 0.40
        if cid_ok:
            score += 0.30
        if merkle_ok:
            score += 0.30
        # Fail-closed: without a valid merkle proof, cap integrity so wrong proofs cannot look strong.
        if not merkle_ok:
            score = min(score, 0.40)

        return {
            "score": score,
            "sig_ok": sig_ok,
            "cid_ok": cid_ok,
            "merkle_ok": merkle_ok,
            "merkle_reason": merkle_reason,
        }

    def _integrity_score(
        self,
        pin_resp: Optional[MessageSynapse],
        proof_resp: Optional[MessageSynapse],
        expected_receipt_cid: str,
        batch_cid: str,
    ) -> float:
        return float(
            self._integrity_breakdown(pin_resp, proof_resp, expected_receipt_cid, batch_cid)["score"]
        )

    def _consistency_breakdown(
        self, index_resp: Optional[MessageSynapse], actor_evm_addr: str
    ) -> Dict[str, Any]:
        """
        Tip agreement is the source of truth for ledger head.

        Absolute receipt_count drift is NOT used as a hard penalty: validator counts are
        in-memory (reset on restart) while miner counts are durable SQLite — comparing
        them produced false negatives (tip OK, consistency stuck at 0.60).
        """
        index_payload = self._payload(index_resp) or {}
        if not index_payload or actor_evm_addr not in self.actor_tips:
            return {
                "score": 0.0,
                "tip_ok": False,
                "batch_ok": False,
                "count_drift": None,
                "miner_count": None,
                "validator_count": None,
            }

        known_tip = self.actor_tips[actor_evm_addr]["tip_cid"]
        known_count = int(self.actor_tips[actor_evm_addr]["receipt_count"])
        miner_tip = index_payload.get("tip_cid")
        miner_count = int(index_payload.get("receipt_count", 0) or 0)
        miner_batch = index_payload.get("tip_batch_cid")
        known_batch = self.batch_tips["global"].get("tip_batch_cid")

        tip_ok = miner_tip == known_tip
        # If validator has not closed a batch yet, do not fail batch agreement.
        if not known_batch:
            batch_ok = True
        else:
            batch_ok = miner_batch == known_batch

        tip_score = 1.0 if tip_ok else 0.0
        batch_score = 1.0 if batch_ok else 0.0
        score = (tip_score * 0.7) + (batch_score * 0.3)
        return {
            "score": score,
            "tip_ok": tip_ok,
            "batch_ok": batch_ok,
            "count_drift": abs(miner_count - known_count),
            "miner_count": miner_count,
            "validator_count": known_count,
        }

    def _consistency_score(self, index_resp: Optional[MessageSynapse], actor_evm_addr: str) -> float:
        return float(self._consistency_breakdown(index_resp, actor_evm_addr)["score"])

    def score_miner_breakdown(
        self,
        actor_evm_addr: str,
        expected_receipt_cid: str,
        batch_cid: str,
        pin_resp: Optional[MessageSynapse],
        proof_resp: Optional[MessageSynapse],
        index_resp: Optional[MessageSynapse],
        elapsed_ms: float,
    ) -> Dict[str, Any]:
        """Return per-dimension scores and weighted composite in [0, 1]."""
        pin_payload = self._payload(pin_resp) or {}
        availability = 1.0 if pin_payload.get("receipt_json") else 0.0
        latency = self._latency_score(bool(availability), elapsed_ms)
        integ = self._integrity_breakdown(pin_resp, proof_resp, expected_receipt_cid, batch_cid)
        cons = self._consistency_breakdown(index_resp, actor_evm_addr)
        integrity = float(integ["score"])
        consistency = float(cons["score"])
        composite = (
            (SCORE_W_AVAILABILITY * availability)
            + (SCORE_W_LATENCY * latency)
            + (SCORE_W_INTEGRITY * integrity)
            + (SCORE_W_CONSISTENCY * consistency)
        )
        composite = max(0.0, min(1.0, composite))
        return {
            "availability": availability,
            "latency": latency,
            "integrity": integrity,
            "consistency": consistency,
            "composite": composite,
            "elapsed_ms": float(elapsed_ms),
            "w_availability": SCORE_W_AVAILABILITY * availability,
            "w_latency": SCORE_W_LATENCY * latency,
            "w_integrity": SCORE_W_INTEGRITY * integrity,
            "w_consistency": SCORE_W_CONSISTENCY * consistency,
            "integrity_detail": integ,
            "consistency_detail": cons,
        }

    def score_miner(
        self,
        actor_evm_addr: str,
        expected_receipt_cid: str,
        batch_cid: str,
        pin_resp: Optional[MessageSynapse],
        proof_resp: Optional[MessageSynapse],
        index_resp: Optional[MessageSynapse],
        elapsed_ms: float,
    ) -> float:
        return self.score_miner_breakdown(
            actor_evm_addr,
            expected_receipt_cid,
            batch_cid,
            pin_resp,
            proof_resp,
            index_resp,
            elapsed_ms,
        )["composite"]

    def run_challenge_round(self) -> Dict[int, float]:
        from concurrent.futures import ThreadPoolExecutor

        self.metagraph.sync()
        scores: Dict[int, float] = {}
        for uid, axon in enumerate(self.metagraph.axons):
            if uid == self.my_uid or getattr(axon, "port", 0) <= 0:
                continue
            actor, receipt_cid, batch_cid = self.select_challenge_target(uid)
            if not actor or not receipt_cid or not batch_cid:
                log.debug(
                    "challenge skip uid=%s axon=%s:%s (no receipt/batch target yet)",
                    uid,
                    getattr(axon, "ip", None),
                    getattr(axon, "port", None),
                )
                continue
            t0 = time.perf_counter()
            pin_req = {"receipt_cid": receipt_cid, "nonce": secrets.token_hex(16), "issued_at_ns": time.time_ns()}
            proof_req = {
                "batch_cid": batch_cid,
                "target_receipt_cid": receipt_cid,
                "nonce": secrets.token_hex(16),
            }
            idx_req = {"actor_evm_addr": actor}
            # Issue pin / merkle / index in parallel against the same axon.
            with ThreadPoolExecutor(max_workers=3) as pool:
                f_pin = pool.submit(
                    self.dendrite.query, axons=[axon], synapse=self._msg(pin_req), timeout=CHALLENGE_TIMEOUT_S
                )
                f_proof = pool.submit(
                    self.dendrite.query, axons=[axon], synapse=self._msg(proof_req), timeout=CHALLENGE_TIMEOUT_S
                )
                f_idx = pool.submit(
                    self.dendrite.query, axons=[axon], synapse=self._msg(idx_req), timeout=CHALLENGE_TIMEOUT_S
                )
                pin_resp = f_pin.result()[0]
                proof_resp = f_proof.result()[0]
                index_resp = f_idx.result()[0]
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            breakdown = self.score_miner_breakdown(
                actor, receipt_cid, batch_cid, pin_resp, proof_resp, index_resp, elapsed_ms
            )
            raw = breakdown["composite"]
            scores[uid] = raw
            prev = self.score_ema.get(uid, raw)
            new_ema = (EMA_ALPHA * raw) + ((1.0 - EMA_ALPHA) * prev)
            self.score_ema[uid] = new_ema
            integ = breakdown["integrity_detail"]
            cons = breakdown["consistency_detail"]
            log.info(
                "challenge uid=%s axon=%s:%s elapsed_ms=%.0f | "
                "avail=%.2f (w=%.3f) latency=%.2f (w=%.3f) integrity=%.2f (w=%.3f) "
                "consistency=%.2f (w=%.3f) | raw=%.4f ema %.4f→%.4f (α=%.2f)",
                uid,
                getattr(axon, "ip", None),
                getattr(axon, "port", None),
                breakdown["elapsed_ms"],
                breakdown["availability"],
                breakdown["w_availability"],
                breakdown["latency"],
                breakdown["w_latency"],
                breakdown["integrity"],
                breakdown["w_integrity"],
                breakdown["consistency"],
                breakdown["w_consistency"],
                raw,
                prev,
                new_ema,
                EMA_ALPHA,
            )
            log.info(
                "challenge uid=%s detail integrity[sig=%s cid=%s merkle=%s reason=%s] "
                "consistency[tip=%s batch=%s drift=%s miner_count=%s val_count=%s]",
                uid,
                integ.get("sig_ok"),
                integ.get("cid_ok"),
                integ.get("merkle_ok"),
                integ.get("merkle_reason"),
                cons.get("tip_ok"),
                cons.get("batch_ok"),
                cons.get("count_drift"),
                cons.get("miner_count"),
                cons.get("validator_count"),
            )

        if scores:
            log.info(
                "challenge round EMA snapshot=%s",
                {u: round(self.score_ema[u], 4) for u in scores},
            )
        return scores

    def _chain_last_weight_block(self) -> Optional[int]:
        """On-chain LastUpdate for this validator uid (commit-reveal bumps at commit time)."""
        try:
            from bittensor._generated import storage as st

            last_raw = self.subtensor.query(st.SubtensorModule.LastUpdate, [self.config.netuid])
            if isinstance(last_raw, (list, tuple)) and 0 <= self.my_uid < len(last_raw):
                return int(last_raw[self.my_uid] or 0)
        except Exception as exc:
            log.debug("LastUpdate query failed: %s", exc)
        return None

    def _sync_weight_rate_limit(self, current_block: int) -> int:
        """Align local last_weight_block with chain; return blocks until next allowed submit."""
        chain_last = self._chain_last_weight_block()
        if chain_last is not None and chain_last > self.last_weight_block:
            self.last_weight_block = chain_last
        blocks_since = current_block - self.last_weight_block
        if self.last_weight_block <= 0:
            return 0
        remaining = WEIGHTS_RATE_LIMIT_BLOCKS - blocks_since
        return max(0, remaining)

    @staticmethod
    def _parse_rate_limit_remaining(exc: BaseException) -> Optional[int]:
        import re

        text = str(exc)
        m = re.search(r"Retry in ~(\d+) blocks", text, re.I)
        if m:
            return int(m.group(1))
        m = re.search(r"last set (\d+) blocks ago", text, re.I)
        if m:
            since = int(m.group(1))
            return max(0, WEIGHTS_RATE_LIMIT_BLOCKS - since)
        return None

    def maybe_set_weights(self):
        try:
            current_block = int(self.subtensor.block)
        except Exception as exc:
            try:
                current_block = int(getattr(self.subtensor, "get_current_block", lambda: 0)())
            except Exception as exc2:
                log.warning("maybe_set_weights: cannot read chain block (%s / %s)", exc, exc2)
                return

        # Challenges may run every ~12s; weights are gated by the on-chain 100-block limit.
        remaining = self._sync_weight_rate_limit(current_block)
        if remaining > 0:
            log.debug(
                "maybe_set_weights: waiting %s more blocks (block=%s last=%s limit=%s)",
                remaining,
                current_block,
                self.last_weight_block,
                WEIGHTS_RATE_LIMIT_BLOCKS,
            )
            return

        n = len(self.metagraph.hotkeys)
        weights = [0.0] * n
        scored_uids = []
        for uid, score in self.score_ema.items():
            if 0 <= uid < n and uid != self.my_uid:
                weights[uid] = float(score)
                scored_uids.append((uid, float(score)))

        if not scored_uids:
            log.info(
                "maybe_set_weights: skip — no EMA scores for other uids "
                "(my_uid=%s score_ema=%s). Challenges must succeed first.",
                self.my_uid,
                dict(self.score_ema),
            )
            return

        total = sum(weights)
        if total <= 0:
            log.info(
                "maybe_set_weights: skip — weight total is %s (scored=%s). Need positive EMA.",
                total,
                scored_uids,
            )
            return

        weights = [w / total for w in weights]
        nonzero = [(uid, w) for uid, w in enumerate(weights) if w > 0]
        log.info(
            "maybe_set_weights: submitting at block=%s netuid=%s my_uid=%s nonzero=%s",
            current_block,
            self.config.netuid,
            self.my_uid,
            nonzero,
        )
        try:
            result = self.subtensor.execute(
                bt.SetWeights(netuid=self.config.netuid, uids=list(range(n)), weights=weights),
                self.wallet,
            )
            ok = bool(getattr(result, "success", False))
            msg = getattr(result, "message", None) or getattr(result, "error", None) or result
            if ok:
                self.last_weight_block = current_block
                log.info("maybe_set_weights: SUCCESS at block %s — %s", current_block, msg)
            else:
                log.warning(
                    "maybe_set_weights: FAILED at block %s (success=%s) — %s",
                    current_block,
                    getattr(result, "success", None),
                    msg,
                )
        except Exception as exc:
            retry = self._parse_rate_limit_remaining(exc)
            if retry is not None:
                # Remember on-chain cadence so we do not spam SetWeights every challenge loop.
                self.last_weight_block = current_block - (WEIGHTS_RATE_LIMIT_BLOCKS - retry)
                log.info(
                    "maybe_set_weights: on-chain rate limit — retry in ~%s blocks (~%ss). last_weight_block=%s",
                    retry,
                    retry * 12,
                    self.last_weight_block,
                )
                return
            log.warning("maybe_set_weights: exception during SetWeights: %s", exc, exc_info=True)

    def run(self):
        while True:
            print("Running validator")
            events = self.poll_evm_commitments()
            print(events)
            for event in events:
                receipt, _ = self.build_receipt(event)
                self.broadcast_receipt(receipt)
            if (time.time_ns() - self.window_open_ns) >= BATCH_WINDOW_NS or len(self.batch_pending) >= MAX_BATCH_SIZE:
                self.close_batch()
            self.run_challenge_round()
            self.maybe_set_weights()
            time.sleep(12)


if __name__ == "__main__":
    ValidatorV1().run()
