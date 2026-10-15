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
epic_sandbox_validate = load_module(
    "epic_sandbox_validate",
    ROOT / "epic_sandbox_validate.py",
)


class EpicSandboxValidateTests(unittest.TestCase):
    def test_validates_sandbox_style_bundle_through_full_pipeline(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            report = epic_sandbox_validate.validate_epic_bundle_pipeline(
                bundle_path=FIXTURE_PATH,
                patient_id="patient-001",
                record_id="epic-sandbox-record-001",
                storage_root=temp_dir,
            )

            record_path = pathlib.Path(temp_dir) / "epic-sandbox-record-001.json"
            self.assertTrue(record_path.exists())

        self.assertEqual(report["status"], "passed")
        self.assertTrue(report["digestVerified"])
        self.assertEqual(report["reference"]["sourceSystem"], "epic-sandbox-fhir")
        self.assertEqual(report["reference"]["recordId"], "epic-sandbox-record-001")
        self.assertEqual(report["resourceCounts"]["observations"], 2)

    def test_cli_writes_validation_report(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = pathlib.Path(temp_dir) / "sandbox-report.json"
            storage_root = pathlib.Path(temp_dir) / "storage"

            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "epic_sandbox_validate.py"),
                    "--bundle",
                    str(FIXTURE_PATH),
                    "--patient-id",
                    "patient-001",
                    "--record-id",
                    "epic-sandbox-record-001",
                    "--storage-root",
                    str(storage_root),
                    "--report-output",
                    str(report_path),
                ],
                check=True,
            )

            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(report["status"], "passed")
        self.assertTrue(report["digestVerified"])
        self.assertTrue(report["reference"]["digest"].startswith("sha256:"))


if __name__ == "__main__":
    unittest.main()
