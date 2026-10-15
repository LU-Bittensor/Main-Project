import io
import sqlite3
import pytest
from grant_api import create_app
from test_grant_api import headers, make_grant, FakeChain


def upload(client, user='patient', content=b'Private test document', name='visit.txt', tags='visit, labs, visit'):
    return client.post('/api/records', headers=headers(user), data={
        'file': (io.BytesIO(content), name), 'tags': tags})


def view(client, identifier, user='patient'):
    return client.post(f'/api/records/{identifier}/document', headers=headers(user))


@pytest.fixture
def setup(tmp_path):
    return create_app(dev=True, data_dir=tmp_path, master_key=b'k' * 32).test_client(), tmp_path


def test_demo_documents_are_memory_only(setup):
    client, directory = setup
    response = upload(client)
    assert response.status_code == 201
    document = response.get_json()
    assert document['tags'] == ['visit', 'labs']
    assert document['owner_id'] == 'patient' and document['has_document']
    assert document in client.get('/api/records', headers=headers()).get_json()
    assert not (directory / 'documents').exists()
    result = view(client, document['id'])
    restarted = create_app(dev=True, data_dir=directory, master_key=b'k' * 32).test_client()
    assert view(restarted, document['id']).status_code == 404
    assert result.status_code == 200 and result.data == b'Private test document'
    assert result.headers['Cache-Control'] == 'no-store'
    assert result.headers['X-Content-Type-Options'] == 'nosniff'
    assert client.post(f"/api/records/{document['id']}/document").status_code == 401


def test_shared_document_authorization_and_revocation(setup):
    client, _ = setup
    identifier = upload(client).get_json()['id']
    assert view(client, identifier, 'clinician').status_code == 403
    grant = make_grant(client, records=[identifier])
    assert grant['document']['tags'] == ['visit', 'labs']
    assert view(client, identifier, 'clinician').data == b'Private test document'
    assert view(client, identifier, 'outsider').status_code == 403
    other = upload(client, name='other.txt').get_json()
    assert view(client, other['id'], 'clinician').status_code == 403
    client.delete('/api/grants/' + grant['id'], headers=headers())
    assert view(client, identifier, 'clinician').status_code == 403
    assert view(client, identifier).status_code == 200


def test_chain_access_and_out_of_band_revocation(tmp_path):
    chain = FakeChain()
    client = create_app(dev=True, data_dir=tmp_path, master_key=b'k' * 32, chain=chain).test_client()
    identifier = upload(client).get_json()['id']
    grant = make_grant(client, records=[identifier])
    assert view(client, identifier, 'clinician').status_code == 200
    assert chain.access_count == 1
    chain.allow = False
    assert view(client, identifier, 'clinician').status_code == 403
    chain.allow = True
    chain.revoked.add((bytes.fromhex(grant['resource_id'][2:]), grant['recipient_wallet']))
    assert view(client, identifier, 'clinician').status_code == 403


@pytest.mark.parametrize('status', ['pending', 'revoking', 'revoked', 'failed'])
def test_inactive_grants_never_deliver(setup, status):
    client, directory = setup
    identifier = upload(client).get_json()['id']
    grant = make_grant(client, records=[identifier])
    with sqlite3.connect(directory / 'registry-grants.sqlite3') as db:
        db.execute('UPDATE grants SET status=? WHERE id=?', (status, grant['id']))
    assert view(client, identifier, 'clinician').status_code == 403


def test_upload_validation(setup):
    client, _ = setup
    assert upload(client, user='clinician').status_code == 403
    assert upload(client, user='outsider').status_code == 403
    assert client.post('/api/records', headers=headers()).status_code == 400
    assert upload(client, content=b'').status_code == 400
    assert upload(client, name='bad.html', content=b'<script>alert(1)</script>').status_code == 400
    assert upload(client, name='fake.pdf').status_code == 400
    assert upload(client, content=b'\xff').status_code == 400
    assert upload(client, tags='x' * 51).status_code == 400
    assert upload(client, tags=','.join(str(i) for i in range(21))).status_code == 400
    assert upload(client, content=b'x' * (10 * 1024 * 1024 + 1)).status_code == 413
    assert upload(client, name='../../visit.txt').get_json()['name'] == 'visit.txt'
    assert view(client, 'missing').status_code == 404


def test_upload_larger_than_original_json_limit(setup):
    client, _ = setup
    content = b'x' * (256 * 1024)
    result = upload(client, content=content)
    assert result.status_code == 201
    assert view(client, result.get_json()['id']).data == content


