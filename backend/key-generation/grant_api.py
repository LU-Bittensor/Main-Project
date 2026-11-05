"""Grant + vault + faucet API. See LOCAL_TESTING.md for setup and limits."""
import argparse
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import time
from threading import RLock
import uuid

import requests
from eth_account import Account
from eth_utils import is_address, to_checksum_address
from flask import Flask, Response, g, jsonify, request
from documents import SupabaseDocumentStore, DemoDocumentStore, DocumentStorageError, MAX_DOCUMENT_BYTES, document_type
from werkzeug.exceptions import HTTPException
from epic_oauth import EpicOAuth, EpicOAuthError
from web3 import Web3
from epic_import import EpicImports, EpicImportError
from epic_share import ImportDocumentStore

from DHkey_share import (
    HIPPIUS_BUCKET,
    create_access_grant,
    decrypt_secret,
    derive_shared_secret,
    encrypt_secret,
    generate_key_pair,
    generate_presigned_put_url,
    generate_presigned_url,
    hippius_configured,
    hippius_object_exists,
)

try:
    import faucet_client
except ImportError:  # pragma: no cover
    faucet_client = None  # type: ignore

VAULT_OBJECT_NAME = "keystore.v1.json"
VAULT_PRESIGN_TTL_S = 300
VAULT_MAX_BYTES = 64 * 1024

DEMO_USERS = {"patient": "Demo patient", "clinician": "Demo clinician", "outsider": "Unrelated user"}
DEMO_RECORDS = [
    {"id": "demo-lab", "name": "Demo lipid panel", "kind": "Lab report"},
    {"id": "demo-imaging", "name": "Demo chest X-ray", "kind": "Imaging"},
]


