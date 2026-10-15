import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "fixtures" / "epic" / "patient_bundle.json"


def load_module(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


epic_fhir_ingest = load_module("epic_fhir_ingest", ROOT / "epic_fhir_ingest.py")


class EpicFhirIngestTests(unittest.TestCase):
    def test_fixture_bundle_normalizes_to_synapse_payload_shape(self):
        payload = epic_fhir_ingest.normalize_fixture_file(FIXTURE_PATH)

        self.assertEqual(payload["patientId"], "patient-001")
        self.assertEqual(payload["allergies"][0]["substance"], "Penicillin")
        self.assertEqual(payload["allergies"][0]["reaction"], "Rash")
        self.assertEqual(payload["allergies"][0]["severity"], "moderate")
        self.assertEqual(payload["immunizations"][0]["name"], "Influenza")
        self.assertEqual(
            payload["immunizations"][0]["provider"],
            "Campus Health Clinic",
        )
        self.assertIn("Hypertension", payload["problems"])

    def test_fixture_bundle_maps_extended_ehr_resources(self):
        payload = epic_fhir_ingest.normalize_fixture_file(FIXTURE_PATH)

        self.assertEqual(payload["medications"][0]["name"], "Lisinopril")
        self.assertEqual(payload["medications"][0]["status"], "active")
        self.assertEqual(payload["encounters"][0]["encounterId"], "epic-encounter-001")
        self.assertEqual(payload["encounters"][0]["classCode"], "AMB")
        self.assertEqual(
            payload["documents"][0]["contentReference"],
            "Binary/epic-document-binary-001",
        )

    def test_fixture_bundle_maps_observations_and_lab_results(self):
        payload = epic_fhir_ingest.normalize_fixture_file(FIXTURE_PATH)

        observations_by_name = {
            observation["name"]: observation for observation in payload["observations"]
        }

        self.assertEqual(observations_by_name["Blood pressure"]["value"], "128/82")
        self.assertEqual(observations_by_name["Blood pressure"]["unit"], "mmHg")
        self.assertEqual(observations_by_name["CBC"]["value"], "Within normal range")
        self.assertEqual(payload["labResults"][0]["testName"], "CBC")
        self.assertEqual(payload["labResults"][0]["summary"], "Within normal range")
        self.assertEqual(
            payload["labResults"][0]["resultReferences"],
            ["Observation/epic-observation-cbc-001"],
        )

    def test_cli_writes_normalized_payload_to_output_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = pathlib.Path(temp_dir) / "normalized_ehr.json"
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "epic_fhir_ingest.py"),
                    "--fixture",
                    str(FIXTURE_PATH),
                    "--patient-id",
                    "patient-001",
                    "--output",
                    str(output_path),
                ],
                check=True,
            )

            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(payload["patientId"], "patient-001")
        self.assertEqual(
            payload["documents"][0]["documentId"],
            "epic-document-reference-001",
        )


if __name__ == "__main__":
    unittest.main()
