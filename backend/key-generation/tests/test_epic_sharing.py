import sqlite3
from types import SimpleNamespace

import pytest

import grant_api
from test_epic_import import seed, remote

IDS = {'owner': '778b3bed-0447-4376-8400-bfc6e273ca2c',
       'recipient': 'aa8b3bed-0447-4376-8400-bfc6e273ca2c',
       'outsider': 'bb8b3bed-0447-4376-8400-bfc6e273ca2c'}


def headers(who='owner'):
    return {'Authorization': 'Bearer ' + who}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('SUPABASE_URL', 'https://project.example')
    monkeypatch.setenv('SUPABASE_PUBLISHABLE_KEY', 'public-test')
    monkeypatch.delenv('SUPABASE_SECRET_KEY', raising=False)
    monkeypatch.delenv('SUPABASE_SERVICE_ROLE_KEY', raising=False)
    monkeypatch.setenv('DOCUMENT_STORAGE_BACKEND', 'supabase')
    def auth(url, **kwargs):
        assert url.endswith('/auth/v1/user')
        who = kwargs['headers']['Authorization'].removeprefix('Bearer ')
        if who not in IDS:
            return SimpleNamespace(status_code=401)
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None,
                               json=lambda: {'id': IDS[who], 'user_metadata': {'full_name': who}})
    monkeypatch.setattr(grant_api.requests, 'get', auth)
    app = grant_api.create_app(data_dir=tmp_path, master_key=b'k' * 32)
    client = app.test_client()
    imports = app.extensions['epic_imports']
    seed(imports.oauth, IDS['owner'])
    imports.transport, imports.launch = remote(), lambda task: task()
    imports.start(IDS['owner'])
    for who in IDS:
        assert client.post('/api/enroll', headers=headers(who)).status_code == 200
    return app, client, tmp_path


def report_id(client, who='owner'):
    result = client.get('/api/records', headers=headers(who))
    assert result.status_code == 200
    record = result.json[0]
    assert record['kind'] == 'Epic import' and record['has_document']
    assert record['mime_type'] == 'text/plain'
    assert 'Later imports are not included' in record['share_note']
    return record['id']


def share(client, identifier, who='owner', recipient='recipient'):
    result = client.post('/api/grants', headers=headers(who), json={
        'recipient_id': IDS[recipient], 'record_ids': [identifier]})
    assert result.status_code == 200
    return result.json['grants'][0]


def view(client, identifier, who='owner'):
    return client.post('/api/records/' + identifier + '/document', headers=headers(who))


def test_import_is_shareable_without_cloud_credentials_but_private_by_default(setup):
    _, client, _ = setup
    identifier = report_id(client)
    assert client.get('/api/health').json['document_uploads_configured'] is False
    assert client.get('/api/records', headers=headers('recipient')).json == []
    assert view(client, identifier, 'recipient').status_code == 403
    assert view(client, identifier, 'outsider').status_code == 403
    assert client.post('/api/records/'+identifier+'/document').status_code == 401
    response = view(client, identifier)
    assert response.status_code == 200 and response.mimetype == 'text/plain'
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.headers['X-Content-Type-Options'] == 'nosniff'
    text = response.get_data(as_text=True)
    assert 'Pulse' in text and '72 /min' in text and 'Vital signs (1)' in text
    assert 'Lab results (0)' in text
    assert 'sandbox-patient' not in text and 'private-epic-token' not in text


def test_exact_recipient_view_revocation_and_no_regrant_by_recipient(setup):
    _, client, _ = setup
    identifier = report_id(client)
    grant = share(client, identifier)
    assert grant['status'] == 'active' and not grant['chain_enabled']
    assert view(client, identifier, 'recipient').status_code == 200
    assert view(client, identifier, 'outsider').status_code == 403
    assert client.get('/api/grants', headers=headers('outsider')).json == []
    assert client.get('/api/external-import/epic/records', headers=headers('recipient')).json['records'] == []
    assert client.post('/api/grants', headers=headers('recipient'), json={
        'recipient_id': IDS['outsider'], 'record_ids': [identifier]}).status_code == 403
    assert client.delete('/api/grants/'+grant['id'], headers=headers('recipient')).status_code == 403
    assert client.delete('/api/grants/'+grant['id'], headers=headers()).json['status'] == 'revoked'
    assert view(client, identifier, 'recipient').status_code == 403
    assert view(client, identifier).status_code == 200


