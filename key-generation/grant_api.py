"""Single-process custodial grant API. See LOCAL_TESTING.md for setup and limits."""
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3
from threading import RLock
import uuid

import requests
from eth_account import Account
from flask import Flask, g, jsonify, request
from werkzeug.exceptions import HTTPException

from DHkey_share import create_access_grant, decrypt_secret, derive_shared_secret, encrypt_secret, generate_key_pair

DEMO_USERS = {"patient": "Demo patient", "clinician": "Demo clinician", "outsider": "Unrelated user"}
DEMO_RECORDS = [
    {"id": "demo-lab", "name": "Demo lipid panel", "kind": "Lab report"},
    {"id": "demo-imaging", "name": "Demo chest X-ray", "kind": "Imaging"},
]


class APIError(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status


def create_app(*, dev=False, data_dir=None, master_key=None, chain=None):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024
    directory = Path(data_dir or os.getenv("GRANT_DATA_DIR", Path(__file__).parent / (".grant-pairs-dev" if dev else ".grant-pairs-data")))
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
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
    db_path = directory / "pair-grants.sqlite3"
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
                contract_address TEXT, deploy_tx TEXT, register_tx TEXT, kill_tx TEXT,
                contract_killed INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL, secret BLOB,
                tx_hash TEXT, error TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE UNIQUE INDEX IF NOT EXISTS live_grant ON grants(owner_id, recipient_id, records)
                WHERE status IN ('pending', 'active', 'revoking');
        """)
        # Non-destructive schema upgrade for existing databases.
        columns = {row["name"] for row in db().execute("PRAGMA table_info(grants)")}
        if "kill_tx" not in columns:
            db().execute("ALTER TABLE grants ADD COLUMN kill_tx TEXT")
        if "contract_killed" not in columns:
            db().execute("ALTER TABLE grants ADD COLUMN contract_killed INTEGER NOT NULL DEFAULT 0")
        # Do not silently reuse off-chain permissions against a different chain or master key.
        environment = json.dumps({"protocol": "dh-pair-v1", "dev": dev,
                                  "chain": None if chain is None else chain.identity})
        previous = db().execute("SELECT value FROM settings WHERE name='environment'").fetchone()
        if previous and previous[0] != environment:
            raise RuntimeError("Use a separate GRANT_DATA_DIR for this auth/chain configuration")
        check = hmac.digest(master_key, b"medilink-master-key-check", "sha256").hex()
        previous_key = db().execute("SELECT value FROM settings WHERE name='key_check'").fetchone()
        if previous_key and not hmac.compare_digest(previous_key[0], check):
            raise RuntimeError("GRANT_MASTER_KEY does not match this database")
        db().execute("INSERT OR IGNORE INTO settings VALUES ('environment', ?)", (environment,))
        db().execute("INSERT OR IGNORE INTO settings VALUES ('key_check', ?)", (check,))
        db().commit()
    db_path.chmod(0o600)

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
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            raise APIError("Sign in before managing access", 401)
        g.token = header[7:]
        if dev:
            uid = g.token.removeprefix("demo-")
            if g.token != "demo-" + uid or uid not in DEMO_USERS:
                raise APIError("Invalid local demo account", 401)
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
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
        response.headers["Cache-Control"] = "no-store"
        return response

    def owner_records():
        if dev:
            return DEMO_RECORDS if g.uid == "patient" else []
        result = requests.get(supabase_url + "/rest/v1/records", headers={
            "apikey": supabase_key, "Authorization": "Bearer " + g.token},
            params={"owner_id": "eq." + g.uid, "select": "id,name,kind"}, timeout=10)
        result.raise_for_status()
        return result.json()

    def serialize(row):
        item = {key: row[key] for key in ("id", "owner_id", "recipient_id", "scope", "status", "tx_hash", "error",
                       "record_name", "contract_address", "deploy_tx", "register_tx", "kill_tx")}
        item["contract_killed"] = bool(row["contract_killed"])
        item["record_ids"] = json.loads(row["records"])
        item["chain_enabled"] = chain is not None
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

    def reconcile(row):
        """Read receipts only. Deployment success alone must not activate a grant."""
        if row["status"] == "revoking":
            return reconcile_revocation(row)
        if chain and row["status"] == "active" and chain.is_killed(row["contract_address"]):
            return update(row["id"], status="revoked", contract_killed=1, error=None)
        if not chain or row["status"] != "pending":
            return row
        if row["deploy_tx"]:
            receipt = chain.receipt(row["deploy_tx"])
            if receipt is None:
                return row
            if receipt["status"] != 1:
                return update(row["id"], status="failed", error="Contract deployment reverted.")
            row = update(row["id"], contract_address=receipt["contractAddress"])
        if row["contract_address"] and chain.is_killed(row["contract_address"]):
            return update(row["id"], status="revoked", contract_killed=1, error=None)
        if row["register_tx"]:
            receipt = chain.receipt(row["register_tx"])
            if receipt is None:
                return row
            if receipt["status"] != 1:
                return update(row["id"], status="failed", error="Commitment registration reverted.")
            secret = decrypt_secret(master_key, row["secret"], "grant:" + row["id"])
            if not chain.matches(row["contract_address"], row["identifier"], secret):
                return update(row["id"], status="failed", error="Contract does not match this grant.")
            row = update(row["id"], status="active", error=None)
        return row

    def reconcile_revocation(row):
        """No transactions here: confirm disablement without ever reactivating access."""
        try:
            if not chain:
                return update(row["id"], status="revoked", error=None)
            if not row["contract_address"]:
                if not row["deploy_tx"]:
                    return update(row["id"], status="revoked", error=None)
                receipt = chain.receipt(row["deploy_tx"])
                if receipt is None:
                    return update(row["id"], error="API access blocked. Waiting for deployment; retry revocation after it confirms.")
                if receipt["status"] != 1:
                    return update(row["id"], status="revoked", error=None)
                row = update(row["id"], contract_address=receipt["contractAddress"])
            if chain.is_killed(row["contract_address"]):
                return update(row["id"], status="revoked", contract_killed=1, error=None)
            if row["kill_tx"]:
                receipt = chain.receipt(row["kill_tx"])
                if receipt is None:
                    return update(row["id"], error="API access blocked. Waiting for the kill transaction to confirm.")
                # A reverted kill is retryable; a receipt alone is not proof of disablement.
                return update(row["id"], kill_tx=None, error="Contract is not disabled. Retry revocation; check operator ownership and gas funds.")
            return update(row["id"], error="API access blocked. Contract disablement needs a revocation retry.")
        except Exception as error:
            app.logger.error("Revocation confirmation interrupted: %s", type(error).__name__)
            return update(row["id"], error="API access blocked. Could not confirm contract disablement; refresh or retry revocation.")

    def advance_revocation(row):
        row = reconcile_revocation(row)
        if row["status"] != "revoking" or not row["contract_address"] or row["kill_tx"]:
            return row
        try:
            chain.kill(row["contract_address"], lambda tx: update(row["id"], kill_tx=tx, tx_hash=tx))
            return reconcile_revocation(get_grant(row["id"]))
        except Exception as error:
            app.logger.error("Contract kill interrupted: %s", type(error).__name__)
            return update(row["id"], error="API access blocked. Kill transaction not confirmed; refresh or retry revocation.")

    def advance(row):
        """Resume setup using persisted transaction hashes; never deploy a pair twice on timeout."""
        try:
            row = reconcile(row)
            if row["status"] != "pending":
                return row
            if not chain:
                return update(row["id"], status="active", error=None)
            if not row["deploy_tx"]:
                chain.deploy(row["identifier"], lambda tx: update(row["id"], deploy_tx=tx, tx_hash=tx))
                row = reconcile(get_grant(row["id"]))
            if not row["contract_address"] or row["status"] != "pending":
                return row
            if not row["register_tx"]:
                secret = decrypt_secret(master_key, row["secret"], "grant:" + row["id"])
                chain.register(row["contract_address"], secret,
                               lambda tx: update(row["id"], register_tx=tx, tx_hash=tx))
            return reconcile(get_grant(row["id"]))
        except Exception as error:
            app.logger.error("Pair setup interrupted: %s", type(error).__name__)
            return update(row["id"], error="Setup not confirmed. Refresh, then Resume setup. Check RPC and operator gas funds if it persists.")

    @app.get("/api/health")
    def health():
        return jsonify(mode="local-demo" if dev else "supabase", chain=chain is not None, protocol="dh-pair-v1")

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
                existing = db().execute("SELECT * FROM grants WHERE owner_id=? AND recipient_id=? AND records=? AND status IN ('pending','active','revoking')",
                                        (g.uid, recipient, record_json)).fetchone()
                if existing:
                    results.append(serialize(advance(existing)))
                    continue
                grant_id = str(uuid.uuid4())
                private = decrypt_secret(master_key, owner["private_key"], "user:" + g.uid)
                envelope = create_access_grant(private, peer["public_key"], grant_id, master_key)
                identifier = hashlib.sha256(json.dumps([g.uid, recipient, record_id, grant_id]).encode()).hexdigest()
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
                raise APIError("Access denied: the contract is deactivated", 403)
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
                if not chain.matches(row["contract_address"], row["identifier"], derived):
                    raise APIError("Contract no longer matches this grant. Check for a chain reset.", 409)
                wallet_key = decrypt_secret(master_key, peer["wallet_key"], "wallet:" + g.uid)
                verification = chain.request_access(row["contract_address"], derived, wallet_key)
                if not verification["allowed"]:
                    raise APIError("AccessDenied event received from contract", 403)
                result.update(verification, chain_verified=True)
            # No file is returned yet; this exercises authorization and the actual contract flow.
            return jsonify(result)

    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dev", action="store_true", help="Loopback-only fixture identities; never deploy")
    parser.add_argument("--chain", action="store_true", help="Deploy one contract per recipient-record pair and verify access on-chain")
    parser.add_argument("--port", type=int, default=5001)
    args = parser.parse_args()
    chain = None
    if args.chain:
        from chain_grants import ChainGrants
        chain = ChainGrants(os.environ["RPC_URL"], os.environ["OWNER_PRIVATE_KEY"],
                            fund_local_wallets=args.dev and os.getenv("FUND_LOCAL_WALLETS") == "1")
    app = create_app(dev=args.dev, chain=chain)
    app.run(host="127.0.0.1", port=args.port, debug=False)


if __name__ == "__main__":
    main()
