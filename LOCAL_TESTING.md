# Local testing — shared AccessRegistry

The frontend and Python grant API use `backend/contracts/AccessRegistry.sol` (and `MediLinkFaucet.sol` for vault gas). Hardhat copies those sources into its contracts directory when loading its config. Deploy both to any EVM with `backend/key-generation/deploy_evm_contracts.sh` (profiles: `hardhat`, `subtensor-testnet`, `subtensor-mainnet`, `custom`).

## Start the services

For the normal real-account setup, run `bash Main-Project/backend/start_backend.sh` from the repository root. In a second terminal run `bash Main-Project/frontend/load_frontend.sh`. The combined backend launcher waits for Hardhat before starting the API and stops its own processes on Ctrl+C; an existing Hardhat node is reused and left running. Manual commands follow.

1. Blockchain, from `Main-Project/backend/key-generation/testContractDeployment`:

   ```sh
   npm install
   npx hardhat compile
   npx hardhat node --hostname 127.0.0.1
   ```

   Reuse an existing node on 8545 rather than launching another. Compiling alone does not deploy the registry.

2. API, from `Main-Project/backend/key-generation`:

   ```sh
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   bash load_API_bc.sh
   ```

   `load_API_bc.sh` uses real Supabase accounts with local Hardhat. Keep its Supabase URL/key aligned with the frontend. For fixture accounts instead, use `bash load_API_bc_demo.sh`.

3. Frontend, from `Main-Project/frontend`:

   ```sh
   bash load_frontend.sh
   ```

   For fixture accounts use `bash load_frontend_demo.sh`. Open <http://127.0.0.1:5173/>. Sign in, or choose **Open local demo** when using fixtures. The API and frontend must use matching authentication modes.

## Registry storage and lifecycle

On the first share, the API deploys ONE registry, stores its deployment transaction/address in `registry-grants.sqlite3`, and registers the record before granting the recipient access. Further records and recipients use the same registry. The deployment hash is saved before broadcast; a timeout does not trigger a second deployment on retry. Alternatively, set `REGISTRY_ADDRESS` to an existing deployment of this exact compiled contract on the selected chain. The API verifies its runtime bytecode. Its operator must own any resources it manages.

The new SQLite filename leaves old `pair-grants.sqlite3` data and old contracts untouched. Old grants are not migrated or automatically revoked; they must be recreated in the registry. Do not delete the old store if you still need its wallets or revocation capability.

`--auto-data-dir` requires `--chain` and local Hardhat chain ID 31337. It selects a child directory for **SQLite / grant DB** based on the Hardhat instance, compiled contract, operator, configured registry, and authentication mode/project. Encrypted **vault keystores** are stored separately at `GRANT_DATA_DIR/vault_objects/` (or `GRANT_VAULT_DIR`) so the same wallet survives Hardhat restarts. Restarting only the API reuses its SQLite directory/address. Resetting Hardhat uses fresh chain data but keeps vault blobs. Keep `GRANT_MASTER_KEY` consistent across restarts. The real-account launcher retains its master key in its parent data directory. Do not commit these key stores.

Each recipient still has a separate custodial wallet and each grant has a fresh DH-derived credential. Each record has a stable resource ID derived from project, owner ID, and record ID. Sharing with another recipient or re-sharing after revocation reuses that resource ID and registry address. The API operator registers resources and signs grant/revoke transactions; the recipient wallet signs challenge/verification transactions.

The records API reads Supabase `public.records`, including uploaded document bytes stored in `file_content`. Registry `dataCid` remains `medilink:record:<resource-id>` and `dataHash` is SHA-256 of that reference, not a file-content hash or Hippius CID. The registry controls sharing; file delivery is authorized by the API.

## Browser sharing test

