import importlib.util
import io
import json
import pathlib
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_module(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


hippius_cli = load_module("hippius_cli_under_test", ROOT / "hippius_cli.py")


class HippiusCliTests(unittest.TestCase):
    def test_build_metadata_request_envelope(self):
        args = hippius_cli.build_parser().parse_args(
            [
                "metadata",
                "--patient-id",
                "patient-001",
                "--request-id",
                "request-001",
                "--record-type",
                "ehr",
                "--dry-run",
            ]
        )

        envelope = hippius_cli.build_request_envelope(args)

        self.assertEqual(envelope["synapseType"], "metadata")
        self.assertEqual(envelope["payload"]["patientId"], "patient-001")
        self.assertEqual(envelope["payload"]["recordType"], "ehr")

    def test_build_mock_retrieval_response(self):
        request_envelope = {
            "synapseType": "retrieve",
            "payload": {
                "requesterId": "requester-001",
                "patientId": "patient-001",
                "recordId": "record-001",
                "chunkRangeStart": 0,
                "chunkRangeEnd": 2,
            },
        }

        response = hippius_cli.build_mock_response(request_envelope)

        self.assertEqual(response["status"], "success")
        self.assertEqual(response["synapseType"], "retrieve")
        self.assertEqual(
            response["payload"]["encryptedPayload"],
            "mock-encrypted-payload:record-001:chunks-0-2",
        )

    def test_parse_response_envelope_validates_required_fields(self):
        valid_response = json.dumps(
            {
                "status": "success",
                "synapseType": "integrity",
                "payload": {"isValid": True},
                "error": None,
            }
        )

        envelope = hippius_cli.parse_response_envelope(valid_response)

        self.assertEqual(envelope["synapseType"], "integrity")
        self.assertTrue(envelope["payload"]["isValid"])

    def test_parse_response_command_renders_text_output(self):
        response_path = ROOT / "tests" / "_tmp_hippius_response.json"
        response_path.write_text(
            json.dumps(
                {
                    "status": "success",
                    "synapseType": "retrieve",
                    "payload": {
                        "encryptedPayload": "mock-encrypted-payload:record-001:chunks-0-2",
                        "digestReference": "digest::record-001",
                    },
                    "error": None,
                }
            ),
            encoding="utf-8",
        )

        argv_backup = sys.argv[:]
        stdout = io.StringIO()
        try:
            sys.argv = [
                "hippius_cli.py",
                "parse-response",
                "--response-file",
                str(response_path),
            ]
            with redirect_stdout(stdout):
                exit_code = hippius_cli.main()
        finally:
            sys.argv = argv_backup
            if response_path.exists():
                response_path.unlink()

        output = stdout.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("Status: success", output)
        self.assertIn("digestReference: digest::record-001", output)

    def test_access_command_mock_response_renders_json(self):
        argv_backup = sys.argv[:]
        stdout = io.StringIO()
        try:
            sys.argv = [
                "hippius_cli.py",
                "access",
                "--requester-id",
                "requester-001",
                "--patient-id",
                "patient-001",
                "--record-id",
                "record-001",
                "--purpose-of-use",
                "treatment",
                "--mock-response",
            ]
            with redirect_stdout(stdout):
                exit_code = hippius_cli.main()
        finally:
            sys.argv = argv_backup

        output = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(output["status"], "success")
        self.assertEqual(output["payload"]["contractDecision"], "approved")

    def test_access_command_mock_response_renders_denied_decision(self):
        argv_backup = sys.argv[:]
        stdout = io.StringIO()
        try:
            sys.argv = [
                "hippius_cli.py",
                "access",
                "--requester-id",
                "requester-001",
                "--patient-id",
                "patient-001",
                "--record-id",
                "record-001",
                "--purpose-of-use",
                "marketing",
                "--mock-response",
            ]
            with redirect_stdout(stdout):
                exit_code = hippius_cli.main()
        finally:
            sys.argv = argv_backup

        output = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertFalse(output["payload"]["accessGranted"])
        self.assertEqual(output["payload"]["contractDecision"], "denied")
        self.assertNotIn("keyReference", output["payload"])

    def test_integrity_command_mock_response_renders_digest_mismatch(self):
        argv_backup = sys.argv[:]
        stdout = io.StringIO()
        try:
            sys.argv = [
                "hippius_cli.py",
                "integrity",
                "--record-id",
                "record-001",
                "--provided-digest",
                "digest-001",
                "--mock-response",
            ]
            with redirect_stdout(stdout):
                exit_code = hippius_cli.main()
        finally:
            sys.argv = argv_backup

        output = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(output["payload"]["blockchainDigest"], "digest::record-001")
        self.assertFalse(output["payload"]["isValid"])

    def test_resolve_hippius_config_combines_file_env_and_cli_settings(self):
        config_path = ROOT / "tests" / "_tmp_hippius_config.yml"
        config_path.write_text(
            "\n".join(
                [
                    "hippius:",
                    "  endpoint: http://file-endpoint.local/request",
                    "  timeout: 5",
                ]
            ),
            encoding="utf-8",
        )

        try:
            args = hippius_cli.build_parser().parse_args(
                [
                    "metadata",
                    "--patient-id",
                    "patient-001",
                    "--request-id",
                    "request-001",
                    "--config",
                    str(config_path),
                    "--endpoint",
                    "http://cli-endpoint.local/request",
                ]
            )
            with mock.patch.dict(
                "os.environ",
                {"HIPPIUS_CLI_TIMEOUT": "9"},
                clear=False,
            ):
                config = hippius_cli.resolve_hippius_config(args)
        finally:
            if config_path.exists():
                config_path.unlink()

        self.assertEqual(config["endpoint"], "http://cli-endpoint.local/request")
        self.assertEqual(config["timeout"], 9.0)

    def test_submit_hippius_request_posts_envelope_and_normalizes_response(self):
        request_envelope = {
            "synapseType": "integrity",
            "payload": {
                "recordId": "record-001",
                "providedDigest": "digest::record-001",
            },
        }

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return None

            def read(self):
                return json.dumps({"isValid": True}).encode("utf-8")

        with mock.patch("urllib.request.urlopen", return_value=FakeResponse()) as urlopen:
            response = hippius_cli.submit_hippius_request(
                request_envelope,
                {"endpoint": "http://hippius.local/synapse", "timeout": 3},
            )

        sent_request = urlopen.call_args.args[0]
        self.assertEqual(sent_request.full_url, "http://hippius.local/synapse")
        self.assertEqual(json.loads(sent_request.data.decode("utf-8")), request_envelope)
        self.assertEqual(response["status"], "success")
        self.assertEqual(response["synapseType"], "integrity")
        self.assertTrue(response["payload"]["isValid"])

    def test_submit_hippius_request_returns_structured_http_error(self):
        request_envelope = {
            "synapseType": "access",
            "payload": {
                "requesterId": "requester-001",
                "patientId": "patient-001",
                "recordId": "record-001",
                "purposeOfUse": "treatment",
            },
        }
        error_response = io.BytesIO(
            json.dumps(
                {
                    "status": "error",
                    "synapseType": "access",
                    "payload": {},
                    "error": {
                        "code": "ACCESS_DENIED",
                        "message": "Access request was denied.",
                    },
                }
            ).encode("utf-8")
        )
        http_error = hippius_cli.urllib.error.HTTPError(
            "http://hippius.local/synapse",
            403,
            "Forbidden",
            {},
            error_response,
        )

        with mock.patch("urllib.request.urlopen", side_effect=http_error):
            response = hippius_cli.submit_hippius_request(
                request_envelope,
                {"endpoint": "http://hippius.local/synapse", "timeout": 3},
            )

        self.assertEqual(response["status"], "error")
        self.assertEqual(response["synapseType"], "access")
        self.assertEqual(response["error"]["code"], "ACCESS_DENIED")

    def test_main_returns_error_when_live_endpoint_is_missing(self):
        argv_backup = sys.argv[:]
        stderr = io.StringIO()
        try:
            sys.argv = [
                "hippius_cli.py",
                "ehr",
                "--patient-id",
                "patient-001",
            ]
            with redirect_stdout(io.StringIO()), mock.patch("sys.stderr", stderr):
                exit_code = hippius_cli.main()
        finally:
            sys.argv = argv_backup

        self.assertEqual(exit_code, 1)
        self.assertIn("Missing Hippius endpoint", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
