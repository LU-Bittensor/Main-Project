"""
Hippius EHR envelope helpers: encrypt payload, per-user DEK wraps, put/get/presign.

Layout:
  ehr/{tenant}/{patient_id}/{record_id}/v{n}/payload.bin
  ehr/{tenant}/{patient_id}/{record_id}/v{n}/wraps/{grantee_h160}.bin

Without HIPPIUS_* credentials, uses a local directory (EHR_LOCAL_DIR, default
key-generation/.ehr_local/) so demos work offline.

Usage:
  python key-generation/ehr_hippius.py demo
  python key-generation/ehr_hippius.py test
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sys

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from web3 import Web3

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# Reuse DH / registry helpers
from access_registry_client import (  # noqa: E402
    CONTRACT_ADDRESS,
    OWNER_PRIVATE_KEY,
    USER_PRIVATE_KEY,
    DHUser,
    derive_kek,
    grant_access,
    register_resource,
    request_access,
    resource_id_bytes,
)

try:
    import boto3
    from botocore.config import Config as BotocoreConfig
    from botocore.exceptions import ClientError
except ImportError:  # pragma: no cover
    boto3 = None
    BotocoreConfig = None
    ClientError = Exception

HIPPIUS_ACCESS_KEY = os.getenv("HIPPIUS_ACCESS_KEY")
HIPPIUS_SECRET_KEY = os.getenv("HIPPIUS_SECRET_KEY")
HIPPIUS_BUCKET = os.getenv("HIPPIUS_BUCKET")
URL_EXPIRATION = int(os.getenv("URL_EXPIRATION", "300"))
EHR_LOCAL_DIR = Path(os.getenv("EHR_LOCAL_DIR", str(Path(__file__).resolve().parent / ".ehr_local")))

ALG = "AES-256-GCM"


def payload_key(tenant: str, patient_id: str, record_id: str, version: int = 1) -> str:
    return f"ehr/{tenant}/{patient_id}/{record_id}/v{version}/payload.bin"


def wrap_key(tenant: str, patient_id: str, record_id: str, grantee: str, version: int = 1) -> str:
    h160 = Web3.to_checksum_address(grantee)[2:].lower()
    return f"ehr/{tenant}/{patient_id}/{record_id}/v{version}/wraps/{h160}.bin"


@dataclass
class EnvelopeResult:
    resource_id: bytes
    data_cid: str
    data_hash: bytes
    dek: bytes
    payload_bytes: bytes
    payload_obj: dict[str, Any]


def encrypt_ehr_payload(plaintext: bytes, resource_id: bytes) -> tuple[bytes, dict[str, Any], bytes]:
    """
    Encrypt EHR with a fresh DEK. Returns (dek, envelope_dict, serialized_payload_bytes).
    AAD binds ciphertext to resource_id.
    """
    dek = AESGCM.generate_key(bit_length=256)
    nonce = secrets.token_bytes(12)
    aead = AESGCM(dek)
    aad = resource_id
    ciphertext = aead.encrypt(nonce, plaintext, aad)
    envelope = {
        "alg": ALG,
        "nonce": nonce.hex(),
        "ciphertext": ciphertext.hex(),
        "resource_id": "0x" + resource_id.hex(),
        "aad": "0x" + resource_id.hex(),
    }
    payload_bytes = json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return dek, envelope, payload_bytes


def wrap_dek_for_grantee(
    dek: bytes, shared_secret: bytes, resource_id: bytes, grantee: str
) -> bytes:
    """AES-GCM wrap DEK under KEK_u; returns serialized wrap sidecar bytes."""
    kek = derive_kek(shared_secret, resource_id, grantee)
    nonce = secrets.token_bytes(12)
    wrapped = AESGCM(kek).encrypt(nonce, dek, resource_id)
    sidecar = {
        "wrapped_dek": wrapped.hex(),
        "wrap_nonce": nonce.hex(),
        "grantee": Web3.to_checksum_address(grantee),
        "resource_id": "0x" + resource_id.hex(),
    }
    return json.dumps(sidecar, separators=(",", ":"), sort_keys=True).encode("utf-8")


def unwrap_dek(wrap_bytes: bytes, shared_secret: bytes, resource_id: bytes, grantee: str) -> bytes:
    sidecar = json.loads(wrap_bytes.decode("utf-8"))
    kek = derive_kek(shared_secret, resource_id, grantee)
    nonce = bytes.fromhex(sidecar["wrap_nonce"])
    wrapped = bytes.fromhex(sidecar["wrapped_dek"])
    return AESGCM(kek).decrypt(nonce, wrapped, resource_id)


def decrypt_ehr_payload(payload_bytes: bytes, dek: bytes) -> bytes:
    envelope = json.loads(payload_bytes.decode("utf-8"))
    if envelope.get("alg") != ALG:
        raise ValueError(f"Unsupported alg: {envelope.get('alg')}")
    resource_id = bytes.fromhex(envelope["resource_id"][2:] if envelope["resource_id"].startswith("0x") else envelope["resource_id"])
    nonce = bytes.fromhex(envelope["nonce"])
    ciphertext = bytes.fromhex(envelope["ciphertext"])
    return AESGCM(dek).decrypt(nonce, ciphertext, resource_id)


def data_hash_of_payload(payload_bytes: bytes) -> bytes:
    return Web3.keccak(payload_bytes)


def _use_hippius() -> bool:
    return bool(HIPPIUS_ACCESS_KEY and HIPPIUS_SECRET_KEY and HIPPIUS_BUCKET and boto3 is not None)


def get_s3_client():
    if not _use_hippius():
        raise RuntimeError("Hippius credentials not configured")
    return boto3.client(
        "s3",
        endpoint_url="https://s3.hippius.com",
        aws_access_key_id=HIPPIUS_ACCESS_KEY,
        aws_secret_access_key=HIPPIUS_SECRET_KEY,
        region_name="decentralized",
        config=BotocoreConfig(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def put_object(key: str, body: bytes) -> str:
    """Upload to Hippius or local mirror. Returns storage backend label."""
    if _use_hippius():
        get_s3_client().put_object(Bucket=HIPPIUS_BUCKET, Key=key, Body=body)
        return f"s3://{HIPPIUS_BUCKET}/{key}"
    path = EHR_LOCAL_DIR / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return str(path)


def get_object(key: str) -> bytes:
    if _use_hippius():
        resp = get_s3_client().get_object(Bucket=HIPPIUS_BUCKET, Key=key)
        return resp["Body"].read()
    path = EHR_LOCAL_DIR / key
    if not path.exists():
        raise FileNotFoundError(path)
    return path.read_bytes()


def delete_object(key: str) -> None:
    if _use_hippius():
        get_s3_client().delete_object(Bucket=HIPPIUS_BUCKET, Key=key)
        return
    path = EHR_LOCAL_DIR / key
    if path.exists():
        path.unlink()


def presign_get(key: str, expiry: int | None = None) -> str:
    """Gateway-style short-lived GET. Local backend returns file:// URI."""
    expiry = expiry if expiry is not None else URL_EXPIRATION
    if _use_hippius():
        try:
            return get_s3_client().generate_presigned_url(
                "get_object",
                Params={"Bucket": HIPPIUS_BUCKET, "Key": key},
                ExpiresIn=expiry,
            )
        except ClientError as e:
            raise RuntimeError(f"presign failed: {e}") from e
    path = (EHR_LOCAL_DIR / key).resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    return path.as_uri()