@pytest.mark.parametrize('status', ['pending', 'failed', 'revoking', 'revoked'])
def test_nonactive_import_grants_never_deliver(setup, status):
    _, client, directory = setup
    identifier = report_id(client)
    grant = share(client, identifier)
    with sqlite3.connect(directory/'registry-grants.sqlite3') as db:
        db.execute('UPDATE grants SET status=? WHERE id=?', (status, grant['id']))
    assert view(client, identifier, 'recipient').status_code == 403


def test_new_import_does_not_expand_existing_grant_and_restart_preserves_access(setup):
    app, client, directory = setup
    old = report_id(client)
    share(client, old)
    content = view(client, old, 'recipient').data
    imports = app.extensions['epic_imports']
    imports.transport = remote()
    imports.start(IDS['owner'])
    new = report_id(client)
    assert new != old
    assert view(client, new, 'recipient').status_code == 403
    assert view(client, old, 'recipient').data == content
    restarted = grant_api.create_app(data_dir=directory, master_key=b'k'*32).test_client()
    assert view(restarted, old, 'recipient').data == content
    assert view(restarted, new, 'recipient').status_code == 403


def test_unknown_or_tampered_import_never_delivers(setup):
    _, client, directory = setup
    assert view(client, 'epic-import-not-an-id').status_code == 404
    identifier = report_id(client)
    share(client, identifier)
    with sqlite3.connect(directory/'registry-grants.sqlite3') as db:
        db.execute("UPDATE epic_import_jobs SET payload=? WHERE status='completed'", (b'tampered',))
    assert view(client, identifier, 'recipient').status_code == 503


def test_two_importing_accounts_keep_charts_separate_when_sharing_both_ways(setup):
    app, owner_client, _ = setup
    recipient_client = app.test_client()
    imports = app.extensions['epic_imports']
    seed(imports.oauth, IDS['recipient'])
    recipient_transport = remote()

    def different_pulse(*args, **kwargs):
        response = recipient_transport(*args, **kwargs)
        for entry in response.get('entry', []):
            resource = entry['resource']
            if resource['resourceType'] == 'Observation':
                resource['valueQuantity']['value'] = 88
        return response

    imports.transport = different_pulse
    assert imports.start(IDS['recipient'])['status'] == 'completed'
    owner_report = report_id(owner_client)
    recipient_report = report_id(recipient_client, 'recipient')
    assert owner_report != recipient_report
    assert view(recipient_client, owner_report, 'recipient').status_code == 403
    assert view(owner_client, recipient_report).status_code == 403

    forward = share(owner_client, owner_report)
    delivered = view(recipient_client, owner_report, 'recipient')
    assert delivered.status_code == 200
    assert delivered.data == view(owner_client, owner_report).data
    assert b'72 /min' in delivered.data and b'88 /min' not in delivered.data
    # A grant is directional and never adds another person's rows to a chart.
    assert view(owner_client, recipient_report).status_code == 403
    for client, who, identifier, pulse in (
        (owner_client, 'owner', owner_report, '72'),
        (recipient_client, 'recipient', recipient_report, '88'),
    ):
        catalog = client.get('/api/records', headers=headers(who))
        assert catalog.status_code == 200
        assert [record['id'] for record in catalog.json] == [identifier]
        chart = client.get('/api/external-import/epic/records', headers=headers(who))
        assert chart.status_code == 200
        assert chart.json['total'] == 1 and chart.json['records'][0]['value'] == pulse
        ledger = client.get('/api/grants', headers=headers(who))
        assert ledger.status_code == 200
        assert [grant['id'] for grant in ledger.json] == [forward['id']]

    reverse = share(recipient_client, recipient_report, 'recipient', 'owner')
    assert reverse['id'] != forward['id']
    assert view(owner_client, recipient_report).data == view(
        recipient_client, recipient_report, 'recipient').data
    assert view(owner_client, owner_report, 'outsider').status_code == 403
    assert view(recipient_client, recipient_report, 'outsider').status_code == 403

    assert owner_client.delete('/api/grants/' + forward['id'], headers=headers()).status_code == 200
    assert view(recipient_client, owner_report, 'recipient').status_code == 403
    # Revoking one direction must not revoke the other account's independent grant.
    assert view(owner_client, recipient_report).status_code == 200
    assert recipient_client.delete('/api/grants/' + reverse['id'], headers=headers('recipient')).status_code == 200
    assert view(owner_client, recipient_report).status_code == 403
    assert view(owner_client, owner_report).status_code == 200
    assert view(recipient_client, recipient_report, 'recipient').status_code == 200


