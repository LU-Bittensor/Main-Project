import json
import sqlite3
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from DHkey_share import encrypt_secret
from epic_oauth import EpicOAuth, EpicOAuthError, FHIR_BASE, IMPORT_SCOPES
from epic_import import EpicImports, EpicImportError, live
import grant_api


def seed(oauth, uid='alice', **changes):
    saved = {'access_token': 'private-epic-token', 'scope': ' '.join(IMPORT_SCOPES),
             'patient': 'sandbox-patient', 'expires_at': int(time.time()) + 3600,
             'issuer': FHIR_BASE, 'client_id': 'public-test-client'}
    saved.update(changes)
    with sqlite3.connect(oauth.path) as db:
        db.execute('INSERT OR REPLACE INTO epic_connections VALUES (?,?)',
                   (uid, encrypt_secret(oauth.key, json.dumps(saved).encode(), 'epic-connection:' + uid)))


def remote():
    patient = {'resourceType': 'Patient', 'id': 'sandbox-patient', 'name': [{'text': 'Synthetic name'}]}
    observation = {'resourceType': 'Observation', 'id': 'obs1', 'subject': {'reference': 'Patient/sandbox-patient'},
                   'code': {'text': 'Pulse'}, 'valueQuantity': {'value': 72, 'unit': '/min'}}
    pages = [{'resourceType': 'Bundle', 'type': 'searchset', 'entry': [{'resource': observation}] if n == 0 else []}
             for n in range(5)]
    return Mock(side_effect=[patient, *pages])


@pytest.fixture
def setup(tmp_path):
    oauth = EpicOAuth(tmp_path / 'store.sqlite3', b'k' * 32, 'client', 'http://127.0.0.1:5173/external-import/callback')
    seed(oauth)
    imports = EpicImports(oauth.path, oauth.key, oauth, transport=remote(), launch=lambda task: task())
    return oauth, imports


def test_live_fetch_pipeline_encrypted_persistence_and_account_isolation(setup):
    oauth, imports = setup
    result = imports.start('alice')
    assert result['status'] == 'completed' and result['digest_verified']
    assert result['counts'] == {'Patient': 1, 'Observation': 1, 'Condition': 0, 'MedicationRequest': 0, 'Encounter': 0}
    assert imports.status('bob')['status'] == 'idle'
    public = json.dumps(result)
    for secret in ('private-epic-token', 'sandbox-patient', 'Synthetic name', 'Pulse'):
        assert secret not in public
        assert secret.encode() not in oauth.path.read_bytes()
    restored = EpicImports(oauth.path, oauth.key, oauth)
    assert restored.status('alice') == result
    for call in imports.transport.call_args_list:
        assert call.kwargs['headers']['Authorization'] == 'Bearer private-epic-token'


@pytest.mark.parametrize('changes', [
    {'expires_at': 0}, {'scope': 'patient/Patient.read'}, {'patient': '../other'}, {'issuer': 'https://evil.example'}])
def test_incomplete_or_invalid_authorization_does_not_fetch(setup, changes):
    oauth, imports = setup
    seed(oauth, **changes)
    with pytest.raises(EpicOAuthError): imports.start('alice')
    imports.transport.assert_not_called()


def test_failed_request_never_saves_partial_success(setup):
    oauth, imports = setup
    imports.transport = Mock(side_effect=live.EpicLiveError('Epic HTTP 403. Check enabled APIs.'))
    assert imports.start('alice')['status'] == 'failed'
    with sqlite3.connect(oauth.path) as db:
        assert db.execute('SELECT payload FROM epic_import_jobs').fetchone()[0] is None


def test_duplicate_start_is_one_job_and_restart_marks_interrupted_job(setup):
    oauth, imports = setup
    tasks = []
    imports.launch = tasks.append
    first = imports.start('alice')
    assert first['status'] == 'running'
    assert imports.start('alice')['id'] == first['id']
    assert len(tasks) == 1
    restarted = EpicImports(oauth.path, oauth.key, oauth)
    assert restarted.status('alice')['status'] == 'failed'
    assert 'restarted' in restarted.status('alice')['message']


def test_unknown_patient_and_tampered_saved_data_are_rejected(setup):
    oauth, imports = setup
    imports.transport = Mock(return_value={'resourceType': 'Patient', 'id': 'someone-else'})
    assert imports.start('alice')['status'] == 'failed'
    imports.transport = remote()
    assert imports.start('alice')['status'] == 'completed'
    with sqlite3.connect(oauth.path) as db:
        db.execute("UPDATE epic_import_jobs SET payload=? WHERE status='completed'", (b'tampered',))
    with pytest.raises(EpicImportError, match='verified'): imports.status('alice')


def test_repeat_import_is_a_fresh_snapshot_not_duplicate_resources(setup):
    _, imports = setup
    first = imports.start('alice')
    imports.transport = remote()
    second = imports.start('alice')
    assert second['id'] != first['id']
    assert imports.status('alice')['counts']['Observation'] == 1


