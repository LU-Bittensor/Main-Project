# Epic import through the MediLink frontend

**Current flow:** New imports use [browser encryption and Hippius storage](EPIC_CLIENT_STORAGE.md). Follow that guide for current setup, limits, sharing and testing. The server-stored workflow and results below are historical and apply to pre-upgrade snapshots only.

## Historical implementation and validation (before November 6)

This connects the existing `/external-import` page to authenticated FHIR retrieval. It uses the same Patient/Observation/Condition/MedicationRequest/Encounter fetcher validated against three Epic sandbox patients in commit `6a37d96` on `testnet`. That shared pipeline lives in `subnet-template/` on this branch. No fixture data is substituted for live responses.

## Local browser test

Use the frontend repository at `frontend/` beside `backend/`, since its Supabase types are shared with the backend. Its repository URL is recorded in `.gitmodules`.

1. In `backend/key-generation`, create `.venv` and install `requirements.txt` if needed. Set `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY` in the ignored `.env` to the same project as the frontend. Set `EPIC_CLIENT_ID` to the registered non-production client ID and `EPIC_REDIRECT_URI=http://127.0.0.1:5173/external-import/callback`.
2. Run `bash load_API_epic.sh`. This starts the real-account API on port 5001 without requiring Hardhat. Its separate `.grant-epic-ui-local/` directory and master key persist across restarts; retain both together.
3. From `frontend/`, run `bash load_frontend.sh`. Use **http://127.0.0.1:5173** consistently; this exact callback is registered with Epic. Frontend `VITE_GRANT_API_URL` must point to `http://127.0.0.1:5001`; demo authentication must be disabled.
4. Open `/external-import`, sign in with a real MediLink patient account, and choose **Connect Epic Sandbox**. Sign in on Epic with a published sandbox patient, approve access, and return to imports after the callback.
5. Choose **Import from Epic**. The page shows progress while the backend fetches data, then counts and saved-record verification. Empty categories remain zero. API coverage/empty-result notices and mapping-warning counts are displayed; full original outcomes remain encrypted on the server.
6. Refresh: the completed import should remain visible. Import again: a fresh snapshot replaces the displayed result rather than appending duplicate resources. Sign out: the displayed result clears. Another account must see only its own job.
7. If authorization expires or access is rejected, the page offers reconnect/retry. A failed job never saves a partially successful snapshot. Previous completed snapshots remain stored, although the page shows the latest attempt.

Never enter Epic credentials into MediLink or share tokens in chat. The callback keeps its existing state/PKCE validation and clears credentials from the browser URL. The frontend receives neither Epic access tokens nor patient identifiers.

## API and storage

- Existing authorize/complete/connection endpoints remain account-bound. Authorization now requests the five-resource SMART v1 read scope set; older Patient-only connections must reconnect.
- `POST /api/external-import/epic/import` with `{}` starts a job and returns HTTP 202. No user ID, patient ID, issuer, redirect URI, scope, or query overrides are accepted.
- `GET /api/external-import/epic/import` returns the latest job for the verified Supabase account. It reports progress, counts, timestamp, notices, and verification status, never clinical payloads or Epic credentials.
- Jobs run in background threads, capped at two simultaneous imports per process and one per account. Repeated starts while an account's job is running reuse it. A process restart marks interrupted jobs failed so they can be retried. This remains a single-process prototype; multiple independent workers need shared job coordination.
- Raw resources, normalized output, reconciliation, and original OperationOutcomes are AES-GCM encrypted in `epic_import_jobs` in the existing API SQLite database. Encryption context binds each snapshot to its account and job. A canonical SHA-256 digest of normalized data is checked after encryption/decryption and again when loading a saved result. There is no plaintext raw-bundle output from this web flow.
- Account-owned sandbox snapshots now populate **Records → Imported Epic records**, with category filters, source labels, original dates, clinical values/statuses, pagination, and explicit empty states. The endpoint is `GET /api/external-import/epic/records?category=all&page=1`; only category/page filters are allowed. It selects the latest completed snapshot for the authenticated account, independently of a newer failed attempt or expired Epic authorization. It does not insert rows into Supabase `records` or upload imports to Hippius. Explicit sharing of an immutable readable import report is now supported through the access ledger; the clinical dashboard remains owner-only. No cloud schema migration is run by this feature.

## Verification

