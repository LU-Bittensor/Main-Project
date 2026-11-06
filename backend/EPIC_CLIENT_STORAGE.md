# Epic snapshots encrypted in the browser and stored in Hippius

New Epic imports use the same document encryption and sharing mechanism as manual uploads. Existing local imports are retained as **Epic import (legacy)** for compatibility; they are not automatically converted or deleted. Choose **Epic import** in the access ledger to share new protected snapshots separately.

## Setup and manual test

1. Keep `DOCUMENT_STORAGE_BACKEND=hippius` and the existing Hippius/Supabase credentials configured. Apply the existing `20261106120000_client_document_encryption.sql` migration if you have not already done so. **No additional migration or secret is required for this change.**
2. Restart `backend/start_backend.sh` and `frontend/load_frontend.sh`. Use a real MediLink patient account and unlock its original vault. Reconnect Epic if its authorization has expired.
3. On `/external-import`, click **Import from Epic** and keep the page open. The API fetches/reconciles the authorized resources, then the page automatically encrypts the snapshot and uploads it. The job is not marked completed until the API confirms a published, owner-bound client-encrypted Hippius document.
4. In Supabase, find the new `records` row: its ID equals the import job ID, `kind` is `Epic import`, and `client_encrypted` is true. Follow `storage_object_id`: `encryption_format` must be `client-x25519-v1`. Hippius contains the ciphertext. The SQLite job row has `payload=NULL` and `digest=NULL`; it contains only job state and a document reference, not the clinical snapshot.
5. Open Records. The latest encrypted snapshot is downloaded through the authenticated document API and decrypted in the browser. The Epic dashboard filters/paginates the decrypted clinical records. Opening the snapshot document shows the complete JSON as plain text, including raw FHIR, normalized data, clinical rows, reconciliation and provider notices.
6. Enroll/unlock the recipient vault. Share the new snapshot from Access ledger after comparing fingerprints. Its UUID uses the same wallet grant path and signed X25519 key envelopes as a manual upload. The recipient's envelope is in `document_key_envelopes`; the recipient unlocks their vault to preview the snapshot.
7. Revoke and retry opening as the recipient: new envelope/download requests must be denied. Re-importing creates a different snapshot and never expands a prior grant.

Sharing a new snapshot shares **all of its contents, including original patient identifiers and raw FHIR IDs**. It is broader than the old redacted text report. The access ledger displays this scope before sharing. The recipient's own Epic dashboard remains their own chart; shared snapshots appear in shared documents.

## What is protected, and when

- The API still handles OAuth tokens and fetches Epic data over HTTPS. It sees plaintext temporarily to fetch and reconcile the import. This implementation does **not** keep plaintext off the backend during transfer.
- A successful fetch is held only in process memory for up to 10 minutes, with at most two pending snapshots and two active fetch workers. The authenticated owner fetches that handoff with `Cache-Control: no-store`. No new clinical payload is written to SQLite or a plaintext file by this flow.
- The browser generates the random document key, AES-GCM encrypts the snapshot, and creates the signed owner-wrapped key using its vault. The API receives ciphertext for Hippius publication. It cannot inspect/verify the clinical contents of that uploaded ciphertext; it validates the signed manifest, owner, size and hash.
- After acknowledgement or timeout the retained handoff byte buffer is cleared. Temporary parsed objects are released to garbage collection; this is not a guarantee against process-memory inspection, swap, crash dumps, compromised servers or malicious frontend code. Disable body-capturing proxies/APM in deployment.
- Existing SQLite import payloads remain server-encrypted, and old grants keep using their old reports. Re-import to create a protected snapshot. This change intentionally does not delete historical records, grants, backups or their decryption keys.
- The fetched resource set is unchanged: Patient, vital/lab Observations, Conditions, MedicationRequests and Encounters. Adding Immunization, attachments or other Epic APIs remains separate work; absent data is not fabricated.
- Snapshot size is limited to 10 MB to match existing document storage. Oversized imports fail explicitly rather than saving a partial snapshot.

## Failure and deployment behavior

The page retries a pending save. If a response is lost after upload, the next attempt detects the already-published record and acknowledges it without uploading another copy. Account/vault changes interrupt the save. Locked vaults cannot save or decrypt new imports. If the page is closed, return within 10 minutes with the same account/vault; after expiry or an API restart, start a new import. A ciphertext record published just before a failed acknowledgement still remains in Records.

The handoff is **single-process**. Run one API worker/process and one backend instance for this prototype; requests must reach the process that performed the fetch. Multiple Cloud Run instances or independent workers require an intentionally designed encrypted handoff/coordinator and durable job state first. Do not work around this by persisting plaintext in Redis, local disk or Supabase.

Endpoints (all authenticated, account-scoped, no-store):

- `POST /api/external-import/epic/import` starts a fetch, requiring Hippius and an enrolled vault public key.
- `GET /api/external-import/epic/import` reports `running`, `awaiting_encryption`, `completed`, or `failed`.
- `GET /api/external-import/epic/import/<job>/payload` returns the temporary snapshot only to its owner, or `stored: true` for an already-published encrypted snapshot.
- `POST /api/records` publishes the browser ciphertext using the job UUID as document ID.
- `POST /api/external-import/epic/import/<job>/complete` accepts `{}` and verifies an owned client-encrypted Epic document exists before clearing the handoff and acknowledging completion.
- `GET /api/external-import/epic/records` returns the latest encrypted record ID for browser decryption. Historical accounts with no encrypted snapshot retain the old read path.

## Validation

Run `npm test`, `npx tsc --noEmit`, and `npm run build` from `frontend`. Run `.venv/bin/python -m pytest tests -q` from `backend/key-generation`.

Tests use synthetic Epic, Supabase and S3 responses. They check handoff ownership, missing-vault/storage rejection, no persisted clinical payload, expiry/restart handling, publication before acknowledgement, retry recovery, local dashboard pagination, and account changes. Existing document crypto and grant/revocation tests cover the shared encryption mechanism. Live Epic/Hippius browser verification is still required after restarting your services.
