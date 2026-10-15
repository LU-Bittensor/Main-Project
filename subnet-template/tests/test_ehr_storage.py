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
ehr_storage = load_module("ehr_storage", ROOT / "ehr_storage.py")


class EhrStorageTests(unittest.TestCase):
    def test_store_ehr_record_writes_payload_and_reference(self):
        payload = epic_fhir_ingest.normalize_fixture_file(FIXTURE_PATH)

        with tempfile.TemporaryDirectory() as temp_dir:
            reference = ehr_storage.store_ehr_record(
                payload,
                record_id="epic-record-001",
                storage_root=temp_dir,
                created_at="2026-09-29T00:00:00+00:00",
            )

            record_path = pathlib.Path(temp_dir) / "epic-record-001.json"
            stored_payload = json.loads(record_path.read_text(encoding="utf-8"))

        self.assertEqual(stored_payload["patientId"], "patient-001")
        self.assertEqual(reference["recordId"], "epic-record-001")
        self.assertEqual(reference["storageUri"], "local://ehr-records/epic-record-001.json")
        self.assertTrue(reference["digest"].startswith("sha256:"))
        self.assertEqual(reference["sourceSystem"], "epic-fhir")

    def test_digest_is_stable_for_equivalent_payloads(self):
        payload = {
            "patientId": "patient-001",
            "problems": ["Hypertension"],
            "allergies": [{"substance": "Penicillin"}],
        }
        reordered_payload = {
            "allergies": [{"substance": "Penicillin"}],
            "problems": ["Hypertension"],
            "patientId": "patient-001",
        }

        first_digest = ehr_storage.compute_sha256_digest(
            ehr_storage.canonical_json_bytes(payload)
        )
        second_digest = ehr_storage.compute_sha256_digest(
            ehr_storage.canonical_json_bytes(reordered_payload)
        )

        self.assertEqual(first_digest, second_digest)

    def test_verify_record_digest_detects_tampering(self):
        payload = epic_fhir_ingest.normalize_fixture_file(FIXTURE_PATH)

        with tempfile.TemporaryDirectory() as temp_dir:
            reference = ehr_storage.store_ehr_record(
                payload,
                record_id="epic-record-001",
                storage_root=temp_dir,
            )

            self.assertTrue(ehr_storage.verify_record_digest(reference, temp_dir))

            record_path = pathlib.Path(temp_dir) / "epic-record-001.json"
            tampered_payload = json.loads(record_path.read_text(encoding="utf-8"))
            tampered_payload["problems"].append("Tampered value")
            record_path.write_text(json.dumps(tampered_payload), encoding="utf-8")

            self.assertFalse(ehr_storage.verify_record_digest(reference, temp_dir))

    def test_cli_stores_payload_and_writes_reference(self):
        payload = epic_fhir_ingest.normalize_fixture_file(FIXTURE_PATH)

        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = pathlib.Path(temp_dir) / "normalized_ehr.json"
            reference_path = pathlib.Path(temp_dir) / "reference.json"
            storage_root = pathlib.Path(temp_dir) / "storage"
            input_path.write_text(json.dumps(payload), encoding="utf-8")

            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "ehr_storage.py"),
                    "--input",
                    str(input_path),
                    "--record-id",
                    "epic-record-001",
                    "--storage-root",
                    str(storage_root),
                    "--reference-output",
                    str(reference_path),
                ],
                check=True,
            )

            reference = json.loads(reference_path.read_text(encoding="utf-8"))

        self.assertEqual(reference["recordId"], "epic-record-001")
        self.assertEqual(reference["patientId"], "patient-001")
        self.assertTrue(reference["digest"].startswith("sha256:"))


if __name__ == "__main__":
    unittest.main()
