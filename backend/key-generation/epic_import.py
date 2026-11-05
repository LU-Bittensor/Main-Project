"""Account-scoped sandbox import jobs. FHIR payloads stay encrypted on the server."""
from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from threading import BoundedSemaphore, Thread
import time
from urllib.parse import parse_qs, urlsplit
import uuid

from DHkey_share import decrypt_secret, encrypt_secret
from epic_oauth import FHIR_BASE, EpicOAuthError

# Share the same fetcher/normalizer used by the verified three-patient CLI runs.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'subnet-template'))
import epic_sandbox_live as live
import epic_fhir_ingest as ingest
import epic_fhir_reconcile as reconcile


class EpicImportError(Exception):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def clinical_records(payload):
    records = []
    for entry in payload['bundle']['entry']:
        resource = entry['resource']
        kind = resource['resourceType']
        value, unit, date, status = None, None, None, resource.get('status')
        if kind == 'Observation':
            groups = {coding.get('code') for item in resource.get('category', []) for coding in item.get('coding', [])}
            group = 'laboratory' if 'laboratory' in groups else 'vital-signs'
            mapped = ingest.map_observation(resource)
            name, value, unit, date = mapped['name'], mapped['value'], mapped['unit'], mapped['effectiveDate']
            date = date or (resource.get('effectivePeriod') or {}).get('start') or resource.get('issued')
        elif kind == 'Condition':
            group, name = 'conditions', ingest.map_condition(resource)
            status = ingest._coding_text(resource.get('clinicalStatus'))
            date = resource.get('onsetDateTime') or resource.get('recordedDate') or (resource.get('onsetPeriod') or {}).get('start')
        elif kind == 'MedicationRequest':
            group = 'medications'
            mapped = ingest.map_medication_request(resource)
            name, date = mapped['name'], mapped['authoredOn']
            value = next((dose.get('text') for dose in resource.get('dosageInstruction', []) if dose.get('text')), None)
        elif kind == 'Encounter':
            group = 'encounters'
            mapped = ingest.map_encounter(resource)
            name = next((ingest._coding_text(item) for item in resource.get('type', []) if ingest._coding_text(item)), None) or mapped['classCode'] or 'Encounter'
            date = mapped['periodStart']
            value = 'Ended ' + mapped['periodEnd'] if mapped['periodEnd'] else None
        else:
            continue
        # Stable within and across repeated imports; no provider patient ID is returned.
        identifier = hashlib.sha256((kind + ':' + resource['id']).encode()).hexdigest()[:24]
        records.append({'id': identifier, 'category': group, 'name': name, 'value': value,
                        'unit': unit, 'date': date, 'status': status, 'source': 'Epic Sandbox'})
    return records


