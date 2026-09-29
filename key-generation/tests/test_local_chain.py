"""Opt-in integration test: compile Solidity and start Hardhat at 127.0.0.1:8545."""
import os
import pytest
from chain_grants import ChainGrants
from grant_api import create_app

pytestmark = pytest.mark.skipif(os.getenv("RUN_CHAIN_TESTS") != "1", reason="requires local Hardhat node")


def test_registry_deployment_and_challenge_response(tmp_path):
    chain = ChainGrants("http://127.0.0.1:18545",
        "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80", fund_local_wallets=True)
    assert chain.chain_id == 31337
    app = create_app(dev=True, data_dir=tmp_path, master_key=b"k" * 32, chain=chain)
    client = app.test_client()
    owner = {"Authorization": "Bearer demo-patient"}
    recipient = {"Authorization": "Bearer demo-clinician"}
    payload = {"recipient_id": "clinician", "record_ids": ["demo-lab", "demo-imaging"]}
    grants = client.post("/api/grants", headers=owner, json=payload).get_json()["grants"]
    assert len(grants) == 2 and all(g["status"] == "active" for g in grants), grants
    assert len({g["contract_address"] for g in grants}) == 1
    assert all(g["deploy_tx"] != g["register_tx"] for g in grants)
    duplicate = client.post("/api/grants", headers=owner, json=payload).get_json()["grants"]
    assert {g["contract_address"] for g in duplicate} == {g["contract_address"] for g in grants}
    wallet = client.post("/api/enroll", headers=recipient).get_json()["wallet"]
    verification_hashes = set()
    for grant in grants:
        for _ in range(2):
            response = client.post(f"/api/grants/{grant['id']}/access", headers=recipient)
            assert response.status_code == 200, response.get_json()
            result = response.get_json()
            assert result["allowed"] and result["chain_verified"]
            verification_hashes.add(result["verify_tx"])
            tx = chain.w3.eth.get_transaction(result["verify_tx"])
            assert tx["from"] == wallet and tx["to"] == grant["contract_address"]
            assert not chain.contract(grant["contract_address"]).functions.hasPendingChallenge(bytes.fromhex(grant["resource_id"][2:]), wallet).call()
    assert len(verification_hashes) == 4
    other = client.post("/api/grants", headers=owner, json={"recipient_id": "outsider", "record_ids": ["demo-lab"]}).get_json()["grants"][0]
    assert other["contract_address"] in {g["contract_address"] for g in grants}
    restarted = create_app(dev=True, data_dir=tmp_path, master_key=b"k" * 32, chain=chain).test_client()
    assert restarted.post(f"/api/grants/{grants[0]['id']}/access", headers=recipient).status_code == 200
    # The operator is not automatically a grantee; arbitrary callers are rejected.
    from web3.exceptions import ContractLogicError
    contract = chain.contract(grants[0]["contract_address"])
    resource = bytes.fromhex(grants[0]["resource_id"][2:])
    with pytest.raises(ContractLogicError, match="Not granted"):
        contract.functions.requestChallenge(resource).call({"from": chain.account.address})
    result = client.delete(f"/api/grants/{grants[0]['id']}", headers=owner).get_json()
    assert result["status"] == "revoked" and result["registry_revoked"] and result["revoke_tx"]
    assert not contract.functions.hasGrant(resource, wallet).call()
    with pytest.raises(ContractLogicError, match="Not granted"):
        contract.functions.requestChallenge(resource).call({"from": wallet})
    assert client.post(f"/api/grants/{grants[0]['id']}/access", headers=recipient).status_code == 403
    assert client.post(f"/api/grants/{grants[1]['id']}/access", headers=recipient).status_code == 200
    assert client.post(f"/api/grants/{other['id']}/access", headers={"Authorization": "Bearer demo-outsider"}).status_code == 200
    regrant = client.post("/api/grants", headers=owner, json=payload).get_json()["grants"]
    assert all(g["contract_address"] == grants[0]["contract_address"] for g in regrant)
    assert all(g["status"] == "active" for g in regrant)
    assert client.post(f"/api/grants/{grants[0]['id']}/access", headers=recipient).status_code == 403
    for grant in regrant:
        assert client.post(f"/api/grants/{grant['id']}/access", headers=recipient).status_code == 200
    # Monitor can identify the resource and recipient directly from registry events.
    events = contract.events.AccessGrantSet().get_logs(from_block=0)
    assert any(e["args"]["resourceId"] == resource and e["args"]["grantee"] == wallet for e in events)
    assert len(contract.events.ResourceRegistered().get_logs(from_block=0)) == 2
