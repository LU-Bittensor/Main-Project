# Epic Sandbox connection

The live frontend now starts authorization, handles Epic's callback, and exchanges the code through the Python API. The connection is bound to the verified MediLink account that initiated it. The import page now offers account-bound retrieval and browser-encrypted Hippius snapshots; see [Epic client storage](EPIC_CLIENT_STORAGE.md).

## Configuration and local test

In `key-generation/.env`:

```dotenv
EPIC_CLIENT_ID=<your Epic application's non-production client ID>
EPIC_REDIRECT_URI=http://127.0.0.1:5173/external-import/callback
```

Register that exact redirect with Epic. This implementation supports a **public patient-facing standalone SMART client using S256 PKCE**, without a client secret. Confidential registrations requiring a client secret or private-key JWT need matching client authentication added before they can work. Configure the Patient read API in your registration. Requested scopes are `launch/patient` plus `patient/Patient.read`, `patient/Observation.read`, `patient/Condition.read`, `patient/MedicationRequest.read`, and `patient/Encounter.read`. Enable the corresponding R4 APIs, including both vital-sign and lab Observation searches. Reconnect older Patient-only authorizations before importing.

1. Start the backend with `./start_backend.sh` from `backend`, and the frontend with `npm run dev` from `frontend`.
2. Use the frontend at the exact origin in the redirect URI (here `http://127.0.0.1:5173`). Mixing `localhost` and `127.0.0.1` loses the origin-bound Supabase session on return. Use a real MediLink login; disable frontend `VITE_GRANT_DEMO` and backend fixture mode.
3. Open `/external-import`, sign in to MediLink, then click **Connect Epic Sandbox**.
4. Sign in with an Epic sandbox patient and consent on Epic's hosted page.
5. The callback shows whether token exchange succeeded and whether authorization is ready. Return to imports to see persisted status and expiry.

Restart the API after changing its environment. Never put Epic passwords or tokens in frontend configuration. In deployed environments use HTTPS and allow the frontend origin in the API CORS configuration. Configure web server/access logs and analytics to redact the callback query string (authorization code/state). The callback strips that query from browser history on mount and declares a no-referrer policy.

## Flow and API

All three endpoints require `Authorization: Bearer <MediLink Supabase access token>` and return `Cache-Control: no-store`. Demo identities are rejected. The API derives the account from verified Supabase authentication, never from the request body.

- `POST /api/external-import/epic/authorize`, JSON `{}`: returns `authorization_url` and `expires_in`. The backend fixes the issuer, client, redirect and scopes.
- Epic redirects to frontend `/external-import/callback` with `code` and `state`, or `error` and `state`.
- `POST /api/external-import/epic/complete`, JSON `{ "state": "...", "code": "..." }` (or `error` instead of `code`): validates and consumes the account-bound attempt, then exchanges the code server-side at Epic's fixed token endpoint with its original PKCE verifier. Returns sanitized connection status, never tokens. Denials also consume the matching attempt. Failed/ambiguous exchanges require a fresh start, not a replay.
- `GET /api/external-import/epic/connection`: returns the current account's saved status. Another account cannot query it by supplying an ID.

Example status:

```json
{
  "token_exchange_succeeded": true,
  "ready_for_transfer": true,
  "ready_for_import": true,
  "reason": "Ready to import your authorized sandbox records.",
  "expires_at": 1792000000,
  "scopes": ["patient/Patient.read", "patient/Observation.read", "patient/Condition.read", "patient/MedicationRequest.read", "patient/Encounter.read"],
  "patient_context_received": true
}
```

A successful exchange means a valid-shaped bearer token response was received from Epic. Ready additionally means the token has not expired, a patient context was returned, and Patient read permission was granted. It does **not** prove record availability or access to labs/documents/medications. Actual access can also be revoked before nominal expiry; the future retrieval step must handle that. Missing patient context or scope is reported as successful exchange but not ready. An expired saved token also becomes not ready. No refresh-token flow is implemented; reconnect when needed.

## Binding and storage

State is generated from 32 random bytes. Only its SHA-256 hash is saved with the initiating account. The independent 64-byte PKCE verifier is encrypted; only its S256 challenge goes to Epic. Pending attempts expire after ten minutes and a new attempt replaces the prior pending attempt for that account. State consumption is atomic, account-bound and single-use; cross-account, expired, replaced and replayed callbacks fail before any exchange.

`epic_oauth_pending` and `epic_connections` live in the existing API SQLite database. Access token, patient context, issuer, scopes and expiry are encrypted together using the existing master key and account-specific authenticated encryption context. Tokens and patient identifiers are not returned to the frontend. Unrequested refresh tokens are not retained. A successful reconnect replaces the old connection; an unsuccessful attempt leaves the previous connection intact. The callback reports its own failure rather than treating that older connection as a new success.

Keep the database and master key together across restarts. A chain reset that selects a new chain-specific data directory also selects a new OAuth store; reconnect afterwards. This is a prototype connection store, separate from the future EHR intake schema. Before multi-worker deployment, move the store to a shared durable database and coordinate concurrent starts/completions across workers (current API serializes these operations in one process). This flow creates no blockchain grants.

## Validation

```sh
# From backend/key-generation
.venv/bin/python -m pytest tests/test_epic_oauth.py tests/test_grant_api.py tests/test_documents.py -q
```

Tests use mocked Epic token responses, covering PKCE, account binding, encryption, persistence, expiry, replay, denial, malformed responses and authenticated API routes. A live Epic sandbox login/token exchange still requires your registered app and sandbox patient.

References: [Epic OAuth documentation](https://fhir.epic.com/Documentation?docId=oauth2), [SMART App Launch](https://hl7.org/fhir/smart-app-launch/STU2.2/app-launch.html).

## EHR intake schema (prepared, not applied)

Apply `supabase/migrations/20261015120000_ehr_intake.sql` after the existing migrations, using the Supabase SQL editor or your normal migration workflow. It adds connections, import jobs/steps, current FHIR resources, automatic resource-version history, and nullable links on `records`. Run `supabase/tests/ehr_intake.sql` on a local/staging database to exercise ownership, deduplication, history, and compatibility; its fixtures roll back.

The new importer stores encrypted account-owned snapshots locally, independently of this prepared cloud schema. This migration does not move those SQLite connections/snapshots or change the current document MIME/size limits. The future importer must reuse a stable connection, canonicalize provider URLs and resource JSON, compute SHA-256 content hashes, set import timestamps, verify patient context, and create catalog/attachment records. Store source timestamps and partial dates unchanged in JSON even when no SQL timestamp can represent them. Keep credentials in backend-only encrypted storage. Shared reads must go through backend grant checks, including FHIR payloads, rather than adding broad recipient RLS policies. Regenerate Supabase TypeScript types after applying the migration.