class EpicImports:
    def __init__(self, path, key, oauth, *, transport=None, launch=None):
        self.path, self.key, self.oauth = path, key, oauth
        self.transport = transport or live.request_json
        self.launch = launch or (lambda task: Thread(target=task, daemon=True).start())
        self.slots = BoundedSemaphore(2)
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('''CREATE TABLE IF NOT EXISTS epic_import_jobs (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, status TEXT NOT NULL,
                summary TEXT NOT NULL, payload BLOB, digest TEXT, created_at REAL NOT NULL)''')
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS epic_active_import ON epic_import_jobs(user_id) WHERE status='running'")
            # A restarted single-process API cannot resume an in-memory worker/token.
            summary = json.dumps({'status': 'failed', 'message': 'The API restarted during import. Start a new import.'})
            db.execute("UPDATE epic_import_jobs SET status='failed', summary=? WHERE status='running'", (summary,))

    def _read_payload(self, user_id, row):
        try:
            payload = json.loads(decrypt_secret(self.key, row[2], 'epic-import:' + user_id + ':' + row[0]))
            if hashlib.sha256(canonical(payload['normalized'])).hexdigest() != row[3]:
                raise ValueError('digest mismatch')
            audit = reconcile.reconcile_bundle(payload['bundle'], payload['normalized'])
            if audit['status'] == 'failed':
                raise ValueError('mapping mismatch')
            return payload
        except Exception:
            raise EpicImportError('The saved import could not be verified. Start a new import.') from None

    def status(self, user_id):
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute('SELECT id, summary, payload, digest FROM epic_import_jobs WHERE user_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1', (user_id,)).fetchone()
        if not row:
            return {'status': 'idle', 'message': 'No records imported yet.'}
        result = json.loads(row[1])
        if row[2] is not None:
            self._read_payload(user_id, row)
            result['digest_verified'] = True
        return result

    def records(self, user_id, category='all', page=1):
        categories = ('vital-signs', 'laboratory', 'conditions', 'medications', 'encounters')
        if category not in ('all', *categories) or type(page) is not int or not 1 <= page <= 100000:
            raise ValueError('Choose a valid record category and page.')
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT id, summary, payload, digest FROM epic_import_jobs WHERE user_id=? AND status='completed' ORDER BY created_at DESC, rowid DESC LIMIT 1", (user_id,)).fetchone()
        if row is None:
            return {'records': [], 'total': 0, 'counts': dict.fromkeys(categories, 0),
                    'page': page, 'page_size': 25, 'imported_at': None, 'source': 'Epic Sandbox'}
        payload = self._read_payload(user_id, row)
        records = clinical_records(payload)
        counts = {name: sum(record['category'] == name for record in records) for name in categories}
        selected = [record for record in records if category == 'all' or record['category'] == category]
        selected.sort(key=lambda record: (record['date'] or '', record['id']), reverse=True)
        start = (page - 1) * 25
        summary = json.loads(row[1])
        return {'records': selected[start:start + 25], 'total': len(selected), 'counts': counts,
                'page': page, 'page_size': 25, 'imported_at': summary['completed_at'],
                'source': 'Epic Sandbox', 'digest_verified': True,
                'mapping_warning_count': len(payload['audit']['warnings']),
                'coverage_note': summary.get('coverage_note'), 'snapshot_id': row[0]}

    def start(self, user_id):
        credentials = self.oauth.for_import(user_id)
        if not self.slots.acquire(blocking=False):
            raise EpicImportError('The import service is busy. Please try again shortly.')
        job_id = str(uuid.uuid4())
        summary = {'id': job_id, 'status': 'running', 'message': 'Starting Epic import…', 'completed_steps': 0, 'total_steps': 6}
        try:
            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute('BEGIN IMMEDIATE')
                existing = db.execute("SELECT summary FROM epic_import_jobs WHERE user_id=? AND status='running'", (user_id,)).fetchone()
                if existing:
                    self.slots.release()
                    return json.loads(existing[0])
                db.execute('INSERT INTO epic_import_jobs VALUES (?,?,?,?,NULL,NULL,?)',
                           (job_id, user_id, 'running', json.dumps(summary), time.time()))
        except Exception:
            self.slots.release()
            raise
        try:
            self.launch(lambda: self._run(user_id, job_id, credentials))
        except Exception:
            self._update(job_id, {'id': job_id, 'status': 'failed', 'message': 'Could not start the import. Please retry.'})
            self.slots.release()
            raise EpicImportError('Could not start the import. Please retry.') from None
        return self.status(user_id)

    def _update(self, job_id, summary, *, payload=None, digest=None):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('UPDATE epic_import_jobs SET status=?, summary=?, payload=?, digest=? WHERE id=?',
                       (summary['status'], json.dumps(summary), payload, digest, job_id))

    def _run(self, user_id, job_id, saved):
        try:
            config = {'fhir_base_url': FHIR_BASE, 'client_id': saved['client_id']}
            token = {'access_token': saved['access_token'], 'patient': saved['patient'],
                     'expires_at': time.monotonic() + saved['expires_at'] - time.time() - 5}
            completed = set()
            observation_category = 'vital-signs'

            def transport(url, **kwargs):
                nonlocal observation_category
                parsed = urlsplit(url)
                resource = parsed.path.removeprefix(urlsplit(FHIR_BASE).path + '/').split('/')[0]
                category = parse_qs(parsed.query).get('category', [''])[0]
                if resource == 'Observation':
                    observation_category = category or observation_category
                    category = observation_category
                label = ('lab results' if category == 'laboratory' else 'vital signs') if resource == 'Observation' else {
                    'Patient': 'patient details', 'Condition': 'conditions',
                    'MedicationRequest': 'medication orders', 'Encounter': 'encounters'}.get(resource, 'records')
                self._update(job_id, {'id': job_id, 'status': 'running', 'message': 'Fetching ' + label + '…',
                                     'completed_steps': len(completed), 'total_steps': 6})
                result = self.transport(url, **kwargs)
                completed.add((resource, category))
                return result

            outcomes = []
            bundle, fetched = live.fetch_patient_bundle(config, token, ('vital-signs', 'laboratory'),
                resources=live.PROFILES['expanded'], transport=transport, outcomes=outcomes)
            normalized = ingest.normalize_bundle_to_ehr_payload(bundle, saved['patient'])
            audit = reconcile.reconcile_bundle(bundle, normalized)
            if audit['status'] == 'failed':
                raise EpicImportError('Imported records did not pass the saved-data checks. Please retry.')
            raw = canonical({'bundle': bundle, 'normalized': normalized, 'audit': audit, 'outcomes': outcomes})
            if len(raw) > 40_000_000:
                raise EpicImportError('This sandbox import exceeds the supported size. No records were saved.')
            digest = hashlib.sha256(canonical(normalized)).hexdigest()
            encrypted = encrypt_secret(self.key, raw, 'epic-import:' + user_id + ':' + job_id)
            restored = json.loads(decrypt_secret(self.key, encrypted, 'epic-import:' + user_id + ':' + job_id))
            if hashlib.sha256(canonical(restored['normalized'])).hexdigest() != digest:
                raise EpicImportError('Saved-data integrity check failed. Please retry.')
            counts = Counter(entry['resource']['resourceType'] for entry in bundle['entry'])
            notices = []
            for outcome in outcomes:
                for issue in outcome['outcome']['issue']:
                    codes = [c.get('code') if c.get('code') in ('4119', '4101') else 'other'
                             for c in issue.get('details', {}).get('coding', [])]
                    notices.append({'resource_type': outcome['resourceType'], 'category': outcome['category'],
                                    'severity': issue['severity'], 'codes': codes})
            self._update(job_id, {'id': job_id, 'status': 'completed', 'message': 'Your sandbox records were imported and saved.',
                'completed_steps': 6, 'total_steps': 6, 'completed_at': int(time.time()),
                'counts': {kind: counts[kind] for kind in ('Patient', 'Observation', 'Condition', 'MedicationRequest', 'Encounter')},
                'queries': fetched['queries'], 'notices': notices, 'mapping_warning_count': len(audit['warnings']),
                'digest_verified': True, 'coverage_note': 'Includes records available to your authorized Epic account; some categories may be empty.'},
                payload=encrypted, digest=digest)
        except (live.EpicLiveError, EpicImportError, EpicOAuthError) as error:
            self._update(job_id, {'id': job_id, 'status': 'failed', 'message': str(error)})
        except Exception:
            self._update(job_id, {'id': job_id, 'status': 'failed', 'message': 'Import could not finish. Your prior imports are retained. Please reconnect and retry.'})
        finally:
            self.slots.release()
