# MediLink Integrations (Epic, Hippius, EHR storage)

How external EHR sources and storage clients relate to the subnet. Core audit path: [`ARCHITECTURE.md`](ARCHITECTURE.md). Running the subnet: [`OPERATOR_GUIDE.md`](OPERATOR_GUIDE.md).

Ciphertext layout detail: [`../../Hippius_EHR_Storage.md`](../../Hippius_EHR_Storage.md).

---

## 1. Storage principle

| Data | Where | Why |
|------|-------|-----|
| Raw Epic FHIR | Temporary ingestion only | Avoid retaining source dumps |
| Normalized EHR JSON | Off-chain, then encrypted | PHI stays private |
| Encrypted payload + DEK wraps | Hippius / local demo store | Scalable retrieval |
| Payload digest / `dataHash` | AccessRegistry / reference record | Integrity without PHI on chain |
| Access decisions | Registry events + subnet receipts | Audit |
| MediLink receipts | Miners (SQLite / CID store) | Incentive ledger — not health bytes |

**Do not** put names, DOB, diagnoses, lab values, or raw Epic patient IDs on chain.

### Hashing rule (prototype)

1. Normalize FHIR → project JSON (stable key order).  
2. Encrypt.  
3. `SHA-256` over **encrypted** bytes → `sha256:<hex>` / registry `dataHash`.  
4. Store ciphertext off-chain; put digest + `dataCid` on registry.

---

## 2. Epic patient authorization (SMART on FHIR)

Patients must not give Epic passwords to MediLink. Use OAuth:

1. App starts “connect Epic” → identifies FHIR issuer.  
2. Redirect to Epic/MyChart authorize.  
3. Patient approves **minimum** scopes (e.g. Patient, AllergyIntolerance, Immunization, Condition, Observation/DiagnosticReport, DocumentReference as needed).  
4. Backend exchanges code for tokens (never in a public client).  
5. Ingestion adapter reads allowed FHIR resources → normalize → encrypt → store → register digest/CID.  
6. Access thereafter follows AccessRegistry + fail-closed gateway ([`ARCHITECTURE.md`](ARCHITECTURE.md)).

**Tokens:** encrypt at rest if stored; never log values; revoke local refs when patient disconnects.

---

## 3. Epic FHIR → Synapse mapping

Epic data enters through an ingestion adapter, then is exposed via existing Synapse-shaped flows in `protocol.py` (demo/CLI path), not by changing miner challenge HTTP.

| Epic FHIR | Project use | Synapse / field |
|-----------|-------------|-----------------|
| `Patient` | Identity anchor | `ElectronicHealthRecordSynapse.patientId` (pseudonymous) |
| `AllergyIntolerance` | Allergies | `.allergies` |
| `Immunization` | Vaccines | `.immunizations` |
| `Condition` | Problems | `.problems` |
| `Observation` / `DiagnosticReport` | Labs / vitals | `.observations` / `.labResults` |
| `MedicationRequest` | Meds | `.medications` |
| `DocumentReference` / `Binary` | Catalog + encrypted docs | metadata + `MedicalDataRetrievalSynapse` |
| `Encounter` | Visit context | `.encounters` |

Synapse types used by the medical-record CLI/demo path:

- `RecordMetadataSynapse` — discover available records  
- `AccessRequestSynapse` — authorization decision / `keyReference`  
- `MedicalDataRetrievalSynapse` — encrypted payload + `digestReference`  
- `IntegrityVerificationSynapse` — compare digest to chain reference  
- `ElectronicHealthRecordSynapse` — demo aggregated EHR payload  

---

## 4. Off-chain store + on-chain reference

Compact on-chain / registry-facing reference (no medical content):

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

Access flow once stored:

1. `AccessRequestSynapse` (requester, patient, record, purpose).  
2. Access layer / registry evaluates grant.  
3. On approval → retrieval authorization / `keyReference`.  
4. `MedicalDataRetrievalSynapse` → ciphertext + `digestReference`.  
5. `IntegrityVerificationSynapse` → `isValid` iff digest matches.

Production gating should also enforce AccessRegistry `verifyAccess` before Presign ([`ARCHITECTURE.md`](ARCHITECTURE.md) §9).

### Local fixture storage prototype

From `subnet-template`, normalize the synthetic Epic FHIR bundle:

```sh
python3 epic_fhir_ingest.py --patient-id patient-001 --fixture fixtures/epic/patient_bundle.json --output /tmp/normalized_ehr.json
```

Store the normalized payload in the local off-chain record directory and create the reference object:

```sh
python3 ehr_storage.py --input /tmp/normalized_ehr.json --record-id epic-record-001 --storage-root storage/ehr_records --reference-output /tmp/epic-record-001-reference.json
```

This fixture prototype stores normalized JSON at `storage/ehr_records/epic-record-001.json` and returns a `local://...` storage URI plus a `sha256:<hex>` digest. It hashes the unencrypted normalized payload; the encrypted Hippius/registry flow described above still requires an encryption step and a digest over the encrypted bytes.

---

## 5. Hippius CLI contract

```sh
python hippius_cli.py <command> [options]
```