From `backend/key-generation`:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_epic_oauth.py tests/test_epic_import.py tests/test_grant_api.py tests/test_documents.py -q
```

Result: **77 passed**. Tests use simulated Epic/Supabase responses and cover authentication, cross-account isolation, input overrides, expanded scopes, expiry, invalid patient context, failed fetches, encrypted persistence, tamper detection, repeat imports, duplicate starts, and interrupted-job recovery. Existing grant and document tests also pass.

From `frontend`:

```sh
npx tsc --noEmit
npx eslint src/lib/external-import.ts src/routes/external-import.tsx src/routes/external-import_.callback.tsx
npm run build
```

Type checking, changed-file lint, and the production build passed. The live local import page was opened and confirmed to require sign-in before importing. The authentication service returned HTTP 200 with email login enabled. **The authenticated browser import test passed October 27, 2026.** The user signed into MediLink and connected a sandbox patient. Clicking Import from Epic returned **1 Patient, 255 Observations (253 vital signs and 2 labs), 1 Condition, 1 MedicationRequest, and 5 Encounters**. The browser displayed completion and saved-record verification. Refresh restored the saved result; a second import created a fresh snapshot with the same counts. Both encrypted snapshots passed independent decryption/digest and mapping-audit checks, with no mapping mismatches or mapping warnings. The five API notices were Epic access-coverage code 4119. See [identifier-free browser test evidence](evidence/epic-frontend/2026-10-27-browser-import.json). Live cross-account and expiry scenarios were not manually exercised; automated tests cover those paths.


## Dashboard and end-to-end follow-up — October 28

**85 backend tests pass**, including verified dashboard projection, all five categories, units/partial dates, pagination, cross-account read isolation, invalid filter/account overrides, stable row IDs across repeat imports, and expired-authorization → reconnect → import recovery. Frontend type checking, changed-file lint, and production build pass. Narrow screens use readable record cards; wider screens use a table.

Live browser verification passed for actual clinical values, source/date/status display, all five category filters, pagination, and saved records remaining readable after API restart and Epic authorization expiry. Signing out cleared clinical data, and reopening `/records` while signed out showed only a sign-in prompt. Sign-out affects the current session. The user signed back in and reauthorized the same Epic sandbox patient; a renewed import and immediate repeat both passed integrity/mapping checks and displayed **269 clinical records** (260 vitals, 2 labs, 1 condition, 1 medication order, 5 encounters). Seven observations were new provider resources compared with October 27; they were verified from the raw resource-ID sets, rather than inferred from the count increase. No duplicate source resources were present.

The separate documents panel may require the pre-existing Supabase server document credentials; that configuration does not block the owner-only Epic dashboard. The UI identifies document-storage failures separately.

See [dashboard evidence](evidence/epic-dashboard/2026-10-28-verification.json) and [sprint status](EPIC_SPRINT_STATUS.md). **The only remaining manual confirmation is switching to a second real MediLink account**, which the user expects to access October 29. Automated two-account isolation tests pass; do not label the real-account switch as manually verified yet.


## Saved-import sharing — October 29

A completed Epic import now appears in the access ledger as an **Epic import** text report. It contains the displayed clinical records, values, source dates, and statuses; provider patient IDs, raw FHIR IDs, account credentials, and access tokens are omitted. Review the report before granting it to another registered account. It is generated on demand from the encrypted snapshot, without a cloud document key or plaintext server file.

The grant points to that exact immutable snapshot. A later import is a different report and is not automatically included in an existing grant. The owner can preview their report; a recipient can open it only after an active grant for that exact report and account. Delivery reuses the existing DH verification and, when configured, registry checks. Pending, failed, revoking, and revoked grants cannot deliver content. Recipients cannot grant the owner's report onward or revoke the owner's grant. Another user's imported chart remains private even when they share a report.

Uploaded document storage remains explicitly unavailable until its server credentials are configured; this no longer blocks the separate local Epic-report catalog. The access ledger and Records page disclose the disabled upload source. Cloud requests still fail normally when that source is configured but unavailable; there is no silent cloud-to-local document fallback.

The local API currently runs with **blockchain verification off**. This tests authenticated local sharing and revocation, not a live blockchain transaction. Use the same local backend data directory/master key across accounts.

### Two-account browser procedure

1. Sign into the recipient MediLink account and open **Access ledger** once. This enrolls the account with the local API.
2. Sign out, sign into the owner account, and open **Access ledger → Refresh**. Select the recipient and **Epic import**. Review the included report, then click **Grant** yourself.
3. Return to the recipient account. Under **Active incoming receipts**, choose **Open shared content**. The same report also appears among shared records on the Records page. The owner's clinical dashboard is not copied into the recipient's own chart.
4. Return to the owner and **Revoke** the grant. Back in the recipient account, refresh and retry opening the report; future delivery must be denied. Copies already viewed or saved cannot be recalled.

**93 targeted backend tests pass**, including the new snapshot sharing suite plus the existing OAuth, import, grant, and document tests. The added tests cover private-by-default reports, exact-recipient delivery, outsider denial, revocation, nonactive grants, recipient re-grant denial, immutable scope after re-import, restart persistence, tamper detection, and preview output without tokens/patient IDs. Frontend type checking, changed-file lint, and production build pass. The live owner's 269-record saved report was successfully previewed in the browser; actual two-account grant/view/revoke remains pending the user's test.

Run the backend suite from `backend/key-generation`:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_epic_oauth.py tests/test_epic_import.py tests/test_epic_sharing.py tests/test_grant_api.py tests/test_documents.py -q
```
