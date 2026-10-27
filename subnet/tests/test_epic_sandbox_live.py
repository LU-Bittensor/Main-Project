import hashlib
import http.client
import io
import json
import pathlib
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
from contextlib import redirect_stdout
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import epic_oauth_config
import epic_sandbox_live as live


def config():
    result = epic_oauth_config.resolve_epic_oauth_config(ROOT / "epic_sandbox.config.example.yml", require_real_client_id=False)
    result["client_id"] = "offline-test-client"
    return result


def token():
    return {"access_token": "offline-secret-token", "patient": "patient-001", "expires_at": time.monotonic() + 300}


def observation(identifier="obs-1", patient="patient-001"):
    return {"resourceType": "Observation", "id": identifier,
            "subject": {"reference": "Patient/" + patient}, "status": "final",
            "code": {"text": "Heart rate"}, "valueQuantity": {"value": 72, "unit": "/min"}}


def page(*resources, next_url=None):
    value = {"resourceType": "Bundle", "type": "searchset", "entry": [{"resource": r} for r in resources]}
    if next_url is not None:
        value["link"] = [{"relation": "next", "url": next_url}]
    return value


PATIENT = {"resourceType": "Patient", "id": "patient-001"}


class OAuthTests(unittest.TestCase):
    def test_real_loopback_callback_returns_code_without_logging_it(self):
        original_server = live.HTTPServer
        servers, workers, responses = [], [], []

        def server_factory(address, handler):
            server = original_server(("127.0.0.1", 0), handler)
            servers.append(server)
            return server

        def open_browser(url):
            def request_callback():
                connection = http.client.HTTPConnection("127.0.0.1", servers[0].server_port, timeout=3)
                try:
                    connection.request("GET", "/callback?state=expected&code=private-code")
                    response = connection.getresponse()
                    responses.append((response.status, response.getheader("Cache-Control"), response.read()))
                finally:
                    connection.close()
            worker = threading.Thread(target=request_callback)
            workers.append(worker)
            worker.start()

        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(live, "HTTPServer", side_effect=server_factory), \
                mock.patch.object(live.webbrowser, "open", side_effect=open_browser), \
                redirect_stdout(stdout), mock.patch("sys.stderr", stderr):
            code = live.receive_authorization_code(config(), "https://example.org/authorize", "expected", timeout=3)
            for worker in workers:
                worker.join(timeout=4)
        self.assertEqual(code, "private-code")
        self.assertEqual(responses[0][:2], (200, "no-store"))
        self.assertNotIn("private-code", stdout.getvalue() + stderr.getvalue())

    def test_pkce_challenge_uses_verifier_and_random_state(self):
        url, state, verifier = live.create_authorization_request(config())
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        import base64
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(query["code_challenge"], [expected])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["state"], [state])
        self.assertNotEqual(state, live.create_authorization_request(config())[1])
        self.assertNotIn(verifier, url)

    def test_callback_requires_exact_state_and_unique_parameters(self):
        self.assertEqual(live.parse_callback("state=expected&code=ok", "expected"), "ok")
        for query in ("state=wrong&code=ok", "code=ok", "state=expected&code=a&code=b",
                      "state=expected&state=expected&code=a", "state=expected", "state=expected&error=access_denied"):
            with self.subTest(query=query), self.assertRaises(live.EpicLiveError):
                live.parse_callback(query, "expected")

    def test_token_exchange_posts_code_verifier_and_exact_redirect(self):
        response = {"access_token": "secret", "token_type": "Bearer", "expires_in": 3600,
                    "patient": "patient-001", "refresh_token": "must-not-retain", "id_token": "must-not-use"}
        transport = mock.Mock(return_value=response)
        result = live.exchange_code(config(), "auth-code", "verifier", transport=transport)
        form = transport.call_args.kwargs["form"]
        self.assertEqual(form["grant_type"], "authorization_code")
        self.assertEqual(form["code_verifier"], "verifier")
        self.assertEqual(form["redirect_uri"], config()["redirect_uri"])
        self.assertEqual(form["client_id"], config()["client_id"])
        self.assertNotIn("refresh_token", result)
        self.assertNotIn("id_token", result)

    def test_bad_tokens_fail_without_including_response_values(self):
        valid = {"access_token": "secret", "token_type": "Bearer", "expires_in": 3600, "patient": "patient-001"}
        for override in ({"access_token": ""}, {"token_type": "Other"}, {"expires_in": 0},
                         {"expires_in": "invalid"}, {"patient": None}, {"patient": "../other"}):
            with self.subTest(override=override), self.assertRaises(live.EpicLiveError) as caught:
                live.exchange_code(config(), "code", "verifier", transport=mock.Mock(return_value={**valid, **override}))
            self.assertNotIn("secret", str(caught.exception))

    def test_unconstrained_patient_id_is_accepted_without_allowing_path_segments(self):
        patient_id = "e" + "Abc123.-" * 30
        response = {"access_token": "secret", "token_type": "Bearer", "expires_in": 3600, "patient": patient_id}
        result = live.exchange_code(config(), "code", "verifier", transport=mock.Mock(return_value=response))
        self.assertEqual(result["patient"], patient_id)
        for invalid in (".", "..", "../other", "id?query=1", "a/b", "id%2Fother", "with space"):
            with self.subTest(invalid=invalid), self.assertRaises(live.EpicLiveError):
                live.exchange_code(config(), "code", "verifier", transport=mock.Mock(return_value={**response, "patient": invalid}))

    def test_live_harness_rejects_other_servers_and_non_loopback_callback(self):
        live.validate_live_config(config())
        for override in ({"token_url": "https://example.org/token"},
                         {"fhir_base_url": "https://example.org/fhir"},
                         {"redirect_uri": "http://0.0.0.0:8080/callback"},
                         {"redirect_uri": "http://localhost:8080/callback?x=1"}):
            with self.subTest(override=override), self.assertRaises(live.EpicLiveError):
                live.validate_live_config({**config(), **override})


