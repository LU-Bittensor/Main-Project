# Hippius CLI Synapse Contract

This document defines the request and response contract for sending Synapse-formatted payloads to Hippius through a command-line interface.

The purpose of this contract is to make later implementation predictable across:

- CLI argument parsing
- Synapse payload construction
- Hippius submission logic
- response parsing
- testing and validation

## Scope

This contract covers the CLI-side interaction model for the following request flows:

- record metadata discovery
- access request submission
- medical data retrieval
- integrity verification
- full EHR request

This contract does not define blockchain smart contract behavior. It only defines how the CLI should package requests and interpret responses.

## Design Goals

- Keep request types aligned with the Synapse classes already defined in `protocol.py`
- Make commands easy to run manually for local testing
- Keep output structured enough for later automation
- Support both human-readable and JSON-friendly response handling

## CLI Command Shape

The base command should follow this pattern:

```sh
python hippius_cli.py <command> [options]
```

Recommended top-level commands:

- `metadata`
- `access`
- `retrieve`
- `integrity`
- `ehr`

Optional future shared flags:

- `--output text|json`
- `--timeout SECONDS`
- `--dry-run`
- `--config PATH`

## Common Request Fields

These fields are shared across multiple request types:

- `patientId`
- `requesterId`
- `recordId`
- `requestId`

CLI arguments should use kebab-case and map to Synapse fields in camelCase.

Examples:

- `--patient-id` -> `patientId`
- `--requester-id` -> `requesterId`
- `--record-id` -> `recordId`
- `--request-id` -> `requestId`

## Request Contract by Command

### 1. Metadata Discovery

#### CLI form

```sh
python hippius_cli.py metadata --patient-id PATIENT_ID --request-id REQUEST_ID [--record-type TYPE]
```

#### Synapse type

`RecordMetadataSynapse`

#### Required fields

- `patientId`
- `requestId`

#### Optional fields

- `recordType`

#### Example request payload

```json
{
  "synapseType": "metadata",
  "payload": {
    "patientId": "patient-001",
    "requestId": "request-001",
    "recordType": "ehr"
  }
}
```

#### Expected response payload

```json
{
  "status": "success",
  "synapseType": "metadata",
  "payload": {
    "patientId": "patient-001",
    "requestId": "request-001",
    "availableRecords": [
      {
        "recordId": "record-001",
        "recordType": "ehr",
        "description": "Patient longitudinal health summary"
      }
    ]
  }
}
```

### 2. Access Request

#### CLI form

```sh
python hippius_cli.py access --requester-id REQUESTER_ID --patient-id PATIENT_ID --record-id RECORD_ID --purpose-of-use PURPOSE [--date-range-start DATE] [--date-range-end DATE]
```

#### Synapse type

`AccessRequestSynapse`

#### Required fields

- `requesterId`
- `patientId`
- `recordId`
- `purposeOfUse`

#### Optional fields

- `dateRangeStart`
- `dateRangeEnd`

#### Example request payload

```json
{
  "synapseType": "access",
  "payload": {
    "requesterId": "requester-001",
    "patientId": "patient-001",
    "recordId": "record-001",
    "purposeOfUse": "treatment"
  }
}
```

#### Expected response payload

```json
{
  "status": "success",
  "synapseType": "access",
  "payload": {
    "accessGranted": true,
    "contractDecision": "approved",
    "keyReference": "mock-key://patient-001/record-001"
  }
}
```

### 3. Medical Data Retrieval

#### CLI form

```sh
python hippius_cli.py retrieve --requester-id REQUESTER_ID --patient-id PATIENT_ID --record-id RECORD_ID [--chunk-range-start N] [--chunk-range-end N]
```

#### Synapse type

`MedicalDataRetrievalSynapse`

#### Required fields

- `requesterId`
- `patientId`
- `recordId`

#### Optional fields

- `chunkRangeStart`
- `chunkRangeEnd`

#### Example request payload

```json
{
  "synapseType": "retrieval",
  "payload": {
    "requesterId": "requester-001",
    "patientId": "patient-001",
    "recordId": "record-001",
    "chunkRangeStart": 0,
    "chunkRangeEnd": 2
  }
}
```

#### Expected response payload

```json
{
  "status": "success",
  "synapseType": "retrieval",
  "payload": {
    "encryptedPayload": "mock-encrypted-payload:record-001:chunks-0-2",
    "digestReference": "digest::record-001"
  }
}
```

