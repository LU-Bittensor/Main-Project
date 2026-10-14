"""Copy legacy encrypted local documents into records, preserving IDs and grants.

Requires SUPABASE_URL, SUPABASE_SECRET_KEY (or SUPABASE_SERVICE_ROLE_KEY), and
GRANT_MASTER_KEY. Defaults to dry-run; --apply writes. Never deletes local files.
"""
import argparse
from contextlib import closing
import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3
import uuid

from DHkey_share import decrypt_secret
from documents import SupabaseDocumentStore, document_type


def migrate(path, key, store, apply=False):
    counts = {'copied': 0, 'already_present': 0, 'pending': 0}
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        expected = hmac.digest(key, b'medilink-document-key', 'sha256').hex()
        saved = db.execute("SELECT value FROM settings WHERE name='key_check'").fetchone()
        if not saved or not hmac.compare_digest(saved[0], expected):
            raise RuntimeError('GRANT_MASTER_KEY does not match the legacy document database')
        for row in db.execute('SELECT * FROM documents ORDER BY created_at, id'):
            # Demo identities must never be migrated into a real account database.
            uuid.UUID(row['owner_id'])
            content = decrypt_secret(key, row['content'], 'document:' + row['id'])
            document_type(row['name'], content)
            existing = store.get(row['id'])
            if existing:
                if (existing['owner_id'] != row['owner_id'] or not existing['has_document'] or
                        store.read(row['id'], row['owner_id']) != content):
                    raise RuntimeError('A conflicting record already exists; migration stopped without overwriting it')
                counts['already_present'] += 1
            elif apply:
                store.add(row['owner_id'], row['name'], json.loads(row['tags']), row['mime_type'], content,
                          identifier=row['id'], kind=row['kind'], created_at=row['created_at'])
                if store.read(row['id'], row['owner_id']) != content:
                    raise RuntimeError('Uploaded document verification failed')
                counts['copied'] += 1
            else:
                counts['pending'] += 1
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    url = os.environ['SUPABASE_URL'].rstrip('/')
    expected_name = hashlib.sha256(url.encode()).hexdigest() + '.sqlite3'
    if args.database.name != expected_name:
        parser.error('This legacy database filename does not match SUPABASE_URL. Use the original project.')
    store = SupabaseDocumentStore(url, os.getenv('SUPABASE_SECRET_KEY') or os.getenv('SUPABASE_SERVICE_ROLE_KEY', ''))
    result = migrate(args.database, bytes.fromhex(os.environ['GRANT_MASTER_KEY']), store, args.apply)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