def store_ehr_record(
    plaintext: bytes,
    *,
    tenant: str,
    patient_id: str,
    record_id: str,
    version: int = 1,
    resource_id: bytes | None = None,
) -> EnvelopeResult:
    data_cid = payload_key(tenant, patient_id, record_id, version)
    rid = resource_id or resource_id_bytes(None, data_cid)
    dek, envelope, payload_bytes = encrypt_ehr_payload(plaintext, rid)
    put_object(data_cid, payload_bytes)
    dhash = data_hash_of_payload(payload_bytes)
    return EnvelopeResult(
        resource_id=rid,
        data_cid=data_cid,
        data_hash=dhash,
        dek=dek,
        payload_bytes=payload_bytes,
        payload_obj=envelope,
    )


def put_wrap_sidecar(
    dek: bytes,
    shared_secret: bytes,
    resource_id: bytes,
    grantee: str,
    *,
    tenant: str,
    patient_id: str,
    record_id: str,
    version: int = 1,
) -> str:
    key = wrap_key(tenant, patient_id, record_id, grantee, version)
    body = wrap_dek_for_grantee(dek, shared_secret, resource_id, grantee)
    put_object(key, body)
    return key


def unlock_and_decrypt(
    shared_secret: bytes,
    resource_id: bytes,
    grantee: str,
    *,
    tenant: str,
    patient_id: str,
    record_id: str,
    version: int = 1,
) -> bytes:
    """After AccessGranted: fetch payload + wrap, unwrap DEK, decrypt."""
    p_key = payload_key(tenant, patient_id, record_id, version)
    w_key = wrap_key(tenant, patient_id, record_id, grantee, version)
    # Gateway would only mint these after AccessGranted
    _ = presign_get(p_key)
    _ = presign_get(w_key)
    payload_bytes = get_object(p_key)
    wrap_bytes = get_object(w_key)
    dek = unwrap_dek(wrap_bytes, shared_secret, resource_id, grantee)
    return decrypt_ehr_payload(payload_bytes, dek)


