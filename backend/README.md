# MediLink backend

> New manually uploaded Hippius documents now use browser-side X25519 key wrapping and AES-GCM encryption. Apply the additional migration and follow [Client document encryption](CLIENT_DOCUMENT_ENCRYPTION.md). Older files and Epic reports retain their existing path.


This directory contains the Python API, blockchain tooling, Supabase database assets, and server-only TypeScript helpers.

## Layout

| Path | Responsibility |
| --- | --- |
| `key-generation/grant_api.py` | Authenticated grant, record, document, vault, enroll, and faucet API |
| `key-generation/faucet_client.py` | MediLinkFaucet balance check and operator drip |
| `key-generation/deploy_access_registry.py` | Compile/deploy AccessRegistry; writes `deployment.json` |
| `key-generation/deploy_medilink_faucet.py` | Compile/deploy faucet; writes `faucet.deployment.json` |
| `key-generation/deploy_evm_contracts.sh` | One-shot deploy of registry + faucet to a chosen EVM |
| `key-generation/documents.py` | Document contents and metadata in Supabase records |
| `key-generation/chain_grants.py` | AccessRegistry blockchain adapter |
| `key-generation/DHkey_share.py` | Key generation, encryption, and DH verification |
| `key-generation/migrate_documents_to_supabase.py` | Legacy local-document migration |
| `key-generation/operation-flow/` | API compatibility entry point and command-line examples |
| `key-generation/tests/` | Python API, document, faucet, and chain integration tests |
| `key-generation/testContractDeployment/` | Hardhat configuration, deployment scripts, and Solidity tests |
| `key-generation/markdowns/Faucet.md` | Local faucet deploy and API wiring |
| `key-generation/markdowns/Records.md` | Document upload path (UI → grant API → Supabase) |
| `contracts/AccessRegistry.sol` | Authoritative registry contract; Hardhat copies it into its compilation directory |
| `contracts/MediLinkFaucet.sol` | Ops-funded gas faucet for vault H160s |
| `supabase/` | Supabase configuration, SQL migrations/tests, and shared schema types |
| `src/integrations/supabase/` | Server-only Supabase admin, request-authentication, and cron helpers |

The complete former `Main-Project/key-generation` project now lives at `Main-Project/backend/key-generation`. Faucet and non-custodial vault enroll are part of `grant_api.py` (single process on port 5001)—do not run a separate `wallet_api`. A leftover untracked `Main-Project/key-generation/` tree (if present) is obsolete. The TypeScript package in this directory and the Hardhat package are separate projects.

### Deploy contracts to an EVM

From `backend/key-generation` (venv with `requirements.txt` installed):

```sh
./deploy_evm_contracts.sh hardhat              # local node on :8545; funds faucet with 1 ETH
OWNER_PRIVATE_KEY=0x... ./deploy_evm_contracts.sh subtensor-testnet
RPC_URL=... CHAIN_ID=... OWNER_PRIVATE_KEY=0x... ./deploy_evm_contracts.sh custom
```

Then `source deployment.exports.sh` and set frontend `VITE_ACCESS_REGISTRY` / `VITE_EVM_RPC_URL` / `VITE_EVM_CHAIN_ID` to the printed values.

### Document upload (frontend → grant API → Supabase)

health-locker-guard **Seal & add record** calls the grant API (not a local-only UI list):

- `POST /api/records` — multipart `file` (+ optional `tags`, `kind`); patient role required
- `GET /api/records` — owned document metadata for Access ledger sharing

Bytes live in `public.records.file_content`. Set `SUPABASE_SECRET_KEY` in `key-generation/.env` to the same project as the UI. One shared `AccessRegistry` holds on-chain grants; uploads themselves are off-chain in Supabase.

## Start locally

From the repository root, run these in two terminals:

**Backend (blockchain + Python API):**

```sh
bash Main-Project/backend/start_backend.sh
```

**Frontend:**

```sh
bash Main-Project/frontend/load_frontend.sh
```

From this directory, `bash start_backend.sh` also works. The backend launcher installs missing dependencies, compiles the contracts, starts Hardhat on port 8545, waits for readiness, then starts the real-account Python API on port 5001. Output from both services stays in the terminal.

Press Ctrl+C to stop both processes started by the launcher. If a compatible Hardhat node already exists, it is reused and left running on exit. An occupied API port causes a clear error rather than launching a second API. If a newly started service fails, the other owned process is stopped.

Configure `key-generation/.env` with your Supabase settings first; the launcher retains the existing API script and persistent master key/data directory. This combined launcher supports the local chain on port 8545. For custom RPC settings or fixture mode, use the individual scripts in `key-generation` instead. Stopping/restarting a launcher-owned Hardhat node resets its in-memory chain; the existing API's chain-instance directories isolate the resulting grant state.

See [LOCAL_TESTING.md](../LOCAL_TESTING.md) for manual service commands, Supabase setup, and environment configuration.

## Existing local state

The move preserved the existing `.env`, `.grant-*` stores, master keys, PEM files, and installed dependencies inside `key-generation`. Their ignore rules moved with them. No credentials were regenerated and no database migration was applied. The API launchers now resolve their default data paths beneath `backend/key-generation`.

Restart existing API processes from the new path after stopping them. Update any custom absolute `GRANT_DATA_DIR`, `GRANT_DOCUMENT_DIR`, virtualenv, IDE, or shell paths that reference the old location. The local virtualenv's text launchers were adjusted for this move; when setting up another checkout, create its virtualenv with the commands above. Use `load_API_bc.sh` to continue using the existing persistent master key.

## Validation

```sh
# From Main-Project/backend/key-generation
.venv/bin/python -m pytest tests/test_grant_api.py tests/test_documents.py tests/test_faucet_enroll.py -q

# From Main-Project/backend/key-generation/testContractDeployment
npx hardhat compile
npx hardhat test

# From Main-Project/backend
npm install
npm run typecheck
```

Chain integration tests require a separate node; see LOCAL_TESTING.md. Run Supabase CLI commands from `Main-Project/backend`, and verify `supabase/config.toml` points to the intended project before applying migrations.

## Frontend runtime boundary

The frontend keeps React UI, browser API clients, and TanStack SSR/startup integration (`src/server.ts`, `src/start.ts`, and rendering-error helpers). Those implement the web application's runtime rather than the medical-record API. Its database types module re-exports `backend/supabase/types.ts`.

The TypeScript admin/auth/cron helpers currently have no application callers; they are available for future server integrations and do not start a separate service.

## Epic import authorization

`key-generation/epic_oauth.py` implements account-bound state/PKCE, callback completion, server-side token exchange, and encrypted connection persistence. The frontend displays verified token-exchange success and transfer authorization readiness. No EHR data is fetched. See [EPIC_OAUTH.md](EPIC_OAUTH.md) for app registration, local testing, API contracts and limitations.

## Hippius encrypted document storage

See [HIPPIUS_STORAGE.md](HIPPIUS_STORAGE.md) for the new private per-patient buckets, encrypted file content, Supabase pointer migration, configuration and test commands. Enable with `DOCUMENT_STORAGE_BACKEND=hippius` only after applying the storage migration. Existing Supabase files remain readable; Epic resource retrieval is still disabled.
