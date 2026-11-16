"""Durability, ownership, immutability and corruption checks for local ciphertext."""
import base64
import json
from pathlib import Path
import sqlite3

import pytest
from documents import DocumentStorageError
from local_documents import LocalDocumentStore


@pytest.fixture
def saved(tmp_path):
    fixture = json.loads((Path(__file__).parent/'fixtures/epic_shared_document.json').read_text())
    info = fixture['info']
    manifest = info['manifest']
    content = base64.b64decode(fixture['ciphertext'])
    store = LocalDocumentStore(tmp_path)
    args = (manifest['owner_id'], 'Epic-snapshot.txt', ['Epic'], 'text/plain', content)
    kwargs = dict(identifier=manifest['record_id'], kind='Epic import', client_info=info)
    row = store.add(*args, **kwargs)
    return store, fixture, args, kwargs, row


def test_local_ciphertext_is_durable_private_and_immutable(saved, tmp_path):
    store, fixture, args, kwargs, row = saved
    restarted = LocalDocumentStore(tmp_path)
    assert restarted.get(row['id']) == row
    assert restarted.read(row['id'], row['owner_id']) == args[-1]
    assert restarted.owned('outsider') == []
    assert restarted.owned(row['owner_id']) == [row]
    assert restarted.add(*args, **kwargs) == row
    assert row['size'] == fixture['info']['manifest']['size']
    assert row['client_encrypted'] and 'ciphertext' not in row
    assert (store.path.stat().st_mode & 0o777) == 0o600
    with pytest.raises(DocumentStorageError):
        restarted.read(row['id'], 'outsider')
    with pytest.raises(DocumentStorageError):
        restarted.add('outsider', *args[1:], **kwargs)
    # Even another valid encryption of the same snapshot ID must not replace it.
    changed = json.loads(json.dumps(kwargs))
    changed['client_info']['envelope']['wrapped_key'] = 'replacement'
    with pytest.raises(DocumentStorageError):
        restarted.add(*args, **changed)
    assert restarted.read(row['id'], row['owner_id']) == args[-1]


def test_local_store_refuses_plaintext_and_tampered_ciphertext(saved):
    store, _, args, kwargs, row = saved
    with pytest.raises(DocumentStorageError):
        store.add(*args)
    with pytest.raises(DocumentStorageError):
        store.add(*args[:-1], b'plaintext', **kwargs)
    with sqlite3.connect(store.path) as db:
        db.execute('UPDATE documents SET ciphertext=?', (b'tampered',))
    with pytest.raises(DocumentStorageError):
        store.read(row['id'], row['owner_id'])


def test_recipient_envelopes_persist_and_cannot_be_written_by_foreign_owner(saved, tmp_path):
    store, fixture, _, _, row = saved
    envelope = fixture['recipientEnvelope']
    peer = envelope['recipient_id']
    with pytest.raises(DocumentStorageError):
        store.encryption_info(row['id'], row['owner_id'], peer)
    with pytest.raises(DocumentStorageError):
        store.save_envelope(row['id'], peer, peer, envelope)
    store.save_envelope(row['id'], row['owner_id'], peer, envelope)
    # The API separately checks the active grant before calling these storage methods.
    info = LocalDocumentStore(tmp_path).encryption_info(row['id'], row['owner_id'], peer)
    assert info['envelope'] == envelope
    assert info['manifest'] == fixture['info']['manifest']