@pytest.mark.parametrize(('name', 'content', 'mime'), [
    ('report.pdf', b'%PDF-1.4\n%%EOF', 'application/pdf'),
    ('scan.png', b'\x89PNG\r\n\x1a\nfixture', 'image/png'),
    ('photo.jpg', b'\xff\xd8\xfffixture', 'image/jpeg'),
])
def test_preview_mime_is_determined_server_side(setup, name, content, mime):
    client, _ = setup
    result = upload(client, name=name, content=content)
    assert result.status_code == 201
    response = view(client, result.get_json()['id'])
    assert response.mimetype == mime
    assert response.data == content


def test_real_account_upload_uses_database_role(tmp_path, monkeypatch, cloud_records):
    import grant_api
    from types import SimpleNamespace
    monkeypatch.setenv('SUPABASE_URL', 'https://project.example')
    monkeypatch.setenv('SUPABASE_PUBLISHABLE_KEY', 'test-key')
    uid = 'a5415b66-3b06-44a3-b7ae-d80ee52b2530'
    roles = []
    def get(url, **kwargs):
        if url.endswith('/auth/v1/user'):
            # Editable metadata must not grant upload permission.
            payload = {'id': uid, 'user_metadata': {'role': 'patient'}}
        else:
            assert url.endswith('/rest/v1/user_roles')
            assert kwargs['params']['user_id'] == 'eq.' + uid
            payload = roles
        return SimpleNamespace(status_code=200, json=lambda: payload, raise_for_status=lambda: None)
    monkeypatch.setattr(grant_api.requests, 'get', get)
    client = create_app(data_dir=tmp_path, master_key=b'k' * 32).test_client()
    assert upload(client).status_code == 403
    roles.append({'role': 'patient'})
    result = upload(client)
    assert result.status_code == 201
    assert result.get_json()['owner_id'] == uid


@pytest.fixture
def cloud_records(monkeypatch):
    """Model PostgREST's records table across new API/store instances."""
    import documents
    from types import SimpleNamespace
    rows, calls = {}, []
    monkeypatch.setenv('SUPABASE_SECRET_KEY', 'sb_secret_test')
    def request(method, url, **kwargs):
        assert url.endswith('/rest/v1/records')
        assert kwargs['headers']['apikey'] == 'sb_secret_test'
        assert 'Authorization' not in kwargs['headers']
        calls.append((method, kwargs))
        params = kwargs['params']
        if method == 'POST':
            row = dict(kwargs['json'])
            assert row['id'] not in rows
            row.setdefault('created_at', '2026-10-13T00:00:00+00:00')
            rows[row['id']] = row
            result = [row]
        else:
            result = list(rows.values())
            for field in ['owner_id', 'id']:
                if field in params:
                    result = [r for r in result if r[field] == params[field].removeprefix('eq.')]
            offset = params.get('offset', 0)
            result = result[offset:offset + params.get('limit', 1000)]
        selected = params['select'].split(',')
        result = [{key: row.get(key) for key in selected} for row in result]
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: result)
    monkeypatch.setattr(documents.requests, 'request', request)
    return rows, calls


def test_supabase_persists_bytes_in_records_across_store_instances(cloud_records):
    from documents import SupabaseDocumentStore
    rows, calls = cloud_records
    store = SupabaseDocumentStore('https://project.example', 'sb_secret_test')
    content = b'%PDF-1.4\n\x00\xff binary data'
    uploaded = store.add('owner', 'report.pdf', ['labs', 'annual'], 'application/pdf', content)
    assert rows[uploaded['id']]['file_content'] == '\\x' + content.hex()
    assert rows[uploaded['id']]['tags'] == 'labs, annual'
    # Listing and lookup must never fetch or expose bytes.
    assert 'file_content' not in uploaded
    restarted = SupabaseDocumentStore('https://project.example', 'sb_secret_test')
    assert restarted.owned('owner') == [uploaded]
    assert restarted.owned('other') == []
    assert restarted.get(uploaded['id']) == uploaded
    for _, call in calls:
        assert 'file_content' not in call['params']['select']
    assert restarted.read(uploaded['id'], 'owner') == content
    from documents import DocumentStorageError
    with pytest.raises(DocumentStorageError):
        restarted.read(uploaded['id'], 'other')


def test_no_key_has_no_local_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv('SUPABASE_SECRET_KEY', raising=False)
    monkeypatch.delenv('SUPABASE_SERVICE_ROLE_KEY', raising=False)
    from documents import SupabaseDocumentStore, DocumentStorageError
    with pytest.raises(DocumentStorageError, match='SUPABASE_SECRET_KEY'):
        SupabaseDocumentStore('https://project.example', '').owned('owner')
    assert list(tmp_path.iterdir()) == []


