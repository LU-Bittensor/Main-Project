"""Authenticated handoff and ciphertext publication, with synthetic S3 and Epic."""
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


def test_epic_handoff_requires_owned_durable_client_ciphertext(setup, monkeypatch, tmp_path):
    make, catalog, s3 = setup
    monkeypatch.setenv('DOCUMENT_STORAGE_BACKEND', 'hippius')
    monkeypatch.setenv('SUPABASE_URL', 'https://project.example')
    monkeypatch.setenv('SUPABASE_PUBLISHABLE_KEY', 'public-test')
    monkeypatch.setenv('SUPABASE_SECRET_KEY', 'test-service')
    store = make()
    monkeypatch.setattr(hippius_storage, 'HippiusDocumentStore', lambda *a: store)
    def auth(url, **kw):
        uid = OWNER if kw['headers']['Authorization'] == 'Bearer owner' else OTHER
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
    fixture = json.loads((Path(__file__).parent / 'fixtures/client_document.json').read_text())
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
        'kind': 'Epic import', 'client_info': json.dumps(info)})
    assert uploaded.status_code == 201
    assert list(s3.objects.values()) == [ciphertext]
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
