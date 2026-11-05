"""Document bytes and metadata live together in Supabase public.records."""
from pathlib import Path
import uuid

import requests

MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
METADATA_COLUMNS = 'id,owner_id,name,kind,tags,file_mime_type,file_size,created_at'


class DocumentStorageError(Exception):
    pass


def document_type(name, content):
    extension = Path(name).suffix.lower()
    if extension == '.pdf' and content.startswith(b'%PDF-'):
        return 'application/pdf'
    if extension == '.png' and content.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if extension in ('.jpg', '.jpeg') and content.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if extension == '.txt' and b'\x00' not in content:
        try:
            content.decode('utf-8')
            return 'text/plain'
        except UnicodeDecodeError:
            pass
    raise ValueError('Choose a PDF, PNG, JPEG, or UTF-8 text file matching its extension.')


def metadata(row):
    return {**{k: row[k] for k in ('id', 'owner_id', 'name', 'kind', 'created_at')},
            'tags': [tag.strip() for tag in row.get('tags', '').split(',') if tag.strip()],
            'mime_type': row.get('file_mime_type'), 'size': row.get('file_size'),
            'has_document': row.get('file_mime_type') is not None}


class SupabaseDocumentStore:
    def __init__(self, url, secret_key):
        self.url = url.rstrip('/') + '/rest/v1/records'
        self.secret_key = secret_key

    def request(self, method, **kwargs):
        if not self.secret_key:
            raise DocumentStorageError('Configure SUPABASE_SECRET_KEY or SUPABASE_SERVICE_ROLE_KEY on the Python API server to use Supabase documents.')
        headers = {'apikey': self.secret_key}
        # New secret keys are opaque API keys, not bearer JWTs.
        if not self.secret_key.startswith('sb_secret_'):
            headers['Authorization'] = 'Bearer ' + self.secret_key
        headers.update(kwargs.pop('headers', {}))
        try:
            response = requests.request(method, self.url, headers=headers, timeout=60, **kwargs)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as error:
            # Do not expose service credentials or document contents in exceptions/logs.
            raise DocumentStorageError('Supabase document storage is unavailable. Check the server key and apply the records document migration.') from error

    def add(self, owner, name, tags, mime, content, *, identifier=None, kind='Document', created_at=None):
        row = {'id': identifier or str(uuid.uuid4()), 'owner_id': owner, 'name': name, 'kind': kind,
               'tags': ', '.join(tags), 'file_mime_type': mime, 'file_size': len(content),
               'file_content': '\\x' + content.hex()}
        if created_at is not None:
            row['created_at'] = created_at
        rows = self.request('POST', params={'select': METADATA_COLUMNS},
                            headers={'Prefer': 'return=representation'}, json=row)
        return metadata(rows[0])

    def get(self, identifier):
        rows = self.request('GET', params={'id': 'eq.' + identifier, 'select': METADATA_COLUMNS})
        return metadata(rows[0]) if rows else None

    def owned(self, owner):
        # Paginate explicitly rather than silently dropping records at the REST row limit.
        records = []
        while True:
            rows = self.request('GET', params={'owner_id': 'eq.' + owner, 'select': METADATA_COLUMNS,
                                'order': 'created_at.desc,id', 'limit': 500, 'offset': len(records)})
            records.extend(metadata(row) for row in rows)
            if not rows:
                return records

    def read(self, identifier, owner):
        # Ownership is constrained again at the point bytes are fetched.
        rows = self.request('GET', params={'id': 'eq.' + identifier, 'owner_id': 'eq.' + owner,
                                          'select': 'file_content'})
        if not rows or rows[0]['file_content'] is None:
            raise DocumentStorageError('This record has no uploaded document.')
        encoded = rows[0]['file_content']
        if not isinstance(encoded, str) or not encoded.startswith('\\x'):
            raise DocumentStorageError('Supabase returned an invalid document encoding.')
        try:
            return bytes.fromhex(encoded[2:])
        except ValueError as error:
            raise DocumentStorageError('Supabase returned an invalid document encoding.') from error


class DemoDocumentStore:
    """Synthetic demo files only: memory-only, discarded when the demo API exits."""
    def __init__(self):
        self.rows = {}
        self.contents = {}

    def add(self, owner, name, tags, mime, content, *, kind='Document'):
        from datetime import datetime, timezone
        identifier = str(uuid.uuid4())
        self.rows[identifier] = {'id': identifier, 'owner_id': owner, 'name': name,
            'kind': kind, 'tags': tags, 'mime_type': mime, 'size': len(content),
            'has_document': True, 'created_at': datetime.now(timezone.utc).isoformat()}
        self.contents[identifier] = content
        return self.get(identifier)

    def get(self, identifier):
        return self.rows.get(identifier)

    def owned(self, owner):
        return [row for row in self.rows.values() if row['owner_id'] == owner]

    def read(self, identifier, owner):
        if self.rows[identifier]['owner_id'] != owner:
            raise DocumentStorageError('Document ownership changed.')
        return self.contents[identifier]