class FetchTests(unittest.TestCase):
    def test_patient_and_paged_observations_use_bearer_and_deduplicate(self):
        transport = mock.Mock(side_effect=[PATIENT, page(observation(), next_url="?page=2"),
                                            page(observation(), observation("obs-2"))])
        bundle, report = live.fetch_patient_bundle(config(), token(), transport=transport)
        self.assertEqual(len(bundle["entry"]), 3)
        self.assertEqual(report["rawResourceCounts"], {"Patient": 1, "Observation": 2})
        self.assertEqual(report["queries"][0]["pages"], 2)
        first_search = urllib.parse.parse_qs(urllib.parse.urlsplit(transport.call_args_list[1].args[0]).query)
        self.assertEqual(first_search["patient"], ["patient-001"])
        self.assertEqual(first_search["category"], ["vital-signs"])
        for call in transport.call_args_list:
            self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer offline-secret-token")
            self.assertEqual(call.kwargs["headers"]["Epic-Client-ID"], config()["client_id"])
        self.assertTrue(transport.call_args_list[2].args[0].endswith("/Observation?page=2"))

    def test_empty_search_is_valid_and_recorded(self):
        transport = mock.Mock(side_effect=[PATIENT, {"resourceType": "Bundle", "type": "searchset", "total": 0}])
        bundle, report = live.fetch_patient_bundle(config(), token(), transport=transport)
        self.assertEqual(len(bundle["entry"]), 1)
        self.assertEqual(report["queries"][0]["returnedEntries"], 0)

    def test_multiple_categories_are_requested_once(self):
        transport = mock.Mock(side_effect=[PATIENT, page(observation()), page(observation("lab-1"))])
        bundle, report = live.fetch_patient_bundle(config(), token(), ["vital-signs", "laboratory", "laboratory"], transport=transport)
        self.assertEqual(len(report["queries"]), 2)
        self.assertEqual(report["rawResourceCounts"]["Observation"], 2)

    def test_cross_origin_and_escaping_next_links_never_receive_token(self):
        base = config()["fhir_base_url"]
        for url in ("https://example.org/steal", "http://fhir.epic.com/steal", "//example.org/steal",
                    base + "/../steal", base + "/%2e%2e/steal", base + "/%252e%252e/steal",
                    base + "other/page", base + "/Observation#fragment"):
            transport = mock.Mock(side_effect=[PATIENT, page(observation(), next_url=url)])
            with self.subTest(url=url), self.assertRaises(live.EpicLiveError):
                live.fetch_patient_bundle(config(), token(), transport=transport)
            self.assertEqual(transport.call_count, 2)

    def test_page_limit_and_cycles_fail_as_incomplete(self):
        transport = mock.Mock(side_effect=[PATIENT, page(next_url="?page=2"), page(next_url="?page=2")])
        with self.assertRaisesRegex(live.EpicLiveError, "incomplete"):
            live.fetch_patient_bundle(config(), token(), transport=transport)
        transport = mock.Mock(side_effect=[PATIENT, page(next_url="?page=2")])
        with self.assertRaisesRegex(live.EpicLiveError, "page limit"):
            live.fetch_patient_bundle(config(), token(), transport=transport, max_pages=1)

    def test_expired_token_prevents_network_access(self):
        transport = mock.Mock()
        with self.assertRaisesRegex(live.EpicLiveError, "expired"):
            live.fetch_patient_bundle(config(), {**token(), "expires_at": 0}, transport=transport)
        transport.assert_not_called()

    def test_patient_mismatch_and_malformed_search_are_rejected(self):
        cases = [({"resourceType": "Patient", "id": "other"},),
                 (PATIENT, page(observation(patient="other"))),
                 (PATIENT, {"resourceType": "OperationOutcome"}),
                 (PATIENT, {"resourceType": "Bundle", "type": "collection"}),
                 (PATIENT, {"resourceType": "Bundle", "type": "searchset", "entry": None})]
        for replies in cases:
            with self.subTest(replies=replies), self.assertRaises(live.EpicLiveError):
                live.fetch_patient_bundle(config(), token(), transport=mock.Mock(side_effect=replies))

    def test_outcome_errors_abort_and_warnings_are_reported_without_diagnostics(self):
        for severity in ("fatal", "error"):
            outcome = {"resourceType": "OperationOutcome", "issue": [{"severity": severity, "diagnostics": "private text"}]}
            with self.assertRaises(live.EpicLiveError):
                live.fetch_patient_bundle(config(), token(), transport=mock.Mock(side_effect=[PATIENT, page(outcome)]))
        outcome["issue"][0]["severity"] = "warning"
        _, report = live.fetch_patient_bundle(config(), token(), transport=mock.Mock(side_effect=[PATIENT, page(outcome)]))
        self.assertEqual(report["warnings"], [{"resourceType": "Observation", "category": "vital-signs", "severity": "warning"}])
        self.assertNotIn("private text", json.dumps(report))


