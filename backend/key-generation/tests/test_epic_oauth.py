import hashlib
import sqlite3
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
import pytest
import epic_oauth
from epic_oauth import EpicOAuth, EpicOAuthError, pkce_challenge
from grant_api import create_app


@pytest.fixture
def store(tmp_path):
    return EpicOAuth(tmp_path / 'oauth.sqlite3', b'k' * 32, 'sandbox-client', 'http://127.0.0.1:5173/external-import/callback')


def query(result):
    return {k: v[0] for k, v in parse_qs(urlsplit(result['authorization_url']).query).items()}


def test_rfc7636_challenge_vector():
    assert pkce_challenge('dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk') == 'E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM'


def test_url_and_encrypted_single_use_state(store):
    result = store.start('alice'); params = query(result)
    assert params['response_type'] == 'code'
    assert params['aud'] == epic_oauth.FHIR_BASE
    assert params['code_challenge_method'] == 'S256'
    assert params['scope'] == 'launch/patient patient/Patient.read'
    assert result['expires_in'] == 600
    assert 'code_verifier' not in params and 'alice' not in result['authorization_url']
    with sqlite3.connect(store.path) as db:
        row = db.execute('SELECT state_hash, verifier FROM epic_oauth_pending').fetchone()
    assert row[0] == hashlib.sha256(params['state'].encode()).hexdigest()
    with pytest.raises(EpicOAuthError): store.consume(params['state'], 'bob')
    restored = EpicOAuth(store.path, b'k' * 32, 'changed-client', 'https://changed.example/callback')
    attempt = restored.consume(params['state'], 'alice')
    assert 43 <= len(attempt['code_verifier']) <= 128
    assert attempt['code_verifier'].encode() not in row[1]
    assert pkce_challenge(attempt['code_verifier']) == params['code_challenge']
    assert attempt['client_id'] == 'sandbox-client'
    with pytest.raises(EpicOAuthError): store.consume(params['state'], 'alice')


def test_replacement_and_expiry(store, monkeypatch):
    first = query(store.start('alice')); second = query(store.start('alice'))
    assert first['state'] != second['state'] and first['code_challenge'] != second['code_challenge']
    with pytest.raises(EpicOAuthError): store.consume(first['state'], 'alice')
    now = epic_oauth.time.time()
    monkeypatch.setattr(epic_oauth.time, 'time', lambda: now + 601)
    with pytest.raises(EpicOAuthError): store.consume(second['state'], 'alice')
    store.start('bob')
    with sqlite3.connect(store.path) as db:
        assert db.execute('SELECT COUNT(*) FROM epic_oauth_pending').fetchone()[0] == 1


@pytest.mark.parametrize('redirect', ['http://remote.example/cb', 'https://user:pass@host/cb', 'https://host/cb#fragment', 'https://host/cb?next=evil', '/relative'])
def test_invalid_configuration(store, redirect):
    store.redirect_uri = redirect
    with pytest.raises(EpicOAuthError): store.start('alice')


def test_authenticated_endpoint(tmp_path, monkeypatch):
    import grant_api
    monkeypatch.setenv('SUPABASE_URL', 'https://project.example')
    monkeypatch.setenv('SUPABASE_PUBLISHABLE_KEY', 'public-test')
    monkeypatch.setenv('EPIC_CLIENT_ID', 'sandbox-client')
    monkeypatch.setenv('EPIC_REDIRECT_URI', 'http://127.0.0.1:5173/external-import/callback')
    uid = '778b3bed-0447-4376-8400-bfc6e273ca2c'
    def get(url, **kwargs):
        assert url.endswith('/auth/v1/user')
        if kwargs['headers']['Authorization'] != 'Bearer valid': return SimpleNamespace(status_code=401)
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: {'id': uid})
    monkeypatch.setattr(grant_api.requests, 'get', get)
    client = create_app(data_dir=tmp_path, master_key=b'k' * 32).test_client()
    endpoint = '/api/external-import/epic/authorize'
    assert client.post(endpoint).status_code == 401
    assert client.post(endpoint, headers={'Authorization':'Bearer invalid'}).status_code == 401
    headers = {'Authorization':'Bearer valid'}
    assert client.post(endpoint, headers=headers, json={'user_id': 'other'}).status_code == 400
    assert client.post(endpoint+'?redirect_uri=https://evil.example', headers=headers).status_code == 400
    result = client.post(endpoint, headers=headers, json={})
    assert result.status_code == 200 and result.headers['Cache-Control'] == 'no-store'
    assert set(result.json) == {'authorization_url', 'expires_in'}
    with sqlite3.connect(tmp_path / 'registry-grants.sqlite3') as db:
        assert db.execute('SELECT user_id FROM epic_oauth_pending').fetchone()[0] == uid
    completion = '/api/external-import/epic/complete'
    status_url = '/api/external-import/epic/connection'
    assert client.post(completion, json={}).status_code == 401
    assert client.get(status_url).status_code == 401
    monkeypatch.setattr(epic_oauth.requests, 'post', lambda *a, **kw: token_response())
    body = {'state': query(result.json)['state'], 'code': 'code'}
    completed = client.post(completion, headers=headers, json=body)
    assert completed.status_code == 200 and completed.json['ready_for_transfer']
    assert completed.headers['Cache-Control'] == 'no-store'
    assert client.get(status_url, headers=headers).json == completed.json
    assert client.post(completion, headers=headers, json=body).status_code == 400
    demo = create_app(dev=True, data_dir=tmp_path / 'demo', master_key=b'k' * 32).test_client()
    assert demo.post(endpoint, headers={'Authorization':'Bearer demo-patient'}).status_code == 403