1. Register/sign in as the recipient and open Records to enroll with the API. Sign out and sign in as the owner (or use a separate browser profile).
2. As a patient in **health-locker-guard**, open **Upload**, choose a PDF/PNG/JPEG/text file (up to 10 MB), set type/tags, and click **Seal & add record**. The UI calls `POST /api/records` (multipart); the grant API writes metadata + `file_content` into Supabase with the service secret. **Do not** expect the old local-only Seal demo or direct browser inserts to populate the Access ledger.
3. Open **Permissions / Access ledger**, select a recipient who has already signed in and enrolled a vault wallet, choose records, and click **Grant**. With `VITE_ACCESS_REGISTRY` set, grants go to the **one shared AccessRegistry** (on-chain commitments per recipient–record)—not a new contract per pair. Offline/API grants use `POST /api/grants` when the registry is unset.
4. As recipient, click **Check access**. Expect `AccessGranted` and a verification transaction. Repeat: each access check sends a new challenge and verification to the same registry.
5. As owner, **Revoke** one receipt. It moves to Revoked. Expect a revocation transaction and “Recipient grant removed from registry.” The registry itself stays active. Other records/recipients must still pass checks.
6. Share that record with the same recipient again. A new API grant/DH credential is created at the SAME registry address/resource ID. The old API receipt stays revoked.
7. Restart only the API: registry, wallets, and grants persist.

Revocation first persists `revoking` to block API access. If a grant transaction is still pending, revocation waits for it before completing. Receipt timeouts keep transaction hashes for refresh/retry; pending revocations never reactivate access. GET/Refresh checks receipts and state without submitting transactions. Use Resume setup or Retry revocation for outstanding work. Failed setup must be revoked before sharing that pair again.

## Monitoring

Monitor the registry address shown in receipts or `/api/health` after setup. Index `ResourceRegistered`, `AccessGrantSet`, `AccessRevoked`, `ChallengeIssued`, `AccessGranted`, and `AccessDenied`. Resource/grantee fields are indexed on most events; `ResourceRegistered` fields are not indexed in the unchanged contract. Use chain ID, registry address, resource ID, and recipient wallet to identify a permission. Keep transaction hash/log index and account for chain reorganizations.

A reverted attempt does not emit AccessDenied. AccessGranted confirms this contract's verification, not a file download. No contract changes were made: the response is still computed from public commitment/nonce, while a caller must have a wallet-specific grant. Resource IDs are first-caller-owned, and resource metadata updates retain grants. API ownership and authentication checks remain necessary. This is a custodial prototype, not a proof of DH-secret possession.

## Manual API configuration

For Supabase, set `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, and a persistent 32-byte hex `GRANT_MASTER_KEY`. Start without `--dev`. Set `RPC_URL`, `OWNER_PRIVATE_KEY`, and `--chain` for registry integration. `REGISTRY_ADDRESS` is optional; use the same persistent API store to reuse deployments and credentials. Do not run multiple workers or independent stores against the same grant pairs.

`FUND_LOCAL_WALLETS=1` explicitly funds **custodial** recipient wallets using Hardhat's test RPC, only on chain ID 31337; it now supports both fixture and real Supabase identities. For **vault (non-custodial) enroll**, prefer the MediLink faucet: deploy with `python deploy_medilink_faucet.py`, fund the contract, set `FAUCET_ADDRESS` (launchers auto-read `faucet.deployment.json` when present). See [`backend/key-generation/markdowns/Faucet.md`](backend/key-generation/markdowns/Faucet.md). On other chains disable `FUND_LOCAL_WALLETS` and fund operator/recipient wallets yourself. The supplied launcher uses public Hardhat credentials and is for local blockchain testing only. For another network use manual configuration without `--auto-data-dir` and a separate `GRANT_DATA_DIR`.

For API/DH testing without any blockchain, omit `--chain` and use a separate data directory. Off-chain mode cannot verify or revoke on-chain permissions.

The frontend needs `VITE_SUPABASE_URL`, `VITE_SUPABASE_PUBLISHABLE_KEY`, and `VITE_GRANT_DEMO=false` for real users. `VITE_GRANT_API_URL` defaults to `http://127.0.0.1:5001`. Use port 5173 with `--strictPort`, or configure API `FRONTEND_ORIGINS` explicitly. Environment files are not automatically loaded by Python. The real-account launcher explicitly loads `backend/key-generation/.env`.

