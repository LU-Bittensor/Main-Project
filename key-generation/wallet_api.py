#!/usr/bin/env python3
"""
Minimal MediLink wallet API: vault blob storage, public-key enroll, faucet ensure.

Non-custodial: never accepts or stores EVM/X25519 private keys.

  export RPC_URL=http://127.0.0.1:8545 CHAIN_ID=31337
  export FAUCET_ADDRESS=0x... OWNER_PRIVATE_KEY=0x...
  export GRANT_DATA_DIR=./.grant-wallets
  # Optional real auth:
  # export SUPABASE_URL=... SUPABASE_PUBLISHABLE_KEY=...
  python wallet_api.py
  # or: python wallet_api.py --dev   # demo-* bearer tokens on loopback
"""

from __future__ import annotations

import argparse
import os
import secrets
import sqlite3
import time
from pathlib import Path

from eth_utils import is_address, to_checksum_address
from flask import Flask, Response, g, jsonify, request
from werkzeug.exceptions import HTTPException

try:
    import faucet_client
except ImportError:  # pragma: no cover
    faucet_client = None  # type: ignore

ROOT = Path(__file__).resolve().parent
VAULT_OBJECT_NAME = "keystore.v1.json"
VAULT_PRESIGN_TTL_S = 120
VAULT_MAX_BYTES = 256_000

DEMO_USERS = {
    "patient": "Demo Patient",
    "clinician": "Demo Clinician",
    "outsider": "Unrelated User",
}


class APIError(Exception):
    def __init__(self, message: str, status: int = 400):
        self.message = message
        self.status = status


