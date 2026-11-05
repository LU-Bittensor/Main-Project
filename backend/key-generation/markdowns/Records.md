# Records upload & sharing

## Data path

```
Browser (health-locker-guard)
  → POST /api/records  (Bearer Supabase JWT, multipart file)
  → grant_api.py (patient role check)
  → Supabase REST with SUPABASE_SECRET_KEY
  → public.records (metadata + file_content bytea)
```

Listing: `GET /api/records` returns **owned** rows only. Access ledger grants use those IDs.

## Important

| Item | Detail |
|------|--------|
| Auth project | Frontend `VITE_SUPABASE_*` and API `SUPABASE_URL` / publishable / **secret** must be the **same** project |
| Secret key | Required for document list/upload; publishable JWT alone is not enough for the grant API store |
| Schema | Apply `backend/supabase/migrations/20261013190000_record_documents.sql` |
| Roles | Upload requires `user_roles.role = patient` for the signed-in user |
| Chain | Uploads are **not** on-chain. Sharing uses one **AccessRegistry**; each Grant writes a commitment for `(resourceId, grantee)` |
| Vault Seal | Must call the API (wired in Vault.tsx). Local-only Seal demos do not appear on the ledger |
| Size / types | API allows ≤10 MB; PDF, PNG, JPEG, UTF-8 text |

## Shared-with-you (on-chain MVP)

The frontend does **not** invent a second contracts-per-share model. For recipients it:

1. Scans `AccessGrantSet` logs where `grantee == vault H160`
2. Confirms each `resourceId` with `hasGrant`
3. Reads `resources(resourceId)` for `dataCid` / owner (`medilink:record:<uuid>` → record id)

See health-locker-guard `Vault` Shared filter + `listSharedGrantsForVault`. On-chain discoveries are merged into the same Shared list as API grants. File preview for recipients is described in [`SharedRecordDelivery.md`](SharedRecordDelivery.md) (dual-write `client_chain` grants + on-chain `hasGrant` document auth).

## Manual check

```bash
# After sign-in, Network tab should show:
# GET  /api/records → 200 [] or [{id,name,kind,...}]
# POST /api/records → 201 {id,name,kind,has_document:true,...}
```

If `GET /api/records` is 503: secret key missing/invalid or schema missing. Restart the API after editing `.env`.