| Command | Envelope `synapseType` | Typical response fields |
|---------|------------------------|-------------------------|
| `metadata` | `metadata` | `availableRecords` |
| `access` | `access` | `accessGranted`, `contractDecision`, `keyReference` |
| `retrieve` | `retrieve` | `encryptedPayload`, `digestReference` |
| `integrity` | `integrity` | `blockchainDigest`, `isValid` |
| `ehr` | `ehr` | demo EHR fields |

Modes: `--dry-run` (print envelope), `--mock-response` (local mock), live HTTP to configured Hippius endpoint.

CLI and miner demo handlers share camelCase fields from `protocol.py`. Note: CLI uses `retrieve`; some configs say `retrieval` — adapters should accept `retrieve` or normalize.

Full request/response field lists live in code (`protocol.py`, `hippius_cli.py`). Prefer running tests over duplicating every JSON schema here.

### Automated + smoke

```bash
cd Main-Project
python3 -m unittest subnet-template/tests/test_hippius_cli.py
# Optional miner synapse flow:
python3 -m unittest subnet-template/tests/test_synapse_flow.py
```

Manual examples:

```bash
python3 subnet-template/hippius_cli.py metadata \
  --patient-id patient-001 --request-id request-001 --record-type ehr --dry-run

python3 subnet-template/hippius_cli.py access \
  --requester-id requester-001 --patient-id patient-001 --record-id record-001 \
  --purpose-of-use treatment --mock-response

python3 subnet-template/hippius_cli.py retrieve \
  --requester-id requester-001 --patient-id patient-001 --record-id record-001 \
  --chunk-range-start 0 --chunk-range-end 2 --mock-response
```

Live mode needs endpoint via config / env / flag (see `hippius_cli.py` help).

Envelope encrypt + registry demo (Python, not CLI): `key-generation/ehr_hippius.py demo` (local `.ehr_local/` without Hippius creds).

---

## 6. Mock medical-record identities

Default demo patient:

| Field | Value |
|-------|-------|
| `patientId` | `patient-001` |
| Label | Mock Patient Alpha |
| Requester | `requester-001` |
| Request ID | `request-001` |

| recordId | type | Notes |
|----------|------|-------|
| `record-001` | `ehr` | Longitudinal summary |
| `record-002` | `lab-results` | Recent labs |

Use these IDs consistently across CLI, miner mock handlers, and walkthroughs.

---

## 7. Epic sandbox test plan (summary)

Environment: Epic on FHIR sandbox or local FHIR fixtures + SMART launch + local adapter.

| # | Case | Expect |
|---|------|--------|
| 1 | Auth launch | Redirect, scopes, code return |
| 2 | FHIR reads | Patient, allergies, immunizations, conditions, labs/docs; empty ≠ crash |
| 3 | Normalize → Synapse | Mapped fields populated; IDs pseudonymized |
| 4 | Encrypt + store | Ciphertext off-chain; digest computed |
| 5 | Registry / reference | Digest + CID registered; no PHI in events |
| 6 | Retrieve + integrity | Payload returns; digest matches |
| 7 | Denial | Ungranted requester cannot retrieve |

Prototype success: one synthetic Epic-derived encrypted payload, digest reference, and integrity check — without production PHI.

### Implemented local prototype commands

From `subnet-template`, configure and validate the Epic sandbox OAuth settings using [`../epic-oauth-config.md`](../epic-oauth-config.md):

```sh
python3 epic_oauth_config.py --config epic_sandbox.config.yml
```

Validate a sandbox-style bundle through ingestion, local storage, and digest verification:

```sh
python3 epic_sandbox_validate.py --bundle fixtures/epic/patient_bundle.json --patient-id patient-001 --record-id epic-sandbox-record-001 --storage-root storage/ehr_records --report-output /tmp/epic-sandbox-validation-report.json
```

The local fixture bundle lives at `fixtures/epic/patient_bundle.json`. See [`../epic-sandbox-validation.md`](../epic-sandbox-validation.md) for the report format and live OAuth follow-up.

Run the fixture, adapter, storage, OAuth configuration, and downstream Synapse tests:

```sh
python3 -m unittest tests/test_epic_fixtures.py
python3 -m unittest tests/test_epic_fhir_ingest.py
python3 -m unittest tests/test_ehr_storage.py
python3 -m unittest tests/test_epic_sandbox_validate.py
python3 -m unittest tests/test_epic_oauth_config.py
python3 -m unittest tests/test_synapse_flow.py
python3 -m unittest tests/test_hippius_cli.py
```

---

## 8. How this relates to `validatorv1` / `minerv1`

| Path | What it proves |
|------|----------------|
| **Incentive path** (`validatorv1` ↔ `minerv1` HTTP) | AccessGranted → AUDIT receipts → challenges → SetWeights ([`OPERATOR_GUIDE.md`](OPERATOR_GUIDE.md)) |
| **Medical Synapse / Hippius CLI path** | App-facing metadata/access/retrieve/integrity envelopes |
| **Epic ingestion path** | How PHI enters encrypted storage before registry + audit |

They share the principle “ciphertext off-chain, commitments/events on-chain,” but the **subnet emissions loop** is the AccessRegistry poller + receipt challenges — not the Epic OAuth redirect itself.
