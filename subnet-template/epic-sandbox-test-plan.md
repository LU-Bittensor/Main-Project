# Epic Sandbox Test Plan

This document defines a prototype test plan for Epic FHIR ingestion.

## Goal

Validate that Epic FHIR resources can be read from a sandbox or synthetic fixture, mapped into the project Synapse model, stored off chain, and verified through an on-chain-style digest reference.

## Test Environment

Use Epic on FHIR sandbox resources where possible:

- Epic FHIR sandbox endpoint
- registered sandbox client ID
- SMART on FHIR OAuth test launch
- synthetic patient data
- local ingestion adapter or fixture-driven script

If live sandbox access is not available, use local FHIR JSON fixtures shaped like Epic FHIR responses.

## Test Cases

### 1. Patient authorization launch

Purpose: confirm the app can start a SMART on FHIR authorization flow.

Expected result:

- app redirects to Epic authorization URL
- requested scopes are visible in app configuration
- redirect URI is accepted
- authorization code is returned in the sandbox flow

### 2. FHIR resource read

Purpose: confirm the adapter can fetch selected resources.

Resources to test first:

- `Patient`
- `AllergyIntolerance`
- `Immunization`
- `Condition`
- `Observation` or `DiagnosticReport`
- `DocumentReference`, if available

Expected result:

- each supported resource returns valid FHIR JSON
- missing resources are handled as empty lists, not fatal errors
- missing scopes are reported clearly

### 3. FHIR-to-Synapse mapping

Purpose: verify Epic-derived resources become project payloads.

Expected result:

- allergies map into `allergies`
- immunizations map into `immunizations`
- conditions map into `problems`
- labs map into `labResults`
- record metadata produces `availableRecords`

### 4. Off-chain storage simulation

Purpose: verify the project can package and reference imported data.

Expected result:

- normalized JSON payload is serialized consistently
- encrypted payload placeholder is generated
- internal `recordId` is assigned
- no raw Epic patient ID is written to the chain-reference object

### 5. Hash reference validation

Purpose: verify retrieval integrity.

Expected result:

- digest is computed from the encrypted payload
- digest is stored in the reference record
- retrieval returns the encrypted payload and digest reference
- recomputed digest matches the stored digest

### 6. Authorization failure handling

Purpose: verify failure paths are clear.

Expected result:

- denied OAuth authorization creates no stored record
- missing scopes skip only the affected resources
- invalid token stops ingestion before storage
- digest mismatch returns `isValid: false`

## Prototype Commands

Validate the Epic sandbox OAuth configuration:

```sh
python3 epic_oauth_config.py --config epic_sandbox.config.yml
```

Validate an Epic sandbox-style bundle through ingestion, storage, and digest verification:

```sh
python3 epic_sandbox_validate.py --bundle fixtures/epic/patient_bundle.json --patient-id patient-001 --record-id epic-sandbox-record-001 --storage-root storage/ehr_records --report-output /tmp/epic-sandbox-validation-report.json
```

Use the Epic fixture adapter to validate the local FHIR-to-Synapse mapping:

```sh
python3 epic_fhir_ingest.py --patient-id patient-001 --fixture fixtures/epic/patient_bundle.json --output /tmp/normalized_ehr.json
```

Then run the fixture, adapter, and downstream Synapse tests:

```sh
python3 -m unittest tests/test_epic_fixtures.py
python3 -m unittest tests/test_epic_fhir_ingest.py
python3 -m unittest tests/test_ehr_storage.py
python3 -m unittest tests/test_epic_sandbox_validate.py
python3 -m unittest tests/test_epic_oauth_config.py
python3 -m unittest tests/test_synapse_flow.py
python3 -m unittest tests/test_hippius_cli.py
```

The local fixture bundle for this prototype lives at:

```sh
fixtures/epic/patient_bundle.json
```

## Acceptance Criteria

The Epic ingestion prototype is acceptable when:

- a sandbox or fixture patient can be converted into the current EHR payload shape
- generated metadata can populate `RecordMetadataSynapse`
- the normalized payload can be represented as encrypted off-chain data
- a SHA-256 digest can verify the retrieved payload
- failure cases are documented and do not produce partial chain references

## Follow-Up Implementation Tasks

- Create FHIR fixture files for the minimum resource set.
- Add an `epic_fhir_ingest.py` adapter.
- Add an `epic_sandbox_validate.py` validation command for sandbox-style bundles.
- Add an `epic_oauth_config.py` loader for live sandbox OAuth settings.
- Add tests for FHIR-to-Synapse mapping.
- Add a local storage placeholder for encrypted payloads.
- Add digest generation and verification tests.

## Sources

- Epic on FHIR sandbox and documentation: https://fhir.epic.com/
- Epic on FHIR documentation: https://fhir.epic.com/Documentation
- open.epic FHIR interface overview: https://open.epic.com/interface/FHIR
