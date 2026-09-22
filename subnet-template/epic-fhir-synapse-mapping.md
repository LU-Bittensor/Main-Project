# Epic FHIR to Synapse Mapping

This document maps likely Epic on FHIR resources into the current project Synapse model. The goal is to define how Epic can act as an enterprise EHR intake source without changing the existing miner/validator request flow.

## Integration Assumption

Epic data should enter the project through authorized FHIR API reads. The ingestion adapter should normalize Epic FHIR resources into project-owned records, then expose those records through the existing Synapse flows:

- `RecordMetadataSynapse` for discovering available records
- `AccessRequestSynapse` for authorization decisions
- `MedicalDataRetrievalSynapse` for encrypted payload retrieval
- `IntegrityVerificationSynapse` for hash verification
- `ElectronicHealthRecordSynapse` for the current demo EHR payload

Epic supports SMART on FHIR app launch patterns and many FHIR resources through open.epic and Epic on FHIR. The adapter should start with the smallest resource set needed for a patient record prototype.

## Priority Resource Mapping

| Epic FHIR resource | Project use | Synapse target | Notes |
| --- | --- | --- | --- |
| `Patient` | Patient identity and demographic anchor | `ElectronicHealthRecordSynapse.patientId`; metadata payload fields | Do not store raw Epic patient identifiers on chain. Map to internal pseudonymous IDs. |
| `AllergyIntolerance` | Patient allergies | `ElectronicHealthRecordSynapse.allergies` | Current model keeps `substance`, `reaction`, and `severity`; adapter should extract those from FHIR coding/text fields. |
| `Immunization` | Vaccination history | `ElectronicHealthRecordSynapse.immunizations` | Current model stores `name`, `dateAdministered`, and `provider`. |
| `Condition` | Problem list / diagnoses | `ElectronicHealthRecordSynapse.problems` | Start with display text and clinical status; expand later if diagnosis codes are needed. |
| `Observation` | Vitals, labs, measurements | `ElectronicHealthRecordSynapse.observations`; `labResults` for existing demo lab summaries | Use `observations` for normalized values and keep `labResults` for backwards-compatible demo payloads. |
| `DiagnosticReport` | Lab report summaries | `ElectronicHealthRecordSynapse.labResults` | Useful for grouping observations into a report-level result. |
| `MedicationRequest` | Active or ordered medications | `ElectronicHealthRecordSynapse.medications` | Start with medication name, status, authored date, and requester. |
| `DocumentReference` | Available clinical documents | `RecordMetadataSynapse.availableRecords`; `ElectronicHealthRecordSynapse.documents` | Can populate record catalog entries before retrieval and document metadata in EHR payloads. |
| `Binary` | Retrieved document contents | `MedicalDataRetrievalSynapse.encryptedPayload` after encryption | Do not expose raw binary content directly through the chain. |
| `Encounter` | Visit context | `ElectronicHealthRecordSynapse.encounters` | Useful for filtering records by time, provider, or visit. |

## Metadata Discovery Mapping

`RecordMetadataSynapse` should be populated from a catalog of imported Epic-derived records. For the prototype:

```json
{
  "patientId": "patient-001",
  "requestId": "request-001",
  "availableRecords": [
    {
      "recordId": "epic-record-001",
      "recordType": "ehr-summary",
      "description": "Epic FHIR patient summary",
      "sourceSystem": "epic-fhir",
      "dateRangeStart": "2026-01-01",
      "dateRangeEnd": "2026-04-01"
    }
  ]
}
```

The `recordId` should be internal to this project. It should not be a raw Epic resource URL or production patient identifier.

## EHR Payload Mapping

The current EHR Synapse can hold a compact patient snapshot:

```json
{
  "patientId": "patient-001",
  "allergies": [],
  "immunizations": [],
  "problems": [],
  "labResults": [],
  "medications": [],
  "encounters": [],
  "documents": [],
  "observations": []
}
```

For the first Epic prototype, populate these supported fields in `protocol.py`:

- `AllergyIntolerance` to `allergies`
- `Immunization` to `immunizations`
- `Condition` to `problems`
- `Observation` or `DiagnosticReport` to `labResults`
- `MedicationRequest` to `medications`
- `Encounter` to `encounters`
- `DocumentReference` to `documents`
- general `Observation` values to `observations`

Adding procedures or richer coded diagnosis structures should be a follow-up schema change rather than hidden inside existing fields.

## Access and Retrieval Mapping

The Epic authorization step decides whether the project can ingest data from Epic. The project access step decides whether another participant can access an already-ingested off-chain record.

Keep those separate:

1. Epic OAuth grants ingestion access from Epic into the project.
2. Project access request grants retrieval access from project storage to an authorized requester.
3. Retrieval returns an encrypted off-chain payload reference.
4. Integrity verification compares the retrieved payload hash with the on-chain digest.

## Minimum Prototype Scope

The first prototype should ingest:

- one synthetic patient
- allergies
- immunizations
- conditions/problem list
- lab result summary
- one document metadata entry, if available in the sandbox

That is enough to prove the pipeline without pretending to support the full Epic chart.

## Sources

- Epic on FHIR documentation: https://fhir.epic.com/Documentation
- Epic on FHIR resource summary and sandbox: https://fhir.epic.com/
- open.epic FHIR interface overview: https://open.epic.com/interface/FHIR
