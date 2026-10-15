"""Epic Sandbox account-bound authorization and encrypted connection storage."""
import base64
from contextlib import closing
import hashlib
import json
import requests
import secrets
import sqlite3
import time
from urllib.parse import urlencode, urlsplit

from DHkey_share import decrypt_secret, encrypt_secret

FHIR_BASE = 'https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4'
AUTHORIZE_URL = 'https://fhir.epic.com/interconnect-fhir-oauth/oauth2/authorize'
TOKEN_URL = 'https://fhir.epic.com/interconnect-fhir-oauth/oauth2/token'
TTL_SECONDS = 600


class EpicOAuthError(Exception):
    pass


def pkce_challenge(verifier):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('ascii')).digest()).rstrip(b'=').decode('ascii')


class EpicOAuth:
    def __init__(self, path, key, client_id, redirect_uri):
        self.path, self.key = path, key
        self.client_id, self.redirect_uri = client_id, redirect_uri
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('''CREATE TABLE IF NOT EXISTS epic_oauth_pending (
                state_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, verifier BLOB NOT NULL,
                client_id TEXT NOT NULL, redirect_uri TEXT NOT NULL, scope TEXT NOT NULL,
                created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL)''')

            db.execute("""CREATE TABLE IF NOT EXISTS epic_connections (
                user_id TEXT PRIMARY KEY, payload BLOB NOT NULL)""")

    def status(self, user_id):
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute('SELECT payload FROM epic_connections WHERE user_id=?', (user_id,)).fetchone()
        if row is None:
            return {'token_exchange_succeeded': False, 'ready_for_transfer': False,
                    'reason': 'Connect your Epic Sandbox account to authorize a future transfer.'}
        saved = json.loads(decrypt_secret(self.key, row[0], 'epic-connection:' + user_id))
        scopes = saved['scope'].split()
        patient_scope = any(s in scopes for s in ('patient/Patient.read', 'patient/Patient.r', 'patient/Patient.rs'))
        if saved['expires_at'] <= time.time():
            reason = 'Authorization expired. Connect again before transferring data.'
        elif not saved.get('patient'):
            reason = 'Epic did not return patient context. Connect again with a patient account.'
        elif not patient_scope:
            reason = 'Epic did not grant the requested Patient read permission.'
        else:
            reason = ''
        return {'token_exchange_succeeded': True, 'ready_for_transfer': not reason,
                'reason': reason or 'Authorization is ready for a future Patient data transfer. No records have been retrieved.',
                'expires_at': saved['expires_at'], 'scopes': scopes,
                'patient_context_received': bool(saved.get('patient'))}

    def complete(self, user_id, payload):
        if (not isinstance(payload, dict) or set(payload) - {'state', 'code', 'error'}
                or ('code' in payload) == ('error' in payload)
                or not isinstance(payload.get('code', payload.get('error')), str)
                or not 1 <= len(payload.get('code', payload.get('error', ''))) <= 8192):
            raise EpicOAuthError('Invalid callback. Start the Epic connection again.')
        attempt = self.consume(payload.get('state'), user_id)
        if 'error' in payload:
            raise EpicOAuthError('Epic authorization was declined or interrupted. Connect again when ready.')
        # Public SMART client with PKCE. Never follow redirects with an authorization code.
        try:
            response = requests.post(TOKEN_URL, data={
                'grant_type': 'authorization_code', 'code': payload['code'],
                'client_id': attempt['client_id'], 'redirect_uri': attempt['redirect_uri'],
                'code_verifier': attempt['code_verifier'],
            }, headers={'Accept': 'application/json'}, timeout=20, allow_redirects=False)
            if response.status_code != 200:
                raise EpicOAuthError('Epic rejected the token exchange. Check the app registration and connect again.')
            token = response.json()
        except (requests.RequestException, ValueError) as error:
            raise EpicOAuthError('Could not complete the Epic token exchange. Connect again; this attempt cannot be replayed.') from error
        if (not isinstance(token, dict) or not isinstance(token.get('access_token'), str)
                or not token['access_token'] or str(token.get('token_type', '')).lower() != 'bearer'
                or type(token.get('expires_in')) is not int or token['expires_in'] <= 0
                or not isinstance(token.get('scope', attempt['scope']), str)
                or ('patient' in token and not isinstance(token['patient'], str))):
            raise EpicOAuthError('Epic returned an incomplete token response. Connect again.')
        # OAuth permits omitted scope when identical to the requested scope.
        saved = {'access_token': token['access_token'], 'scope': token.get('scope', attempt['scope']),
                 'patient': token.get('patient'), 'expires_at': int(time.time()) + token['expires_in'],
                 'issuer': FHIR_BASE, 'client_id': attempt['client_id']}
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('INSERT OR REPLACE INTO epic_connections VALUES (?,?)',
                       (user_id, encrypt_secret(self.key, json.dumps(saved).encode(), 'epic-connection:' + user_id)))
        return self.status(user_id)

    def start(self, user_id):
        if not self.client_id or not self.redirect_uri:
            raise EpicOAuthError('Set EPIC_CLIENT_ID and EPIC_REDIRECT_URI in the backend environment first.')
        url = urlsplit(self.redirect_uri)
        if (not url.hostname or url.username or url.password or url.fragment or url.query or
            not (url.scheme == 'https' or
                 (url.scheme == 'http' and url.hostname in ('localhost', '127.0.0.1')))):
            raise EpicOAuthError('EPIC_REDIRECT_URI must be a fixed HTTPS callback URL (HTTP is allowed for localhost development), without query or fragment.')
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        state_hash = hashlib.sha256(state.encode()).hexdigest()
        now = int(time.time())
        # Start with patient context and demographics; expand scopes with later imports.
        scope = 'launch/patient patient/Patient.read'
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('DELETE FROM epic_oauth_pending WHERE expires_at <= ?', (now,))
            # Only the latest attempt for this account is valid.
            db.execute('DELETE FROM epic_oauth_pending WHERE user_id = ?', (user_id,))
            db.execute('INSERT INTO epic_oauth_pending VALUES (?,?,?,?,?,?,?,?)',
                       (state_hash, user_id, encrypt_secret(self.key, verifier.encode(), 'epic-oauth:' + state_hash),
                        self.client_id, self.redirect_uri, scope, now, now + TTL_SECONDS))
        params = {'response_type': 'code', 'client_id': self.client_id,
                  'redirect_uri': self.redirect_uri, 'scope': scope, 'aud': FHIR_BASE,
                  'state': state, 'code_challenge': pkce_challenge(verifier), 'code_challenge_method': 'S256'}
        return {'authorization_url': AUTHORIZE_URL + '?' + urlencode(params), 'expires_in': TTL_SECONDS}

    def consume(self, state, user_id):
        """For the authenticated completion endpoint, never a public lookup.

        Caller MUST authenticate the MediLink user before calling. Atomically consume
        state only for that user; expired, substituted, or replayed state is rejected.
        """
        if not isinstance(state, str) or not 20 <= len(state) <= 128:
            raise EpicOAuthError('Invalid or expired Epic authorization attempt. Start again.')
        state_hash = hashlib.sha256(state.encode()).hexdigest()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM epic_oauth_pending WHERE state_hash=? AND user_id=? AND expires_at>?',
                             (state_hash, user_id, int(time.time()))).fetchone()
            if row is None:
                raise EpicOAuthError('Invalid or expired Epic authorization attempt. Start again.')
            verifier = decrypt_secret(self.key, row['verifier'], 'epic-oauth:' + state_hash).decode()
            db.execute('DELETE FROM epic_oauth_pending WHERE state_hash=?', (state_hash,))
        return {'code_verifier': verifier, 'client_id': row['client_id'],
                'redirect_uri': row['redirect_uri'], 'scope': row['scope'], 'user_id': row['user_id']}
