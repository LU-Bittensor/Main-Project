# Mock Payloads for Medical Record Flow

This document defines the mock payloads used to test the validator-to-miner medical record request flow.

The goal is to keep one consistent set of sample identities, record IDs, and expected responses across:

- validator CLI testing
- miner handler development
- demo walkthroughs
- end-to-end request flow checks

## Mock Patient

Use the following patient as the default testing identity:

- `patientId`: `patient-001`
- patient label: `Mock Patient Alpha`
- primary requester: `requester-001`
- default request ID: `request-001`

## Mock Record Catalog

The miner currently exposes these sample records during metadata discovery:

| recordId | recordType | Description | Date Range |
| --- | --- | --- | --- |
| `record-001` | `ehr` | Patient longitudinal health summary | `2025-01-01` to `2026-04-01` |
| `record-002` | `lab-results` | Recent laboratory panel results | `2026-03-01` to `2026-03-31` |

## Metadata Discovery Example

### Example request

```json
{
  "patientId": "patient-001",
  "requestId": "request-001",
  "recordType": null
}
```

### Expected response

```json
{
  "patientId": "patient-001",
  "requestId": "request-001",
  "availableRecords": [
    {
      "recordId": "record-001",
      "recordType": "ehr",
      "description": "Patient longitudinal health summary",
      "dateRangeStart": "2025-01-01",
      "dateRangeEnd": "2026-04-01"
    },
    {
      "recordId": "record-002",
      "recordType": "lab-results",
      "description": "Recent laboratory panel results",
      "dateRangeStart": "2026-03-01",
      "dateRangeEnd": "2026-03-31"
    }
  ]
}
```

## Access Request Examples

### Approved example

Use this to test a successful contract decision:

```json
{
  "requesterId": "requester-001",
  "patientId": "patient-001",
  "recordId": "record-001",
  "purposeOfUse": "treatment",
  "dateRangeStart": null,
  "dateRangeEnd": null
}
```

Expected result:

```json
{
  "accessGranted": true,
  "contractDecision": "approved",
  "keyReference": "mock-key://patient-001/record-001"
}
```

### Denied example

Use this to test a rejected request:

```json
{
  "requesterId": "requester-001",
  "patientId": "patient-001",
  "recordId": "record-001",
  "purposeOfUse": "marketing"
}
```

Expected result:

```json
{
  "accessGranted": false,
  "contractDecision": "denied",
  "keyReference": null
}
```

## Retrieval Example

This mock flow assumes access has already been approved.

### Example request

```json
{
  "requesterId": "requester-001",
  "patientId": "patient-001",
  "recordId": "record-001",
  "chunkRangeStart": 0,
  "chunkRangeEnd": 2
}
```

### Expected response

```json
{
  "encryptedPayload": "mock-encrypted-payload:record-001:chunks-0-2",
  "digestReference": "digest::record-001"
}
```

## Integrity Verification Examples

### Matching digest

```json
{
  "recordId": "record-001",
  "providedDigest": "digest::record-001"
}
```

Expected result:

```json
{
  "blockchainDigest": "digest::record-001",
  "isValid": true
}
```

### Non-matching digest

```json
{
  "recordId": "record-001",
  "providedDigest": "digest-001"
}
```

Expected result:

```json
{
  "blockchainDigest": "digest::record-001",
  "isValid": false
}
```

## Full Mock EHR Payload

Use this as the canonical sample patient record returned by the miner:

```json
{
  "patientId": "patient-001",
  "allergies": [
    {
      "substance": "Penicillin",
      "reaction": "Rash",
      "severity": "moderate"
    }
  ],
  "immunizations": [
    {
      "name": "Influenza",
      "dateAdministered": "2026-01-12",
      "provider": "Campus Health Clinic"
    }
  ],
  "problems": [
    "Hypertension",
    "Seasonal allergies"
  ],
  "labResults": [
    {
      "testName": "CBC",
      "date": "2026-03-21",
      "status": "final",
      "summary": "Within normal range"
    }
  ],
  "medications": [
    {
      "name": "Lisinopril",
      "status": "active",
      "authoredOn": "2026-02-14",
      "requester": "Campus Health Clinic"
    }
  ],
  "encounters": [
    {
      "encounterId": "encounter-001",
      "status": "finished",
      "classCode": "ambulatory",
      "periodStart": "2026-03-21T09:00:00Z",
      "periodEnd": "2026-03-21T09:30:00Z"
    }
  ],
  "documents": [
    {
      "documentId": "document-001",
      "documentType": "clinical-note",
      "title": "Primary care visit summary",
      "date": "2026-03-21",
      "contentReference": "mock-document://patient-001/document-001"
    }
  ],
  "observations": [
    {
      "name": "Blood pressure",
      "value": "128/82",
      "unit": "mmHg",
      "effectiveDate": "2026-03-21",
      "status": "final"
    }
  ]
}
```

## Suggested Validator Test Commands

Run these from the `subnet-template` directory after the miner is running:

```sh
python validator.py --request-type metadata --patient-id patient-001 --request-id request-001
python validator.py --request-type access --requester-id requester-001 --patient-id patient-001 --record-id record-001 --purpose-of-use treatment
python validator.py --request-type retrieval --requester-id requester-001 --patient-id patient-001 --record-id record-001 --chunk-range-start 0 --chunk-range-end 2
python validator.py --request-type integrity --record-id record-001 --provided-digest digest::record-001
python validator.py --request-type ehr --patient-id patient-001
```

## Notes

- The values are intentionally simple so validator and miner behavior is easy to inspect in logs.