def test_missing_config(store):
    store.client_id = ''
    with pytest.raises(EpicOAuthError, match='EPIC_CLIENT_ID'): store.start('alice')


def token_response(**changes):
    token = dict(access_token='secret-access-token', token_type='Bearer', expires_in=3600,
                 scope='patient/Patient.read', patient='sandbox-patient')
    token.update(changes)
    return SimpleNamespace(status_code=200, json=lambda: token)


def test_exchange_binding_persistence_and_replay(store, monkeypatch):
    params = query(store.start('alice'))
    calls = []
    def post(url, **kwargs):
        assert url == epic_oauth.TOKEN_URL
        assert kwargs['allow_redirects'] is False
        form = kwargs['data']
        assert form['grant_type'] == 'authorization_code'
        assert form['code'] == 'epic-code'
        assert form['redirect_uri'] == store.redirect_uri
        assert form['client_id'] == store.client_id
        assert pkce_challenge(form['code_verifier']) == params['code_challenge']
        calls.append(url)
        return token_response()
    monkeypatch.setattr(epic_oauth.requests, 'post', post)
    body = {'state': params['state'], 'code': 'epic-code'}
    with pytest.raises(EpicOAuthError): store.complete('bob', body)
    assert not calls
    status = store.complete('alice', body)
    assert status['token_exchange_succeeded'] and status['ready_for_transfer']
    assert 'access_token' not in str(status) and 'sandbox-patient' not in str(status)
    assert b'secret-access-token' not in store.path.read_bytes()
    assert b'sandbox-patient' not in store.path.read_bytes()
    assert EpicOAuth(store.path, store.key, '', '').status('alice') == status
    assert not store.status('bob')['token_exchange_succeeded']
    with pytest.raises(EpicOAuthError): store.complete('alice', body)
    assert len(calls) == 1
    now = epic_oauth.time.time()
    monkeypatch.setattr(epic_oauth.time, 'time', lambda: now + 3601)
    assert not store.status('alice')['ready_for_transfer']


@pytest.mark.parametrize('changes', [{'patient': ''}, {'scope': 'launch/patient'}])
def test_successful_exchange_not_ready(store, monkeypatch, changes):
    monkeypatch.setattr(epic_oauth.requests, 'post', lambda *a, **kw: token_response(**changes))
    result = store.complete('alice', {'state': query(store.start('alice'))['state'], 'code': 'code'})
    assert result['token_exchange_succeeded'] and not result['ready_for_transfer']


@pytest.mark.parametrize('response', [SimpleNamespace(status_code=400), SimpleNamespace(status_code=302),
                                    token_response(access_token=''), token_response(expires_in=True),
                                    token_response(token_type='unexpected')])
def test_bad_response_consumes_attempt(store, monkeypatch, response):
    monkeypatch.setattr(epic_oauth.requests, 'post', lambda *a, **kw: response)
    body = {'state': query(store.start('alice'))['state'], 'code': 'code'}
    with pytest.raises(EpicOAuthError): store.complete('alice', body)
    with pytest.raises(EpicOAuthError): store.consume(body['state'], 'alice')
    assert not store.status('alice')['token_exchange_succeeded']


def test_denial_and_invalid_callback(store, monkeypatch):
    def unexpected(*a, **kw): pytest.fail('No token exchange expected')
    monkeypatch.setattr(epic_oauth.requests, 'post', unexpected)
    state = query(store.start('alice'))['state']
    for body in [None, {}, {'state': state, 'code': 'code', 'user_id': 'bob'},
                 {'state': state, 'code': 'code', 'error': 'denied'}]:
        with pytest.raises(EpicOAuthError): store.complete('alice', body)
    with pytest.raises(EpicOAuthError, match='declined'):
        store.complete('alice', {'state': state, 'error': 'access_denied'})
    with pytest.raises(EpicOAuthError): store.consume(state, 'alice')
