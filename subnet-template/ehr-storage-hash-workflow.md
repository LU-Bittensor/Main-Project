# Off-Chain EHR Storage and On-Chain Hash Workflow

This document defines the recommended storage pattern for Epic-derived EHR data in the project.

## Goal

Use Epic FHIR ingestion to populate an encrypted off-chain medical record store, then write only integrity and access metadata on chain. Protected health information should not be stored directly on chain.

## Recommended Architecture

1. A patient authorizes Epic FHIR access through SMART on FHIR / OAuth.
2. The ingestion adapter reads selected FHIR resources.
3. The adapter normalizes the data into the project EHR/Synapse format.
4. The normalized payload is encrypted.
5. The encrypted payload is stored off-chain.
6. The project computes a digest of the encrypted payload.
7. The chain stores a reference record containing a digest and non-sensitive metadata.
8. Authorized requesters retrieve the off-chain payload.
9. Validators or clients recompute the digest and compare it to the on-chain value.

## Data Placement

| Data element | Storage location | Reason |
| --- | --- | --- |
| Raw Epic FHIR response | Temporary ingestion workspace only | Avoid retaining unnecessary raw source payloads. |
| Normalized EHR payload | Off-chain encrypted store | Contains medical data and should remain private. |
| Encrypted payload | Off-chain store or Hippius-backed storage | Supports scalable retrieval without chain bloat. |
| Payload digest | On chain | Provides integrity verification. |
| Internal record ID | On chain and off chain | Links access decisions to retrievable records. |
| Raw patient identifier | Off chain only, if needed | Avoid permanent on-chain PHI or linkable identifiers. |
| Pseudonymous patient ID | On chain if needed | Reduces exposure while supporting lookup and audit. |
| Access decision receipt | On chain or contract event | Supports auditability. |

## Reference Record Shape

The on-chain record should be compact and should not include raw medical content:

```json
{
  "recordId": "record-001",
  "patientRef": "patient-ref-7f3a",
  "sourceSystem": "epic-fhir",
  "recordType": "ehr-summary",
  "digest": "sha256:...",
  "storageReference": "encrypted-reference",
  "createdAt": "2026-09-22T00:00:00Z",
  "accessPolicyRef": "policy-001"
}
```

`storageReference` should not reveal protected information. In a production version, it should be encrypted, indirect, or resolvable only by the project backend after authorization.

## Hashing Rule

The digest should be computed over a canonical payload representation. Recommended prototype rule:

1. Normalize the selected FHIR resources into project JSON.
2. Sort object keys consistently.
3. Encrypt the normalized JSON payload.
4. Compute `SHA-256` over the encrypted bytes.
5. Store the digest on chain as `sha256:<hex>`.

Hashing the encrypted payload verifies the exact retrievable object without placing medical details on chain.

## Access Flow

1. Requester submits an `AccessRequestSynapse` with requester, patient, record, and purpose of use.
2. Contract or access layer evaluates whether the requester can access the record.
3. If approved, the response includes a `keyReference` or retrieval authorization.
4. Requester submits a `MedicalDataRetrievalSynapse`.
5. Project returns the encrypted payload and `digestReference`.
6. Requester or validator runs `IntegrityVerificationSynapse`.
7. `isValid` is true only when the retrieved payload digest matches the chain digest.

## Privacy Constraints

- Do not write raw Epic patient identifiers on chain.
- Do not write names, dates of birth, addresses, notes, diagnoses, lab values, or documents on chain.
- Avoid stable identifiers that could be linked back to a patient across systems.
- Keep consent and access revocation separate from immutable hash records.
- Treat on-chain metadata as permanent.

## Prototype Deliverable

The first prototype should demonstrate:

- one synthetic Epic-derived EHR payload
- encrypted off-chain storage placeholder
- SHA-256 digest generation
- on-chain reference shape
- retrieval integrity check against the stored digest

This is enough to show the design without handling live production health data.

## Local Prototype Commands

Normalize the synthetic Epic FHIR bundle:

```sh
python3 epic_fhir_ingest.py --patient-id patient-001 --fixture fixtures/epic/patient_bundle.json --output /tmp/normalized_ehr.json
```

Store the normalized payload in the local off-chain record directory and create the reference object:

```sh
python3 ehr_storage.py --input /tmp/normalized_ehr.json --record-id epic-record-001 --storage-root storage/ehr_records --reference-output /tmp/epic-record-001-reference.json
```

The prototype stores the full normalized payload at:

```sh
storage/ehr_records/epic-record-001.json
```

The generated reference JSON contains the local storage URI and `sha256:<hex>` digest. In a later Hippius-backed version, the `storageUri` can change from `local://...` to a Hippius content reference while keeping the digest and verification behavior the same.