**Local demo account error:** the frontend has a Supabase session but the API uses `--dev`. Use matching modes, not extra records. Empty record lists show “No records yet.”

## Automated checks

From `backend/key-generation`:

```sh
.venv/bin/python -m pytest tests/test_grant_api.py tests/test_documents.py tests/test_faucet_enroll.py -q
```

For the integration test, start a separate compiled Hardhat node on port 18545, then run:

```sh
RUN_CHAIN_TESTS=1 .venv/bin/python -m pytest tests/test_local_chain.py -q
```

It checks shared deployment, per-resource/recipient isolation, repeated access checks, restart, revocation, re-grant, and monitor events. Hardhat tests cover MediLinkFaucet source guards; deploy locally with `./deploy_evm_contracts.sh hardhat`.

From `frontend`, run `npx tsc --noEmit`, lint the changed components, and `npm run build`.

## Patient account registration

Start `bash Main-Project/frontend/load_frontend.sh` from the repository root (or `bash load_frontend.sh` from the frontend directory). This launcher explicitly enables real-account mode. Visit `/signin` and choose **Need an account? Create one**. Both patient and care-provider accounts persist in Supabase Auth, with matching `profiles` and `user_roles` rows; patient accounts also populate `patients`. Account creation and subsequent login do not require Hardhat or the Python grant API to be running. The Python SQLite database stores grant identities and wallets when accounts use the grant API; it is not the password/account database.

The **Create Patient Account** form sends full name, email, and password to Supabase Auth. The new signup trigger creates `public.patients` in the same transaction, including when email confirmation is required. No separate browser insert is needed.

