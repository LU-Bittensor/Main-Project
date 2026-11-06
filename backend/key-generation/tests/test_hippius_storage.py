import base64
import copy
import io
import json
from pathlib import Path
import uuid

import pytest
from hippius_storage import HippiusDocumentStore, patient_hash, seal, unseal
from documents import DocumentStorageError

OWNER = 'c8f0280b-5237-4391-9cfe-e977fabfca92'
OTHER = '84816262-e3d1-465b-9945-fb9457fe1fb9'


class FakeS3:
    def __init__(self):
        self.buckets = set()
        self.objects = {}
        self.public = False
        self.fail_put = False
    def create_bucket(self, Bucket, ACL):
        assert ACL == 'private'
        self.buckets.add(Bucket)
    def get_bucket_acl(self, Bucket):
        assert Bucket in self.buckets
        return {'Owner': {'ID': 'operator'}, 'Grants': [{'Grantee':
            {'Type': 'Group', 'URI': 'public'} if self.public else {'Type': 'CanonicalUser', 'ID': 'operator'}}]}
    def put_object(self, Bucket, Key, Body, ContentType):
        assert ContentType == 'application/octet-stream'
        if self.fail_put: raise RuntimeError('credential-bearing upstream error')
        self.objects[Bucket, Key] = Body
    def get_object(self, Bucket, Key):
        return {'Body': io.BytesIO(self.objects[Bucket, Key])}


class AccessDenied(Exception):
    response = {'ResponseMetadata': {'HTTPStatusCode': 403}}


class FakeAnonymous:
    def get_object(self, **kwargs): raise AccessDenied()
    def list_objects_v2(self, **kwargs): raise AccessDenied()


class FakeCatalog:
    def __init__(self):
        self.tables = {k: [] for k in ('patient_storage', 'storage_objects', 'records', 'document_key_envelopes')}
        self.lose_publish_response = False
    def table(self, table, method='GET', params=None, json=None, headers=None):
        params = params or {}
        if table == 'rpc/publish_hippius_document':
            obj = next(o for o in self.tables['storage_objects'] if o['id'] == json['p_object'])
            assert obj['status'] == 'uploaded' and obj['owner_id'] == json['p_owner']
            row = {'id': json['p_record'], 'owner_id': json['p_owner'], 'name': json['p_name'],
                   'kind': json['p_kind'], 'tags': json['p_tags'], 'created_at': 'now',
                   'client_encrypted': obj.get('encryption_format', 'aes256gcm-v1') == 'client-x25519-v1', 'file_mime_type': obj['mime_type'], 'file_size': obj['plaintext_size'], 'storage_object_id': obj['id']}
            self.tables['records'].append(row)
            obj['status'] = 'ready'
            if self.lose_publish_response: raise RuntimeError('timeout after commit')
            return [copy.deepcopy(row)]
        rows = self.tables[table]
        matched = [r for r in rows if all(str(r.get(k)) == v[3:] for k, v in params.items() if isinstance(v, str) and v.startswith('eq.'))]
        if method == 'GET':
            offset = int(params.get('offset', 0)); limit = int(params.get('limit', len(matched)))
            return copy.deepcopy(matched[offset:offset+limit])
        if method == 'POST':
            if table == 'patient_storage' and any(r['owner_id'] == json['owner_id'] for r in rows): return []
            row = {'id': str(uuid.uuid4()), 'status': 'provisioning' if table == 'patient_storage' else 'pending', **json}
            rows.append(row)
            return [copy.deepcopy(row)]
        if method == 'PATCH':
            for row in matched: row.update(json)
            return copy.deepcopy(matched)
        pytest.fail('Unexpected request')


@pytest.fixture
def setup():
    catalog, s3 = FakeCatalog(), FakeS3()
    def make(master=b'm'*32):
        store = HippiusDocumentStore('https://example.invalid', 'test', catalog=catalog, s3=s3,
                                    hash_key=b'h'*32, master_key=master, namespace='test', anonymous=FakeAnonymous())
        store.request = lambda method, **kw: catalog.table('records', method, **kw)
        return store
    return make, catalog, s3


