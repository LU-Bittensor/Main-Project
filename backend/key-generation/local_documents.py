"""Opt-in development storage: durable browser ciphertext and signed key envelopes.

Supabase still authenticates users. The grant API authorizes every key/file read.
No plaintext upload or server-held document decryption key is supported here.
"""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from documents import DocumentStorageError, metadata


class LocalDocumentStore:
    supports_client_encryption = True

    def __init__(self, directory):
        """Keep local records beside the auth-bound grant database."""
        directory = Path(directory)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = directory / 'local-documents.sqlite3'
        # Pre-create privately before SQLite opens it; protect rollback journals too.
        if not self.path.exists():
            try:
                with self.path.open('xb'):
                    pass
            except FileExistsError:
                pass
        self.path.chmod(0o600)
        with closing(self._db()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
                    metadata TEXT NOT NULL, client_info TEXT NOT NULL,
                    ciphertext BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS envelopes (
                    record_id TEXT NOT NULL REFERENCES documents(id),
                    recipient_id TEXT NOT NULL, envelope TEXT NOT NULL,
                    PRIMARY KEY (record_id, recipient_id));
            """)

    def _db(self):
        """Open a short-lived SQLite connection with integrity constraints."""
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        return db

    def add(self, owner, name, tags, mime, content, *, identifier=None,
            kind='Document', created_at=None, client_info=None):
        """Publish an immutable browser-encrypted document after API validation."""
        if not client_info or not identifier:
            raise DocumentStorageError('Local storage requires browser-encrypted documents.')
        manifest = client_info['manifest']
        if (manifest['record_id'] != identifier or manifest['owner_id'] != owner
                or manifest['ciphertext_sha256'] != hashlib.sha256(content).hexdigest()):
            raise DocumentStorageError('Encrypted document identity or integrity mismatch.')
        row = metadata(dict(id=identifier, owner_id=owner, name=name, kind=kind,
            tags=', '.join(tags), file_mime_type=mime, file_size=manifest['size'],
            created_at=created_at or datetime.now(timezone.utc).isoformat(), client_encrypted=True))
        encoded_info = json.dumps(client_info, sort_keys=True)
        with closing(self._db()) as db, db:
            existing = db.execute('SELECT * FROM documents WHERE id=?', (identifier,)).fetchone()
            if existing:
                if (existing['owner_id'] != owner or existing['ciphertext'] != content
                        or existing['client_info'] != encoded_info):
                    raise DocumentStorageError('This saved document already exists and cannot be replaced.')
                return json.loads(existing['metadata'])
            db.execute('INSERT INTO documents VALUES (?,?,?,?,?)',
                       (identifier, owner, json.dumps(row), encoded_info, content))
        return row

    def get(self, identifier):
        """Return metadata; callers must authorize access before delivery."""
        with closing(self._db()) as db:
            row = db.execute('SELECT metadata FROM documents WHERE id=?', (identifier,)).fetchone()
        return json.loads(row['metadata']) if row else None

    def owned(self, owner):
        """List only the owner's saved snapshots, newest first."""
        with closing(self._db()) as db:
            rows = db.execute('SELECT metadata FROM documents WHERE owner_id=? ORDER BY rowid DESC',
                              (owner,)).fetchall()
        return [json.loads(row['metadata']) for row in rows]

    def read(self, identifier, owner):
        """Return ciphertext only, scoped to the authenticated document owner."""
        with closing(self._db()) as db:
            row = db.execute('SELECT ciphertext,client_info FROM documents WHERE id=? AND owner_id=?',
                             (identifier, owner)).fetchone()
        if row is None:
            raise DocumentStorageError('Encrypted document unavailable.')
        info = json.loads(row['client_info'])
        if hashlib.sha256(row['ciphertext']).hexdigest() != info['manifest']['ciphertext_sha256']:
            raise DocumentStorageError('Encrypted document failed integrity verification.')
        return row['ciphertext']

    def encryption_info(self, identifier, owner, recipient):
        """Read a signed owner/recipient envelope after the API checks a live grant."""
        with closing(self._db()) as db:
            row = db.execute('SELECT client_info FROM documents WHERE id=? AND owner_id=?',
                             (identifier, owner)).fetchone()
            if row is None:
                raise DocumentStorageError('Encrypted document unavailable.')
            info = json.loads(row['client_info'])
            if owner == recipient:
                return info
            peer = db.execute('SELECT envelope FROM envelopes WHERE record_id=? AND recipient_id=?',
                              (identifier, recipient)).fetchone()
        if peer is None:
            raise DocumentStorageError('The owner must share a document key with your vault first.')
        return dict(manifest=info['manifest'], envelope=json.loads(peer['envelope']))

    def save_envelope(self, identifier, owner, recipient, envelope):
        """Store an API-validated signed recipient envelope without granting access."""
        with closing(self._db()) as db, db:
            row = db.execute('SELECT id FROM documents WHERE id=? AND owner_id=?',
                             (identifier, owner)).fetchone()
            if row is None:
                raise DocumentStorageError('Encrypted document unavailable.')
            db.execute('INSERT INTO envelopes VALUES (?,?,?) '
                       'ON CONFLICT(record_id,recipient_id) DO UPDATE SET envelope=excluded.envelope',
                       (identifier, recipient, json.dumps(envelope)))
