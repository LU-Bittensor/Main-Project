# Local testing — shared AccessRegistry

The frontend and Python grant API now use `frontend/AccessRegistry.sol` unchanged. Hardhat copies that source into its contracts directory when loading its config, so there is one authoritative source. `DataAccessControl.sol` and its standalone CLI remain legacy examples; the API no longer deploys pair contracts.

## Start the services

1. Blockchain, from `Main-Project/key-generation/testContractDeployment`:

   ```sh
   npm install
   npx hardhat compile
   npx hardhat node --hostname 127.0.0.1
   ```

   Reuse an existing node on 8545 rather than launching another. Compiling alone does not deploy the registry.

2. API, from `Main-Project/key-generation`:

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

`--auto-data-dir` requires `--chain` and local Hardhat chain ID 31337. It selects a child directory based on the Hardhat instance, compiled contract, operator, configured registry, and authentication mode/project. Restarting only the API reuses its directory/address. Resetting Hardhat uses fresh chain data. Keep `GRANT_MASTER_KEY` consistent across restarts. The real-account launcher retains its master key in its parent data directory. Do not commit these key stores.

Each recipient still has a separate custodial wallet and each grant has a fresh DH-derived credential. Each record has a stable resource ID derived from project, owner ID, and record ID. Sharing with another recipient or re-sharing after revocation reuses that resource ID and registry address. The API operator registers resources and signs grant/revoke transactions; the recipient wallet signs challenge/verification transactions.

The current records API exposes metadata only. `dataCid` is explicitly `medilink:record:<resource-id>` and `dataHash` is SHA-256 of that reference. It is NOT a medical file content hash or Hippius CID. Actual file storage remains separate work.

## Browser sharing test

1. Register/sign in as the recipient and open Records to enroll with the API. Sign out and sign in as the owner (or use a separate browser profile).
2. Add test metadata in Supabase Table Editor → `records`: patient's UUID as `owner_id`, a `name`, and `kind` such as `Lab report`. Leave default columns unchanged. Demo fixtures already provide lab and imaging records.
3. Open **Access ledger**, select recipient and records, and click **Grant**. All active receipts should show the SAME registry address, with different resource IDs for different records. Sharing the same record with another recipient keeps that resource ID but has a different recipient wallet.
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

`FUND_LOCAL_WALLETS=1` explicitly funds recipient wallets using Hardhat's test RPC, only on chain ID 31337; it now supports both fixture and real Supabase identities. On other chains disable it and fund operator/recipient wallets yourself. The supplied launcher uses public Hardhat credentials and is for local blockchain testing only. For another network use manual configuration without `--auto-data-dir` and a separate `GRANT_DATA_DIR`.

For API/DH testing without any blockchain, omit `--chain` and use a separate data directory. Off-chain mode cannot verify or revoke on-chain permissions.

The frontend needs `VITE_SUPABASE_URL`, `VITE_SUPABASE_PUBLISHABLE_KEY`, and `VITE_GRANT_DEMO=false` for real users. `VITE_GRANT_API_URL` defaults to `http://127.0.0.1:5001`. Use port 5173 with `--strictPort`, or configure API `FRONTEND_ORIGINS` explicitly. Environment files are not automatically loaded by Python.

**Local demo account error:** the frontend has a Supabase session but the API uses `--dev`. Use matching modes, not extra records. Empty record lists show “No records yet.”

## Automated checks

From `key-generation`:

```sh
.venv/bin/python -m pytest tests/test_grant_api.py -q
```

For the integration test, start a separate compiled Hardhat node on port 18545, then run:

```sh
RUN_CHAIN_TESTS=1 .venv/bin/python -m pytest tests/test_local_chain.py -q
```

It checks shared deployment, per-resource/recipient isolation, repeated access checks, restart, revocation, re-grant, and monitor events. Existing Solidity tests for the legacy pair contract remain separate.

From `frontend`, run `npx tsc --noEmit`, lint the changed components, and `npm run build`.

## Patient account registration

The existing **Create Patient Account** form sends full name, email, and password to Supabase Auth. The new signup trigger creates `public.patients` in the same transaction, including when email confirmation is required. No separate browser insert is needed.

The table contains `id` (Auth's generated UUID, also used by records/grants), plaintext `name` and `email`, and `created_at` (database timestamp). Supabase Auth stores the salted bcrypt password hash in protected authentication storage, not in `patients`. Password resets continue through Auth. Patients can read only their own row; direct client inserts/updates are denied. Profile name and confirmed Auth email changes synchronize to the patient row.

### Apply the migration

The supplied pooler URI targets project `cwyxwmzquatqlbufsdob`, while `frontend/supabase/config.toml` names another project. Choose the intended project in Supabase's SQL Editor. For a new database, apply all files in `frontend/supabase/migrations` in filename order. If the two September 7 migrations are already applied, apply only `20260928210000_create_patients.sql`. It also backfills existing patients. Do not rerun previously applied migrations.

Alternatively, with `psql` installed, run this from `Main-Project` after the earlier migrations have been applied:

```sh
PGSSLMODE=require psql \
  --host=aws-0-ca-central-1.pooler.supabase.com --port=6543 \
  --username=postgres.cwyxwmzquatqlbufsdob --dbname=postgres \
  --password --set=ON_ERROR_STOP=1 \
  --file=frontend/supabase/migrations/20260928210000_create_patients.sql
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
4. On local/staging Supabase, run `frontend/supabase/tests/patients.sql` to test signup, provider exclusion, updates, row isolation, and deletion. It rolls back its fixtures. Browser testing is still needed to verify password authentication.