def test_hash_is_stable_scoped_and_bucket_safe():
    a = patient_hash(OWNER, 'test', b'h'*32)
    assert len(a) == 52 and len('ml-p-' + a) <= 63
    assert a == patient_hash(OWNER, 'test', b'h'*32)
    assert len({a, patient_hash(OTHER, 'test', b'h'*32), patient_hash(OWNER, 'prod', b'h'*32),
                patient_hash(OWNER, 'test', b'k'*32)}) == 4


def test_encrypted_roundtrip_restart_and_per_patient_key(setup):
    make, catalog, s3 = setup
    first = make().add(OWNER, 'private.txt', ['tag'], 'text/plain', b'private medical contents')
    make().add(OTHER, 'other.txt', [], 'text/plain', b'other')
    assert len(s3.buckets) == 2
    assert make().read(first['id'], OWNER) == b'private medical contents'
    assert b'private medical contents' not in b''.join(s3.objects.values())
    assert OWNER not in str(list(s3.objects)) and 'private.txt' not in str(list(s3.objects))
    assert 'wrapped_key' not in str(first) and 'storage_object_id' not in first
    assert all('file_content' not in r for r in catalog.tables['records'])
    keys = [unseal(b'm'*32, base64.b64decode(r['wrapped_key']), 'patient-key:'+r['owner_id'])
            for r in catalog.tables['patient_storage']]
    assert keys[0] != keys[1]


def test_wrong_owner_never_downloads(setup):
    make, _, s3 = setup
    row = make().add(OWNER, 'file.txt', [], 'text/plain', b'secret')
    s3.get_object = lambda **kw: pytest.fail('Unauthorized S3 read')
    with pytest.raises(DocumentStorageError): make().read(row['id'], OTHER)


@pytest.mark.parametrize('mode', ['tamper', 'wrong-key', 'swap', 'public'])
def test_fail_closed(setup, mode):
    make, catalog, s3 = setup
    row = make().add(OWNER, 'file.txt', [], 'text/plain', b'secret')
    if mode == 'tamper':
        location = next(iter(s3.objects)); s3.objects[location] = b'corrupt'
    if mode == 'swap': catalog.tables['storage_objects'][0]['patient_storage_id'] = str(uuid.uuid4())
    if mode == 'public': s3.public = True
    with pytest.raises(DocumentStorageError):
        make(b'x'*32 if mode == 'wrong-key' else b'm'*32).read(row['id'], OWNER)


def test_failed_upload_not_published(setup):
    make, catalog, s3 = setup
    s3.fail_put = True
    with pytest.raises(DocumentStorageError) as error:
        make().add(OWNER, 'file.txt', [], 'text/plain', b'secret')
    assert 'credential-bearing' not in str(error.value)
    assert catalog.tables['records'] == []
    assert catalog.tables['storage_objects'][0]['status'] == 'pending'


def test_lost_commit_response_keeps_recoverable_ciphertext(setup):
    make, catalog, s3 = setup
    catalog.lose_publish_response = True
    with pytest.raises(DocumentStorageError): make().add(OWNER, 'file.txt', [], 'text/plain', b'secret')
    row = catalog.tables['records'][0]
    assert s3.objects and make().read(row['id'], OWNER) == b'secret'


def test_legacy_document_still_readable(setup):
    make, catalog, _ = setup
    catalog.tables['records'].append({'id': 'legacy', 'owner_id': OWNER, 'storage_object_id': None,
                                      'file_content': '\\x' + b'old content'.hex()})
    assert make().read('legacy', OWNER) == b'old content'


def test_seal_binds_context_and_uses_fresh_nonce():
    a = seal(b'k'*32, b'data', 'first')
    assert a != seal(b'k'*32, b'data', 'first')
    with pytest.raises(Exception): unseal(b'k'*32, a, 'second')


def test_anonymous_object_read_prevents_publication(setup):
    make, catalog, _ = setup
    store = make()
    store.anonymous.get_object = lambda **kw: {'Body': io.BytesIO(b'public')}
    with pytest.raises(DocumentStorageError): store.add(OWNER, 'file.txt', [], 'text/plain', b'secret')
    assert not catalog.tables['records']


