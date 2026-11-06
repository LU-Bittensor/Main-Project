"""Immutable Epic import reports served through existing authenticated grants."""
from contextlib import closing
from datetime import datetime, UTC
import json
import sqlite3
import uuid

from documents import DocumentStorageError, MAX_DOCUMENT_BYTES
from epic_import import clinical_records

PREFIX = 'epic-import-'


class ImportDocumentStore:
    def __init__(self, documents, imports, uploads_configured):
        self.documents, self.imports = documents, imports
        self.uploads_configured = uploads_configured

    @property
    def supports_client_encryption(self):
        return getattr(self.documents, 'supports_client_encryption', False)

    def encryption_info(self, identifier, owner, recipient):
        if identifier.startswith(PREFIX) or not self.supports_client_encryption:
            return None
        return self.documents.encryption_info(identifier, owner, recipient)

    def save_envelope(self, identifier, owner, recipient, envelope):
        if identifier.startswith(PREFIX) or not self.supports_client_encryption:
            raise DocumentStorageError('This source does not support client document encryption.')
        return self.documents.save_envelope(identifier, owner, recipient, envelope)

    def _snapshot(self, identifier, owner=None):
        value = identifier.removeprefix(PREFIX)
        try:
            if str(uuid.UUID(value)) != value:
                return None
        except (ValueError, AttributeError):
            return None
        with closing(sqlite3.connect(self.imports.path)) as db:
            row = db.execute("SELECT id, summary, payload, digest, user_id FROM epic_import_jobs WHERE id=? AND status='completed'", (value,)).fetchone()
        if row is None or (owner is not None and row[4] != owner):
            return None
        return row

    def _metadata(self, row):
        summary = json.loads(row[1])
        date = datetime.fromtimestamp(summary['completed_at'], UTC)
        return {'id': PREFIX + row[0], 'owner_id': row[4],
                'name': 'Epic Sandbox import ' + date.strftime('%Y-%m-%d %H:%M:%S UTC') + '.txt',
                'kind': 'Epic import', 'created_at': date.isoformat(),
                'tags': ['Epic Sandbox', 'Saved import'], 'mime_type': 'text/plain',
                'size': None, 'has_document': True,
                'share_note': 'Shares all clinical records in this saved import. Later imports are not included.'}

    def owned(self, owner):
        # A new import is a new immutable report. Existing grants keep pointing
        # to their original snapshot and never expand to newly fetched data.
        with closing(sqlite3.connect(self.imports.path)) as db:
            row = db.execute("SELECT id, summary, payload, digest, user_id FROM epic_import_jobs WHERE user_id=? AND status='completed' ORDER BY created_at DESC, rowid DESC LIMIT 1", (owner,)).fetchone()
        snapshots = [self._metadata(row)] if row else []
        # A disabled document source must not block the configured local Epic
        # source. The API health response and UI explicitly disclose this state.
        return (self.documents.owned(owner) if self.uploads_configured else []) + snapshots

    def get(self, identifier):
        if not identifier.startswith(PREFIX):
            return self.documents.get(identifier)
        row = self._snapshot(identifier)
        return self._metadata(row) if row else None

    def add(self, *args, **kwargs):
        return self.documents.add(*args, **kwargs)

    def read(self, identifier, owner):
        if not identifier.startswith(PREFIX):
            return self.documents.read(identifier, owner)
        row = self._snapshot(identifier, owner)
        if row is None:
            raise DocumentStorageError('This saved import is unavailable.')
        # The calling route must verify ownership or the active exact-recipient
        # grant first. Cryptographic context is still bound to the import owner.
        payload = self.imports._read_payload(owner, row)
        records = clinical_records(payload)
        metadata = self._metadata(row)
        lines = ['MediLink — Epic Sandbox saved import', 'Imported: ' + metadata['created_at'],
                 f'Clinical records: {len(records)}',
                 metadata['share_note'],
                 'Contains records available to the authorized Epic account, not necessarily the full chart.',
                 'Patient identifiers, raw FHIR IDs, and access tokens are not included.', '']
        labels = {'vital-signs': 'Vital signs', 'laboratory': 'Lab results', 'conditions': 'Conditions',
                  'medications': 'Medications', 'encounters': 'Encounters'}
        for category, label in labels.items():
            group = [record for record in records if record['category'] == category]
            lines += [f'{label} ({len(group)})', '-' * 32]
            if not group:
                lines.append('No records returned in this category.')
            for record in group:
                value = 'Not provided' if record['value'] is None else str(record['value'])
                if record['unit']:
                    value += ' ' + record['unit']
                lines += [record['name'], '  Value / details: ' + value,
                          '  Date from Epic: ' + (record['date'] or 'Not provided'),
                          '  Status: ' + (record['status'] or 'Not provided'), '']
            lines.append('')
        content = '\n'.join(lines).encode('utf-8')
        if len(content) > MAX_DOCUMENT_BYTES:
            raise DocumentStorageError('This import report is too large to preview.')
        return content