### 4. Integrity Verification

#### CLI form

```sh
python hippius_cli.py integrity --record-id RECORD_ID --provided-digest DIGEST
```

#### Synapse type

`IntegrityVerificationSynapse`

#### Required fields

- `recordId`
- `providedDigest`

#### Example request payload

```json
{
  "synapseType": "integrity",
  "payload": {
    "recordId": "record-001",
    "providedDigest": "digest::record-001"
  }
}
```

#### Expected response payload

```json
{
  "status": "success",
  "synapseType": "integrity",
  "payload": {
    "blockchainDigest": "digest::record-001",
    "isValid": true
  }
}
```

### 5. Full EHR Request

#### CLI form

```sh
python hippius_cli.py ehr --patient-id PATIENT_ID
```

#### Synapse type

`ElectronicHealthRecordSynapse`

#### Required fields

- `patientId`

#### Example request payload

```json
{
  "synapseType": "ehr",
  "payload": {
    "patientId": "patient-001"
  }
}
```

#### Expected response payload

```json
{
  "status": "success",
  "synapseType": "ehr",
  "payload": {
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
    ]
  }
}
```

## Response Envelope

Every CLI-facing response should use a consistent envelope.

Recommended structure:

```json
{
  "status": "success",
  "synapseType": "metadata",
  "payload": {},
  "error": null
}
```

Field meanings:

- `status`: `success` or `error`
- `synapseType`: request category being processed
- `payload`: returned Synapse-compatible response body
- `error`: machine-readable or human-readable error description when present

## Error Contract

If a request fails, the CLI should return a consistent error object.

Recommended structure:

```json
{
  "status": "error",
  "synapseType": "access",
  "payload": null,
  "error": {
    "code": "ACCESS_DENIED",
    "message": "Access request was denied by the authorization layer."
  }
}
```

Suggested error codes:

- `INVALID_ARGUMENTS`
- `REQUEST_BUILD_FAILED`
- `HIPPIUS_CONNECTION_FAILED`
- `HIPPIUS_TIMEOUT`
- `ACCESS_DENIED`
- `RECORD_NOT_FOUND`
- `DIGEST_MISMATCH`
- `UNEXPECTED_RESPONSE_FORMAT`

## Expected Failure Cases

The CLI should handle these failed access, retrieval, and integrity paths without printing partial JSON or mixing request output with response output.

| Flow | Failure case | Expected envelope behavior |
| --- | --- | --- |
| Access | requester purpose is not approved | return `status: "success"` with `accessGranted: false`, `contractDecision: "denied"`, and no `keyReference` |
| Access | Hippius or contract layer rejects the request | return `status: "error"` with an `ACCESS_DENIED` error when the endpoint provides one |
| Retrieval | record cannot be found | return `status: "error"` with a `RECORD_NOT_FOUND` error |
| Retrieval | access was not approved before retrieval | return `status: "error"` with an `ACCESS_DENIED` error |
| Retrieval | Hippius endpoint is unreachable | exit non-zero and print a clear `Could not reach Hippius endpoint` message to stderr |
| Integrity | provided digest does not match chain digest | return `status: "success"` with `isValid: false` and the expected `blockchainDigest` |
| Integrity | returned response is not valid JSON | exit non-zero and print a clear `Hippius returned non-JSON response` message to stderr |
| Any flow | response omits the standard envelope but returns a JSON object | normalize the object into `status`, `synapseType`, `payload`, and `error` |

## Output Modes

The CLI should support two output modes:

- `text`
  - concise, human-readable summary
- `json`
  - raw structured response envelope

Examples:

- `--output text`
- `--output json`

## Mapping Rules

The implementation should follow these mapping rules:

- CLI flags use kebab-case
- internal Synapse fields use camelCase
- request command names map to one Synapse class each
- returned response fields should preserve the names already used by the Synapse models

## Relationship to Existing Project Files

This contract should stay aligned with:

- `protocol.py`
- `validator.py`
- `mock-medical-record-flow.md`
- `hippius-cli-integration-plan.md`

Future Hippius CLI implementation should follow this contract before adding transport-specific logic.

## Next Steps

This contract directly supports the next sprint tasks:

1. validate live Hippius CLI flow against miner-side Hippius interaction requirements
2. maintain an integration test plan for Hippius CLI, Synapse request objects, and miner response handling
3. keep expected error cases aligned with access, retrieval, and integrity behavior
