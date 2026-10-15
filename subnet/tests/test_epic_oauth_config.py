import importlib.util
import io
import json
import pathlib
import sys
import tempfile
import unittest
import urllib.parse
from contextlib import redirect_stdout
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
EXAMPLE_CONFIG_PATH = ROOT / "epic_sandbox.config.example.yml"


def load_module(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


epic_oauth_config = load_module("epic_oauth_config", ROOT / "epic_oauth_config.py")


class EpicOAuthConfigTests(unittest.TestCase):
    def test_example_config_loads_with_placeholder_allowed(self):
        config = epic_oauth_config.resolve_epic_oauth_config(
            EXAMPLE_CONFIG_PATH,
            require_real_client_id=False,
        )

        self.assertEqual(
            config["authorization_url"],
            "https://fhir.epic.com/interconnect-fhir-oauth/oauth2/authorize",
        )
        self.assertIn("patient/Observation.read", config["scopes"])
        self.assertEqual(config["redirect_uri"], "http://localhost:8080/callback")

    def test_missing_client_id_fails_validation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = pathlib.Path(temp_dir) / "epic.yml"
            config_path.write_text(
                "\n".join(
                    [
                        "redirect_uri: http://localhost:8080/callback",
                        "authorization_url: https://fhir.epic.com/interconnect-fhir-oauth/oauth2/authorize",
                        "token_url: https://fhir.epic.com/interconnect-fhir-oauth/oauth2/token",
                        "fhir_base_url: https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4",
                        "scopes: launch/patient, patient/Patient.read",
                    ]
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                epic_oauth_config.EpicOAuthConfigError,
                "client_id",
            ):
                epic_oauth_config.resolve_epic_oauth_config(config_path)

    def test_placeholder_client_id_fails_by_default(self):
        with self.assertRaisesRegex(
            epic_oauth_config.EpicOAuthConfigError,
            "real non-production client_id",
        ):
            epic_oauth_config.resolve_epic_oauth_config(EXAMPLE_CONFIG_PATH)

    def test_environment_overrides_file_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = pathlib.Path(temp_dir) / "epic.yml"
            config_path.write_text(
                "\n".join(
                    [
                        "client_id: file-client-id",
                        "redirect_uri: http://localhost:8080/callback",
                    ]
                ),
                encoding="utf-8",
            )

            with mock.patch.dict(
                "os.environ",
                {"EPIC_SANDBOX_CLIENT_ID": "env-client-id"},
                clear=False,
            ):
                config = epic_oauth_config.resolve_epic_oauth_config(config_path)

        self.assertEqual(config["client_id"], "env-client-id")

    def test_build_authorization_url_contains_oauth_parameters(self):
        config = epic_oauth_config.resolve_epic_oauth_config(
            EXAMPLE_CONFIG_PATH,
            require_real_client_id=False,
        )

        authorization_url = epic_oauth_config.build_authorization_url(
            config,
            state="state-001",
        )
        parsed_url = urllib.parse.urlparse(authorization_url)
        query = urllib.parse.parse_qs(parsed_url.query)

        self.assertEqual(parsed_url.scheme, "https")
        self.assertEqual(query["response_type"], ["code"])
        self.assertEqual(query["client_id"], ["your-epic-non-production-client-id"])
        self.assertEqual(query["state"], ["state-001"])
        self.assertEqual(query["aud"], [config["fhir_base_url"]])
        self.assertIn("patient/DocumentReference.read", query["scope"][0])

    def test_cli_prints_public_summary_without_client_secret(self):
        argv_backup = sys.argv[:]
        stdout = io.StringIO()
        try:
            sys.argv = [
                "epic_oauth_config.py",
                "--config",
                str(EXAMPLE_CONFIG_PATH),
                "--allow-placeholder-client-id",
            ]
            with redirect_stdout(stdout):
                exit_code = epic_oauth_config.main()
        finally:
            sys.argv = argv_backup

        summary = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertFalse(summary["clientIdConfigured"])
        self.assertEqual(summary["scopeCount"], 10)
        self.assertNotIn("client_id", summary)


if __name__ == "__main__":
    unittest.main()
