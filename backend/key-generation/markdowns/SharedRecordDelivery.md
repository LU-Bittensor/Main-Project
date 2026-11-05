# Shared record delivery (on-chain grant → file preview)

## Problem

AccessRegistry grants only store a **pointer** (`dataCid` = `medilink:record:<uuid>`) and a grantee wallet. File bytes live in Supabase via the grant API. The Shared filter could discover on-chain grants (record ID only) but recipients could not open the document, because:

1. Vault-signed Grants often never created an API `/grants` row.
2. `POST /api/records/<id>/document` only allowed the owner or an **active SQLite grant** (then DH/`access` checks).

## What we implemented

### 1. Dual-write on Grant (frontend)

In health-locker-guard `AccessPanel`, when `VITE_ACCESS_REGISTRY` is set:

1. Register resource + `grantAccess` on AccessRegistry (vault-signed, as before).
2. Immediately `POST /api/grants` with:
   - `recipient_id`, `record_ids`
   - `client_chain: true`
   - optional `tx_hash`

That persists a delivery grant so Shared can load names/MIME/`has_document` from the API.

### 2. `client_chain` grants (backend)

`POST /api/grants` with `client_chain: true`:

- Requires API `--chain` and a recipient vault wallet.
- Uses the **same** resource id as the browser:  
  `keccak256(utf8 "medilink:record:<record_id>")`  
  (`vault_record_resource_id` in `grant_api.py`, matching `resourceIdFromRecord`).
- Inserts the grant as **`active`** with the configured registry address.
- Does **not** re-run operator `advance()` (avoids a second, conflicting on-chain registration).
- `reconcile` still checks live `hasGrant`; it **skips** DH `matches()` for these rows because commitments are interim vault commitments, not `derive_commitment(DH secret)`.

### 3. Document authorization (backend)

`POST /api/records/<id>/document` now allows a recipient if either:

- classic path: active API grant → existing `access()` (DH + optional custodial challenge), or  
- **on-chain path**: enrolled wallet has `hasGrant(registry, vault_record_resource_id(id), wallet)`.

So even older on-chain-only shares can open files once the recipient is enrolled.

### 4. Metadata for Shared UI (backend + frontend)

- New `GET /api/records/<id>` returns document metadata if the caller is the owner or `recipient_can_view_record` (API grant or on-chain `hasGrant`).
- Vault Shared filter merges AccessGrantSet discoveries, then calls `fetchRecordMetadata` so the row shows the real name and `has_document`.
- Clicking the name opens `DocumentViewer` → `viewDocument` (same preview path as owned records).

## Data flow (happy path)

```
Owner uploads → POST /api/records → Supabase file_content
Owner Grants  → AccessRegistry AccessGrantSet (vault key)
             → POST /api/grants { client_chain: true } (SQLite delivery row)
Recipient Shared → list API grants + poll AccessGrantSet
                → GET /api/records/<id> (metadata)
                → click → POST /api/records/<id>/document → preview
```

## Limits / follow-ups

- Chain still does not store file bytes; Hippius can replace Supabase later without changing AccessRegistry shape.
- On-chain delivery auth for MVP trusts `hasGrant` + API auth; challenge/prove remains available on the Access ledger.
- Revoking should remove the on-chain grant (and API row); reconcile will then fail `hasGrant` and mark client_chain rows revoked.
- Restart the grant API after pulling these backend changes; refresh the frontend so AccessPanel dual-write is active.

## Manual check

1. Two users enrolled with vault wallets; `VITE_ACCESS_REGISTRY` set; Hardhat + API running.
2. Owner uploads a PDF, Grants to recipient.
3. Recipient → Records → **Shared** → underline name → preview loads.
4. Network: `POST /api/grants` includes `"client_chain": true`; `POST /api/records/<uuid>/document` returns 200.

## Troubleshooting: “Document not found or access denied”

1. Confirm `/api/health` shows a non-null `registry_address` (same as `VITE_ACCESS_REGISTRY`). If null, restart via `start_backend.sh` so `REGISTRY_ADDRESS` is exported.
2. Confirm Hardhat still has the grant: recipient wallet `hasGrant` for `keccak256("medilink:record:<uuid>")`. A Hardhat restart wipes grants — redeploy with `deploy_evm_contracts.sh hardhat`, unlock both vaults, and **Grant** again.
3. Confirm the record exists in the **same** Supabase project as the API (`GET` metadata 404 = missing row / wrong project).
4. Re-Grant once so dual-write can create the API delivery row (optional once on-chain auth works).