class APIError(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status


def vault_object_key(user_id: str) -> str:
    return f"vaults/{user_id}/{VAULT_OBJECT_NAME}"


def vault_record_resource_id(record_id: str) -> bytes:
    """Match health-locker-guard resourceIdFromRecord: keccak256(utf8 medilink:record:<id>)."""
    return bytes(Web3.keccak(text=f"medilink:record:{record_id}"))


def vault_record_resource_id_hex(record_id: str) -> str:
    return vault_record_resource_id(record_id).hex()


def _is_auto_data_fingerprint(name: str) -> bool:
    return len(name) == 64 and all(character in "0123456789abcdef" for character in name)


def resolve_local_vault_dir(data_directory: Path) -> Path:
    """Stable on-disk vault root (auth-bound), not Hardhat instance shards.

    `--auto-data-dir` nests SQLite under GRANT_DATA_DIR/<fingerprint> so chain
    resets do not reuse stale grants. Encrypted vault keystores must survive those
    resets, so they live at GRANT_DATA_DIR/vault_objects (or GRANT_VAULT_DIR).
    """
    override = os.getenv("GRANT_VAULT_DIR", "").strip()
    if override:
        return Path(override)
    base = os.getenv("GRANT_DATA_DIR", "").strip()
    data_path = data_directory.resolve()
    if base:
        base_path = Path(base).resolve()
        if data_path == base_path:
            return base_path / "vault_objects"
        # --auto-data-dir child: GRANT_DATA_DIR/<sha256-fingerprint>
        if data_path.parent == base_path and _is_auto_data_fingerprint(data_directory.name):
            return base_path / "vault_objects"
    if _is_auto_data_fingerprint(data_directory.name):
        return data_directory.parent / "vault_objects"
    return data_directory / "vault_objects"


def recover_vault_from_legacy_shards(vault_dir: Path, user_id: str) -> Path | None:
    """If a keystore only exists under old per-Hardhat shard dirs, promote the newest copy."""
    dest = vault_dir / user_id / VAULT_OBJECT_NAME
    if dest.is_file():
        return dest
    root = vault_dir.parent
    if not root.is_dir():
        return None
    candidates = sorted(
        (
            path
            for path in root.glob(f"*/vault_objects/{user_id}/{VAULT_OBJECT_NAME}")
            if path.is_file()
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return None
    dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    shutil.copy2(candidates[0], dest)
    dest.chmod(0o600)
    return dest


def create_app(*, dev=False, data_dir=None, master_key=None, chain=None):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = VAULT_MAX_BYTES
    directory = Path(data_dir or os.getenv("GRANT_DATA_DIR", Path(__file__).parent / (".grant-pairs-dev" if dev else ".grant-pairs-data")))
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    vault_dir = resolve_local_vault_dir(directory)
    vault_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Short-lived tokens for local (non-Hippius) vault PUT/GET when credentials are unset.
    vault_local_tokens: dict[str, dict] = {}
    directory.chmod(0o700)
    if master_key is None:
        configured = os.getenv("GRANT_MASTER_KEY")
        if configured:
            master_key = bytes.fromhex(configured)
        elif dev:
            key_file = directory / "master.key"
            if not key_file.exists():
                with key_file.open("xb") as file:
                    file.write(os.urandom(32))
                key_file.chmod(0o600)
            master_key = key_file.read_bytes()
        else:
            raise RuntimeError("Set GRANT_MASTER_KEY to 32 random bytes encoded as hex")
    if len(master_key) != 32:
        raise RuntimeError("GRANT_MASTER_KEY must contain exactly 32 bytes")
    supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    supabase_key = os.getenv("SUPABASE_PUBLISHABLE_KEY", "")
    if not dev and (not supabase_url or not supabase_key):
        raise RuntimeError("Set SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY")
    # Local Vite/Lovable setups use either 5173 or 8080. Keep an explicit
    # allowlist; deployed frontends can override it with FRONTEND_ORIGINS.
    allowed_origins = {origin.strip() for origin in os.getenv(
        "FRONTEND_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8080,http://127.0.0.1:8080",
    ).split(",") if origin.strip()}
    db_path = directory / "registry-grants.sqlite3"
    lock = RLock()

    def db():
        if "db" not in g:
            g.db = sqlite3.connect(db_path, timeout=30)
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA foreign_keys=ON")
        return g.db

    @app.teardown_appcontext
    def close_db(_error):
        connection = g.pop("db", None)
        if connection:
            connection.close()

    with app.app_context():
        db().executescript("""
            CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, private_key BLOB NOT NULL,
                public_key BLOB NOT NULL, wallet TEXT NOT NULL, wallet_key BLOB NOT NULL);
            CREATE TABLE IF NOT EXISTS grants (
                id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES users(id),
                recipient_id TEXT NOT NULL REFERENCES users(id), scope TEXT NOT NULL,
                records TEXT NOT NULL, record_name TEXT NOT NULL, identifier TEXT NOT NULL,
                contract_address TEXT, deploy_tx TEXT, resource_tx TEXT, register_tx TEXT, revoke_tx TEXT,
                registry_revoked INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL, secret BLOB,
                tx_hash TEXT, error TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS registry_resources (
                identifier TEXT PRIMARY KEY, tx_hash TEXT);
            CREATE UNIQUE INDEX IF NOT EXISTS live_grant ON grants(owner_id, recipient_id, records)
                WHERE status IN ('pending', 'active', 'revoking');
        """)
        # Non-destructive schema upgrade for existing databases.
        columns = {row["name"] for row in db().execute("PRAGMA table_info(grants)")}
        if "revoke_tx" not in columns:
            db().execute("ALTER TABLE grants ADD COLUMN revoke_tx TEXT")
        if "registry_revoked" not in columns:
            db().execute("ALTER TABLE grants ADD COLUMN registry_revoked INTEGER NOT NULL DEFAULT 0")
        user_columns = {row["name"] for row in db().execute("PRAGMA table_info(users)")}
        if "x25519_public_key_b64" not in user_columns:
            db().execute("ALTER TABLE users ADD COLUMN x25519_public_key_b64 TEXT")
        # Do not silently reuse off-chain permissions against a different chain or master key.
        environment = json.dumps({"protocol": "dh-registry-v1", "dev": dev,
                                  "auth_project": None if dev else supabase_url,
                                  "chain": None if chain is None else chain.identity})
        previous = db().execute("SELECT value FROM settings WHERE name='environment'").fetchone()
        if previous and previous[0] != environment:
            raise RuntimeError(
                f"GRANT_DATA_DIR {directory.resolve()} belongs to a different auth/chain configuration. "
                "For local Hardhat testing, run ./load_API_bc.sh, or use --dev --chain --auto-data-dir. "
                "Existing data has not been replaced."
            )
        check = hmac.digest(master_key, b"medilink-master-key-check", "sha256").hex()
        previous_key = db().execute("SELECT value FROM settings WHERE name='key_check'").fetchone()
        if previous_key and not hmac.compare_digest(previous_key[0], check):
            raise RuntimeError("GRANT_MASTER_KEY does not match this database")
        db().execute("INSERT OR IGNORE INTO settings VALUES ('environment', ?)", (environment,))
        db().execute("INSERT OR IGNORE INTO settings VALUES ('key_check', ?)", (check,))
        db().commit()
    db_path.chmod(0o600)
    epic_oauth = EpicOAuth(db_path, master_key, os.getenv("EPIC_CLIENT_ID", "").strip(),
                           os.getenv("EPIC_REDIRECT_URI", "").strip())
    epic_imports = EpicImports(db_path, master_key, epic_oauth)
    app.extensions['epic_imports'] = epic_imports

    document_store_class = SupabaseDocumentStore
    storage_backend = os.getenv("DOCUMENT_STORAGE_BACKEND", "supabase")
    if not dev and storage_backend == "hippius":
        from hippius_storage import HippiusDocumentStore
        document_store_class = HippiusDocumentStore
    elif not dev and storage_backend != "supabase":
        raise ValueError("Unknown DOCUMENT_STORAGE_BACKEND")
    documents = DemoDocumentStore() if dev else document_store_class(
        supabase_url, os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""))
    uploads_configured = dev or bool(os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY"))
    if not dev:
        documents = ImportDocumentStore(documents, epic_imports, uploads_configured)


    def enroll(uid, name, *, wallet=None, x25519_public_key_b64=None):
        """Ensure a user row exists. Optional wallet = vault public EVM address (non-custodial)."""
        with lock:
            row = db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
            if row is None:
                private, public = generate_key_pair()
                if wallet:
                    wallet_addr = to_checksum_address(wallet)
                    # Empty wallet_key: EVM signing stays in the browser vault.
                    wallet_key = b""
                else:
                    account = Account.create()
                    wallet_addr = account.address
                    wallet_key = encrypt_secret(master_key, bytes(account.key), "wallet:" + uid)
                db().execute(
                    "INSERT INTO users (id, name, private_key, public_key, wallet, wallet_key, x25519_public_key_b64) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (uid, name, encrypt_secret(master_key, private, "user:" + uid), public,
                     wallet_addr, wallet_key, x25519_public_key_b64),
                )
                db().commit()
                return
            if wallet:
                wallet_addr = to_checksum_address(wallet)
                taken = db().execute(
                    "SELECT id FROM users WHERE wallet=? AND id!=?", (wallet_addr, uid)
                ).fetchone()
                if taken:
                    raise APIError("evmAddress already enrolled to another account", 409)
                db().execute(
                    "UPDATE users SET name=?, wallet=?, wallet_key=?, "
                    "x25519_public_key_b64=COALESCE(?, x25519_public_key_b64) WHERE id=?",
                    (name, wallet_addr, b"", x25519_public_key_b64, uid),
                )
                db().commit()

    if dev:
        with app.app_context():
            for uid, name in DEMO_USERS.items():
                enroll(uid, name)

    @app.errorhandler(APIError)
    def api_error(error):
        return jsonify(error=error.message), error.status

    @app.errorhandler(DocumentStorageError)
    def document_error(error):
        return jsonify(error=str(error)), 503

    @app.errorhandler(HTTPException)
    def http_error(error):
        return jsonify(error=error.description), error.code

    @app.errorhandler(Exception)
    def unexpected_error(error):
        # Do not log payloads, bearer tokens, keys, or exception strings containing requests.
        app.logger.error("Grant API failed: %s", type(error).__name__)
        return jsonify(error="Grant service unavailable. Retry or check the API configuration."), 503

    @app.before_request
    def authenticate():
        if request.path == "/api/records" and request.method == "POST":
            request.max_content_length = MAX_DOCUMENT_BYTES + 64 * 1024
        origin = request.headers.get("Origin")
        if origin and origin not in allowed_origins:
            raise APIError("Origin not allowed", 403)
        if dev and (request.remote_addr not in ("127.0.0.1", "::1") or
                    request.host.split(":")[0] not in ("localhost", "127.0.0.1")):
            raise APIError("Local demo only accepts loopback requests", 403)
        if request.method == "OPTIONS":
            return "", 204
        if request.path == "/api/health":
            return None
        if request.path == "/api/faucet/status":
            return None
        # Local vault object uses a short-lived query token (no Bearer).
        if request.path == "/api/vault/local-object":
            return None
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            raise APIError("Sign in before managing access", 401)
        g.token = header[7:]
        if dev:
            uid = g.token.removeprefix("demo-")
            if g.token != "demo-" + uid or uid not in DEMO_USERS:
                raise APIError(
                    "The API is running in local demo mode and cannot accept this account. "
                    "For Supabase accounts, restart the API without --dev and configure it "
                    "for the same Supabase project as the frontend. This is not a missing-records error.",
                    401,
                )
            g.uid, g.name = uid, DEMO_USERS[uid]
        else:
            # Ask Supabase Auth to verify the token; never trust a decoded JWT payload alone.
            result = requests.get(supabase_url + "/auth/v1/user", headers={
                "apikey": supabase_key, "Authorization": header}, timeout=10)
            if result.status_code in (401, 403):
                raise APIError("Session expired or invalid. Sign in again.", 401)
            result.raise_for_status()
            user = result.json()
            g.uid = str(uuid.UUID(user["id"]))
            g.name = str(user.get("user_metadata", {}).get("full_name") or "Registered user")[:120]

    @app.after_request
    def headers(response):
        origin = request.headers.get("Origin")
        if origin in allowed_origins:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
            response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        response.headers["Cache-Control"] = "no-store"
        return response

    def owner_records():
        if dev:
            return (DEMO_RECORDS if g.uid == "patient" else []) + documents.owned(g.uid)
        return documents.owned(g.uid)

    def serialize(row):
        item = {key: row[key] for key in ("id", "owner_id", "recipient_id", "scope", "status", "tx_hash", "error",
                       "record_name", "contract_address", "deploy_tx", "resource_tx", "register_tx", "revoke_tx")}
        item["resource_id"] = "0x" + row["identifier"]
        item["recipient_wallet"] = db().execute("SELECT wallet FROM users WHERE id=?", (row["recipient_id"],)).fetchone()[0]
        item["registry_revoked"] = bool(row["registry_revoked"])
        item["record_ids"] = json.loads(row["records"])
        item["chain_enabled"] = chain is not None
        item["document"] = documents.get(item["record_ids"][0]) if len(item["record_ids"]) == 1 else None
        for field in ("owner", "recipient"):
            item[field + "_name"] = db().execute("SELECT name FROM users WHERE id=?", (row[field + "_id"],)).fetchone()[0]
        return item

    def get_grant(grant_id):
        row = db().execute("SELECT * FROM grants WHERE id=? AND (owner_id=? OR recipient_id=?)",
                           (grant_id, g.uid, g.uid)).fetchone()
        if not row:
            raise APIError("Grant not found", 404)
        return row

    def update(grant_id, **fields):
        # Field names are internal constants, never request input.
        db().execute("UPDATE grants SET " + ",".join(key + "=?" for key in fields) + " WHERE id=?",
                     (*fields.values(), grant_id))
        db().commit()
        return get_grant(grant_id)

    def setting(name, value=None):
        if value is not None:
            db().execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (name, value))
            db().commit()
        row = db().execute("SELECT value FROM settings WHERE name=?", (name,)).fetchone()
        return row[0] if row else None

    def registry():
        """One deployment per store, with its hash persisted before broadcast."""
        address = setting("registry_address") or chain.registry_address
        if address:
            chain.validate_registry(address)
            setting("registry_address", address)
            return address
        tx = setting("registry_deploy_tx")
        if not tx:
            chain.deploy(lambda tx: setting("registry_deploy_tx", tx))
            tx = setting("registry_deploy_tx")
        receipt = chain.receipt(tx)
        if receipt is None:
            raise RuntimeError("Registry deployment pending")
        if receipt["status"] != 1:
            # A mined revert is safe to retry; an unknown receipt is not.
            db().execute("DELETE FROM settings WHERE name='registry_deploy_tx'")
            db().commit()
            raise RuntimeError("Registry deployment reverted; resume to retry")
        address = receipt["contractAddress"]
        chain.validate_registry(address)
        setting("registry_address", address)
        return address

    def pair(row):
        wallet = db().execute("SELECT wallet FROM users WHERE id=?", (row["recipient_id"],)).fetchone()[0]
        return bytes.fromhex(row["identifier"]), wallet

    def secret_for(row):
        return decrypt_secret(master_key, row["secret"], "grant:" + row["id"])

    def is_client_chain_grant(row) -> bool:
        """Vault-signed AccessRegistry grants use keccak(medilink:record:<uuid>) ids + interim commitments."""
        try:
            record_ids = json.loads(row["records"])
        except (TypeError, json.JSONDecodeError):
            return False
        if len(record_ids) != 1 or not isinstance(record_ids[0], str):
            return False
        return row["identifier"] == vault_record_resource_id_hex(record_ids[0])

    def registry_address_for_checks():
        if not chain:
            return None
        address = (
            setting("registry_address")
            or chain.registry_address
            or os.getenv("REGISTRY_ADDRESS")
            or os.getenv("ACCESS_REGISTRY")
            or os.getenv("CONTRACT_ADDRESS")
        )
        if address:
            return address
        # Fall back to the last local Hardhat deploy artifact.
        deploy_path = Path(__file__).parent / "deployment.json"
        if deploy_path.is_file():
            try:
                payload = json.loads(deploy_path.read_text())
                found = payload.get("address") or payload.get("ACCESS_REGISTRY")
                if isinstance(found, str) and found.startswith("0x"):
                    return found
            except (OSError, json.JSONDecodeError, TypeError):
                pass
        return None

    def recipient_has_api_grant(record_id: str, recipient_id: str):
        rows = db().execute(
            "SELECT * FROM grants WHERE recipient_id=? AND status='active'",
            (recipient_id,),
        ).fetchall()
        for row in rows:
            try:
                if record_id in json.loads(row["records"]):
                    return row
            except (TypeError, json.JSONDecodeError):
                continue
        return None

    def recipient_has_on_chain_grant(record_id: str, recipient_id: str) -> bool:
        if not chain:
            return False
        user = db().execute("SELECT wallet FROM users WHERE id=?", (recipient_id,)).fetchone()
        if not user or not user["wallet"]:
            return False
        address = registry_address_for_checks()
        if not address:
            app.logger.warning("No AccessRegistry address configured for on-chain document checks")
            return False
        try:
            chain.validate_registry(address)
            allowed = bool(
                chain.has_grant(address, vault_record_resource_id(record_id), user["wallet"])
            )
            if allowed and not setting("registry_address"):
                setting("registry_address", address)
            return allowed
        except Exception:
            app.logger.exception("On-chain grant check failed for record %s", record_id[:8])
            return False

    def recipient_can_view_record(record_id: str, recipient_id: str) -> bool:
        return (
            recipient_has_api_grant(record_id, recipient_id) is not None
            or recipient_has_on_chain_grant(record_id, recipient_id)
        )

    def reconcile(row):
        if row["status"] == "revoking":
            return reconcile_revocation(row)
        if not chain or row["status"] not in ("pending", "active"):
            return row
        resource, wallet = pair(row)
        if row["status"] == "active":
            if not chain.has_grant(row["contract_address"], resource, wallet):
                return update(row["id"], status="revoked", registry_revoked=1, error=None)
            # Vault interim commitments are not DH-derived; only require live hasGrant.
            if not is_client_chain_grant(row) and not chain.matches(
                row["contract_address"], resource, wallet, secret_for(row)
            ):
                return update(row["id"], status="failed", error="Registry commitment changed. Revoke before sharing again.")
        elif row["register_tx"]:
            receipt = chain.receipt(row["register_tx"])
            if receipt is None:
                return row
            if receipt["status"] != 1:
                return update(row["id"], status="failed", error="Grant transaction reverted.")
            if not chain.matches(row["contract_address"], resource, wallet, secret_for(row)):
                return update(row["id"], status="failed", error="Registry no longer matches this grant.")
            return update(row["id"], status="active", error=None)
        return row

    def reconcile_revocation(row):
        try:
            if not chain or not row["contract_address"]:
                return update(row["id"], status="revoked", error=None)
            # Never declare revocation complete while a grant transaction could still mine.
            if row["register_tx"] and chain.receipt(row["register_tx"]) is None:
                return update(row["id"], error="API access blocked. Waiting for grant transaction before revocation.")
            if row["revoke_tx"]:
                receipt = chain.receipt(row["revoke_tx"])
                if receipt is None:
                    return update(row["id"], error="API access blocked. Revocation transaction pending.")
                if receipt["status"] != 1:
                    row = update(row["id"], revoke_tx=None, error="Revocation reverted; retry.")
            resource, wallet = pair(row)
            if not chain.has_grant(row["contract_address"], resource, wallet):
                return update(row["id"], status="revoked", registry_revoked=1, error=None)
            if row["revoke_tx"]:
                row = update(row["id"], revoke_tx=None)
            return update(row["id"], error="API access blocked. Retry registry revocation.")
        except Exception as error:
            app.logger.error("Revocation confirmation interrupted: %s", type(error).__name__)
            return update(row["id"], error="API access blocked. Could not confirm revocation; refresh or retry.")

    def advance_revocation(row):
        row = reconcile_revocation(row)
        if row["status"] != "revoking" or row["revoke_tx"]:
            return row
        try:
            if row["register_tx"] and chain.receipt(row["register_tx"]) is None:
                return row
            resource, wallet = pair(row)
            chain.revoke(row["contract_address"], resource, wallet,
                         lambda tx: update(row["id"], revoke_tx=tx, tx_hash=tx))
            return reconcile_revocation(get_grant(row["id"]))
        except Exception as error:
            app.logger.error("Registry revocation interrupted: %s", type(error).__name__)
            return update(row["id"], error="API access blocked. Revocation not confirmed; refresh or retry.")

    def advance(row):
        try:
            row = reconcile(row)
            if row["status"] != "pending":
                return row
            if not chain:
                return update(row["id"], status="active", error=None)
            address = registry()
            row = update(row["id"], contract_address=address, deploy_tx=setting("registry_deploy_tx"))
            resource, wallet = pair(row)
            # There are no file bytes/CIDs in the records API yet. Use an explicit
            # metadata reference and its hash, not a fabricated medical-file digest.
            cid = "medilink:record:" + row["identifier"]
            data_hash = hashlib.sha256(cid.encode()).digest()
            saved = db().execute("SELECT tx_hash FROM registry_resources WHERE identifier=?", (row["identifier"],)).fetchone()
            resource_tx = saved[0] if saved else None
            if resource_tx:
                receipt = chain.receipt(resource_tx)
                if receipt is None:
                    return row
                if receipt["status"] != 1:
                    db().execute("DELETE FROM registry_resources WHERE identifier=?", (row["identifier"],))
                    db().commit()
                    raise RuntimeError("Resource registration reverted; resume to retry")
            if not chain.resource_matches(address, resource, data_hash, cid):
                def save_resource(tx):
                    db().execute("INSERT OR REPLACE INTO registry_resources VALUES (?,?)", (row["identifier"], tx))
                    db().commit()
                    update(row["id"], resource_tx=tx, tx_hash=tx)
                chain.register_resource(address, resource, data_hash, cid, save_resource)
            saved = db().execute("SELECT tx_hash FROM registry_resources WHERE identifier=?", (row["identifier"],)).fetchone()
            row = update(row["id"], resource_tx=saved[0] if saved else None)
            if not row["register_tx"]:
                chain.register(address, resource, wallet, secret_for(row),
                               lambda tx: update(row["id"], register_tx=tx, tx_hash=tx))
            return reconcile(get_grant(row["id"]))
        except Exception as error:
            app.logger.error("Registry setup interrupted: %s", type(error).__name__)
            return update(row["id"], error="Setup not confirmed. Refresh, then Resume setup. Check registry, RPC and operator gas funds.")

    @app.get("/api/health")
    def health():
        faucet_ok = False
        try:
            if faucet_client and os.getenv("FAUCET_ADDRESS"):
                faucet_client.resolve_faucet_address()
                faucet_ok = True
        except Exception:
            faucet_ok = False
        return jsonify(
            mode="local-demo" if dev else "supabase",
            chain=chain is not None,
            faucet=faucet_ok,
            protocol="dh-registry-v1",
            registry_address=registry_address_for_checks() if chain else setting("registry_address"),
            document_uploads_configured=uploads_configured,
        )

    @app.post("/api/external-import/epic/authorize")
    def authorize_epic():
        # Existing before_request verifies the Supabase session. No user ID,
        # redirect, issuer, or scope is accepted from the browser.
        if dev:
            raise APIError("Sign in with a real Medilink account to connect to Epic Sandbox.", 403)
        if request.args or (request.content_length and request.get_json(silent=True) != {}):
            raise APIError("This endpoint accepts no configuration overrides.")
        try:
            with lock:
                return jsonify(epic_oauth.start(g.uid))
        except EpicOAuthError as error:
            raise APIError(str(error), 503) from error

    @app.get("/api/external-import/epic/connection")
    def epic_connection():
        if dev:
            raise APIError("Sign in with a real Medilink account to connect to Epic Sandbox.", 403)
        return jsonify(epic_oauth.status(g.uid))

    @app.post("/api/external-import/epic/complete")
    def complete_epic():
        if dev:
            raise APIError("Sign in with a real Medilink account to connect to Epic Sandbox.", 403)
        if request.args:
            raise APIError("Callback parameters must be sent in the JSON body.")
        try:
            with lock:
                return jsonify(epic_oauth.complete(g.uid, request.get_json(silent=True)))
        except EpicOAuthError as error:
            raise APIError(str(error), 400) from error

    def user_public_x25519(row) -> str | None:
        if row["x25519_public_key_b64"]:
            return row["x25519_public_key_b64"]
        if row["public_key"]:
            return base64.b64encode(bytes(row["public_key"])).decode("ascii")
        return None

    @app.route("/api/external-import/epic/import", methods=["GET", "POST"])
    def import_epic():
        if dev:
            raise APIError("Sign in with a real MediLink account to import Epic records.", 403)
        if request.args or (request.method == "POST" and request.get_json(silent=True) != {}):
            raise APIError("This endpoint accepts no account, patient, or configuration overrides.")
        try:
            if request.method == "GET":
                return jsonify(epic_imports.status(g.uid))
            return jsonify(epic_imports.start(g.uid)), 202
        except EpicOAuthError as error:
            raise APIError(str(error), 409) from error
        except EpicImportError as error:
            raise APIError(str(error), 503) from error

    @app.get("/api/external-import/epic/records")
    def epic_records():
        if dev:
            raise APIError("Sign in with a real MediLink account to view imported Epic records.", 403)
        if set(request.args) - {'category', 'page'} or any(len(request.args.getlist(key)) != 1 for key in request.args):
            raise APIError("Only category and page filters are accepted.")
        try:
            return jsonify(epic_imports.records(g.uid, request.args.get('category', 'all'), int(request.args.get('page', '1'))))
        except ValueError:
            raise APIError("Choose a valid record category and page.", 400) from None
        except EpicImportError as error:
            raise APIError(str(error), 503) from error

    @app.post("/api/enroll")
    def enroll_self():
        body = request.get_json(silent=True) or {}
        wallet = (body.get("evmAddress") or body.get("wallet") or "").strip()
        x25519 = body.get("x25519PublicKeyB64") or body.get("x25519_public_key") or None
        if isinstance(x25519, str):
            x25519 = x25519.strip() or None

        for banned in ("evmPrivateKeyHex", "private_key", "privateKey", "wallet_key"):
            if banned in body:
                raise APIError("Private keys must not be sent to the API", 400)

        if not wallet:
            # Idempotent read / custodial create for legacy AccessPanel fallback.
            row = db().execute("SELECT * FROM users WHERE id=?", (g.uid,)).fetchone()
            if row is None:
                enroll(g.uid, g.name)
                row = db().execute("SELECT * FROM users WHERE id=?", (g.uid,)).fetchone()
            return jsonify(
                id=row["id"],
                name=row["name"],
                wallet=row["wallet"],
                x25519PublicKeyB64=user_public_x25519(row),
                funded=None,
            )

        if not is_address(wallet):
            raise APIError("Invalid evmAddress", 400)
        wallet = to_checksum_address(wallet)
        taken = db().execute(
            "SELECT id FROM users WHERE wallet=? AND id!=?", (wallet, g.uid)
        ).fetchone()
        if taken:
            raise APIError("evmAddress already enrolled to another account", 409)

        enroll(g.uid, g.name, wallet=wallet, x25519_public_key_b64=x25519)
        row = db().execute("SELECT * FROM users WHERE id=?", (g.uid,)).fetchone()

        funded = None
        drip_info = None
        if faucet_client and os.getenv("FAUCET_ADDRESS"):
            try:
                drip_info = faucet_client.ensure_funded(wallet)
                funded = True
            except Exception as exc:
                app.logger.warning("ensure_funded failed: %s", type(exc).__name__)
                funded = False

        return jsonify(
            id=row["id"],
            name=row["name"],
            wallet=row["wallet"],
            x25519PublicKeyB64=user_public_x25519(row),
            funded=funded,
            faucet=drip_info,
        )

    @app.get("/api/users")
    def users():
        rows = db().execute(
            "SELECT id, name, wallet, public_key, x25519_public_key_b64 FROM users WHERE id != ? ORDER BY name",
            (g.uid,),
        ).fetchall()
        return jsonify(
            [
                {
                    "id": row["id"],
                    "name": row["name"],
                    "wallet": row["wallet"],
                    "x25519PublicKeyB64": user_public_x25519(row),
                }
                for row in rows
            ]
        )

    @app.get("/api/faucet/status")
    def faucet_status():
        if not faucet_client:
            raise APIError("Faucet client unavailable", 503)
        try:
            return jsonify(faucet_client.faucet_status())
        except Exception as exc:
            raise APIError(f"Faucet unavailable: {exc}", 503) from exc

    @app.post("/api/faucet/ensure")
    def faucet_ensure():
        row = db().execute("SELECT * FROM users WHERE id=?", (g.uid,)).fetchone()
        if not row:
            raise APIError("Enroll a vault wallet before requesting faucet funds", 400)
        if not faucet_client or not os.getenv("FAUCET_ADDRESS"):
            raise APIError("Faucet is not configured", 503)
        try:
            result = faucet_client.ensure_funded(row["wallet"])
        except Exception as exc:
            raise APIError(f"Could not fund wallet: {exc}", 503) from exc
        return jsonify(result)

    @app.get("/api/records")
    def records():
        return jsonify(owner_records())

    @app.post("/api/records")
    def upload_document():
        if dev:
            patient = g.uid == "patient"
        else:
            result = requests.get(supabase_url + "/rest/v1/user_roles", headers={
                "apikey": supabase_key, "Authorization": "Bearer " + g.token},
                params={"user_id": "eq." + g.uid, "role": "eq.patient", "select": "role"}, timeout=10)
            result.raise_for_status()
            patient = any(row.get("role") == "patient" for row in result.json())
        if not patient:
            raise APIError("Only patients can upload documents", 403)
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            raise APIError("Choose a document to upload")
        name = upload.filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
        if not name or len(name) > 200 or any(ord(c) < 32 for c in name):
            raise APIError("Use a filename of 1–200 characters without control characters")
        raw_tags = request.form.get("tags", "")
        if len(raw_tags) > 1000:
            raise APIError("Tags are too long")
        tags = list(dict.fromkeys(tag.strip() for tag in raw_tags.split(",") if tag.strip()))
        if len(tags) > 20 or any(len(tag) > 50 for tag in tags):
            raise APIError("Use at most 20 tags, each up to 50 characters")
        content = upload.read(MAX_DOCUMENT_BYTES + 1)
        if not content:
            raise APIError("The document is empty")
        if len(content) > MAX_DOCUMENT_BYTES:
            raise APIError("Documents must be 10 MB or smaller", 413)
        try:
            mime = document_type(name, content)
        except ValueError as error:
            raise APIError(str(error)) from error
        kind = (request.form.get("kind") or "Document").strip() or "Document"
        if len(kind) > 50 or any(ord(c) < 32 for c in kind):
            raise APIError("Use a record type of 1–50 characters without control characters")
        return jsonify(documents.add(g.uid, name, tags, mime, content, kind=kind)), 201

    @app.get("/api/records/<record_id>")
    def record_metadata(record_id):
        """Metadata for owner or an authorized recipient (API grant or on-chain hasGrant)."""
        with lock:
            document = documents.get(record_id)
            if document is None:
                raise APIError("Record not found", 404)
            if document["owner_id"] != g.uid and not recipient_can_view_record(record_id, g.uid):
                raise APIError("Record not found or access denied", 403)
            return jsonify(document)

    @app.post("/api/records/<record_id>/document")
    def view_document(record_id):
        # Every delivery is authenticated. Never hand out a public or reusable storage URL.
        with lock:
            document = documents.get(record_id)
            if document is None or not document["has_document"]:
                raise APIError("No uploaded document is available for this record", 404)
            if document["owner_id"] != g.uid:
                permitted = recipient_has_api_grant(record_id, g.uid)
                allowed = False
                if permitted is not None:
                    try:
                        # Reuse DH verification and live registry checks, including revocation.
                        access(permitted["id"])
                        allowed = True
                    except APIError:
                        # Stale/mismatched API grant: fall through to vault hasGrant.
                        allowed = False
                if not allowed and recipient_has_on_chain_grant(record_id, g.uid):
                    # Vault-signed AccessGrantSet is enough for MVP file delivery.
                    allowed = True
                if not allowed:
                    raise APIError("Document not found or access denied", 403)
            response = Response(documents.read(record_id, document["owner_id"]), mimetype=document["mime_type"])
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Content-Security-Policy"] = "sandbox; default-src 'none'"
            return response

    @app.get("/api/grants")
    def grants():
        with lock:
            rows = db().execute("SELECT * FROM grants WHERE owner_id=? OR recipient_id=? ORDER BY created_at DESC", (g.uid, g.uid)).fetchall()
            return jsonify([serialize(reconcile(row)) for row in rows])

    @app.post("/api/grants")
    def grant():
        body = request.get_json()
        if not isinstance(body, dict):
            raise APIError("Expected a JSON object")
        recipient = body.get("recipient_id")
        record_ids = body.get("record_ids")
        client_chain = bool(body.get("client_chain"))
        client_tx = body.get("tx_hash")
        if client_tx is not None and (not isinstance(client_tx, str) or not 1 <= len(client_tx) <= 128):
            raise APIError("Invalid tx_hash")
        if not isinstance(recipient, str) or recipient == g.uid:
            raise APIError("Select another registered user")
        if (not isinstance(record_ids, list) or not 1 <= len(record_ids) <= 100 or
                any(not isinstance(value, str) for value in record_ids)):
            raise APIError("Select between 1 and 100 records")
        if client_chain and not chain:
            raise APIError("client_chain grants require the API to run with --chain", 400)
        record_ids = sorted(set(record_ids))
        owned = {record["id"]: record for record in owner_records()}
        if any(record_id not in owned for record_id in record_ids):
            raise APIError("You can only grant access to records you own", 403)
        with lock:
            enroll(g.uid, g.name)
            owner = db().execute("SELECT * FROM users WHERE id=?", (g.uid,)).fetchone()
            peer = db().execute("SELECT * FROM users WHERE id=?", (recipient,)).fetchone()
            if not peer:
                raise APIError("Recipient must sign in and open the access panel first", 404)
            if client_chain and not peer["wallet"]:
                raise APIError("Recipient must enroll a vault wallet first", 400)
            results = []
            for record_id in record_ids:
                record_json = json.dumps([record_id])
                existing = db().execute("SELECT * FROM grants WHERE owner_id=? AND recipient_id=? AND records=? AND status IN ('pending','active','revoking','failed')",
                                        (g.uid, recipient, record_json)).fetchone()
                if existing:
                    if client_chain:
                        results.append(serialize(reconcile(existing)))
                    else:
                        results.append(serialize(advance(existing)))
                    continue
                grant_id = str(uuid.uuid4())
                private = decrypt_secret(master_key, owner["private_key"], "user:" + g.uid)
                envelope = create_access_grant(private, peer["public_key"], grant_id, master_key)
                if client_chain:
                    # Same resource id the browser already registered on AccessRegistry.
                    identifier = vault_record_resource_id_hex(record_id)
                    address = registry_address_for_checks()
                    if not address:
                        raise APIError("No AccessRegistry address configured for client_chain grants", 503)
                    db().execute(
                        "INSERT INTO grants (id,owner_id,recipient_id,scope,records,record_name,identifier,"
                        "contract_address,status,secret,tx_hash) VALUES (?,?,?,?,?,?,?,?, 'active',?,?)",
                        (grant_id, g.uid, recipient, owned[record_id]["kind"], record_json,
                         owned[record_id]["name"], identifier, address, envelope, client_tx),
                    )
                    db().commit()
                    results.append(serialize(reconcile(get_grant(grant_id))))
                else:
                    identifier = hashlib.sha256(json.dumps(["medilink-resource-v1", supabase_url or "local-demo", g.uid, record_id]).encode()).hexdigest()
                    db().execute("INSERT INTO grants (id,owner_id,recipient_id,scope,records,record_name,identifier,status,secret) VALUES (?,?,?,?,?,?,?,'pending',?)",
                                 (grant_id, g.uid, recipient, owned[record_id]["kind"], record_json,
                                  owned[record_id]["name"], identifier, envelope))
                    db().commit()
                    results.append(serialize(advance(get_grant(grant_id))))
            return jsonify(grants=results), 200

    @app.post("/api/grants/<grant_id>/resume")
    def resume(grant_id):
        with lock:
            row = get_grant(grant_id)
            if row["owner_id"] != g.uid:
                raise APIError("Only the owner can resume setup", 403)
            return jsonify(serialize(advance(row)))

    @app.delete("/api/grants/<grant_id>")
    def revoke(grant_id):
        with lock:
            row = get_grant(grant_id)
            if row["owner_id"] != g.uid:
                raise APIError("Only the owner can revoke this grant", 403)
            if row["status"] != "revoked":
                # Commit the deny decision BEFORE contacting RPC, including on failure/timeouts.
                row = update(grant_id, status="revoking", error=None)
                row = advance_revocation(row)
            return jsonify(serialize(row)), 202 if row["status"] == "revoking" else 200

    @app.post("/api/grants/<grant_id>/access")
    def access(grant_id):
        with lock:
            row = get_grant(grant_id)
            if row["recipient_id"] != g.uid or row["status"] != "active":
                raise APIError("Access denied: this grant is not active for your account", 403)
            row = reconcile(row)
            if row["status"] != "active":
                raise APIError("Access denied: the registry grant is not active", 403)
            peer = db().execute("SELECT * FROM users WHERE id=?", (g.uid,)).fetchone()
            owner = db().execute("SELECT * FROM users WHERE id=?", (row["owner_id"],)).fetchone()
            private = decrypt_secret(master_key, peer["private_key"], "user:" + g.uid)
            derived = derive_shared_secret(private, owner["public_key"], grant_id)
            stored = decrypt_secret(master_key, row["secret"], "grant:" + grant_id)
            if not hmac.compare_digest(derived, stored):
                raise APIError("Key exchange verification failed", 403)
            result = {"allowed": True, "key_exchange_verified": True,
                      "record_ids": json.loads(row["records"]), "contract_address": row["contract_address"],
                      "chain_verified": False}
            if chain:
                if not chain.matches(row["contract_address"], *pair(row), derived):
                    raise APIError("Contract no longer matches this grant. Check for a chain reset.", 409)
                # Vault-enrolled users keep EVM keys client-side; skip custodial challenge.
                if peer["wallet_key"]:
                    wallet_key = decrypt_secret(master_key, peer["wallet_key"], "wallet:" + g.uid)
                    verification = chain.request_access(
                        row["contract_address"], bytes.fromhex(row["identifier"]), derived, wallet_key
                    )
                    if not verification["allowed"]:
                        raise APIError("AccessDenied event received from contract", 403)
                    result.update(verification, chain_verified=True)
            # No file is returned yet; this exercises authorization and the actual contract flow.
            return jsonify(result)

    def local_vault_path(user_id: str) -> Path:
        path = vault_dir / user_id / VAULT_OBJECT_NAME
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        return path

    def ensure_local_vault_file(user_id: str) -> Path | None:
        path = local_vault_path(user_id)
        if path.is_file():
            return path
        return recover_vault_from_legacy_shards(vault_dir, user_id)

    def purge_expired_vault_tokens():
        now = time.time()
        expired = [token for token, meta in vault_local_tokens.items() if meta["exp"] <= now]
        for token in expired:
            vault_local_tokens.pop(token, None)

    def issue_local_vault_url(operation: str) -> str:
        purge_expired_vault_tokens()
        token = secrets.token_urlsafe(32)
        vault_local_tokens[token] = {"uid": g.uid, "op": operation, "exp": time.time() + VAULT_PRESIGN_TTL_S}
        return request.url_root.rstrip("/") + "/api/vault/local-object?token=" + token

    def vault_exists_for(user_id: str) -> bool:
        if hippius_configured():
            return hippius_object_exists(HIPPIUS_BUCKET, vault_object_key(user_id))
        return ensure_local_vault_file(user_id) is not None

    @app.get("/api/vault/status")
    def vault_status():
        try:
            exists = vault_exists_for(g.uid)
        except RuntimeError:
            raise APIError("Vault storage is unavailable. Check Hippius configuration.", 503)
        return jsonify(
            exists=exists,
            user_id=g.uid,
            object_key=vault_object_key(g.uid),
            backend="hippius" if hippius_configured() else "local",
        )

    @app.post("/api/vault/presign-put")
    def vault_presign_put():
        key = vault_object_key(g.uid)
        if hippius_configured():
            try:
                url = generate_presigned_put_url(HIPPIUS_BUCKET, key, VAULT_PRESIGN_TTL_S)
            except RuntimeError:
                raise APIError("Could not create vault upload URL.", 503)
            return jsonify(url=url, method="PUT", object_key=key, backend="hippius",
                           headers={"Content-Type": "application/json"})
        return jsonify(
            url=issue_local_vault_url("put"),
            method="PUT",
            object_key=key,
            backend="local",
            headers={"Content-Type": "application/json"},
        )

    @app.post("/api/vault/presign-get")
    def vault_presign_get():
        key = vault_object_key(g.uid)
        if not vault_exists_for(g.uid):
            raise APIError("No vault keystore found for this account.", 404)
        if hippius_configured():
            try:
                url = generate_presigned_url(HIPPIUS_BUCKET, key, VAULT_PRESIGN_TTL_S)
            except RuntimeError:
                raise APIError("Could not create vault download URL.", 503)
            return jsonify(url=url, method="GET", object_key=key, backend="hippius", headers={})
        return jsonify(
            url=issue_local_vault_url("get"),
            method="GET",
            object_key=key,
            backend="local",
            headers={},
        )

    @app.route("/api/vault/local-object", methods=["GET", "PUT"])
    def vault_local_object():
        purge_expired_vault_tokens()
        token = request.args.get("token", "")
        meta = vault_local_tokens.get(token)
        if not meta or meta["exp"] <= time.time():
            raise APIError("Vault URL expired or invalid.", 401)
        user_id = meta["uid"]
        path = local_vault_path(user_id)
        if request.method == "PUT":
            if meta["op"] != "put":
                raise APIError("Vault URL is not valid for upload.", 403)
            body = request.get_data()
            if not body:
                raise APIError("Empty vault upload.", 400)
            if len(body) > VAULT_MAX_BYTES:
                raise APIError("Vault blob too large.", 413)
            path.write_bytes(body)
            path.chmod(0o600)
            vault_local_tokens.pop(token, None)
            return jsonify(ok=True), 200
        if meta["op"] != "get":
            raise APIError("Vault URL is not valid for download.", 403)
        if ensure_local_vault_file(user_id) is None:
            raise APIError("No vault keystore found for this account.", 404)
        path = local_vault_path(user_id)
        vault_local_tokens.pop(token, None)
        return Response(path.read_bytes(), mimetype="application/json")

    return app


def local_chain_data_dir(chain, dev=True):
    """Keep SQLite/test DBs separate across Hardhat resets and contract builds.

    Vault keystores are intentionally NOT stored under this fingerprint directory;
    see resolve_local_vault_dir().
    """
    if chain.chain_id != 31337:
        raise RuntimeError("Automatic data directories are only supported for local Hardhat testing")
    metadata = chain.w3.provider.make_request("hardhat_metadata", [])
    instance = metadata.get("result", {}).get("instanceId")
    if not isinstance(instance, str) or not instance:
        raise RuntimeError("Cannot identify the Hardhat instance; start the local Hardhat node first")
    identity = json.dumps(["dh-registry-v1", dev, os.getenv("SUPABASE_URL", "") if not dev else "", chain.identity, instance], sort_keys=True)
    fingerprint = hashlib.sha256(identity.encode()).hexdigest()
    base = Path(os.getenv("GRANT_DATA_DIR", Path(__file__).parent / ".grant-pairs-local"))
    return base / fingerprint


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dev", action="store_true", help="Loopback-only fixture identities; never deploy")
    parser.add_argument("--chain", action="store_true", help="Use one shared AccessRegistry and verify access on-chain")
    parser.add_argument("--port", type=int, default=5001)
    parser.add_argument("--auto-data-dir", action="store_true",
                        help="Use a separate SQLite directory per Hardhat instance and contract build "
                             "(vault keystores stay in GRANT_DATA_DIR/vault_objects)")
    args = parser.parse_args()
    if args.auto_data_dir and not args.chain:
        parser.error("--auto-data-dir requires --chain")
    chain = None
    if args.chain:
        from chain_grants import ChainGrants
        chain = ChainGrants(os.environ["RPC_URL"], os.environ["OWNER_PRIVATE_KEY"],
                            fund_local_wallets=os.getenv("FUND_LOCAL_WALLETS") == "1")
    directory = local_chain_data_dir(chain, dev=args.dev) if args.auto_data_dir else None
    if directory is not None:
        print(f"Local test data: {directory.resolve()}", flush=True)
        print(f"Local vault store: {resolve_local_vault_dir(directory).resolve()}", flush=True)
    app = create_app(dev=args.dev, chain=chain, data_dir=directory)
    app.run(host="127.0.0.1", port=args.port, debug=False)


if __name__ == "__main__":
    main()
