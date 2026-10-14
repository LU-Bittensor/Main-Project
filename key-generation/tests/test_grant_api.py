import json
import sqlite3
from types import SimpleNamespace

import pytest
from cryptography.exceptions import InvalidTag

import grant_api
from grant_api import create_app
from DHkey_share import decrypt_secret, derive_shared_secret, encrypt_secret, generate_key_pair


def test_local_data_directory_tracks_hardhat_instance_and_build(tmp_path, monkeypatch):
    monkeypatch.setenv("GRANT_DATA_DIR", str(tmp_path))
    metadata = {"result": {"instanceId": "first-instance"}}
    chain = SimpleNamespace(chain_id=31337, identity=[31337, "genesis", "owner", "build"],
                            w3=SimpleNamespace(provider=SimpleNamespace(
                                make_request=lambda method, params: metadata)))
    first = grant_api.local_chain_data_dir(chain)
    assert first.parent == tmp_path
    assert first == grant_api.local_chain_data_dir(chain)
    metadata["result"]["instanceId"] = "restarted-instance"
    assert first != grant_api.local_chain_data_dir(chain)
    metadata["result"]["instanceId"] = "first-instance"
    chain.identity[-1] = "new-build"
    assert first != grant_api.local_chain_data_dir(chain)
    metadata.clear()
    with pytest.raises(RuntimeError, match="Cannot identify"):
        grant_api.local_chain_data_dir(chain)
    chain.chain_id = 1
    with pytest.raises(RuntimeError, match="local Hardhat"):
        grant_api.local_chain_data_dir(chain)


def headers(user="patient"):
    return {"Authorization": "Bearer demo-" + user}


def make_grants(client, user="patient", recipient="clinician", records=None):
    return client.post("/api/grants", headers=headers(user), json={
        "recipient_id": recipient, "record_ids": records if records is not None else ["demo-lab"]})


def make_grant(client, **kwargs):
    return make_grants(client, **kwargs).get_json()["grants"][0]


@pytest.fixture
def setup(tmp_path):
    app = create_app(dev=True, data_dir=tmp_path, master_key=b"k" * 32)
    return app.test_client(), tmp_path


def test_grant_access_revoke_and_restart(setup):
    client, directory = setup
    grant = make_grant(client)
    assert grant["status"] == "active" and not grant["chain_enabled"]
    grant_id = grant["id"]
    assert make_grant(client)["id"] == grant_id
    check = client.post(f"/api/grants/{grant_id}/access", headers=headers("clinician"))
    assert check.status_code == 200 and check.get_json()["key_exchange_verified"]
    assert check.get_json()["chain_verified"] is False
    restarted = create_app(dev=True, data_dir=directory, master_key=b"k" * 32).test_client()
    assert restarted.get("/api/grants", headers=headers()).get_json()[0]["id"] == grant_id
    assert restarted.post(f"/api/grants/{grant_id}/access", headers=headers("clinician")).status_code == 200
    assert client.delete(f"/api/grants/{grant_id}", headers=headers()).get_json()["status"] == "revoked"
    assert client.post(f"/api/grants/{grant_id}/access", headers=headers("clinician")).status_code == 403
    assert make_grant(client)["id"] != grant_id


