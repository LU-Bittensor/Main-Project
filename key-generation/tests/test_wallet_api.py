"""Enroll + faucet ensure API tests (faucet chain calls mocked)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from wallet_api import create_app


@pytest.fixture()
def client(tmp_path: Path):
    app = create_app(dev=True, data_dir=tmp_path)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def auth(user: str = "patient"):
    return {"Authorization": f"Bearer demo-{user}", "Content-Type": "application/json"}


def test_enroll_requires_address_first_time(client):
    resp = client.post("/api/enroll", headers=auth())
    assert resp.status_code == 400


def test_enroll_stores_public_wallet_and_rejects_private_key(client):
    bad = client.post(
        "/api/enroll",
        headers=auth(),
        data=json.dumps(
            {
                "evmAddress": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
                "evmPrivateKeyHex": "0xdead",
            }
        ),
    )
    assert bad.status_code == 400

    with mock.patch("wallet_api.faucet_client", None):
        ok = client.post(
            "/api/enroll",
            headers=auth(),
            data=json.dumps(
                {
                    "evmAddress": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
                    "x25519PublicKeyB64": "AAAA",
                }
            ),
        )
    assert ok.status_code == 200
    body = ok.get_json()
    assert body["wallet"] == "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
    assert body["id"] == "patient"

    users = client.get("/api/users", headers=auth()).get_json()
    assert any(u["wallet"] == body["wallet"] for u in users)


def test_vault_round_trip(client):
    status = client.get("/api/vault/status", headers=auth()).get_json()
    assert status["exists"] is False
    put = client.post("/api/vault/presign-put", headers=auth()).get_json()
    blob = {"version": 1, "userId": "patient", "kdf": {"salt_b64": "YQ=="}, "cipher": {"ciphertext_b64": "Yg=="}}
    uploaded = client.put(put["url"], data=json.dumps(blob), headers={"Content-Type": "application/json"})
    assert uploaded.status_code == 200
    assert client.get("/api/vault/status", headers=auth()).get_json()["exists"] is True
    get = client.post("/api/vault/presign-get", headers=auth()).get_json()
    downloaded = client.get(get["url"])
    assert downloaded.status_code == 200
    assert downloaded.get_json()["userId"] == "patient"


def test_faucet_ensure_uses_enrolled_wallet(client, monkeypatch):
    monkeypatch.setenv("FAUCET_ADDRESS", "0x00000000000000000000000000000000000000fA")

    fake = mock.Mock()
    fake.ensure_funded.return_value = {
        "address": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        "balance": 10**16,
        "dripped": True,
        "txHash": "0xabc",
    }
    with mock.patch("wallet_api.faucet_client", fake):
        client.post(
            "/api/enroll",
            headers=auth(),
            data=json.dumps({"evmAddress": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"}),
        )
        resp = client.post("/api/faucet/ensure", headers=auth())
    assert resp.status_code == 200
    assert resp.get_json()["dripped"] is True
    fake.ensure_funded.assert_called()