class TransportTests(unittest.TestCase):
    def test_form_encoding_and_redirect_handler(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"ok": true}'
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch.object(live.urllib.request, "build_opener", return_value=opener) as build:
            self.assertEqual(live.request_json("https://example.org/token", form={"code": "a+b&c"}), {"ok": True})
        self.assertIs(build.call_args.args[0], live.NoRedirect)
        self.assertIsInstance(build.call_args.args[1], live.urllib.request.HTTPSHandler)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(urllib.parse.parse_qs(request.data.decode()), {"code": ["a+b&c"]})
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 30)
        self.assertIsNone(live.NoRedirect().redirect_request(None, None, 302, None, None, "https://example.org"))

    def test_http_errors_do_not_expose_body_or_request_url(self):
        for status in (400, 401, 403, 429, 500, 302):
            opener = mock.Mock()
            opener.open.side_effect = urllib.error.HTTPError("https://example.org/?secret", status, "secret", {}, io.BytesIO(b"private"))
            with mock.patch.object(live.urllib.request, "build_opener", return_value=opener):
                with self.subTest(status=status), self.assertRaises(live.EpicLiveError) as caught:
                    live.request_json("https://example.org")
            self.assertIn(str(status), str(caught.exception))
            self.assertNotIn("secret", str(caught.exception))
            self.assertNotIn("private", str(caught.exception))

    def test_invalid_json_or_top_level_outcome_fails(self):
        for raw in (b"not json", b"[]", b'{"resourceType":"OperationOutcome"}'):
            response = mock.MagicMock()
            response.__enter__.return_value.read.return_value = raw
            opener = mock.Mock()
            opener.open.return_value = response
            with mock.patch.object(live.urllib.request, "build_opener", return_value=opener):
                with self.subTest(raw=raw), self.assertRaises(live.EpicLiveError):
                    live.request_json("https://example.org")