def test_dashboard_uses_latest_verified_snapshot_and_preserves_values_dates(setup):
    _, imports = setup
    imports.start('alice')
    first = imports.records('alice')
    assert first['total'] == 1 and first['digest_verified']
    assert first['records'][0]['name'] == 'Pulse'
    assert first['records'][0]['value'] == '72'
    assert first['records'][0]['unit'] == '/min'
    assert first['records'][0]['source'] == 'Epic Sandbox'
    assert 'sandbox-patient' not in json.dumps(first)
    assert imports.records('bob')['total'] == 0
    assert imports.records('alice', 'laboratory')['records'] == []
    imports.transport = remote()
    imports.start('alice')
    repeated = imports.records('alice')
    assert repeated['total'] == 1
    assert repeated['records'] == first['records']
    assert repeated['snapshot_id'] != first['snapshot_id']
    assert imports.records('alice', page=2)['records'] == []


def test_dashboard_category_counts_and_pagination(setup):
    _, imports = setup
    def page(*items):
        return {'resourceType': 'Bundle', 'type': 'searchset', 'entry': [{'resource': r} for r in items]}
    subject = {'reference': 'Patient/sandbox-patient'}
    vitals = [{'resourceType': 'Observation', 'id': f'v{n}', 'subject': subject, 'status': 'final',
               'code': {'text': 'Pulse'}, 'valueQuantity': {'value': n, 'unit': '/min'},
               'effectiveDateTime': '2026-10'} for n in range(30)]
    lab = {'resourceType': 'Observation', 'id': 'lab1', 'subject': subject,
           'category': [{'coding': [{'code': 'laboratory'}]}], 'code': {'text': 'Lab test'},
           'valueString': 'negative', 'effectiveDateTime': '2026-10-28'}
    condition = {'resourceType': 'Condition', 'id': 'c1', 'subject': subject,
                 'code': {'text': 'Test condition'}, 'recordedDate': '2026-10-01',
                 'clinicalStatus': {'coding': [{'code': 'active'}]}}
    medication = {'resourceType': 'MedicationRequest', 'id': 'm1', 'subject': subject,
                  'medicationCodeableConcept': {'text': 'Test medication'}, 'authoredOn': '2026-10-02',
                  'dosageInstruction': [{'text': 'Synthetic dosage instruction'}]}
    encounter = {'resourceType': 'Encounter', 'id': 'e1', 'subject': subject,
                 'status': 'finished', 'class': {'code': 'AMB'}, 'period': {'start': '2026-10-03'}}
    imports.transport = Mock(side_effect=[{'resourceType': 'Patient', 'id': 'sandbox-patient'},
        page(*vitals), page(lab), page(condition), page(medication), page(encounter)])
    assert imports.start('alice')['status'] == 'completed'
    first = imports.records('alice', 'vital-signs')
    second = imports.records('alice', 'vital-signs', page=2)
    assert first['counts'] == {'vital-signs': 30, 'laboratory': 1, 'conditions': 1, 'medications': 1, 'encounters': 1}
    assert len(first['records']) == 25 and len(second['records']) == 5
    assert not {r['id'] for r in first['records']} & {r['id'] for r in second['records']}
    assert all(r['date'] == '2026-10' for r in first['records'])
    assert imports.records('alice', 'laboratory')['records'][0]['value'] == 'negative'
    assert imports.records('alice', 'conditions')['records'][0]['status'] == 'active'
    assert imports.records('alice', 'medications')['records'][0]['value'] == 'Synthetic dosage instruction'
    assert imports.records('alice', 'encounters')['records'][0]['date'] == '2026-10-03'


@pytest.mark.parametrize('category,page', [('unknown', 1), ('all', 0), ('all', -1), ('all', True), ('all', 100001)])
def test_dashboard_rejects_invalid_filters(setup, category, page):
    _, imports = setup
    with pytest.raises(ValueError): imports.records('alice', category, page)


def test_expiry_preserves_saved_records_and_reconnect_restores_import(setup, monkeypatch):
    from urllib.parse import parse_qs, urlsplit
    import epic_oauth
    oauth, imports = setup
    imports.start('alice')
    previous = imports.records('alice')['records']
    seed(oauth, expires_at=0)
    assert not oauth.status('alice')['ready_for_import']
    with pytest.raises(EpicOAuthError): imports.start('alice')
    assert imports.records('alice')['records'] == previous
    state = parse_qs(urlsplit(oauth.start('alice')['authorization_url']).query)['state'][0]
    monkeypatch.setattr(epic_oauth.requests, 'post', lambda *a, **kw: SimpleNamespace(status_code=200, json=lambda: {
        'access_token': 'renewed-test-token', 'token_type': 'Bearer', 'expires_in': 3600,
        'scope': ' '.join(IMPORT_SCOPES), 'patient': 'sandbox-patient'}))
    assert oauth.complete('alice', {'state': state, 'code': 'new-code'})['ready_for_import']
    imports.transport = remote()
    assert imports.start('alice')['status'] == 'completed'
    assert imports.records('alice')['records'] == previous


