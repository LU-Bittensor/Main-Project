import pytest
import cloud_app
import grant_api


@pytest.mark.parametrize('forwarded,scheme', [(None, 'http'), ('https', 'https')])
def test_cloud_vault_url_scheme(tmp_path, monkeypatch, forwarded, scheme):
    monkeypatch.setenv('HARDHAT_SIDECAR', 'false')
    monkeypatch.setenv('GRANT_CHAIN_ENABLED', 'false')
    monkeypatch.setattr(grant_api, 'hippius_configured', lambda: False)
    create = grant_api.create_app
    monkeypatch.setattr(grant_api, 'create_app',
                        lambda **kwargs: create(dev=True, data_dir=tmp_path))
    client = cloud_app.create_cloud_app().test_client()
    headers = {'Authorization': 'Bearer demo-patient',
               'X-Forwarded-Host': 'untrusted.example'}
    if forwarded:
        headers['X-Forwarded-Proto'] = forwarded
    response = client.post('/api/vault/presign-put', headers=headers)
    assert response.status_code == 200
    assert response.json['url'].startswith(f'{scheme}://localhost/api/vault/local-object?token=')