class PipelineTests(unittest.TestCase):
    def test_warning_details_are_retained_separately_without_marking_run_clean(self):
        outcome = {"resourceType": "OperationOutcome", "issue": [
            {"severity": "warning", "code": "incomplete", "diagnostics": "Review sandbox search coverage"}]}
        with tempfile.TemporaryDirectory() as directory:
            run_dir = pathlib.Path(directory)
            transport = mock.Mock(side_effect=[PATIENT, page(observation(), outcome)])
            result = live.run_pipeline(config(), token(), run_dir, "test-run", ["vital-signs"], transport=transport)
            self.assertEqual(result["status"], "passed_with_warnings")
            self.assertNotIn("Review sandbox search coverage", json.dumps(result))
            path = run_dir / "search-outcomes.json"
            saved = json.loads(path.read_text())
            self.assertEqual(saved[0]["outcome"], outcome)
            self.assertEqual(saved[0]["resourceType"], "Observation")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertNotIn("offline-secret-token", path.read_text())

    def test_outcome_details_survive_a_later_request_failure(self):
        outcome = {"resourceType": "OperationOutcome", "issue": [
            {"severity": "warning", "code": "incomplete"}]}
        with tempfile.TemporaryDirectory() as directory:
            run_dir = pathlib.Path(directory)
            transport = mock.Mock(side_effect=[PATIENT, page(outcome), live.EpicLiveError("Epic HTTP 403.")])
            with self.assertRaises(live.EpicLiveError):
                live.run_pipeline(config(), token(), run_dir, "test-run", ["vital-signs", "laboratory"], transport=transport)
            self.assertTrue((run_dir / "search-outcomes.json").is_file())
            self.assertFalse((run_dir / "report.json").exists())
            self.assertFalse((run_dir / "bundle.json").exists())

    def test_mock_api_to_normalization_storage_and_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            run_dir = pathlib.Path(directory)
            transport = mock.Mock(side_effect=[PATIENT, page(observation())])
            result = live.run_pipeline(config(), token(), run_dir, "test-run", ["vital-signs"], transport=transport)
            self.assertTrue(result["pipeline"]["digestVerified"])
            self.assertEqual(result["pipeline"]["resourceCounts"]["observations"], 1)
            normalized = json.loads((run_dir / "records/test-run.json").read_text())
            self.assertEqual(normalized["patientId"], "patient-001")
            self.assertEqual(normalized["observations"][0]["value"], "72")
            for path in run_dir.rglob("*.json"):
                self.assertNotIn("offline-secret-token", path.read_text())
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_failed_fetch_does_not_create_success_or_bundle_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            run_dir = pathlib.Path(directory)
            transport = mock.Mock(side_effect=[PATIENT, live.EpicLiveError("Epic HTTP 403.")])
            with self.assertRaises(live.EpicLiveError):
                live.run_pipeline(config(), token(), run_dir, "test-run", ["vital-signs"], transport=transport)
            self.assertEqual(list(run_dir.iterdir()), [])

    def test_unsafe_record_id_rejected_before_request(self):
        transport = mock.Mock()
        with self.assertRaises(live.EpicLiveError):
            live.run_pipeline(config(), token(), pathlib.Path("unused"), "../escape", ["vital-signs"], transport=transport)
        transport.assert_not_called()

    def test_placeholder_config_cannot_be_mistaken_for_live_success(self):
        with redirect_stdout(io.StringIO()), mock.patch.object(live, "receive_authorization_code") as receive:
            self.assertEqual(live.main(["--config", str(ROOT / "epic_sandbox.config.example.yml")]), 1)
        receive.assert_not_called()


if __name__ == "__main__":
    unittest.main()
