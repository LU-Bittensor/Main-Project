import io

from grant_api import create_app


def test_download_permissions_and_revocation(tmp_path):
    client = create_app(dev=True, data_dir=tmp_path).test_client()
    def headers(user):
        return {'Authorization': 'Bearer demo-' + user}
    owner, peer, outsider = [headers(u) for u in ('patient', 'clinician', 'outsider')]
    for h in (owner, peer, outsider):
        assert client.post('/api/enroll', headers=h).status_code == 200
    upload = client.post('/api/records', headers=owner,
                         data={'file': (io.BytesIO(b'example document'), 'test.txt')})
    assert upload.status_code == 201
    rid = upload.json['id']
    base = '/api/records/' + rid
    grant = client.post('/api/grants', headers=owner,
                        json={'recipient_id': 'clinician', 'record_ids': [rid]})
    assert grant.status_code == 200
    gid = grant.json['grants'][0]['id']
    assert client.get(base + '/permission', headers=peer).json['level'] == 'preview'
    assert client.post(base + '/document', headers=peer).status_code == 200
    assert client.post(base + '/download', headers=peer).status_code == 403
    assert client.post(base + '/download', headers=owner).data == b'example document'
    payload = {'recipient_id': 'clinician', 'level': 'download'}
    assert client.put(base + '/permission', headers=peer, json=payload).status_code == 403
    assert client.put(base + '/permission', headers=owner, json={**payload, 'level': 'edit'}).status_code == 400
    assert client.put(base + '/permission', headers=owner, json=payload).status_code == 200
    assert client.post(base + '/download', headers=peer).data == b'example document'
    assert client.post(base + '/download', headers=outsider).status_code == 403
    assert client.post(base + '/download').status_code == 401
    assert client.put(base + '/permission', headers=owner, json={**payload, 'level': 'preview'}).status_code == 200
    assert client.post(base + '/download', headers=peer).status_code == 403
    client.put(base + '/permission', headers=owner, json=payload)
    assert client.delete('/api/grants/' + gid, headers=owner).status_code == 200
    assert client.post(base + '/download', headers=peer).status_code == 403
    assert client.get(base + '/permission', headers=peer).status_code == 403
