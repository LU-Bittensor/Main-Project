# Hippius CLI Smoke Tests

This document defines a basic validation and smoke-testing workflow for the Hippius CLI integration layer.

The goal is to quickly confirm that:

- request envelopes can be constructed from CLI arguments
- mock Hippius responses can be generated
- live Hippius endpoints can be targeted with config or environment settings
- response envelopes can be parsed and rendered
- the CLI contract remains stable as the implementation grows

## Automated Test Command

Run the focused Hippius CLI test suite:

```sh
python3 -m unittest subnet-template/tests/test_hippius_cli.py
```

This suite validates:

- metadata request construction
- retrieval mock response generation
- live HTTP transport request submission
- Hippius CLI config/env/flag resolution
- denied access and digest mismatch response handling
- structured HTTP error handling
- response envelope parsing
- text rendering for parsed responses
- JSON rendering for mock response output

## Manual Smoke Test Commands

Run these from the project root.

### 1. Build a metadata request

```sh
python3 subnet-template/hippius_cli.py metadata --patient-id patient-001 --request-id request-001 --record-type ehr --dry-run
```

Expected result:

- prints a JSON request envelope
- includes `synapseType: metadata`
- includes `patientId`, `requestId`, and `recordType`

### 2. Build an access request and return a mock response

```sh
python3 subnet-template/hippius_cli.py access --requester-id requester-001 --patient-id patient-001 --record-id record-001 --purpose-of-use treatment --mock-response
```

Expected result:

- prints a success response envelope
- includes `contractDecision: approved`
- includes a mock `keyReference`

### 3. Build a retrieval request and save the mock response

```sh
python3 subnet-template/hippius_cli.py retrieve --requester-id requester-001 --patient-id patient-001 --record-id record-001 --chunk-range-start 0 --chunk-range-end 2 --mock-response > /tmp/hippius_response.json
```

Expected result:

- writes a JSON response envelope to `/tmp/hippius_response.json`
- includes `encryptedPayload`
- includes `digestReference`

### 4. Parse the saved response file

```sh
python3 subnet-template/hippius_cli.py parse-response --response-file /tmp/hippius_response.json
```

Expected result:

- prints a human-readable response summary
- includes `Status: success`
- includes `Synapse type: retrieve`
- includes `digestReference: digest::record-001`

### 5. Submit a live Hippius request

```sh
python3 subnet-template/hippius_cli.py metadata --patient-id patient-001 --request-id request-001 --config subnet-template/hippius.config.example.yml
```

Equivalent environment-based setup:

```sh
export HIPPIUS_CLI_ENDPOINT=http://127.0.0.1:8080/synapse
export HIPPIUS_CLI_TIMEOUT=30
python3 subnet-template/hippius_cli.py metadata --patient-id patient-001 --request-id request-001
```

Expected result:

- posts the JSON Synapse envelope to the configured endpoint
- prints one parsed Hippius response envelope
- exits with an error message if the endpoint is missing, unreachable, or returns invalid JSON

## Minimum Pass Criteria

The smoke test is considered successful if:

- the automated test suite passes
- all five manual commands run without crashing
- each command returns the expected request or response shape
- live mode is configured through `--config`, `--endpoint`, or `HIPPIUS_CLI_ENDPOINT`

## Notes

- The automated tests mock live transport so they do not require a running Hippius service.
- Live blockchain execution still depends on the endpoint behind Hippius.
- The broader validation plan is documented in `hippius-cli-integration-plan.md`.