def test_legacy_key_and_failure_sanitization(monkeypatch):
    import documents
    from types import SimpleNamespace
    def request(method, url, **kwargs):
        assert kwargs['headers']['Authorization'] == 'Bearer legacy-secret'
        assert kwargs['headers']['apikey'] == 'legacy-secret'
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: [])
    monkeypatch.setattr(documents.requests, 'request', request)
    assert documents.SupabaseDocumentStore('https://project.example', 'legacy-secret').get('missing') is None
    def fail(*args, **kwargs):
        raise documents.requests.ConnectionError('legacy-secret PRIVATE FILE DATA')
    monkeypatch.setattr(documents.requests, 'request', fail)
    with pytest.raises(documents.DocumentStorageError) as error:
        documents.SupabaseDocumentStore('https://project.example', 'legacy-secret').get('missing')
    assert 'legacy-secret' not in str(error.value) and 'PRIVATE FILE DATA' not in str(error.value)


def test_migrate_local_files_preserves_ids_and_is_repeatable(tmp_path, cloud_records):
    import json
    import hmac
    from documents import SupabaseDocumentStore
    from DHkey_share import encrypt_secret
    from migrate_documents_to_supabase import migrate
    key = b'k' * 32
    path = tmp_path / 'legacy.sqlite3'
    identifier = 'ac1b4a82-0809-416e-a58b-e45b19be2a6e'
    owner = '57cfc884-c048-4839-8915-3a30dc213ca4'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE settings (name TEXT, value TEXT)')
        db.execute('INSERT INTO settings VALUES (?, ?)', ('key_check', hmac.digest(key, b'medilink-document-key', 'sha256').hex()))
        db.execute('CREATE TABLE documents (id TEXT, owner_id TEXT, name TEXT, tags TEXT, mime_type TEXT, kind TEXT, created_at TEXT, content BLOB)')
        db.execute('INSERT INTO documents VALUES (?,?,?,?,?,?,?,?)', (identifier, owner, 'legacy.txt', json.dumps(['tag']), 'text/plain', 'Document', '2026-10-13', encrypt_secret(key, b'preserved document', 'document:' + identifier)))
    store = SupabaseDocumentStore('https://project.example', 'sb_secret_test')
    assert migrate(path, key, store)['pending'] == 1
    assert cloud_records[0] == {}
    assert migrate(path, key, store, apply=True)['copied'] == 1
    assert store.read(identifier, owner) == b'preserved document'
    assert migrate(path, key, store, apply=True)['already_present'] == 1
    assert path.exists()


def test_cloud_api_restart_and_shared_view(tmp_path, monkeypatch, cloud_records):
    import grant_api
    from types import SimpleNamespace
    monkeypatch.setenv('SUPABASE_URL', 'https://project.example')
    monkeypatch.setenv('SUPABASE_PUBLISHABLE_KEY', 'publishable')
    identities = {
        'patient': '831bf62b-673b-4f7d-bdcd-ffe435194241',
        'clinician': '831bf62b-673b-4f7d-bdcd-ffe435194242',
        'outsider': '831bf62b-673b-4f7d-bdcd-ffe435194243',
    }
    def get(url, **kwargs):
        token = kwargs['headers']['Authorization'].removeprefix('Bearer demo-')
        uid = identities[token]
        payload = ({'id': uid, 'user_metadata': {'full_name': token}} if url.endswith('/auth/v1/user')
                   else [{'role': 'patient'}] if token == 'patient' else [])
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: payload)
    monkeypatch.setattr(grant_api.requests, 'get', get)
    client = create_app(data_dir=tmp_path, master_key=b'k' * 32).test_client()
    for user in identities:
        assert client.post('/api/enroll', headers=headers(user)).status_code == 200
    uploaded = upload(client)
    assert uploaded.status_code == 201
    identifier = uploaded.get_json()['id']
    grant = make_grant(client, records=[identifier], recipient=identities['clinician'])
    restarted = create_app(data_dir=tmp_path, master_key=b'k' * 32).test_client()
    assert restarted.get('/api/records', headers=headers()).get_json()[0]['id'] == identifier
    assert view(restarted, identifier).data == b'Private test document'
    assert view(restarted, identifier, 'clinician').data == b'Private test document'
    assert view(restarted, identifier, 'outsider').status_code == 403
    assert restarted.delete('/api/grants/' + grant['id'], headers=headers()).status_code == 200
    assert view(restarted, identifier, 'clinician').status_code == 403
    assert not (tmp_path / 'documents').exists()


def test_cloud_pagination_does_not_drop_records(cloud_records):
    from documents import SupabaseDocumentStore
    rows, _ = cloud_records
    for i in range(501):
        rows[str(i)] = {'id': str(i), 'owner_id': 'owner', 'name': 'metadata', 'kind': 'Document',
                       'tags': '', 'created_at': '2026-10-13'}
    result = SupabaseDocumentStore('https://project.example', 'sb_secret_test').owned('owner')
    assert len(result) == 501 and len({row['id'] for row in result}) == 501
    assert not any(row['has_document'] for row in result)
