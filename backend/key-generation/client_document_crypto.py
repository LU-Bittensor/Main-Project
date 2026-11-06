"""Validate signed client envelopes only. No content/DH private keys or decryption."""
import base64
import hashlib
import json
import re
import uuid
from eth_account import Account
from eth_account.messages import encode_defunct

MIMES = {'application/pdf', 'image/png', 'image/jpeg', 'text/plain'}


def decode(value, size):
    if not isinstance(value, str): raise ValueError('Invalid key encoding')
    raw = base64.b64decode(value, validate=True)
    if len(raw) != size: raise ValueError('Invalid key length')
    return raw


def message(manifest, envelope):
    m = manifest
    core = ['medilink-document-v1', m['record_id'], m['owner_id'], m['owner_wallet'].lower(),
            m['owner_public_key'], m['mime'], m['size'], m['ciphertext_sha256'],
            envelope['recipient_id'], envelope['recipient_public_key']]
    context = json.dumps(core, separators=(',', ':'), ensure_ascii=False)
    return json.dumps(['medilink-document-envelope-v1', context, envelope['wrapped_key']], separators=(',', ':'), ensure_ascii=False)


def validate_manifest(m, owner):
    if not isinstance(m, dict) or set(m) != {'version','record_id','owner_id','owner_wallet','owner_public_key','mime','size','ciphertext_sha256'}:
        raise ValueError('Invalid client encryption manifest')
    if type(m['version']) is not int or m['version'] != 1 or m['owner_id'] != owner:
        raise ValueError('Encryption owner or version mismatch')
    if not isinstance(m['record_id'], str): raise ValueError('Invalid document identifier')
    if str(uuid.UUID(m['record_id'])) != m['record_id']: raise ValueError('Invalid document identifier')
    decode(m['owner_public_key'], 32)
    if not isinstance(m['owner_wallet'], str) or not re.fullmatch(r'0x[0-9a-fA-F]{40}', m['owner_wallet']): raise ValueError('Invalid owner wallet')
    if m['mime'] not in MIMES or type(m['size']) is not int or not 1 <= m['size'] <= 10485760: raise ValueError('Invalid document type or size')
    if not isinstance(m['ciphertext_sha256'], str) or not re.fullmatch('[a-f0-9]{64}', m['ciphertext_sha256']): raise ValueError('Invalid ciphertext hash')


def validate_envelope(m, e, recipient, public_key):
    if not isinstance(e, dict) or set(e) != {'recipient_id','recipient_public_key','wrapped_key','signature'}:
        raise ValueError('Invalid document key envelope')
    if e['recipient_id'] != recipient or e['recipient_public_key'] != public_key:
        raise ValueError('Encryption recipient mismatch')
    decode(e['recipient_public_key'], 32)
    decode(e['wrapped_key'], 60)
    if not isinstance(e['signature'], str) or not re.fullmatch(r'0x[0-9a-fA-F]{130}', e['signature']): raise ValueError('Invalid envelope signature')
    recovered = Account.recover_message(encode_defunct(text=message(m, e)), signature=e['signature'])
    if recovered.lower() != m['owner_wallet'].lower(): raise ValueError('Invalid envelope signer')


def validate_upload(info, content, owner, wallet, public_key):
    if not isinstance(info, dict) or set(info) != {'manifest','envelope'}: raise ValueError('Invalid client encryption payload')
    m = info['manifest']
    validate_manifest(m, owner)
    if m['owner_wallet'].lower() != wallet.lower() or m['owner_public_key'] != public_key:
        raise ValueError('Unlock and enroll the original vault before uploading')
    validate_envelope(m, info['envelope'], owner, public_key)
    if len(content) != m['size'] + 28 or hashlib.sha256(content).hexdigest() != m['ciphertext_sha256']:
        raise ValueError('Ciphertext size or hash mismatch')
