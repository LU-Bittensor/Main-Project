"""Synthetic S3 round trip and read-only Supabase schema probe. No patient data.
Run with server environment loaded: python check_hippius.py
"""
import os
import uuid
import requests
from hippius_storage import s3_client, patient_hash, env_key, seal, unseal, HippiusDocumentStore


def main():
    s3 = s3_client()
    owner = str(uuid.uuid4())
    bucket = 'ml-p-' + patient_hash(owner, 'smoke-test', env_key('HIPPIUS_PATIENT_HASH_KEY'))
    object_key = 'synthetic-check.enc'
    key = os.urandom(32)
    content = b'MediLink synthetic storage verification; no patient data.'
    encrypted = seal(key, content, owner)
    created = False
    stage = "create bucket"
    try:
        s3.create_bucket(Bucket=bucket, ACL='private')
        created = True
        stage = "anonymous bucket listing"
        anonymous = s3_client(anonymous=True)
        HippiusDocumentStore._denied(anonymous.list_objects_v2, Bucket=bucket, MaxKeys=1)
        stage = "upload"
        s3.put_object(Bucket=bucket, Key=object_key, Body=encrypted, ContentType='application/octet-stream')
        stage = "anonymous object read"
        HippiusDocumentStore._denied(anonymous.get_object, Bucket=bucket, Key=object_key, Range='bytes=0-0')
        stage = "authenticated download"
        response = s3.get_object(Bucket=bucket, Key=object_key)
        try:
            downloaded = response['Body'].read()
        finally:
            response['Body'].close()
        assert downloaded == encrypted and unseal(key, downloaded, owner) == content
        print('PASS: private bucket, encrypted upload, download and decryption.')
    except Exception as error:
        # Never print remote responses, endpoints with queries, or credentials.
        code = getattr(error, 'response', {}).get('Error', {}).get('Code', type(error).__name__)
        print('FAIL: Hippius synthetic verification at ' + stage + ' (' + str(code) + ').')
        return 1
    finally:
        if created:
            try:
                s3.delete_object(Bucket=bucket, Key=object_key)
                s3.delete_bucket(Bucket=bucket)
                print('Synthetic object and bucket removed.')
            except Exception:
                print('Cleanup incomplete. Remove synthetic bucket: ' + bucket)
    url = os.environ.get('SUPABASE_URL', '').rstrip('/')
    secret = os.getenv('SUPABASE_SECRET_KEY') or os.getenv('SUPABASE_SERVICE_ROLE_KEY', '')
    headers = {'apikey': secret}
    if not secret.startswith('sb_secret_'): headers['Authorization'] = 'Bearer ' + secret
    schema_ready = True
    for table, columns in [('patient_storage', 'id'), ('storage_objects', 'id'), ('records', 'storage_object_id')]:
        response = requests.get(url + '/rest/v1/' + table, headers=headers,
                                params={'select': columns, 'limit': 0}, timeout=20)
        print('Supabase ' + table + ' schema probe: HTTP ' + str(response.status_code))
        schema_ready = schema_ready and response.status_code == 200
    if not schema_ready:
        print('Apply the Supabase storage migration before enabling Hippius document mode.')
    return 0 if schema_ready else 2


if __name__ == '__main__':
    raise SystemExit(main())