def test_acl_disabled_gateway_requires_anonymous_denial(setup):
    make, _, s3 = setup
    class Disabled(Exception):
        response = {'Error': {'Code': 'AccessControlListNotSupported'}}
    def acl(**kw): raise Disabled()
    s3.get_bucket_acl = acl
    store = make()
    row = store.add(OWNER, 'file.txt', [], 'text/plain', b'secret')
    assert store.read(row['id'], OWNER) == b'secret'
    store.anonymous.list_objects_v2 = lambda **kw: {'Contents': []}
    with pytest.raises(DocumentStorageError): store.read(row['id'], OWNER)


def test_api_selects_hippius_and_keeps_authentication(setup, monkeypatch, tmp_path):
    from types import SimpleNamespace
    import grant_api
    import hippius_storage
    make, catalog, s3 = setup
    store = make()
    monkeypatch.setenv('DOCUMENT_STORAGE_BACKEND', 'hippius')
    monkeypatch.setenv('SUPABASE_URL', 'https://project.example')
    monkeypatch.setenv('SUPABASE_PUBLISHABLE_KEY', 'test-public')
    monkeypatch.setattr(hippius_storage, 'HippiusDocumentStore', lambda *a: store)
    def auth_get(url, **kw):
        user = OWNER if kw['headers']['Authorization'] == 'Bearer owner' else OTHER
        body = {'id': user} if url.endswith('/auth/v1/user') else [{'role': 'patient'}]
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: body)
    monkeypatch.setattr(grant_api.requests, 'get', auth_get)
    client = grant_api.create_app(data_dir=tmp_path, master_key=b'g'*32).test_client()
    result = client.post('/api/records', headers={'Authorization': 'Bearer owner'},
                         data={'file': (io.BytesIO(b'private test'), 'visit.txt')})
    assert result.status_code == 409  # No plaintext fallback for Hippius uploads.
    fixture = json.loads((Path(__file__).parent / 'fixtures/client_document.json').read_text())
    info = fixture['info']; manifest = info['manifest']
    ciphertext = base64.b64decode(fixture['ciphertext'])
    enrolled = client.post('/api/enroll', headers={'Authorization': 'Bearer owner'}, json={
        'evmAddress': manifest['owner_wallet'], 'x25519PublicKeyB64': manifest['owner_public_key']})
    assert enrolled.status_code == 200
    for invalid in ('{}', 'null', '[]'):
        bad = client.post('/api/records', headers={'Authorization': 'Bearer owner'},
            data={'file': (io.BytesIO(b'plaintext'), 'visit.txt'), 'client_info': invalid})
        assert bad.status_code == 400
    result = client.post('/api/records', headers={'Authorization': 'Bearer owner'},
        data={'file': (io.BytesIO(ciphertext), 'visit.txt'), 'client_info': json.dumps(info)})
    assert result.status_code == 201, result.json
    identifier = result.json['id']
    encryption_url = '/api/records/' + identifier + '/encryption'
    assert client.get(encryption_url, headers={'Authorization': 'Bearer owner'}).json['manifest'] == manifest
    assert client.get(encryption_url, headers={'Authorization': 'Bearer other'}).status_code == 403
    assert s3.objects
    url = '/api/records/' + identifier + '/document'
    assert client.post(url).status_code == 401
    assert client.post(url, headers={'Authorization': 'Bearer other'}).status_code == 403
    response = client.post(url, headers={'Authorization': 'Bearer owner'})
    assert response.data == ciphertext and response.headers['Cache-Control'] == 'no-store'


def test_client_ciphertext_is_stored_unchanged_and_does_not_use_server_key(setup):
    make, catalog, s3 = setup
    ciphertext = b'x' * 34
    info = {'manifest': {'size': 6}, 'envelope': {'wrapped_key': 'opaque'}}
    row = make().add(OWNER, 'file.txt', [], 'text/plain', ciphertext, client_info=info)
    assert row['client_encrypted'] is True
    assert list(s3.objects.values()) == [ciphertext]
    assert make(b'z'*32).read(row['id'], OWNER) == ciphertext
    assert make().encryption_info(row['id'], OWNER, OWNER) == info
    with pytest.raises(DocumentStorageError): make().encryption_info(row['id'], OWNER, OTHER)
    make().save_envelope(row['id'], OWNER, OTHER, {'wrapped_key': 'recipient-opaque'})
    assert make().encryption_info(row['id'], OWNER, OTHER)['envelope']['wrapped_key'] == 'recipient-opaque'