def test_authenticated_routes_reject_overrides_and_other_accounts(tmp_path, monkeypatch):
    monkeypatch.setenv('SUPABASE_URL', 'https://project.example')
    monkeypatch.setenv('SUPABASE_PUBLISHABLE_KEY', 'public-test')
    alice = '778b3bed-0447-4376-8400-bfc6e273ca2c'
    bob = 'aa8b3bed-0447-4376-8400-bfc6e273ca2c'
    def auth(url, **kwargs):
        assert url.endswith('/auth/v1/user')
        who = kwargs['headers']['Authorization']
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None,
                               json=lambda: {'id': bob if who == 'Bearer bob' else alice})
    monkeypatch.setattr(grant_api.requests, 'get', auth)
    app = grant_api.create_app(data_dir=tmp_path, master_key=b'k' * 32)
    imports = app.extensions['epic_imports']
    seed(imports.oauth, alice)
    imports.transport, imports.launch = remote(), lambda task: task()
    client = app.test_client()
    endpoint = '/api/external-import/epic/import'
    headers = {'Authorization': 'Bearer alice'}
    assert client.get(endpoint).status_code == 401
    assert client.post(endpoint).status_code == 401
    assert client.post(endpoint, headers=headers, json={'user_id': bob}).status_code == 400
    assert client.get(endpoint + '?user_id=' + alice, headers=headers).status_code == 400
    result = client.post(endpoint, headers=headers, json={})
    assert result.status_code == 409  # New imports require Hippius and an enrolled browser vault.
    imports.client_storage = False  # Seed a historical import for legacy read/isolation assertions.
    imports.start(alice)
    assert result.headers['Cache-Control'] == 'no-store'
    assert client.get(endpoint, headers={'Authorization': 'Bearer bob'}).json['status'] == 'idle'
    assert client.post(endpoint, headers={'Authorization': 'Bearer bob'}, json={}).status_code == 409
    records = '/api/external-import/epic/records'
    assert client.get(records).status_code == 401
    assert client.get(records, headers=headers).json['records'][0]['value'] == '72'
    assert client.get(records, headers={'Authorization': 'Bearer bob'}).json['records'] == []
    for query in ('?user_id='+alice, '?category=unknown', '?page=0', '?page=no', '?page=1&page=2'):
        assert client.get(records+query, headers=headers).status_code == 400
    demo = grant_api.create_app(dev=True, data_dir=tmp_path / 'demo', master_key=b'k' * 32).test_client()
    assert demo.get(endpoint, headers={'Authorization': 'Bearer demo-patient'}).status_code == 403
    assert demo.get(records, headers={'Authorization': 'Bearer demo-patient'}).status_code == 403


def test_browser_handoff_never_persists_clinical_payload_and_clears_on_ack(setup):
    oauth, imports = setup
    imports.client_storage = True
    job = imports.start('alice')
    assert job['status'] == 'awaiting_encryption'
    assert imports.start('alice')['id'] == job['id']
    with pytest.raises(EpicImportError): imports.transfer('bob', job['id'])
    payload = imports.transfer('alice', job['id'])
    assert payload['format'] == 'medilink-epic-snapshot-v1'
    assert payload['records'][0]['value'] == '72'
    assert payload['bundle']['entry'][0]['resource']['resourceType'] == 'Patient'
    with sqlite3.connect(oauth.path) as db:
        assert db.execute('SELECT payload,digest FROM epic_import_jobs').fetchone() == (None, None)
    for secret in (b'Pulse', b'Synthetic name'):
        assert secret not in oauth.path.read_bytes()
    held = imports.pending[job['id']]['raw']
    done = imports.finish('alice', job['id'])
    assert done['client_encrypted'] and done['record_id'] == job['id']
    assert not imports.pending and not any(held)
    assert imports.finish('alice', job['id']) == done
    with pytest.raises(EpicImportError): imports.transfer('alice', job['id'])
    assert EpicImports(oauth.path, oauth.key, oauth, client_storage=True).status('alice') == done


def test_browser_handoff_expiry_and_restart_fail_without_leaking_payload(setup):
    oauth, imports = setup
    imports.client_storage = True
    job = imports.start('alice')
    item = imports.pending[job['id']]
    item['timer'].cancel()
    imports._expire(job['id'])
    assert not any(item['raw'])
    assert imports.status('alice')['status'] == 'failed'
    with pytest.raises(EpicImportError): imports.transfer('alice', job['id'])
    imports.transport = remote()
    job = imports.start('alice')
    restarted = EpicImports(oauth.path, oauth.key, oauth, client_storage=True)
    assert restarted.status('alice')['status'] == 'failed'
    with pytest.raises(EpicImportError): restarted.transfer('alice', job['id'])
    imports.pending[job['id']]['timer'].cancel()
    imports._expire(job['id'])
