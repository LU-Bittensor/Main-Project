import json
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "fixtures" / "epic"


class EpicFixtureTests(unittest.TestCase):
    def test_individual_fixtures_are_valid_fhir_resources(self):
        expected_resource_types = {
            "patient.json": "Patient",
            "allergy_intolerance.json": "AllergyIntolerance",
            "immunization.json": "Immunization",
            "condition.json": "Condition",
            "observation_blood_pressure.json": "Observation",
            "observation_cbc.json": "Observation",
            "diagnostic_report.json": "DiagnosticReport",
            "medication_request.json": "MedicationRequest",
            "encounter.json": "Encounter",
            "document_reference.json": "DocumentReference",
        }

        for file_name, expected_resource_type in expected_resource_types.items():
            with self.subTest(file_name=file_name):
                fixture_path = FIXTURE_ROOT / file_name
                resource = json.loads(fixture_path.read_text(encoding="utf-8"))

                self.assertEqual(resource["resourceType"], expected_resource_type)
                self.assertTrue(resource["id"])

    def test_patient_bundle_contains_minimum_ingestion_resource_set(self):
        bundle_path = FIXTURE_ROOT / "patient_bundle.json"
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))

        self.assertEqual(bundle["resourceType"], "Bundle")
        self.assertEqual(bundle["type"], "collection")

        resource_types = {
            entry["resource"]["resourceType"]
            for entry in bundle["entry"]
        }
        expected_resource_types = {
            "Patient",
            "AllergyIntolerance",
            "Immunization",
            "Condition",
            "Observation",
            "DiagnosticReport",
            "MedicationRequest",
            "Encounter",
            "DocumentReference",
        }

        self.assertTrue(expected_resource_types.issubset(resource_types))

    def test_fixture_patient_references_are_synthetic(self):
        bundle_path = FIXTURE_ROOT / "patient_bundle.json"
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))

        references = []
        for entry in bundle["entry"]:
            resource = entry["resource"]
            for field_name in ("patient", "subject"):
                if field_name in resource:
                    references.append(resource[field_name]["reference"])

        self.assertTrue(references)
        self.assertTrue(
            all(reference == "Patient/epic-patient-001" for reference in references)
        )


if __name__ == "__main__":
    unittest.main()
