# Epic FHIR Fixtures

These fixtures are synthetic Epic-style FHIR resources for local prototype ingestion. They are not real patient records and should not be treated as production Epic exports.

## Purpose

The fixtures let the ingestion adapter be built and tested before the project connects to Epic sandbox endpoints. They cover the minimum resource set needed to map an Epic-derived patient record into the current Synapse/EHR payload shape.

## Files

| File | FHIR resource | Intended Synapse field |
| --- | --- | --- |
| `patient.json` | `Patient` | `patientId` and patient metadata |
| `allergy_intolerance.json` | `AllergyIntolerance` | `allergies` |
| `immunization.json` | `Immunization` | `immunizations` |
| `condition.json` | `Condition` | `problems` |
| `observation_blood_pressure.json` | `Observation` | `observations` |
| `observation_cbc.json` | `Observation` | `labResults` and `observations` |
| `diagnostic_report.json` | `DiagnosticReport` | `labResults` |
| `medication_request.json` | `MedicationRequest` | `medications` |
| `encounter.json` | `Encounter` | `encounters` |
| `document_reference.json` | `DocumentReference` | `documents` and metadata catalog |
| `patient_bundle.json` | `Bundle` | combined ingestion input |

## Synthetic Identity

- Internal project patient ID: `patient-001`
- Synthetic FHIR patient resource ID: `epic-patient-001`
- Primary encounter ID: `epic-encounter-001`
- Prototype record ID target: `epic-record-001`

The adapter should map Epic/FHIR identifiers into project-owned identifiers before any on-chain reference is created.
