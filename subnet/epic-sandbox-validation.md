# Epic Sandbox Validation

This document describes how Main-217 validates Epic sandbox FHIR data through the local ingestion, storage, and digest workflow.

## What We Need From Epic

Live sandbox validation requires:

- Epic on FHIR registered application
- non-production client ID
- redirect URI configured on the app
- sandbox test patient or launch context
- OAuth2 access token with the needed FHIR resource scopes

The local code does not store the OAuth token or client secret. Export or save a sandbox FHIR `Bundle` response, then run it through the validation command below.

Use `epic_oauth_config.py` and `epic_sandbox.config.yml` to load and validate the OAuth settings before starting a live sandbox flow.

## Sandbox Validation Command

```sh
python3 epic_sandbox_validate.py --bundle fixtures/epic/patient_bundle.json --patient-id patient-001 --record-id epic-sandbox-record-001 --storage-root storage/ehr_records --report-output /tmp/epic-sandbox-validation-report.json
```

For live sandbox data, replace `fixtures/epic/patient_bundle.json` with the saved Epic sandbox FHIR bundle path.

## What The Validation Proves

The command proves that an Epic-style FHIR bundle can:

1. normalize into the current project EHR/Synapse payload shape
2. store locally as the off-chain record prototype
3. generate a compact reference object
4. verify the stored payload against the generated SHA-256 digest

## Expected Report Shape

```json
{
  "digestVerified": true,
  "reference": {
    "digest": "sha256:...",
    "recordId": "epic-sandbox-record-001",
    "sourceSystem": "epic-sandbox-fhir",
    "storageUri": "local://ehr-records/epic-sandbox-record-001.json"
  },
  "resourceCounts": {
    "allergies": 1,
    "documents": 1,
    "encounters": 1,
    "immunizations": 1,
    "labResults": 1,
    "medications": 1,
    "observations": 2,
    "problems": 1
  },
  "status": "passed"
}
```

## Live OAuth Fetch Layer

`epic_sandbox_live.py` now provides the fetch layer in front of this command. See [the live integration runbook](epic-live-integration.md) for setup and evidence. Registered-client live validation still requires the non-production app configuration:

```text
Epic OAuth2 token -> FHIR resource requests -> Bundle JSON -> epic_sandbox_validate.py
```

The ingestion, local storage, and digest verification layers should not need to change.
