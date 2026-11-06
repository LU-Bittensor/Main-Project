# Browser-side document encryption

New real-account uploads to Hippius use the unlocked browser vault's X25519 keys. Apply `supabase/migrations/20261106120000_client_document_encryption.sql` in Supabase **before starting the updated API/frontend**. This migration requires the existing record documents and Hippius migrations. It has not been applied to your live database by this change.

Keep `DOCUMENT_STORAGE_BACKEND=hippius` and your existing Hippius configuration. Restart with `bash start_backend.sh` in `backend`, then `bash load_frontend.sh` in `frontend`. No new secret environment variables are needed. Existing master keys must remain available for older records.

## Flow

1. Unlock the original account vault. Uploading generates a fresh random 256-bit document key in the browser and encrypts the file with AES-256-GCM (random 96-bit nonce).
2. X25519 derives a shared secret; HKDF-SHA256 derives a wrapping key bound to document, owner, recipient, ciphertext hash and public keys. AES-GCM encrypts the document key for the owner. The owner's vault wallet signs this envelope.
3. The API validates the signature, identity, ciphertext size and hash. It uploads the exact ciphertext to the private Hippius bucket. Supabase stores the pointer, public manifest and encrypted key envelopes. No new document plaintext, plaintext document key, or vault private key is submitted to the API.
4. Sharing unwraps the document key in the owner's browser and wraps it for the recipient using the owner's private X25519 key and recipient's public X25519 key. The API saves this signed envelope before the on-chain grant is submitted. An envelope alone does not grant access.
5. Opening a file checks current backend/on-chain permissions for both envelope and ciphertext delivery. The browser verifies the owner's signature, derives the same wrapping key using the recipient's private key and owner's public key, unwraps the document key, checks integrity and decrypts locally for preview. S3 credentials stay on the backend.

Code: `frontend/src/lib/document-crypto.ts` implements crypto; `grant-api.ts` connects upload/share/view; `backend/key-generation/client_document_crypto.py` validates envelopes; `hippius_storage.py` stores ciphertext. The older Python DH grant-verification code remains for compatibility; it does not perform new document encryption.

## Manual verification

1. Run the migration and restart both services. Use real accounts, not the simulated demo. Sign in and unlock both accounts' existing vaults so their public keys are enrolled.
2. In Access ledger, each user can see their encryption fingerprint. Compare the full fingerprint through a separate trusted channel. The first share/open asks for confirmation and pins that identity in the browser. A later key change is blocked. Do not accept a mismatched fingerprint.
3. As the patient, upload a new PDF/PNG/JPEG/TXT (up to 10 MB), then open it. Supabase `records.client_encrypted` should be true; its `storage_objects.encryption_format` should be `client-x25519-v1`, with `ciphertext_size = plaintext_size + 28` and a `client_info` manifest/envelope. Hippius receives opaque bytes, not the original file. Network inspection of the multipart file payload and document response should show ciphertext.
4. Share with the other account; confirm the fingerprint, then wait for the grant transaction. `document_key_envelopes` should contain that recipient's wrapped key. Sign into the recipient account, unlock its original vault, confirm the owner's fingerprint, and open the shared document.
5. Revoke access, close the recipient's preview, then try opening it again. New envelope/download requests must be denied. A locked vault must not open a new encrypted document.
6. Confirm that an older document still opens through its legacy path.

## Scope and limitations

- Applies to **new manually uploaded Hippius documents and new Epic snapshots**; see [Epic client storage](EPIC_CLIENT_STORAGE.md). Existing Supabase bytes, legacy server-encrypted Hippius documents, and historical server-generated Epic import reports are not converted. Epic retrieval still runs on the server. This is not an all-data end-to-end encryption claim.
- File names, tags, MIME types, sizes, account relationships and public keys remain visible metadata. The API cannot inspect encrypted file contents for malware or independently verify their MIME type.
- Preserve the original encrypted vault and its unlock credentials. Replacing a vault/key does not migrate old document envelopes; automatic rotation/recovery is not implemented. New devices must recover the same private keys, not create a replacement vault.
- Fingerprints are local browser pins. First contact depends on an actual independent comparison. Clearing browser storage loses pins. Account-directory compromise cannot be detected on first contact without that comparison.
- Static X25519 sharing does not provide forward secrecy. Compromise of a participant's private key can expose retained encrypted records/envelopes. Browser compromise or malicious frontend code can access unlocked keys and plaintext.
- Revocation stops future requests through the API. It cannot erase a document or key already downloaded by a recipient. An open preview is already decrypted locally.
- Local demo uploads retain their simulated path. Legacy clients sending plaintext to a Hippius-configured API are rejected; there is no silent plaintext fallback.

Protocol references: [X25519, RFC 7748](https://www.rfc-editor.org/rfc/rfc7748.html), [HKDF, RFC 5869](https://www.rfc-editor.org/rfc/rfc5869.html). This implementation has automated checks, not an independent cryptographic audit.

## Automated checks

From `frontend`: `npx tsc --noEmit`, `npm test`, `npm run build`.
From `backend/key-generation`: `.venv/bin/python -m pytest tests -q`.

`tests/fixtures/client_document.json` is synthetic ciphertext/envelope generated by the TypeScript implementation, used to check Python signature/context compatibility. To regenerate from `frontend`, run:

```bash
DOCUMENT_CRYPTO_FIXTURE=../backend/key-generation/tests/fixtures/client_document.json npx vitest run src/lib/document-crypto.test.ts
```

Use synthetic data for validation. Do not test with real patient records before reviewing deployment security and recovery.