def run_offline_crypto_tests() -> None:
    print("=== ehr_hippius offline tests ===")
    rid = Web3.keccak(text="test-resource")
    plaintext = b'{"note":"lab result","value":42}'
    dek, _, payload_bytes = encrypt_ehr_payload(plaintext, rid)
    assert decrypt_ehr_payload(payload_bytes, dek) == plaintext

    grantee = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
    secret = secrets.token_bytes(32)
    wrap = wrap_dek_for_grantee(dek, secret, rid, grantee)
    assert unwrap_dek(wrap, secret, rid, grantee) == dek

    # Local store round-trip
    os.environ.pop("HIPPIUS_BUCKET", None)
    stored = store_ehr_record(
        plaintext, tenant="demo", patient_id="p1", record_id="r1", version=1, resource_id=rid
    )
    wrap_path = put_wrap_sidecar(
        stored.dek,
        secret,
        rid,
        grantee,
        tenant="demo",
        patient_id="p1",
        record_id="r1",
    )
    assert wrap_path.endswith(".bin")
    out = unlock_and_decrypt(
        secret, rid, grantee, tenant="demo", patient_id="p1", record_id="r1"
    )
    assert out == plaintext
    print("All ehr_hippius tests passed")


def run_demo() -> None:
    """
    Full path: encrypt → put → registerResource → grantAccess → verifyAccess → decrypt.
    Requires CONTRACT_ADDRESS + OWNER/USER keys. Uses local storage if no Hippius creds.
    """
    if not CONTRACT_ADDRESS:
        raise RuntimeError("CONTRACT_ADDRESS required (redeploy AccessRegistry first)")
    if not OWNER_PRIVATE_KEY:
        raise RuntimeError("OWNER_PRIVATE_KEY required")

    user_key = USER_PRIVATE_KEY or OWNER_PRIVATE_KEY
    w3 = Web3(Web3.HTTPProvider(os.getenv("RPC_URL", "http://127.0.0.1:8545")))
    grantee = w3.eth.account.from_key(user_key).address

    print("=== EHR Hippius + multi-grantee AccessRegistry demo ===")
    print(f"Backend: {'Hippius S3' if _use_hippius() else f'local {EHR_LOCAL_DIR}'}")
    print(f"Grantee: {grantee}")

    passphrase = os.getenv("DH_PASSPHRASE", "demo-passphrase")
    owner = DHUser.from_script("ehr_owner", overwrite=True, passphrase=passphrase)
    user = DHUser.from_script("ehr_user", overwrite=True, passphrase=passphrase)
    secret = owner.derive_shared_secret(user.get_public_key_pem())
    assert secret == user.derive_shared_secret(owner.get_public_key_pem())

    plaintext = b'{"patient":"demo","note":"secure EHR sample","labs":[1,2,3]}'
    tenant, patient_id, record_id = "demo", "patient-001", "encounter-42"
    stored = store_ehr_record(
        plaintext, tenant=tenant, patient_id=patient_id, record_id=record_id, version=1
    )
    print(f"Stored payload dataCid={stored.data_cid}")
    print(f"dataHash=0x{stored.data_hash.hex()}")

    register_resource(stored.resource_id, stored.data_hash, stored.data_cid)
    wrap_path = put_wrap_sidecar(
        stored.dek,
        secret,
        stored.resource_id,
        grantee,
        tenant=tenant,
        patient_id=patient_id,
        record_id=record_id,
    )
    print(f"Wrap sidecar: {wrap_path}")
    grant_access(stored.resource_id, grantee, secret)

    result = request_access(secret, stored.resource_id, user_key)
    print(f"Access result: {result}")
    if result is None:
        raise RuntimeError("AccessDenied — abort decrypt")

    recovered = unlock_and_decrypt(
        secret,
        stored.resource_id,
        grantee,
        tenant=tenant,
        patient_id=patient_id,
        record_id=record_id,
    )
    assert recovered == plaintext
    print(f"Decrypted EHR: {recovered.decode()}")
    print("Demo OK")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        run_offline_crypto_tests()
    else:
        run_demo()