def test_permissions_and_inputs(setup):
    client, _ = setup
    assert client.get("/api/grants").status_code == 401
    assert client.get("/api/grants", headers=headers("fake")).status_code == 401
    assert make_grants(client, user="outsider").status_code == 403
    assert make_grants(client, records=["other-patients-record"]).status_code == 403
    assert make_grants(client, recipient="patient").status_code == 400
    assert make_grants(client, recipient="missing").status_code == 404
    assert client.post("/api/grants", headers=headers(), json=[]).status_code == 400
    assert make_grants(client, records=[]).status_code == 400
    grant_id = make_grant(client)["id"]
    assert client.post(f"/api/grants/{grant_id}/access", headers=headers()).status_code == 403
    assert client.post(f"/api/grants/{grant_id}/access", headers=headers("outsider")).status_code == 404
    assert client.delete(f"/api/grants/{grant_id}", headers=headers("clinician")).status_code == 403
    assert client.post(f"/api/grants/{grant_id}/resume", headers=headers("clinician")).status_code == 403
    assert client.get("/api/grants", headers=headers("outsider")).get_json() == []
    assert client.get("/api/grants", headers={**headers(), "Origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/grants", headers=headers(), environ_base={"REMOTE_ADDR": "10.0.0.1"}).status_code == 403


def test_vault_presign_acl_and_local_round_trip(setup):
    client, directory = setup
    assert client.get("/api/vault/status").status_code == 401
    status = client.get("/api/vault/status", headers=headers("patient"))
    assert status.status_code == 200
    body = status.get_json()
    assert body["exists"] is False
    assert body["user_id"] == "patient"
    assert body["object_key"] == "vaults/patient/keystore.v1.json"
    assert body["backend"] == "local"

    put = client.post("/api/vault/presign-put", headers=headers("patient"))
    assert put.status_code == 200
    put_body = put.get_json()
    assert put_body["method"] == "PUT"
    assert put_body["object_key"] == "vaults/patient/keystore.v1.json"
    assert "token=" in put_body["url"]

    # Another user's JWT cannot obtain a put URL for the patient's path — path is always g.uid.
    other = client.post("/api/vault/presign-put", headers=headers("clinician")).get_json()
    assert other["object_key"] == "vaults/clinician/keystore.v1.json"
    assert other["object_key"] != put_body["object_key"]

    from urllib.parse import urlparse

    payload = json.dumps({"version": 1, "userId": "patient", "cipher": {"ciphertext_b64": "dGVzdA=="}}).encode()
    parsed = urlparse(put_body["url"])
    uploaded = client.put(
        parsed.path + "?" + parsed.query,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    assert uploaded.status_code == 200
    assert (directory / "vault_objects" / "patient" / "keystore.v1.json").is_file()

    # Token is one-shot; reuse fails.
    assert client.put(parsed.path + "?" + parsed.query, data=payload).status_code == 401

    assert client.get("/api/vault/status", headers=headers("patient")).get_json()["exists"] is True
    get = client.post("/api/vault/presign-get", headers=headers("patient")).get_json()
    get_parsed = urlparse(get["url"])
    downloaded = client.get(get_parsed.path + "?" + get_parsed.query)
    assert downloaded.status_code == 200
    assert downloaded.get_json()["userId"] == "patient"

    # Clinician has no vault of their own yet.
    assert client.post("/api/vault/presign-get", headers=headers("clinician")).status_code == 404


def test_pair_granularity_overlap_and_encrypted_storage(setup):
    client, directory = setup
    first = make_grant(client)
    batch = make_grants(client, records=["demo-lab", "demo-imaging", "demo-lab"]).get_json()["grants"]
    assert len(batch) == 2
    assert next(g for g in batch if g["record_ids"] == ["demo-lab"])["id"] == first["id"]
    second = make_grant(client, recipient="outsider")
    assert first["id"] != second["id"]
    assert "secret" not in first and "private_key" not in json.dumps(first)
    with sqlite3.connect(directory / "registry-grants.sqlite3") as db:
        private, public, wallet = db.execute("SELECT private_key, public_key, wallet_key FROM users WHERE id='patient'").fetchone()
        envelope = db.execute("SELECT secret FROM grants WHERE id=?", (first["id"],)).fetchone()[0]
        assert len(private) > 32 and len(envelope) > 32 and len(public) == 32 and len(wallet) > 32
    client.delete(f"/api/grants/{first['id']}", headers=headers())
    assert client.post(f"/api/grants/{second['id']}/access", headers=headers("outsider")).status_code == 200


def test_dh_binding_and_tamper_detection():
    a, ap = generate_key_pair()
    b, bp = generate_key_pair()
    shared = derive_shared_secret(a, bp, "pair-one")
    assert shared == derive_shared_secret(b, ap, "pair-one")
    assert shared != derive_shared_secret(a, bp, "pair-two")
    encrypted = encrypt_secret(b"k" * 32, shared, "pair-one")
    with pytest.raises(InvalidTag):
        decrypt_secret(b"k" * 32, encrypted, "pair-two")


class FakeChain:
    identity = [31337, "test-genesis", "operator", "bytecode"]

    def __init__(self):
        self.receipts = {}
        self.contracts = {}
        self.deploy_count = 0
        self.register_count = 0
        self.access_count = 0
        self.timeout_deploy = False
        self.timeout_register = False
        self.match = True
        self.allow = True
        self.revoked = set()
        self.resources = {}
        self.granted = {}
        self.revoke_count = 0
        self.revoke_failure = None

    registry_address = None

    def validate_registry(self, address):
        assert address in self.contracts

    def deploy(self, save_hash):
        self.deploy_count += 1
        tx = "deploy-" + str(self.deploy_count)
        address = "address-" + str(self.deploy_count)
        self.contracts[address] = True
        save_hash(tx)
        self.receipts[tx] = {"status": 1, "contractAddress": address}
        if self.timeout_deploy:
            raise TimeoutError()
        return self.receipts[tx]

    def resource_matches(self, address, resource, data_hash, cid):
        return resource in self.resources

    def register_resource(self, address, resource, data_hash, cid, save_hash):
        tx = "resource-" + resource.hex()
        save_hash(tx)
        self.resources[resource] = (data_hash, cid)
        self.receipts[tx] = {"status": 1}

    def register(self, address, resource, wallet, secret, save_hash):
        self.register_count += 1
        tx = "register-" + str(self.register_count)
        save_hash(tx)
        self.granted[(resource, wallet)] = secret
        self.revoked.discard((resource, wallet))
        self.receipts[tx] = {"status": 1}
        if self.timeout_register:
            raise TimeoutError()
        return self.receipts[tx]

    def has_grant(self, address, resource, wallet):
        if self.revoke_failure == "offline":
            raise ConnectionError()
        return (resource, wallet) in self.granted and (resource, wallet) not in self.revoked

    def revoke(self, address, resource, wallet, save_hash):
        if self.revoke_failure == "before_send":
            raise ConnectionError()
        self.revoke_count += 1
        tx = "revoke-" + str(self.revoke_count)
        save_hash(tx)
        if self.revoke_failure == "timeout":
            raise TimeoutError()
        self.receipts[tx] = {"status": 1}
        self.revoked.add((resource, wallet))
        return self.receipts[tx]

    def receipt(self, tx):
        return self.receipts.get(tx)

    def matches(self, address, resource, wallet, secret):
        return self.match and self.granted.get((resource, wallet)) == secret

    def request_access(self, *args):
        self.access_count += 1
        return {"allowed": self.allow, "challenge_tx": "challenge", "verify_tx": "verify"}


def test_shared_registry_and_access_every_time(tmp_path):
    chain = FakeChain()
    client = create_app(dev=True, data_dir=tmp_path, master_key=b"k" * 32, chain=chain).test_client()
    grants = make_grants(client, records=["demo-lab", "demo-imaging"]).get_json()["grants"]
    assert len({g["contract_address"] for g in grants}) == 1
    assert all(g["status"] == "active" for g in grants)
    assert chain.deploy_count == 1 and chain.register_count == 2
    lab = next(g for g in grants if g["record_ids"] == ["demo-lab"])
    assert make_grant(client)["id"] == lab["id"]
    assert chain.deploy_count == 1
    for _ in range(2):
        response = client.post(f"/api/grants/{lab['id']}/access", headers=headers("clinician"))
        assert response.status_code == 200 and response.get_json()["chain_verified"]
    assert chain.access_count == 2
    chain.allow = False
    assert client.post(f"/api/grants/{lab['id']}/access", headers=headers("clinician")).status_code == 403
    client.delete(f"/api/grants/{lab['id']}", headers=headers())
    assert client.post(f"/api/grants/{lab['id']}/access", headers=headers("clinician")).status_code == 403
    assert chain.access_count == 3  # Revoked access did not reach the chain.


def test_deploy_timeout_resume_after_restart_without_duplicate(tmp_path):
    chain = FakeChain()
    chain.timeout_deploy = True
    client = create_app(dev=True, data_dir=tmp_path, master_key=b"k" * 32, chain=chain).test_client()
    grant = make_grant(client)
    assert grant["status"] == "pending" and not grant["register_tx"]
    refreshed = client.get("/api/grants", headers=headers()).get_json()[0]
    assert refreshed["status"] == "pending"  # Deployment alone never grants access.
    assert chain.register_count == 0  # GET does not submit a transaction.
    restarted = create_app(dev=True, data_dir=tmp_path, master_key=b"k" * 32, chain=chain).test_client()
    resumed = restarted.post(f"/api/grants/{grant['id']}/resume", headers=headers()).get_json()
    assert resumed["status"] == "active" and chain.deploy_count == 1 and chain.register_count == 1


def test_registration_timeout_and_revert(tmp_path):
    chain = FakeChain()
    chain.timeout_register = True
    client = create_app(dev=True, data_dir=tmp_path, master_key=b"k" * 32, chain=chain).test_client()
    grant = make_grant(client)
    assert grant["status"] == "pending"
    receipt = chain.receipts.pop(grant["register_tx"])
    assert client.get("/api/grants", headers=headers()).get_json()[0]["status"] == "pending"
    assert client.post(f"/api/grants/{grant['id']}/resume", headers=headers()).get_json()["status"] == "pending"
    assert chain.register_count == 1
    chain.receipts[grant["register_tx"]] = {**receipt, "status": 0}
    assert client.get("/api/grants", headers=headers()).get_json()[0]["status"] == "failed"


def test_pending_revocation_never_reactivates(tmp_path):
    chain = FakeChain()
    chain.timeout_register = True
    client = create_app(dev=True, data_dir=tmp_path, master_key=b"k" * 32, chain=chain).test_client()
    grant = make_grant(client)
    client.delete(f"/api/grants/{grant['id']}", headers=headers())
    assert client.post(f"/api/grants/{grant['id']}/resume", headers=headers()).get_json()["status"] == "revoked"
    assert client.get("/api/grants", headers=headers()).get_json()[0]["status"] == "revoked"


def test_mismatched_master_key_and_mode_rejected(setup):
    _, directory = setup
    with pytest.raises(RuntimeError, match="does not match"):
        create_app(dev=True, data_dir=directory, master_key=b"x" * 32)
    with pytest.raises(RuntimeError, match="different auth/chain configuration"):
        create_app(dev=True, data_dir=directory, master_key=b"k" * 32, chain=FakeChain())


def test_real_account_rejected_by_demo_with_configuration_guidance(setup):
    client, _ = setup
    response = client.get("/api/records", headers={"Authorization": "Bearer real-session-token"})
    assert response.status_code == 401
    assert "without --dev" in response.get_json()["error"]
    assert "not a missing-records error" in response.get_json()["error"]


def test_supabase_auth_is_verified_and_ownership_filtered(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "publishable")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_test")
    uid = "00000000-0000-0000-0000-000000000001"
    def get(url, **kwargs):
        if url.endswith("/auth/v1/user"):
            if kwargs["headers"]["Authorization"] == "Bearer invalid":
                return SimpleNamespace(status_code=401)
            body = {"id": uid, "user_metadata": {"full_name": "Patient"}}
        else:
            body = []
            assert kwargs["params"]["owner_id"] == "eq." + uid
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: body)
    monkeypatch.setattr(grant_api.requests, "get", get)
    monkeypatch.setattr(grant_api.requests, "request", lambda method, url, **kwargs: get(url, **kwargs))
    client = create_app(data_dir=tmp_path, master_key=b"k" * 32).test_client()
    assert client.get("/api/records", headers={"Authorization": "Bearer invalid"}).status_code == 401
    assert client.post("/api/enroll", headers={"Authorization": "Bearer valid"}).get_json()["id"] == uid
    response = client.get("/api/records", headers={"Authorization": "Bearer valid"})
    assert response.status_code == 200
    assert response.get_json() == []


@pytest.fixture
def chain_setup(tmp_path):
    chain = FakeChain()
    client = create_app(dev=True, data_dir=tmp_path, master_key=b"k" * 32, chain=chain).test_client()
    return client, chain, tmp_path


def test_registry_revoke_confirms_idempotently_and_regrant_reuses_registry(chain_setup):
    client, chain, _ = chain_setup
    grant = make_grant(client)
    response = client.delete(f"/api/grants/{grant['id']}", headers=headers())
    result = response.get_json()
    assert response.status_code == 200 and result["registry_revoked"]
    assert result["revoke_tx"] and result["status"] == "revoked"
    assert (bytes.fromhex(grant["resource_id"][2:]), grant["recipient_wallet"]) in chain.revoked
    assert client.delete(f"/api/grants/{grant['id']}", headers=headers()).status_code == 200
    assert chain.revoke_count == 1
    new_grant = make_grant(client)
    assert new_grant["contract_address"] == grant["contract_address"]
    assert new_grant["status"] == "active"


@pytest.mark.parametrize("failure", ["offline", "before_send"])
def test_rpc_failure_blocks_access_and_allows_retry(chain_setup, failure):
    client, chain, _ = chain_setup
    grant = make_grant(client)
    chain.revoke_failure = failure
    response = client.delete(f"/api/grants/{grant['id']}", headers=headers())
    assert response.status_code == 202 and response.get_json()["status"] == "revoking"
    # The access endpoint must deny even when RPC cannot be reached.
    assert client.post(f"/api/grants/{grant['id']}/access", headers=headers("clinician")).status_code == 403
    assert client.post(f"/api/grants/{grant['id']}/resume", headers=headers()).get_json()["status"] == "revoking"
    assert make_grant(client)["id"] == grant["id"]  # No duplicate while revoking.
    chain.revoke_failure = None
    assert client.delete(f"/api/grants/{grant['id']}", headers=headers()).get_json()["registry_revoked"]


def test_registry_revoke_timeout_persists_across_restart_without_resubmission(chain_setup):
    client, chain, directory = chain_setup
    grant = make_grant(client)
    chain.revoke_failure = "timeout"
    result = client.delete(f"/api/grants/{grant['id']}", headers=headers()).get_json()
    assert result["status"] == "revoking" and result["revoke_tx"]
    restarted = create_app(dev=True, data_dir=directory, master_key=b"k" * 32, chain=chain).test_client()
    assert restarted.delete(f"/api/grants/{grant['id']}", headers=headers()).status_code == 202
    assert chain.revoke_count == 1
    chain.receipts[result["revoke_tx"]] = {"status": 1}
    chain.revoked.add((bytes.fromhex(grant["resource_id"][2:]), grant["recipient_wallet"]))
    current = restarted.get("/api/grants", headers=headers()).get_json()[0]
    assert current["status"] == "revoked" and current["registry_revoked"]
    assert chain.revoke_count == 1


def test_reverted_revoke_stays_blocked_until_retry(chain_setup):
    client, chain, _ = chain_setup
    grant = make_grant(client)
    chain.revoke_failure = "timeout"
    result = client.delete(f"/api/grants/{grant['id']}", headers=headers()).get_json()
    chain.receipts[result["revoke_tx"]] = {"status": 0}
    current = client.get("/api/grants", headers=headers()).get_json()[0]
    assert current["status"] == "revoking" and current["revoke_tx"] is None
    assert client.post(f"/api/grants/{grant['id']}/access", headers=headers("clinician")).status_code == 403
    chain.revoke_failure = None
    assert client.delete(f"/api/grants/{grant['id']}", headers=headers()).get_json()["registry_revoked"]
    assert chain.revoke_count == 2


def test_registry_revoke_success_receipt_without_disabled_state_does_not_mark_revoked(chain_setup):
    client, chain, _ = chain_setup
    grant = make_grant(client)
    chain.revoke_failure = "timeout"
    result = client.delete(f"/api/grants/{grant['id']}", headers=headers()).get_json()
    chain.receipts[result["revoke_tx"]] = {"status": 1}
    current = client.get("/api/grants", headers=headers()).get_json()[0]
    assert current["status"] == "revoking" and not current["registry_revoked"]


def test_revoke_before_registry_ready_never_registers(chain_setup):
    client, chain, _ = chain_setup
    chain.timeout_deploy = True
    grant = make_grant(client)
    result = client.delete(f"/api/grants/{grant['id']}", headers=headers()).get_json()
    assert result["status"] == "revoked" and not result["registry_revoked"]
    assert chain.register_count == chain.revoke_count == 0


def test_revoke_waits_for_pending_grant_transaction(chain_setup):
    client, chain, _ = chain_setup
    chain.timeout_register = True
    grant = make_grant(client)
    receipt = chain.receipts.pop(grant["register_tx"])
    assert client.delete(f"/api/grants/{grant['id']}", headers=headers()).status_code == 202
    assert chain.revoke_count == 0
    chain.receipts[grant["register_tx"]] = receipt
    assert client.delete(f"/api/grants/{grant['id']}", headers=headers()).get_json()["status"] == "revoked"
    assert chain.revoke_count == 1


def test_external_revoke_is_detected_before_access(chain_setup):
    client, chain, _ = chain_setup
    grant = make_grant(client)
    chain.revoked.add((bytes.fromhex(grant["resource_id"][2:]), grant["recipient_wallet"]))
    assert client.post(f"/api/grants/{grant['id']}/access", headers=headers("clinician")).status_code == 403
    assert chain.access_count == 0
    assert client.get("/api/grants", headers=headers()).get_json()[0]["registry_revoked"]


def test_resource_timeout_is_shared_across_recipients(chain_setup, monkeypatch):
    client, chain, _ = chain_setup
    original = chain.register_resource
    def timeout(*args):
        original(*args)
        raise TimeoutError()
    monkeypatch.setattr(chain, "register_resource", timeout)
    first = make_grant(client)
    assert first["status"] == "pending" and first["resource_tx"]
    receipt = chain.receipts.pop(first["resource_tx"])
    second = make_grant(client, recipient="outsider")
    assert second["status"] == "pending" and chain.register_count == 0
    assert chain.deploy_count == 1
    chain.receipts[first["resource_tx"]] = receipt
    for grant in [first, second]:
        result = client.post(f"/api/grants/{grant['id']}/resume", headers=headers()).get_json()
        assert result["status"] == "active"
        assert result["resource_id"] == first["resource_id"]
        assert result["contract_address"] == first["contract_address"]
    assert len(chain.resources) == 1 and chain.register_count == 2


def test_old_database_is_not_reused_or_deleted(tmp_path):
    old = tmp_path / "pair-grants.sqlite3"
    old.write_bytes(b"old pair data")
    create_app(dev=True, data_dir=tmp_path, master_key=b"k" * 32)
    assert old.read_bytes() == b"old pair data"
    assert (tmp_path / "registry-grants.sqlite3").exists()
