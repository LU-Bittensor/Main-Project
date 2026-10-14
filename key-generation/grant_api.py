"""Single-process custodial grant API. See LOCAL_TESTING.md for setup and limits."""
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
from threading import RLock
import uuid

import requests
from eth_account import Account
from flask import Flask, Response, g, jsonify, request, Response
from documents import SupabaseDocumentStore, DemoDocumentStore, DocumentStorageError, MAX_DOCUMENT_BYTES, document_type
from werkzeug.exceptions import HTTPException

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


def create_app(*, dev=False, data_dir=None, master_key=None, chain=None):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = VAULT_MAX_BYTES
    directory = Path(data_dir or os.getenv("GRANT_DATA_DIR", Path(__file__).parent / (".grant-pairs-dev" if dev else ".grant-pairs-data")))
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    vault_dir = directory / "vault_objects"
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
    allowed_origins = set(os.getenv("FRONTEND_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(","))
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
    documents = DemoDocumentStore() if dev else SupabaseDocumentStore(
        supabase_url, os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""))


    def enroll(uid, name):
        with lock:
            if not db().execute("SELECT id FROM users WHERE id=?", (uid,)).fetchone():
                private, public = generate_key_pair()
                # Separate custodial EVM key signs this recipient's challenge transactions.
                wallet = Account.create()
                db().execute("INSERT INTO users VALUES (?, ?, ?, ?, ?, ?)",
                             (uid, name, encrypt_secret(master_key, private, "user:" + uid), public,
                              wallet.address, encrypt_secret(master_key, bytes(wallet.key), "wallet:" + uid)))
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

    def reconcile(row):
        if row["status"] == "revoking":
            return reconcile_revocation(row)
        if not chain or row["status"] not in ("pending", "active"):
            return row
        resource, wallet = pair(row)
        if row["status"] == "active":
            if not chain.has_grant(row["contract_address"], resource, wallet):
                return update(row["id"], status="revoked", registry_revoked=1, error=None)
            if not chain.matches(row["contract_address"], resource, wallet, secret_for(row)):
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
        return jsonify(mode="local-demo" if dev else "supabase", chain=chain is not None, protocol="dh-registry-v1", registry_address=setting("registry_address"))

    @app.post("/api/enroll")
    def enroll_self():
        enroll(g.uid, g.name)
        wallet = db().execute("SELECT wallet FROM users WHERE id=?", (g.uid,)).fetchone()[0]
        return jsonify(id=g.uid, name=g.name, wallet=wallet)

    @app.get("/api/users")
    def users():
        return jsonify([dict(row) for row in db().execute("SELECT id, name FROM users WHERE id != ? ORDER BY name", (g.uid,))])

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
        return jsonify(documents.add(g.uid, name, tags, mime, content)), 201

    @app.post("/api/records/<record_id>/document")
    def view_document(record_id):
        # Every delivery is authenticated. Never hand out a public or reusable storage URL.
        with lock:
            document = documents.get(record_id)
            if document is None or not document["has_document"]:
                raise APIError("No uploaded document is available for this record", 404)
            if document["owner_id"] != g.uid:
                rows = db().execute("SELECT * FROM grants WHERE owner_id=? AND recipient_id=? AND status='active'",
                                    (document["owner_id"], g.uid)).fetchall()
                permitted = next((row for row in rows if record_id in json.loads(row["records"])), None)
                if permitted is None:
                    raise APIError("Document not found or access denied", 403)
                # Reuse DH verification and live registry checks, including revocation.
                access(permitted["id"])
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
        if not isinstance(recipient, str) or recipient == g.uid:
            raise APIError("Select another registered user")
        if (not isinstance(record_ids, list) or not 1 <= len(record_ids) <= 100 or
                any(not isinstance(value, str) for value in record_ids)):
            raise APIError("Select between 1 and 100 records")
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
            results = []
            for record_id in record_ids:
                record_json = json.dumps([record_id])
                existing = db().execute("SELECT * FROM grants WHERE owner_id=? AND recipient_id=? AND records=? AND status IN ('pending','active','revoking','failed')",
                                        (g.uid, recipient, record_json)).fetchone()
                if existing:
                    results.append(serialize(advance(existing)))
                    continue
                grant_id = str(uuid.uuid4())
                private = decrypt_secret(master_key, owner["private_key"], "user:" + g.uid)
                envelope = create_access_grant(private, peer["public_key"], grant_id, master_key)
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
                wallet_key = decrypt_secret(master_key, peer["wallet_key"], "wallet:" + g.uid)
                verification = chain.request_access(row["contract_address"], bytes.fromhex(row["identifier"]), derived, wallet_key)
                if not verification["allowed"]:
                    raise APIError("AccessDenied event received from contract", 403)
                result.update(verification, chain_verified=True)
            # No file is returned yet; this exercises authorization and the actual contract flow.
            return jsonify(result)

    def local_vault_path(user_id: str) -> Path:
        path = vault_dir / user_id / VAULT_OBJECT_NAME
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        return path

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
        return local_vault_path(user_id).is_file()

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
        if not path.is_file():
            raise APIError("No vault keystore found for this account.", 404)
        vault_local_tokens.pop(token, None)
        return Response(path.read_bytes(), mimetype="application/json")

    return app


def local_chain_data_dir(chain, dev=True):
    """Keep test databases separate across Hardhat resets and contract builds."""
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
                        help="Use a separate test directory per Hardhat instance and contract build")
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
    app = create_app(dev=args.dev, chain=chain, data_dir=directory)
    app.run(host="127.0.0.1", port=args.port, debug=False)


if __name__ == "__main__":
    main()
