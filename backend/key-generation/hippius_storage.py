"""Encrypted, private Hippius objects with backend-only Supabase pointers.

No patient identity, plaintext name or clinical metadata is sent to S3. Existing
Supabase documents remain readable. Storage keys are independent of chain resets.
"""
import base64
import hashlib
import hmac
import os
import re
import uuid

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from documents import SupabaseDocumentStore, DocumentStorageError, MAX_DOCUMENT_BYTES, metadata

MAGIC = b'MLHP1'


def seal(key, plaintext, context):
    nonce = os.urandom(12)
    return MAGIC + nonce + AESGCM(key).encrypt(nonce, plaintext, context.encode())


def unseal(key, ciphertext, context):
    if not ciphertext.startswith(MAGIC) or len(ciphertext) < 33:
        raise ValueError('Invalid encrypted object')
    return AESGCM(key).decrypt(ciphertext[5:17], ciphertext[17:], context.encode())


def patient_hash(owner, namespace, secret):
    owner = str(uuid.UUID(owner))
    digest = hmac.digest(secret, (namespace + ':' + owner).encode(), 'sha256')
    return base64.b32encode(digest).decode().rstrip('=').lower()


def env_key(name):
    try:
        key = bytes.fromhex(os.environ[name])
        if len(key) != 32:
            raise ValueError()
        return key
    except (KeyError, ValueError):
        raise DocumentStorageError(f'Set {name} to a persistent 32-byte hex key on the API server.') from None


def s3_client(*, anonymous=False):
    import boto3
    from botocore.config import Config
    from botocore import UNSIGNED
    if not anonymous and (not os.getenv('HIPPIUS_ACCESS_KEY_ID') or not os.getenv('HIPPIUS_SECRET_ACCESS_KEY')):
        raise DocumentStorageError('Configure server-only Hippius S3 credentials.')
    return boto3.client('s3', endpoint_url='https://s3.hippius.com', region_name='decentralized',
        aws_access_key_id=None if anonymous else os.environ['HIPPIUS_ACCESS_KEY_ID'],
        aws_secret_access_key=None if anonymous else os.environ['HIPPIUS_SECRET_ACCESS_KEY'],
        config=Config(signature_version=UNSIGNED if anonymous else 's3v4', s3={'addressing_style': 'path'},
                      connect_timeout=10, read_timeout=60, retries={'max_attempts': 2},
                      request_checksum_calculation='when_required', response_checksum_validation='when_required'))


class Catalog(SupabaseDocumentStore):
    """Reuse the existing sanitized REST transport for fixed internal table paths."""
    def table(self, table, method='GET', **kwargs):
        client = SupabaseDocumentStore.__new__(SupabaseDocumentStore)
        client.url = self.url.rsplit('/', 1)[0] + '/' + table
        client.secret_key = self.secret_key
        return client.request(method, **kwargs)


