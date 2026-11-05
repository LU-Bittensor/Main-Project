# Pre-prod integration — November 5, 2026

The active checkout is `Main-Project-integration/`. Both it and its separate
`frontend/` repository now use local `pre-prod` branches tracking their respective
`origin/pre-prod` branches.

## Integrated upstream versions

- Main-Project: `16128ca` — faucet contract/API and vault wallet enrollment.
- Frontend: `248b2d6` — wallet enrollment and signing, recovery phrase flow,
  document upload support, and discovery of on-chain shares. `origin/main` at
  `d64b93a` has the same file contents as this frontend `pre-prod` revision.

The upstream parent gitlink still pointed to the older frontend `eef5315` and
lacked `.gitmodules`. The submodule declaration now records the frontend URL and
`pre-prod` branch; the parent records the integrated frontend commit.

## Preserved work and merge decisions

- Epic authorization, live imports, encrypted snapshots, clinical dashboard,
  filtering/pagination, repeat imports, and immutable saved-report previews.
- Saved-report grants, recipient delivery, and revocation through the authenticated
  API. Existing API grants continue using that API for access checks and revocation,
  even when a browser wallet registry is configured.
- Uploaded documents retain the upstream wallet-signed sharing path when a
  registry is configured. Epic reports use the report API because a standalone
  wallet transaction does not create the API permission needed to deliver them.
  Mixed report/upload batches in wallet mode require choosing a record type first.
- The shared-record display, incoming/outgoing receipt tabs, and account-bound
  remounting remain available alongside the new on-chain discovery panel.
- The health endpoint reports both faucet availability and document-storage
  configuration. Missing cloud document credentials do not block local Epic reports.
- Signing out clears in-memory wallet keys. Enrollment rejects a vault belonging
  to a different signed-in account, preventing account-switch cross-contamination.

## Validation

- 116 backend tests pass: OAuth, imports, saved-report sharing, grant API,
  documents, Hippius storage, and faucet/enrollment tests. Network services are
  simulated in these tests; this is not a live blockchain or cloud-storage test.
- 9 frontend tests pass: vault crypto, sharing-path selection, and account-bound
  wallet enrollment.
- Frontend TypeScript checking, lint for the integration files, and production
  build pass. Python dependency consistency check passes.
- All 16 Hardhat tests pass; all six Solidity source files compile. This includes
  the existing challenge/replay tests and the new faucet source checks; the live
  faucet funding and cooldown integration tests were not run against an RPC node.
- MAIN-267's real patient-to-provider grant/view/revoke browser confirmation is
  still pending. The pre-update 269-record owner report preview was verified;
  no real-account grant was submitted during this work.

## Recovery and publishing

Recovery branches preserve the pre-merge work:

- Main-Project: `codex/backup-epic-before-pre-prod-20261105` (`9250c9a`).
- Frontend: `codex/backup-epic-ui-before-pre-prod-20261105` (`43f427a`).

These snapshots do not include ignored passwords, master keys, or local databases;
those remain in their existing local directories. Keep the data directory and its
master key together.

This integration is committed locally. Publish the frontend `pre-prod` first,
then the parent `pre-prod`, so teammates can resolve the frontend gitlink.
