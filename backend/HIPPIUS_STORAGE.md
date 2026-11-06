# Encrypted Hippius document storage

> New manually uploaded Hippius documents now use browser-side X25519 key wrapping and AES-GCM encryption. Apply the additional migration and follow [Client document encryption](CLIENT_DOCUMENT_ENCRYPTION.md). Older files and Epic reports retain their existing path.


This first implementation sends new patient document uploads to a private per-patient Hippius bucket. Supabase retains record metadata and backend-only object pointers. Files are encrypted in the API before upload and decrypted only after the existing owner/shared-grant checks. No presigned links, S3 credentials, or encryption keys are returned to the browser.

## Enable

1. Apply `supabase/migrations/20261026120000_hippius_storage.sql` in the Supabase SQL editor, after the existing `record_documents` migration. It is additive and does not require the optional EHR intake migration. Run `supabase/tests/hippius_storage.sql` against a development database; its synthetic fixtures roll back.
2. Install Python requirements in `backend/key-generation/.venv`.
3. Set server-only values in `backend/key-generation/.env`:

```dotenv
DOCUMENT_STORAGE_BACKEND=hippius
HIPPIUS_ACCESS_KEY_ID=<S3 access key>
HIPPIUS_SECRET_ACCESS_KEY=<S3 secret key>
HIPPIUS_NAMESPACE=medilink-local
HIPPIUS_PATIENT_HASH_KEY=<persistent 32-byte hex key>
HIPPIUS_STORAGE_MASTER_KEY=<different persistent 32-byte hex key>
```

The local ignored `.env` was provisioned with S3 credentials and randomly generated storage keys during implementation. Storage mode was deliberately left unchanged because the live pointer schema was absent. Rotate the credentials shared in chat before real patient use. The account password is not used or stored.

4. Restart `./start_backend.sh`. Sign in with a real MediLink patient account, upload a synthetic text file, and open it. Demo mode deliberately keeps its in-memory store.
5. Confirm `records.file_content` is NULL for the new record, `storage_object_id` is set, and the referenced object is `ready`. Confirm an unrelated account cannot view it, a shared recipient can, and revocation blocks subsequent requests.

Existing Supabase documents remain readable in Hippius mode. Do not switch back to the old storage backend after writing Hippius records: it cannot resolve their object pointers. No existing bytes have been removed or migrated.

## Identity and encryption

Bucket names are `ml-p-` followed by a 52-character lowercase Base32 HMAC-SHA256 of `namespace:MediLink-user-UUID`. This is a pseudonym, not authentication. The mapping is persisted; existing mappings are reused without recalculating the hash. Each patient gets an independently random 256-bit data key, wrapped with AES-256-GCM under `HIPPIUS_STORAGE_MASTER_KEY`. Concurrent first uploads re-read the winning stored key before encryption.

Objects use fresh UUID keys and AES-256-GCM with fresh 96-bit nonces. Authenticated context includes the owner UUID, object UUID and object key. The encrypted format is `MLHP1` (5 bytes), nonce (12 bytes), then ciphertext and tag (16 bytes overhead). Ciphertext SHA-256 and length are checked before authenticated decryption. Filenames, tags, patient UUIDs, and MIME types are not sent as S3 keys or metadata. Names/tags/MIME types and sizes still exist in Supabase for the current UI; this is not a strict clinical-metadata-free catalog.

Storage keys are independent of the blockchain data directory, so a Hardhat reset does not rotate them. Back up both storage keys separately from the database and retain the wrapped patient keys. Losing the master key makes stored content unreadable. This first version supports only key version `v1`; do not replace the master key in place. A future rotation must rewrap every patient key before switching versions. Namespace mismatch fails closed.

## Upload consistency and privacy

The API creates a pending pointer before upload, downloads and verifies the ciphertext, then marks it uploaded. A service-role-only PostgreSQL function atomically publishes the record and marks its object ready. Retrying publication of the same record/object pair is safe. An ambiguous publish response never causes remote deletion: it may already have committed. Interrupted writes remain pending/uploaded for operator reconciliation. Do not delete these blindly; check for references and in-flight requests first. A browser retry currently starts a new upload, so a lost success response can result in a duplicate record.

Buckets use private creation. The live gateway returned `AccessControlListNotSupported` for ACL APIs/header combinations despite the documentation. Where ACL reads are disabled, the adapter requires anonymous listing denial. Every object verification/download requires anonymous GET denial (HTTP 401/403); unexpected responses or network failures fail closed. Uploads omit the unsupported object ACL header and rely on private bucket behavior plus these checks. Content remains encrypted even if a storage policy is later misconfigured.

Both new tables have RLS enabled with no browser grants/policies; only the backend service role accesses them. Composite foreign keys enforce owner consistency. Storage rows do not cascade away when an account is removed: deletion needs an explicit remote-object cleanup workflow first. The existing record-owner RLS and grant API continue controlling metadata and content access.

## Validation

```sh
# From backend/key-generation
.venv/bin/python -m pytest tests/test_hippius_storage.py tests/test_documents.py tests/test_grant_api.py tests/test_epic_oauth.py -q
# Live check: only temporary synthetic content; creates and deletes its own bucket.
set -a
source .env
set +a
.venv/bin/python check_hippius.py
```

The live check also probes the Supabase pointer schema without reading patient rows. It does not apply SQL. Keep SDK debug logging off to avoid exposing authorization details.

## Deliberate limits of this first step

- Document upload/viewing is connected; Epic retrieval remains disabled. Vaccination/medication FHIR JSON and the prior EHR JSONB/version-history schema are not migrated to object pointers yet.
- Existing files stay in Supabase. A separate verified copy-and-switch migration is needed before removing old bytes.
- The existing 10 MB document limit and PDF/PNG/JPEG/plain-text validation remain.
- No automated cleanup, retention, backup/restore or key rotation service is implemented. Record deletion can leave remote objects; account deletion is blocked until storage references are explicitly cleaned up.
- Validate Hippius account bucket quotas and the contractual/data-location requirements before real-patient deployment. One bucket per patient is created lazily on first upload.

Connection settings follow [Hippius Python guidance](https://docs.hippius.com/storage/s3/python): HTTPS endpoint `https://s3.hippius.com`, region `decentralized`, SigV4, path-style. The [compatibility matrix](https://docs.hippius.com/storage/s3/compatibility) documents that lifecycle expiration and S3 event notifications are not available, so reconciliation must be application-driven.

Implementation validation: 76 Python tests passed, including the real API route with a simulated Hippius/Supabase transport. All repository migrations and the document/EHR/Hippius SQL checks passed in a temporary PGlite PostgreSQL engine with stubbed Supabase auth roles/schema. A live Hippius synthetic round trip and anonymous-access checks passed and test resources were removed. Live Supabase probes confirmed the pointer schema was absent; no live SQL was applied and no browser upload in Hippius mode has been tested. The separate existing vault-keystore feature is unchanged.

If the browser shows `Failed to fetch`, check the frontend origin and the API health endpoint at `http://127.0.0.1:5001/api/health`. Local frontend origins on ports 5173 and 8080 are allowed by default; `FRONTEND_ORIGINS` overrides that list. Restart the backend after changing this configuration or updating the API code.
