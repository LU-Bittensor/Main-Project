"""Authenticated handoff and ciphertext publication, with synthetic S3 and Epic."""
import pytest
import shutil
import base64
import io
import json
from pathlib import Path
from types import SimpleNamespace
import sqlite3
import grant_api
import hippius_storage
import epic_import
from test_hippius_storage import setup, OWNER, OTHER
from test_epic_import import seed, remote


@pytest.mark.parametrize("storage_backend", ["hippius", "local"])
def test_epic_handoff_requires_owned_durable_client_ciphertext(setup, monkeypatch, tmp_path, storage_backend):
    make, catalog, s3 = setup
    monkeypatch.setenv('DOCUMENT_STORAGE_BACKEND', storage_backend)
    monkeypatch.setenv('SUPABASE_URL', 'https://project.example')
    monkeypatch.setenv('SUPABASE_PUBLISHABLE_KEY', 'public-test')
    monkeypatch.setenv('SUPABASE_SECRET_KEY', 'test-service')
    if storage_backend == 'local':
        monkeypatch.delenv('SUPABASE_SECRET_KEY')
        monkeypatch.delenv('SUPABASE_SERVICE_ROLE_KEY', raising=False)
    store = make()
    monkeypatch.setattr(hippius_storage, 'HippiusDocumentStore', lambda *a: store)
    def auth(url, **kw):
        uid = {'Bearer owner': OWNER, 'Bearer other': OTHER,
               'Bearer outsider': '94816262-e3d1-465b-9945-fb9457fe1fb9'}.get(kw['headers']['Authorization'])
        if uid is None:
            return SimpleNamespace(status_code=401)
        value = {'id': uid} if url.endswith('/auth/v1/user') else [{'role': 'patient'}]
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: value)
    monkeypatch.setattr(grant_api.requests, 'get', auth)
    app = grant_api.create_app(data_dir=tmp_path, master_key=b'g'*32)
    client = app.test_client()
    owner = {'Authorization': 'Bearer owner'}
    other = {'Authorization': 'Bearer other'}
    imports = app.extensions['epic_imports']
    seed(imports.oauth, OWNER)
    imports.transport, imports.launch = remote(), lambda task: task()
    fixture = json.loads((Path(__file__).parent / 'fixtures/epic_shared_document.json').read_text())
    info = fixture['info']; m = info['manifest']
    monkeypatch.setattr(epic_import, 'uuid', SimpleNamespace(uuid4=lambda: m['record_id']))
    root = '/api/external-import/epic/import'
    assert client.post(root, headers=owner, json={}).status_code == 409
    assert client.post('/api/enroll', headers=owner, json={'evmAddress': m['owner_wallet'],
        'x25519PublicKeyB64': m['owner_public_key']}).status_code == 200
    job = client.post(root, headers=owner, json={})
    assert job.status_code == 202 and job.json['status'] == 'awaiting_encryption'
    url = root + '/' + m['record_id']
    assert client.get(url+'/payload').status_code == 401
    assert client.get(url+'/payload', headers=other).status_code == 404
    handoff = client.get(url+'/payload', headers=owner)
    assert handoff.headers['Cache-Control'] == 'no-store'
    assert handoff.json['snapshot']['records'][0]['value'] == '72'
    assert client.post(url+'/complete', headers=owner, json={}).status_code == 409
    ciphertext = base64.b64decode(fixture['ciphertext'])
    # The API cannot inspect encrypted contents: synthetic browser fixture exercises publication.
    uploaded = client.post('/api/records', headers=owner, data={'file': (io.BytesIO(ciphertext), 'Epic-snapshot.txt'),
        'kind': 'Epic import', 'tags': 'Lab results, Imaging', 'client_info': json.dumps(info)})
    assert uploaded.status_code == 201
    assert uploaded.json['tags'] == ['Lab results', 'Imaging']
    if storage_backend == 'hippius':
        assert list(s3.objects.values()) == [ciphertext]
    else:
        assert client.get('/api/health').json['document_storage_backend'] == 'local'
    assert client.get(url+'/payload', headers=owner).json == {'stored': True}
    assert client.post(url+'/complete', headers=other, json={}).status_code == 404
    done = client.post(url+'/complete', headers=owner, json={})
    assert done.status_code == 200 and done.json['client_encrypted']
    assert not imports.pending
    assert client.post(url+'/complete', headers=owner, json={}).json == done.json
    records = client.get('/api/external-import/epic/records', headers=owner).json
    assert records == {'client_encrypted': True, 'record_id': m['record_id']}
    assert client.get('/api/external-import/epic/records', headers=other).json['records'] == []
    with sqlite3.connect(imports.path) as db:
        assert db.execute('SELECT payload,digest FROM epic_import_jobs').fetchone() == (None, None)
    assert client.get('/api/records', headers=owner).json[0]['id'] == m['record_id']

    # The signed recipient envelope was generated and decrypted by the frontend
    # crypto test using public synthetic keys. An envelope alone never grants access.
    peer = fixture['recipientEnvelope']
    outsider = {'Authorization': 'Bearer outsider'}
    assert client.post('/api/enroll', headers=other, json={
        'evmAddress': '0x5050A4F4b3f9338C3472dcC01A87C76A144b3c9c',
        'x25519PublicKeyB64': peer['recipient_public_key']}).status_code == 200
    assert client.post('/api/enroll', headers=outsider).status_code == 200
    doc_url = '/api/records/' + m['record_id']
    for suffix, method in [('/encryption', 'GET'), ('/document', 'POST')]:
        assert client.open(doc_url+suffix, method=method, headers=other).status_code == 403
    assert client.post(doc_url+'/encryption', headers=other, json={}).status_code == 403
    envelope = client.post(doc_url+'/encryption', headers=owner, json={
        'recipient_id': OTHER, 'envelope': peer})
    assert envelope.status_code == 200, envelope.json
    assert client.post(doc_url+'/document', headers=other).status_code == 403
    assert client.post(doc_url+'/document').status_code == 401
    assert client.get(doc_url+'/encryption', headers={'Authorization': 'Bearer expired'}).status_code == 401
    grant = client.post('/api/grants', headers=owner, json={
        'recipient_id': OTHER, 'record_ids': [m['record_id']]})
    assert grant.status_code == 200, grant.json
    grant_id = grant.json['grants'][0]['id']
    assert client.get('/api/grants', headers=other).json[0]['document']['client_encrypted']
    assert client.get('/api/grants', headers=other).json[0]['document']['tags'] == ['Lab results', 'Imaging']
    assert client.get('/api/records', headers=other).json == []
    received_info = client.get(doc_url+'/encryption', headers=other)
    assert received_info.status_code == 200
    assert received_info.json['envelope'] == peer
    assert client.post(doc_url+'/document', headers=other).data == ciphertext
    assert client.post(doc_url+'/document', headers=outsider).status_code == 403
    assert client.get(doc_url+'/encryption', headers=outsider).status_code == 403
    # Restart keeps the grant and ciphertext, and revoke blocks both key and file delivery.
    restarted = grant_api.create_app(data_dir=tmp_path, master_key=b'g'*32).test_client()
    assert restarted.post(doc_url+'/document', headers=other).data == ciphertext
    assert restarted.get('/api/grants', headers=other).json[0]['document']['tags'] == ['Lab results', 'Imaging']
    assert restarted.delete('/api/grants/'+grant_id, headers=other).status_code == 403
    assert restarted.delete('/api/grants/'+grant_id, headers=owner).status_code == 200
    assert restarted.post(doc_url+'/document', headers=other).status_code == 403
    assert restarted.get(doc_url+'/encryption', headers=other).status_code == 403
    assert restarted.post(doc_url+'/document', headers=owner).data == ciphertext

    # New encrypted imports use wallet-signed registry grants when configured.
    # Simulate registry reads here; no RPC or real wallet transaction is claimed.
    from test_wallet_document_sharing import WalletChain
    chain = WalletChain()
    if storage_backend == 'local':
        (tmp_path/'wallet-chain').mkdir()
        shutil.copyfile(tmp_path/'local-documents.sqlite3', tmp_path/'wallet-chain/local-documents.sqlite3')
    wallet_app = grant_api.create_app(data_dir=tmp_path/'wallet-chain', master_key=b'g'*32, chain=chain)
    wallet_client = wallet_app.test_client()
    assert wallet_client.post('/api/enroll', headers=owner, json={
        'evmAddress': m['owner_wallet'], 'x25519PublicKeyB64': m['owner_public_key']}).status_code == 200
    assert wallet_client.post('/api/enroll', headers=other, json={
        'evmAddress': '0x5050A4F4b3f9338C3472dcC01A87C76A144b3c9c',
        'x25519PublicKeyB64': peer['recipient_public_key']}).status_code == 200
    assert wallet_client.post('/api/enroll', headers=outsider).status_code == 200
    assert wallet_client.post('/api/registry/resolve', headers=owner).status_code == 200
    chain.shares.append(dict(record_id=m['record_id'], resource_id='0x'+'01'*32,
        owner_wallet=m['owner_wallet'],
        recipient_wallet='0x5050A4F4b3f9338C3472dcC01A87C76A144b3c9c',
        active=True, tx_hash='0x'+'02'*32))
    for identity in (owner, other):
        ledger = wallet_client.get('/api/grants', headers=identity)
        assert ledger.status_code == 200
        assert any(g['id'] == m['record_id']+':'+OTHER and g['status'] == 'active'
                   for g in ledger.json)
    assert wallet_client.get(doc_url+'/encryption', headers=other).json['envelope'] == peer
    assert wallet_client.post(doc_url+'/document', headers=other).data == ciphertext
    assert wallet_client.post(doc_url+'/document', headers=outsider).status_code == 403
    chain.offline = True
    assert wallet_client.post(doc_url+'/document', headers=other).status_code == 503
    assert wallet_client.get(doc_url+'/encryption', headers=other).status_code == 503
    chain.offline = False
    chain.shares[0]['active'] = False
    assert wallet_client.post(doc_url+'/document', headers=other).status_code == 403
    assert wallet_client.get(doc_url+'/encryption', headers=other).status_code == 403
    assert wallet_client.post(doc_url+'/document', headers=owner).data == ciphertext