def test_duplicate_share_and_regrant_after_persisted_revocation(setup):
    _, client, directory = setup
    identifier = report_id(client)
    original = share(client, identifier)
    assert share(client, identifier)['id'] == original['id']
    assert len(client.get('/api/grants', headers=headers('recipient')).json) == 1
    revoked = client.delete('/api/grants/' + original['id'], headers=headers())
    assert revoked.status_code == 200 and revoked.json['status'] == 'revoked'
    restarted = grant_api.create_app(data_dir=directory, master_key=b'k' * 32).test_client()
    assert view(restarted, identifier, 'recipient').status_code == 403
    assert restarted.post('/api/grants/' + original['id'] + '/access',
                          headers=headers('recipient')).status_code == 403
    replacement = share(restarted, identifier)
    assert replacement['id'] != original['id'] and replacement['status'] == 'active'
    assert view(restarted, identifier, 'recipient').status_code == 200
    states = {grant['id']: grant['status'] for grant in
              restarted.get('/api/grants', headers=headers('recipient')).json}
    assert states == {original['id']: 'revoked', replacement['id']: 'active'}
    assert restarted.delete('/api/grants/' + replacement['id'], headers=headers()).status_code == 200
    assert view(restarted, identifier, 'recipient').status_code == 403


@pytest.mark.parametrize('session_headers', [{}, {'Authorization': 'Bearer expired-session'}])
def test_missing_or_expired_session_cannot_read_or_change_sharing(setup, session_headers):
    _, client, _ = setup
    identifier = report_id(client)
    active = share(client, identifier)
    for method, path in (
        ('GET', '/api/records'),
        ('GET', '/api/grants'),
        ('GET', '/api/external-import/epic/records'),
        ('POST', '/api/records/' + identifier + '/document'),
        ('POST', '/api/grants/' + active['id'] + '/access'),
        ('DELETE', '/api/grants/' + active['id']),
    ):
        response = client.open(path, method=method, headers=session_headers)
        assert response.status_code == 401, (method, path, response.status_code)
        assert response.headers['Cache-Control'] == 'no-store'
        assert b'72 /min' not in response.data
    assert client.post('/api/grants', headers=session_headers, json={
        'recipient_id': IDS['outsider'], 'record_ids': [identifier]}).status_code == 401
    assert view(client, identifier, 'recipient').status_code == 200
    assert view(client, identifier, 'outsider').status_code == 403


def test_saved_report_grants_survive_vault_wallet_enrollment(setup, monkeypatch):
    _, client, directory = setup
    monkeypatch.delenv('FAUCET_ADDRESS', raising=False)
    identifier = report_id(client)
    grant = share(client, identifier)
    original = view(client, identifier, 'recipient').data
    # New pre-prod vault enrollment must not discard existing report permissions.
    wallets = {
        'owner': '0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266',
        'recipient': '0x70997970C51812dc3A010C7d01b50e0d17dc79C8',
    }
    for who, wallet in wallets.items():
        enrollment = client.post('/api/enroll', headers=headers(who), json={
            'evmAddress': wallet, 'x25519PublicKeyB64': 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA='})
        assert enrollment.status_code == 200
        assert enrollment.json['wallet'] == wallet
    restarted = grant_api.create_app(data_dir=directory, master_key=b'k' * 32).test_client()
    delivered = view(restarted, identifier, 'recipient')
    assert delivered.status_code == 200 and delivered.data == original
    assert view(restarted, identifier, 'outsider').status_code == 403
    ledger = restarted.get('/api/grants', headers=headers('recipient')).json
    assert ledger[0]['id'] == grant['id'] and ledger[0]['status'] == 'active'
    assert restarted.delete('/api/grants/' + grant['id'], headers=headers()).status_code == 200
    assert view(restarted, identifier, 'recipient').status_code == 403
    assert view(restarted, identifier).status_code == 200
