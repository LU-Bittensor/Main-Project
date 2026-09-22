# Hippius CLI Integration Plan

This document validates the live Hippius CLI flow against the current miner-side Synapse handlers and defines the next integration test path.

## Current Alignment

| Flow | CLI command | Request envelope type | Miner handler | Miner response fields |
| --- | --- | --- | --- | --- |
| Metadata discovery | `metadata` | `metadata` | `record_metadata` | `availableRecords` |
| Access request | `access` | `access` | `access_request` | `accessGranted`, `contractDecision`, `keyReference` |
| Medical data retrieval | `retrieve` | `retrieve` | `medical_data_retrieval` | `encryptedPayload`, `digestReference` |
| Integrity verification | `integrity` | `integrity` | `integrity_verification` | `blockchainDigest`, `isValid` |
| Full EHR request | `ehr` | `ehr` | `electronic_health_record` | `patientId`, `allergies`, `immunizations`, `problems`, `labResults` |

The CLI and miner use the same camelCase payload fields from `protocol.py`. The main naming difference is the retrieval flow: the CLI command and envelope use `retrieve`, while validator configuration uses `retrieval`. Any live Hippius adapter should accept `retrieve` from the CLI or normalize it before creating a `MedicalDataRetrievalSynapse`.

## Live CLI Flow

1. The user runs `hippius_cli.py <command>`.
2. The CLI builds a JSON envelope with `synapseType` and `payload`.
3. In dry-run mode, the CLI prints the envelope and exits.
4. In mock mode, the CLI returns the local mock response envelope.
5. In live mode, the CLI posts the envelope to the configured Hippius endpoint.
6. The response is normalized into a single response envelope with `status`, `synapseType`, `payload`, and `error`.

## Miner-Side Requirements

The endpoint behind live Hippius transport should be able to map each envelope into the matching Synapse class:

- `metadata` maps to `RecordMetadataSynapse`
- `access` maps to `AccessRequestSynapse`
- `retrieve` maps to `MedicalDataRetrievalSynapse`
- `integrity` maps to `IntegrityVerificationSynapse`
- `ehr` maps to `ElectronicHealthRecordSynapse`

The adapter should return the miner response as a JSON object. Returning the full response envelope is preferred, but the CLI can also normalize a bare payload object into the envelope format.

## Integration Test Plan

### 1. Local CLI contract tests

Run:

```sh
python3 -m unittest tests/test_hippius_cli.py
```

Expected result:

- request envelopes are built with the correct field names
- mock success, denial, and digest mismatch responses are stable
- live HTTP submission posts the expected JSON body
- HTTP error envelopes stay structured

### 2. Miner handler tests

Run:

```sh
python3 -m unittest tests/test_synapse_flow.py
```

Expected result:

- each miner handler returns the expected response fields
- validator request construction still maps to the expected Synapse classes

### 3. Live endpoint smoke test

With the Hippius adapter running:

```sh
python3 hippius_cli.py metadata --patient-id patient-001 --request-id request-001 --endpoint http://127.0.0.1:8080/synapse
python3 hippius_cli.py access --requester-id requester-001 --patient-id patient-001 --record-id record-001 --purpose-of-use treatment --endpoint http://127.0.0.1:8080/synapse
python3 hippius_cli.py retrieve --requester-id requester-001 --patient-id patient-001 --record-id record-001 --chunk-range-start 0 --chunk-range-end 2 --endpoint http://127.0.0.1:8080/synapse
python3 hippius_cli.py integrity --record-id record-001 --provided-digest digest::record-001 --endpoint http://127.0.0.1:8080/synapse
```

Expected result:

- each command prints one JSON response envelope
- access returns `contractDecision`
- retrieval returns `encryptedPayload` and `digestReference`
- integrity returns `blockchainDigest` and `isValid`

## Contract Receipt Review

Contract receipt fields should be carried in the response envelope without changing the existing Synapse payload names. Recommended receipt fields are:

| Receipt field | Purpose | Related Synapse flow |
| --- | --- | --- |
| `receiptId` | Unique receipt identifier for the event | access, retrieval |
| `recordId` | Record associated with the event | access, retrieval, integrity |
| `requesterId` | Requesting identity | access, retrieval |
| `patientId` | Patient identity | metadata, access, retrieval, EHR |
| `contractDecision` | Access-control result | access |
| `digestReference` | Stored payload digest reference | retrieval |
| `blockchainDigest` | Digest read from chain | integrity |
| `transactionHash` | On-chain transaction reference | access, retrieval, integrity |
| `timestamp` | Event emission time | access, retrieval, integrity |

If the contract emits a receipt, the CLI should include it under `payload.receipt` or preserve it if the live endpoint already returns that field. This keeps the top-level envelope stable for downstream tooling.

## Open Integration Questions

- Should the live adapter expose one `/synapse` route or separate routes per request type?
- Should retrieval require a prior approved access receipt before returning encrypted payload data?
- Should denied access use `status: "success"` with `contractDecision: "denied"` or `status: "error"` with `ACCESS_DENIED`?
- Which contract receipt fields are final once receipt emission is standardized?