The table contains `id` (Auth's generated UUID, also used by records/grants), plaintext `name` and `email`, and `created_at` (database timestamp). Supabase Auth stores the salted bcrypt password hash in protected authentication storage, not in `patients`. Password resets continue through Auth. Patients can read only their own row; direct client inserts/updates are denied. Profile name and confirmed Auth email changes synchronize to the patient row.

### Apply the migration

The supplied pooler URI targets project `cwyxwmzquatqlbufsdob`, while `backend/supabase/config.toml` names another project. Choose the intended project in Supabase's SQL Editor. For a new database, apply all files in `backend/supabase/migrations` in filename order. If the two September 7 migrations are already applied, apply only `20260928210000_create_patients.sql`. It also backfills existing patients. Do not rerun previously applied migrations.

Alternatively, with `psql` installed, run this from `Main-Project` after the earlier migrations have been applied:

```sh
PGSSLMODE=require psql \
  --host=aws-0-ca-central-1.pooler.supabase.com --port=6543 \
  --username=postgres.cwyxwmzquatqlbufsdob --dbname=postgres \
  --password --set=ON_ERROR_STOP=1 \
  --file=backend/supabase/migrations/20260928210000_create_patients.sql
```

Enter the database password at the prompt. The transaction-pooler connection is for database administration/server code; never put it or the database password in frontend code or `VITE_` variables.

Configure the untracked frontend environment for the intended project, then restart Vite:

```dotenv
VITE_SUPABASE_URL=https://cwyxwmzquatqlbufsdob.supabase.co
VITE_SUPABASE_PUBLISHABLE_KEY=<publishable key from this project's dashboard>
VITE_GRANT_DEMO=false
```

Use the same project for the Python API's `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY`; start without `--dev` for real accounts. Demo fixtures do not create patient accounts. Configure the local `/signin` URL as an allowed Auth redirect URL for confirmation emails.

### Verify registration

1. Visit `/signin`, select **Patient Sign In**, then **Need an account? Create one**. Enter a name, an email you control, and a password.
2. Submit and confirm your email if prompted. In SQL Editor, run `select id, name, email, created_at from public.patients order by created_at desc;`. The ID should match **Authentication → Users**.
3. Sign out and sign back in. Creating a care provider should not create a patient row.
4. On local/staging Supabase, run `backend/supabase/tests/patients.sql` to test signup, provider exclusion, updates, row isolation, and deletion. It rolls back its fixtures. Browser testing is still needed to verify password authentication.

## Client-side Hippius key vault

After Supabase sign-in/sign-up, the frontend creates or unlocks an encrypted keystore. The password is stretched with Argon2id **in the browser**; private EVM and X25519 keys never leave the tab. The grant API only issues vault-scoped upload/download URLs.

**API (same process as grant API):**

```sh
cd Main-Project/backend/key-generation
# Optional: real Hippius. If unset, vault blobs are stored under GRANT_DATA_DIR/vault_objects/
# (stable across --auto-data-dir Hardhat shards; override with GRANT_VAULT_DIR)
# export HIPPIUS_ACCESS_KEY=...
# export HIPPIUS_SECRET_KEY=...
# export HIPPIUS_BUCKET=...
.venv/bin/python grant_api.py   # or --dev for local demo identities
```

Endpoints (Bearer Supabase JWT, or `demo-*` in `--dev`):

- `GET /api/vault/status`
- `POST /api/vault/presign-put`
- `POST /api/vault/presign-get`

**Frontend:** set `VITE_GRANT_API_URL` (default `http://127.0.0.1:5001`), leave Hippius secrets out of `VITE_*`. Sign up → recovery phrase modal → vault unlocked in memory. Sign out clears the session keys; a refresh requires password unlock again.

**Unit tests:**

```sh
cd health-locker-guard && npm test
cd Main-Project/backend/key-generation && pytest tests/test_grant_api.py -k vault
```

Vault enroll publishes the browser EVM/`x25519` public keys via `POST /api/enroll` (`evmAddress`, `x25519PublicKeyB64`) and tops up gas through `POST /api/faucet/ensure` when `FAUCET_ADDRESS` is set. Legacy empty-body enroll still creates a custodial wallet for off-registry `/grants` demos. Server-side AccessRegistry challenge signing runs only when a custodial `wallet_key` exists; vault users sign with the unlocked browser key.

### Account persistence verification (October 13, 2026)

Verified the effective local frontend configuration against project `cwyxwmzquatqlbufsdob`: email signup is enabled, and email confirmation is currently disabled. A signup through the same Supabase client used by the frontend created matching Auth, profile, role, and patient rows. Signing out and signing in through a new client returned the same user UUID. No schema changes were needed on this project. The labeled test account `medilink-test-1791914846157@example.com` remains in the database and can be removed through Authentication → Users. Its randomly generated password was not retained. This verifies the backend authentication flow, not a browser interaction test.

Vite's `.env.local` overrides the older project values in `.env`. Keep the frontend and Python API pointed at the same project. Do not change `supabase/config.toml` or deploy migrations to a different project merely to resolve a login error. If account-role loading fails, sign-in now displays the database problem instead of silently treating the account as a patient.

## Document upload and preview — Supabase records

Uploads now store the **actual file bytes in `public.records.file_content`** (`bytea`), with `file_mime_type`, `file_size`, and the existing `name`, `owner_id`, `tags`, and timestamps on that same row. No Storage bucket or local document database is used for real accounts. Record listings select metadata only; file bytes are fetched when an authorized user opens the preview.

### Required setup

1. In the SQL Editor for the same Supabase project used by the frontend/API, apply `backend/supabase/migrations/20261013190000_record_documents.sql` once, after the existing migrations. It adds the file columns and constraints and limits direct client writes to metadata. Existing records remain intact.
2. Copy `backend/key-generation/.env.example` to `backend/key-generation/.env`. Set `SUPABASE_SECRET_KEY` to a server secret key from that project, or use the legacy `SUPABASE_SERVICE_ROLE_KEY` instead. Do not put either secret in the frontend, a `VITE_` variable, source control, or chat. The API needs this key to fetch a shared record after validating its grant; the recipient's own Supabase token cannot bypass the owner-only records policy.
3. Restart `bash load_API_bc.sh` from `backend/key-generation`, then start the frontend normally. The launcher reads the backend `.env`; direct Python starts must have those variables exported. Preserve `GRANT_MASTER_KEY` and the grant database for existing shares.

Missing schema or server credentials return a configuration error. There is no automatic local-file fallback. Authenticated owners retain direct read access to their own records under RLS. Shared recipients receive bytes only through the grant API, which checks ownership or the exact active grant and runs DH/on-chain access verification as configured. Revocation blocks future delivery, not copies already delivered.

### Use

As a patient in health-locker-guard: **Upload → choose file → Seal & add record**. That hits `POST /api/records` with `file`, optional `tags`, and optional `kind`. Listing uses `GET /api/records` (owned rows only). Share through **Access ledger** (same API-backed records). Providers can view shared files after a grant; patient upload requires a `patient` role in `user_roles`.

**Required for uploads:** `SUPABASE_SECRET_KEY` (or `SUPABASE_SERVICE_ROLE_KEY`) on the Python API for the **same** project as the frontend; apply `20261013190000_record_documents.sql`. Restart the API after changing `.env`. Publishable keys alone authenticate users but cannot write `file_content` through the grant API.

Inspect saved rows in Supabase Table Editor → `records`, or run:

```sql
select id, owner_id, name, tags, file_mime_type, file_size,
       octet_length(file_content) as stored_bytes
from public.records
order by created_at desc;
```

The `bytea` value appears as encoded binary in the table editor. Use the application preview to view the document. Metadata-only records have null file columns.

### Copy previous local uploads

Legacy local files are preserved and are not automatically deleted or silently uploaded. To copy them into Supabase while retaining IDs (and therefore existing grants), use `migrate_documents_to_supabase.py`. Stop the old API first. After applying the SQL migration, export the backend `.env` variables and the original `GRANT_MASTER_KEY` in your terminal. For the standard launcher setup, from `backend/key-generation`:

```sh
set -a
source .env
set +a
export GRANT_MASTER_KEY="$(.venv/bin/python -c 'from pathlib import Path; print(Path(".grant-supabase-local/master.key").read_bytes().hex())')"
# Replace <project-hash> with the original document database's filename.
.venv/bin/python migrate_documents_to_supabase.py .grant-supabase-local/documents/<project-hash>.sqlite3
# Review the pending count, then copy:
.venv/bin/python migrate_documents_to_supabase.py .grant-supabase-local/documents/<project-hash>.sqlite3 --apply
```

The tool verifies the original project filename and encryption key, preserves record IDs/owners/tags/timestamps, verifies uploaded bytes, skips identical existing files, and refuses conflicting records. It never deletes the local source. Demo identities cannot be migrated. Remove old copies only after verifying your cloud records and backups.

### Verification boundaries

Automated tests cover the Supabase REST payload/byte decoding, pagination, metadata-only listing, persistence across client/API instances with a simulated remote table, missing-key failures, owner/shared/revoked access, role checks, and repeatable local-file migration. `backend/supabase/tests/record_documents.sql` exercises constraints, owner-only RLS, and column privileges with rollback fixtures on a local/staging database. The live schema migration and live cloud upload/download verification require the project admin connection and server secret; they have not been run in this workspace yet.

`--dev` remains a synthetic demo: uploaded demo files live only in memory and disappear when its API stops. Use real-account mode for persistent Supabase uploads. Grant/wallet data still uses the existing local SQLite service store; only document storage changed.

## Epic Sandbox connection (no record retrieval)

Follow [backend/EPIC_OAUTH.md](backend/EPIC_OAUTH.md) to configure the public SMART app and exact callback URL. With the backend and frontend running, sign in to MediLink and visit `/external-import`. Connect to Epic Sandbox and authorize a patient account. The callback displays token-exchange success and transfer authorization readiness. Tokens stay encrypted on the backend; no EHR data is fetched. Use the same frontend origin throughout (do not mix `localhost` and `127.0.0.1`).

## Hippius document storage

The backend supports encrypted Hippius uploads with Supabase pointers. Apply the migration and follow [backend/HIPPIUS_STORAGE.md](backend/HIPPIUS_STORAGE.md) before enabling `DOCUMENT_STORAGE_BACKEND=hippius`. The standalone `check_hippius.py` checks a synthetic encrypted round trip and removes its temporary cloud bucket. Existing documents are not automatically migrated.