class HippiusDocumentStore(SupabaseDocumentStore):
    supports_client_encryption = True
    def __init__(self, url, secret_key, *, s3=None, hash_key=None, master_key=None,
                 namespace=None, catalog=None, key_version='v1', anonymous=None):
        super().__init__(url, secret_key)
        self.catalog = catalog or Catalog(url, secret_key)
        self.hash_key = hash_key if hash_key is not None else env_key('HIPPIUS_PATIENT_HASH_KEY')
        self.master_key = master_key if master_key is not None else env_key('HIPPIUS_STORAGE_MASTER_KEY')
        self.namespace = namespace or os.getenv('HIPPIUS_NAMESPACE', '')
        if not re.fullmatch(r'[a-z0-9-]{1,40}', self.namespace):
            raise DocumentStorageError('Set HIPPIUS_NAMESPACE to a stable deployment name (letters, digits, hyphens).')
        self.key_version = key_version
        self.s3 = s3 if s3 is not None else s3_client()
        self.anonymous = anonymous if anonymous is not None else s3_client(anonymous=True)

    def _private_bucket(self, bucket, *, create=False):
        if not re.fullmatch(r'ml-p-[a-z2-7]{52}', bucket):
            raise ValueError('Invalid bucket pointer')
        if create:
            try:
                self.s3.create_bucket(Bucket=bucket, ACL='private')
            except Exception as error:
                # Never adopt a collision owned by a different account, or treat
                # AccessDenied/network errors as a missing bucket.
                if getattr(error, 'response', {}).get('Error', {}).get('Code') != 'BucketAlreadyOwnedByYou':
                    raise
        try:
            acl = self.s3.get_bucket_acl(Bucket=bucket)
        except Exception as error:
            if getattr(error, 'response', {}).get('Error', {}).get('Code') != 'AccessControlListNotSupported':
                raise
            # Some deployed gateways disable ACL APIs. Verify actual anonymous
            # listing denial instead; each object's read denial is checked too.
            self._denied(self.anonymous.list_objects_v2, Bucket=bucket, MaxKeys=1)
        else:
            owner = acl['Owner']['ID']
            if not acl['Grants'] or any(g['Grantee'].get('Type') != 'CanonicalUser'
                                       or g['Grantee'].get('ID') != owner for g in acl['Grants']):
                raise ValueError('Bucket is not private')

    @staticmethod
    def _denied(operation, **kwargs):
        try:
            result = operation(**kwargs)
        except Exception as error:
            response = getattr(error, 'response', {})
            if response.get('ResponseMetadata', {}).get('HTTPStatusCode') in (401, 403):
                return
            raise
        else:
            if 'Body' in result:
                result['Body'].close()
            raise ValueError('Anonymous storage access was not denied')

    def _patient(self, owner, *, provision=True, need_key=True):
        rows = self.catalog.table('patient_storage', params={'owner_id': 'eq.' + owner, 'select': '*'})
        if not rows:
            if not provision:
                raise ValueError('Missing patient storage')
            digest = patient_hash(owner, self.namespace, self.hash_key)
            data_key = os.urandom(32)
            row = {'owner_id': owner, 'patient_hash': digest, 'bucket_name': 'ml-p-' + digest,
                   'namespace': self.namespace, 'hash_key_version': self.key_version,
                   'encryption_key_version': self.key_version,
                   'wrapped_key': base64.b64encode(seal(self.master_key, data_key, 'patient-key:' + owner)).decode()}
            # Winner's key is always re-read: simultaneous first uploads must
            # never encrypt with the losing request's generated data key.
            self.catalog.table('patient_storage', 'POST', params={'on_conflict': 'owner_id'},
                               headers={'Prefer': 'resolution=ignore-duplicates,return=representation'}, json=row)
            rows = self.catalog.table('patient_storage', params={'owner_id': 'eq.' + owner, 'select': '*'})
        patient = rows[0]
        if patient['namespace'] != self.namespace or patient['encryption_key_version'] != self.key_version:
            raise ValueError('Storage configuration mismatch')
        key = unseal(self.master_key, base64.b64decode(patient['wrapped_key'], validate=True), 'patient-key:' + owner) if need_key else None
        if not provision and patient['status'] != 'ready':
            raise ValueError('Patient storage not ready')
        self._private_bucket(patient['bucket_name'], create=patient['status'] == 'provisioning')
        if patient['status'] != 'ready':
            self.catalog.table('patient_storage', 'PATCH', params={'id': 'eq.' + patient['id']},
                               headers={'Prefer': 'return=representation'}, json={'status': 'ready'})
        return patient, key

    @staticmethod
    def _context(owner, obj):
        return 'hippius-object:' + owner + ':' + obj['id'] + ':' + obj['object_key']

    def _download(self, patient, obj, key):
        self._denied(self.anonymous.get_object, Bucket=patient['bucket_name'], Key=obj['object_key'], Range='bytes=0-0')
        result = self.s3.get_object(Bucket=patient['bucket_name'], Key=obj['object_key'])
        body = result['Body']
        try:
            encrypted = body.read(MAX_DOCUMENT_BYTES + 34)
        finally:
            body.close()
        if len(encrypted) != obj['ciphertext_size'] or not hmac.compare_digest(
                hashlib.sha256(encrypted).hexdigest(), obj['ciphertext_sha256']):
            raise ValueError('Object integrity failure')
        if obj.get('encryption_format') == 'client-x25519-v1':
            return encrypted
        plaintext = unseal(key, encrypted, self._context(obj['owner_id'], obj))
        if len(plaintext) != obj['plaintext_size']:
            raise ValueError('Object size mismatch')
        return plaintext

    def add(self, owner, name, tags, mime, content, *, identifier=None, kind='Document', created_at=None, client_info=None):
        try:
            owner = str(uuid.UUID(owner))
            if not 0 < len(content) <= MAX_DOCUMENT_BYTES + (28 if client_info else 0):
                raise ValueError('Invalid size')
            patient, key = self._patient(owner, need_key=client_info is None)
            record_id = str(uuid.UUID(identifier)) if identifier else str(uuid.uuid4())
            object_id = str(uuid.uuid4())
            obj = {'id': object_id, 'owner_id': owner, 'patient_storage_id': patient['id'],
                   'object_key': 'documents/' + object_id + '.enc', 'mime_type': mime,
                   'plaintext_size': len(content), 'ciphertext_size': len(content) + 33}
            if client_info:
                obj.update(plaintext_size=client_info['manifest']['size'], ciphertext_size=len(content),
                           encryption_format='client-x25519-v1', client_info=client_info)
            encrypted = content if client_info else seal(key, content, self._context(owner, obj))
            obj['ciphertext_sha256'] = hashlib.sha256(encrypted).hexdigest()
            self.catalog.table('storage_objects', 'POST', headers={'Prefer': 'return=representation'}, json=obj)
            self.s3.put_object(Bucket=patient['bucket_name'], Key=obj['object_key'], Body=encrypted,
                               ContentType='application/octet-stream')
            # Verify actual downloaded bytes/decryption, not multipart ETag.
            if self._download(patient, obj, key) != content:
                raise ValueError('Upload verification failed')
            self.catalog.table('storage_objects', 'PATCH', params={'id': 'eq.' + object_id},
                               headers={'Prefer': 'return=representation'}, json={'status': 'uploaded'})
            rows = self.catalog.table('rpc/publish_hippius_document', 'POST', json={
                'p_owner': owner, 'p_record': record_id, 'p_object': object_id,
                'p_name': name, 'p_tags': ', '.join(tags), 'p_kind': kind, 'p_created_at': created_at})
            return metadata(rows[0])
        except Exception:
            # Never delete after an ambiguous publish response: the transaction
            # might have committed. Retain pending entries for reconciliation.
            raise DocumentStorageError('Encrypted upload could not be completed. Check Hippius configuration and the storage migration.') from None

    def read(self, identifier, owner):
        try:
            rows = self.request('GET', params={'id': 'eq.' + identifier, 'owner_id': 'eq.' + owner,
                                               'select': 'storage_object_id'})
            if not rows:
                raise ValueError('Missing record')
            object_id = rows[0]['storage_object_id']
            if object_id is None:
                return super().read(identifier, owner)
            objects = self.catalog.table('storage_objects', params={
                'id': 'eq.' + object_id, 'owner_id': 'eq.' + owner, 'status': 'eq.ready', 'select': '*'})
            if not objects:
                raise ValueError('Missing ready object')
            obj = objects[0]
            patient, key = self._patient(owner, provision=False, need_key=obj.get('encryption_format') != 'client-x25519-v1')
            if obj['patient_storage_id'] != patient['id']:
                raise ValueError('Patient storage mismatch')
            return self._download(patient, obj, key)
        except Exception:
            raise DocumentStorageError('Encrypted document is unavailable or failed integrity verification.') from None


    def encryption_info(self, identifier, owner, recipient):
        rows = self.request('GET', params={'id': 'eq.' + identifier, 'owner_id': 'eq.' + owner, 'select': 'storage_object_id'})
        if not rows or not rows[0]['storage_object_id']:
            return None
        object_id = rows[0]['storage_object_id']
        objects = self.catalog.table('storage_objects', params={'id': 'eq.' + object_id, 'owner_id': 'eq.' + owner, 'status': 'eq.ready', 'select': 'client_info'})
        if not objects or not objects[0].get('client_info'):
            return None
        info = objects[0]['client_info']
        if owner == recipient:
            return info
        envelopes = self.catalog.table('document_key_envelopes', params={'object_id': 'eq.' + object_id, 'recipient_id': 'eq.' + recipient, 'owner_id': 'eq.' + owner, 'select': 'envelope'})
        if not envelopes:
            raise DocumentStorageError('The owner must share a document key with your vault before you can open it.')
        return {'manifest': info['manifest'], 'envelope': envelopes[0]['envelope']}

    def save_envelope(self, identifier, owner, recipient, envelope):
        rows = self.request('GET', params={'id': 'eq.' + identifier, 'owner_id': 'eq.' + owner, 'select': 'storage_object_id'})
        if not rows or not rows[0]['storage_object_id']:
            raise DocumentStorageError('Encrypted document unavailable.')
        return self.catalog.table('document_key_envelopes', 'POST', params={'on_conflict': 'object_id,recipient_id'},
            headers={'Prefer': 'resolution=merge-duplicates,return=representation'},
            json={'object_id': rows[0]['storage_object_id'], 'owner_id': owner, 'recipient_id': recipient, 'envelope': envelope})
