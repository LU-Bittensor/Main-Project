"""API wallet-share indexing and fail-closed delivery regressions."""
import io
import sqlite3
import pytest
from grant_api import create_app
from test_grant_api import FakeChain, headers

class WalletChain(FakeChain):
    chain_id = 31337
    def __init__(self):
        super().__init__()
        self.shares = []
        self.offline = False
    def wallet_shares(self, address, wallet):
        if self.offline: raise ConnectionError('RPC down')
        return [s for s in self.shares if wallet in (s['owner_wallet'], s['recipient_wallet'])]
    def wallet_resource(self, address, record, owner, recipient):
        if self.offline: raise ConnectionError('RPC down')
        return any(s['record_id'] == record and s['owner_wallet'] == owner and
                   s['recipient_wallet'] == recipient and s['active'] for s in self.shares)

@pytest.fixture
def shared(tmp_path):
    chain = WalletChain()
    client = create_app(dev=True, data_dir=tmp_path, master_key=b'k'*32, chain=chain).test_client()
    client.post('/api/registry/resolve', headers=headers())
    doc = client.post('/api/records', headers=headers(), data={'file': (io.BytesIO(b'private'), 'file.txt')}).json
    with sqlite3.connect(tmp_path/'registry-grants.sqlite3') as db:
        wallets = dict(db.execute('SELECT id,wallet FROM users').fetchall())
    chain.shares.append(dict(record_id=doc['id'], resource_id='0x'+'01'*32,
                             owner_wallet=wallets['patient'], recipient_wallet=wallets['clinician'],
                             active=True, tx_hash='0x'+'02'*32))
    return client, chain, doc, wallets

def test_wallet_share_visible_in_both_ledgers_and_downloadable(shared):
    client, chain, doc, _ = shared
    for user in ('patient', 'clinician'):
        response = client.get('/api/grants', headers=headers(user))
        assert response.status_code == 200
        grant = response.json[0]
        assert grant['id'] == doc['id'] + ':clinician'
        assert grant['status'] == 'active' and grant['document']['id'] == doc['id']
    assert client.get('/api/grants', headers=headers('outsider')).json == []
    url = '/api/records/'+doc['id']+'/document'
    assert client.post(url, headers=headers('clinician')).data == b'private'
    assert client.post(url, headers=headers('outsider')).status_code == 403
    chain.shares[0]['active'] = False
    assert client.get('/api/grants', headers=headers('clinician')).json[0]['status'] == 'revoked'
    assert client.post(url, headers=headers('clinician')).status_code == 403
    assert client.post(url, headers=headers()).status_code == 200

def test_foreign_document_claim_is_not_authorization(shared):
    client, chain, doc, wallets = shared
    chain.shares[0]['owner_wallet'] = wallets['outsider']
    assert client.get('/api/grants', headers=headers('clinician')).json == []
    assert client.post('/api/records/'+doc['id']+'/document', headers=headers('clinician')).status_code == 403

def test_rpc_outage_never_releases_document(shared):
    client, chain, doc, _ = shared
    chain.offline = True
    assert client.get('/api/grants', headers=headers('clinician')).status_code == 503
    assert client.post('/api/records/'+doc['id']+'/document', headers=headers('clinician')).status_code == 503
