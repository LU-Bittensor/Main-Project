"""Vault enroll + faucet ensure API tests (faucet chain calls mocked)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from grant_api import create_app


@pytest.fixture()
def client(tmp_path: Path):
    app = create_app(dev=True, data_dir=tmp_path)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def auth(user: str = "patient"):
    return {"Authorization": f"Bearer demo-{user}", "Content-Type": "application/json"}


def test_enroll_empty_body_creates_custodial(client):
    resp = client.post("/api/enroll", headers=auth())
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["id"] == "patient"
    assert body["wallet"].startswith("0x")


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

    with mock.patch("grant_api.faucet_client", None):
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
    assert body["x25519PublicKeyB64"] == "AAAA"

    users = client.get("/api/users", headers=auth("clinician")).get_json()
    patient = next(u for u in users if u["id"] == "patient")
    assert patient["wallet"] == body["wallet"]
    assert patient["x25519PublicKeyB64"] == "AAAA"


def test_faucet_ensure_uses_enrolled_wallet(client, monkeypatch):
    monkeypatch.setenv("FAUCET_ADDRESS", "0x00000000000000000000000000000000000000fA")

    fake = mock.Mock()
    fake.ensure_funded.return_value = {
        "address": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        "balance": 10**16,
        "dripped": True,
        "txHash": "0xabc",
    }
    with mock.patch("grant_api.faucet_client", fake):
        client.post(
            "/api/enroll",
            headers=auth(),
            data=json.dumps({"evmAddress": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"}),
        )
        resp = client.post("/api/faucet/ensure", headers=auth())
    assert resp.status_code == 200
    assert resp.get_json()["dripped"] is True
    fake.ensure_funded.assert_called()


def test_health_reports_faucet_when_configured(client, monkeypatch):
    monkeypatch.setenv("FAUCET_ADDRESS", "0x00000000000000000000000000000000000000fA")
    fake = mock.Mock()
    fake.resolve_faucet_address.return_value = "0x00000000000000000000000000000000000000fA"
    with mock.patch("grant_api.faucet_client", fake):
        health = client.get("/api/health").get_json()
    assert health["faucet"] is True
    assert health["mode"] == "local-demo"
