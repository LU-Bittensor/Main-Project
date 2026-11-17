from unittest.mock import Mock
import pytest
from grant_api import create_app
from test_rpc import install_rpc

@pytest.fixture
def client(tmp_path):
    app = create_app(dev=True, data_dir=tmp_path)
    app.config['HARDHAT_RPC_ENABLED'] = True
    install_rpc(app)
    return app.test_client()

@pytest.mark.parametrize('method', ['hardhat_reset', 'hardhat_setBalance', 'evm_mine', 'eth_sendTransaction', 'eth_accounts', 'personal_unlockAccount'])
def test_admin_methods_blocked(client, monkeypatch, method):
    post = Mock()
    monkeypatch.setattr('test_rpc.requests.post', post)
    assert client.post('/rpc', json={'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': []}).status_code == 400
    post.assert_not_called()

def test_rpc_forward_and_cors(client, monkeypatch):
    monkeypatch.setenv('unused', '')
    response = Mock()
    response.json.return_value = {'jsonrpc': '2.0', 'id': 1, 'result': '0x7a69'}
    post = Mock(return_value=response)
    monkeypatch.setattr('test_rpc.requests.post', post)
    body = {'jsonrpc': '2.0', 'id': 1, 'method': 'eth_chainId', 'params': []}
    assert client.post('/rpc', json=body).json['result'] == '0x7a69'
    assert client.post('/rpc', json=[body, {**body, 'method': 'hardhat_reset'}]).status_code == 400
    assert client.post('/rpc', json=body, headers={'Origin': 'https://untrusted.example'}).status_code == 403
    assert client.options('/rpc', headers={'Origin': 'http://localhost:5173'}).status_code == 204

def test_rpc_disabled_by_default(tmp_path):
    client = create_app(dev=True, data_dir=tmp_path).test_client()
    assert client.post('/rpc', json={}).status_code == 401