def create_app(dev: bool = False, data_dir: Path | None = None) -> Flask:
    app = Flask(__name__)
    directory = Path(data_dir or os.getenv("GRANT_DATA_DIR", ROOT / ".grant-wallets"))
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    db_path = directory / "wallets.sqlite3"
    vault_dir = directory / "vault_objects"
    vault_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    vault_local_tokens: dict[str, dict] = {}

    origins = {
        o.strip()
        for o in os.getenv(
            "FRONTEND_ORIGINS",
            "http://localhost:8080,http://127.0.0.1:8080,http://127.0.0.1:5173,http://localhost:5173",
        ).split(",")
        if o.strip()
    }

    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            wallet TEXT NOT NULL UNIQUE,
            x25519_public_key TEXT,
            updated_at REAL NOT NULL
        )
        """
    )
    conn.commit()
    db_path.chmod(0o600)

    def db() -> sqlite3.Connection:
        return conn

    @app.errorhandler(APIError)
    def api_error(error: APIError):
        return jsonify(error=error.message), error.status

    @app.errorhandler(HTTPException)
    def http_error(error: HTTPException):
        return jsonify(error=error.description), error.code

    @app.errorhandler(Exception)
    def unexpected(_error: Exception):
        app.logger.exception("wallet_api failure")
        return jsonify(error="Wallet service unavailable."), 503

    @app.after_request
    def cors(response: Response):
        origin = request.headers.get("Origin")
        if origin and origin in origins:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        return response

    @app.before_request
    def authenticate():
        if request.method == "OPTIONS":
            return jsonify(ok=True)
        if request.path == "/api/vault/local-object":
            return None
        if request.path == "/api/health":
            return None
        if request.path == "/api/faucet/status":
            return None

        origin = request.headers.get("Origin")
        if origin and origin not in origins:
            raise APIError("Origin not allowed", 403)

        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            raise APIError("Missing bearer token", 401)
        token = auth[7:].strip()

        if dev:
            if request.remote_addr not in ("127.0.0.1", "::1") or request.host.split(":")[0] not in (
                "localhost",
                "127.0.0.1",
            ):
                raise APIError("Demo auth is loopback-only", 403)
            if token.startswith("demo-"):
                uid = token[5:]
                if uid not in DEMO_USERS:
                    raise APIError("Unknown demo user", 401)
                g.uid = uid
                g.name = DEMO_USERS[uid]
                return None
            raise APIError("Use demo-<id> bearer in --dev", 401)

        supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
        supabase_key = os.getenv("SUPABASE_PUBLISHABLE_KEY", "")
        if not supabase_url or not supabase_key:
            raise APIError("SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY required", 503)
        import urllib.request

        req = urllib.request.Request(
            f"{supabase_url}/auth/v1/user",
            headers={"Authorization": f"Bearer {token}", "apikey": supabase_key},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                import json as _json

                user = _json.loads(resp.read().decode())
        except Exception as exc:
            raise APIError("Invalid session", 401) from exc
        g.uid = user["id"]
        meta = user.get("user_metadata") or {}
        g.name = meta.get("name") or meta.get("full_name") or user.get("email") or g.uid

    def local_vault_path(user_id: str) -> Path:
        path = vault_dir / user_id / VAULT_OBJECT_NAME
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        return path

    def vault_exists(user_id: str) -> bool:
        return local_vault_path(user_id).is_file()

    def issue_local_vault_url(operation: str) -> str:
        now = time.time()
        expired = [t for t, meta in vault_local_tokens.items() if meta["exp"] <= now]
        for t in expired:
            vault_local_tokens.pop(t, None)
        token = secrets.token_urlsafe(24)
        vault_local_tokens[token] = {"uid": g.uid, "op": operation, "exp": now + VAULT_PRESIGN_TTL_S}
        return request.url_root.rstrip("/") + "/api/vault/local-object?token=" + token

    def user_row(uid: str | None = None):
        uid = uid or g.uid
        return db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()

    @app.get("/api/health")
    def health():
        faucet_ok = False
        try:
            if faucet_client and os.getenv("FAUCET_ADDRESS"):
                faucet_client.resolve_faucet_address()
                faucet_ok = True
        except Exception:
            faucet_ok = False
        return jsonify(ok=True, chain=bool(os.getenv("RPC_URL")), faucet=faucet_ok, dev=dev)

    @app.post("/api/enroll")
    def enroll():
        body = request.get_json(silent=True) or {}
        wallet = (body.get("evmAddress") or body.get("wallet") or "").strip()
        x25519 = (body.get("x25519PublicKeyB64") or body.get("x25519_public_key") or None)
        name = (body.get("name") or g.name or g.uid).strip()

        if not wallet:
            # Idempotent read if already enrolled (legacy AccessPanel POST without body).
            row = user_row()
            if not row:
                raise APIError("evmAddress is required for first enroll", 400)
            return jsonify(id=row["id"], name=row["name"], wallet=row["wallet"], funded=None)

        if not is_address(wallet):
            raise APIError("Invalid evmAddress", 400)
        wallet = to_checksum_address(wallet)

        # Reject private-key shaped payloads if a client sends them by mistake.
        for banned in ("evmPrivateKeyHex", "private_key", "privateKey", "wallet_key"):
            if banned in body:
                raise APIError("Private keys must not be sent to the API", 400)

        existing = db().execute("SELECT id FROM users WHERE wallet=? AND id!=?", (wallet, g.uid)).fetchone()
        if existing:
            raise APIError("evmAddress already enrolled to another account", 409)

        now = time.time()
        db().execute(
            """
            INSERT INTO users (id, name, wallet, x25519_public_key, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              name=excluded.name,
              wallet=excluded.wallet,
              x25519_public_key=COALESCE(excluded.x25519_public_key, users.x25519_public_key),
              updated_at=excluded.updated_at
            """,
            (g.uid, name, wallet, x25519, now),
        )
        conn.commit()

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
            id=g.uid,
            name=name,
            wallet=wallet,
            funded=funded,
            faucet=drip_info,
        )

    @app.get("/api/users")
    def users():
        rows = db().execute(
            "SELECT id, name, wallet, x25519_public_key FROM users ORDER BY name"
        ).fetchall()
        return jsonify(
            [
                {
                    "id": r["id"],
                    "name": r["name"],
                    "wallet": r["wallet"],
                    "x25519PublicKeyB64": r["x25519_public_key"],
                }
                for r in rows
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
        row = user_row()
        if not row:
            raise APIError("Enroll a vault wallet before requesting faucet funds", 400)
        if not faucet_client or not os.getenv("FAUCET_ADDRESS"):
            raise APIError("Faucet is not configured", 503)
        try:
            result = faucet_client.ensure_funded(row["wallet"])
        except Exception as exc:
            raise APIError(f"Could not fund wallet: {exc}", 503) from exc
        return jsonify(result)

    @app.get("/api/vault/status")
    def vault_status():
        return jsonify(
            exists=vault_exists(g.uid),
            user_id=g.uid,
            object_key=f"vaults/{g.uid}/{VAULT_OBJECT_NAME}",
            backend="local",
        )

    @app.post("/api/vault/presign-put")
    def vault_presign_put():
        key = f"vaults/{g.uid}/{VAULT_OBJECT_NAME}"
        return jsonify(
            url=issue_local_vault_url("put"),
            method="PUT",
            object_key=key,
            backend="local",
            headers={"Content-Type": "application/json"},
        )

    @app.post("/api/vault/presign-get")
    def vault_presign_get():
        if not vault_exists(g.uid):
            raise APIError("No vault keystore found for this account.", 404)
        key = f"vaults/{g.uid}/{VAULT_OBJECT_NAME}"
        return jsonify(
            url=issue_local_vault_url("get"),
            method="GET",
            object_key=key,
            backend="local",
            headers={},
        )

    @app.route("/api/vault/local-object", methods=["GET", "PUT"])
    def vault_local_object():
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


def main() -> int:
    parser = argparse.ArgumentParser(description="MediLink wallet + faucet API")
    parser.add_argument("--dev", action="store_true", help="Accept demo-* bearer tokens on loopback")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "5001")))
    parser.add_argument("--data-dir", default=None)
    args = parser.parse_args()
    app = create_app(dev=args.dev, data_dir=Path(args.data_dir) if args.data_dir else None)
    app.run(host=args.host, port=args.port, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
