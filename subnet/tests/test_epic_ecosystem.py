import io
import json
from pathlib import Path
import sys
import ssl
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import ehr_storage
import epic_fhir_ingest as ingest
import epic_fhir_reconcile as reconcile
import epic_oauth_config as oauth
import epic_sandbox_live as live
import epic_sandbox_report as reports
import epic_sandbox_setup as setup

CLIENT_ID = "11111111-2222-4333-8444-555555555555"


def resources(patient_id="test-patient"):
    return [
        {"resourceType": "Patient", "id": patient_id},
        {"resourceType": "Observation", "id": "o1", "subject": {"reference": "Patient/" + patient_id},
         "status": "final", "code": {"text": "Pulse"}, "valueQuantity": {"value": 70, "unit": "/min"}},
        {"resourceType": "Condition", "id": "c1", "subject": {"reference": "Patient/" + patient_id}, "code": {"text": "Test condition"}},
        {"resourceType": "MedicationRequest", "id": "m1", "subject": {"reference": "Patient/" + patient_id},
         "medicationReference": {"reference": "Medication/md1", "display": "Test medication"}, "status": "active"},
        {"resourceType": "Encounter", "id": "e1", "subject": {"reference": "Patient/" + patient_id}, "status": "finished", "class": {"code": "AMB"}},
    ]


def bundle(patient_id="test-patient"):
    return {"resourceType": "Bundle", "type": "collection", "entry": [{"resource": r} for r in resources(patient_id)]}


def transport(patient_id="test-patient"):
    values = resources(patient_id)
    return mock.Mock(side_effect=[values[0]] + [
        {"resourceType": "Bundle", "type": "searchset", "entry": [{"resource": r}]} for r in values[1:]
    ])


def token(patient_id="test-patient"):
    return {"access_token": "test-only-secret", "patient": patient_id, "expires_at": time.monotonic() + 300}


class SetupTests(unittest.TestCase):
    def test_tls_certificate_and_hostname_verification_remain_enabled(self):
        context = live.tls_context()
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)

    def test_init_is_idempotent_and_configuration_round_trips(self):
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()):
            path = Path(directory) / "epic.yml"
            self.assertEqual(setup.main(["--config", str(path), "init"]), 0)
            self.assertEqual(setup.check_setup(path, "expanded")["status"], "blocked")
            self.assertEqual(setup.main(["--config", str(path), "configure", "--client-id", CLIENT_ID,
                                         "--redirect-uri", "http://localhost:8080/callback"]), 0)
            before = path.read_bytes()
            self.assertEqual(setup.main(["--config", str(path), "init"]), 0)
            self.assertEqual(before, path.read_bytes())
            actual = oauth.resolve_epic_oauth_config(path)
            self.assertEqual(actual["client_id"], CLIENT_ID)
            self.assertEqual(len(actual["scopes"]), 6)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(setup.check_setup(path, "expanded")["status"], "ready_for_login")

    def test_invalid_config_does_not_overwrite_existing(self):
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()):
            path = Path(directory) / "epic.yml"
            setup.save_config(path, setup.new_config())
            original = path.read_bytes()
            result = setup.main(["--config", str(path), "configure", "--client-id", CLIENT_ID,
                                 "--redirect-uri", "https://example.org/callback"])
            self.assertEqual(result, 1)
            self.assertEqual(path.read_bytes(), original)

    def test_public_discovery_with_placeholder_does_not_imply_readiness(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "epic.yml"
            setup.save_config(path, setup.new_config())
            remote = mock.Mock(return_value={"authorization_endpoint": oauth.DEFAULT_AUTHORIZATION_URL,
                                              "token_endpoint": oauth.DEFAULT_TOKEN_URL,
                                              "code_challenge_methods_supported": ["S256"]})
            report = setup.check_setup(path, "expanded", online=True, transport=remote)
            self.assertEqual(report["status"], "blocked")
            self.assertFalse(report["liveOAuthValidated"])
            self.assertFalse(report["discoveryClientIdProvided"])
            self.assertEqual(remote.call_args.kwargs["headers"], {})

    def test_discovery_endpoint_mismatch_blocks_login_readiness(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "epic.yml"
            setup.save_config(path, setup.new_config(CLIENT_ID))
            report = setup.check_setup(path, "expanded", online=True, transport=mock.Mock(return_value={"token_endpoint": "https://example.org"}))
            self.assertEqual(report["status"], "blocked")
            self.assertNotIn(CLIENT_ID, json.dumps(report))

    def test_expanded_profile_requires_additional_scopes(self):
        config = setup.new_config(CLIENT_ID, profile="baseline")
        live.validate_profile_scopes(config, "baseline")
        with self.assertRaisesRegex(live.EpicLiveError, "Condition"):
            live.validate_profile_scopes(config, "expanded")

    def test_non_string_config_fields_are_rejected_cleanly(self):
        for value in (None, [], 12, False):
            with self.subTest(value=value), self.assertRaises(oauth.EpicOAuthConfigError):
                oauth.validate_epic_oauth_config({**setup.new_config(CLIENT_ID), "client_id": value})


class ExpandedFetchTests(unittest.TestCase):
    def test_fetches_all_five_resource_types_and_patient_scoped_searches(self):
        remote = transport()
        result, report = live.fetch_patient_bundle(setup.new_config(CLIENT_ID), token(), resources=live.PROFILES["expanded"], transport=remote)
        self.assertEqual(len(result["entry"]), 5)
        self.assertEqual(set(report["rawResourceCounts"]), {"Patient", *live.PROFILES["expanded"]})
        from urllib.parse import urlsplit, parse_qs
        for call in remote.call_args_list[1:]:
            query = parse_qs(urlsplit(call.args[0]).query)
            self.assertEqual(query["patient"], ["test-patient"])
        condition_url = remote.call_args_list[2].args[0]
        self.assertEqual(parse_qs(urlsplit(condition_url).query)["category"], ["problem-list-item"])

    def test_medication_for_another_patient_is_rejected(self):
        values = resources()
        values[3]["subject"]["reference"] = "Patient/another-patient"
        remote = mock.Mock(side_effect=[values[0], {"resourceType": "Bundle", "type": "searchset", "entry": [{"resource": values[3]}]}])
        with self.assertRaisesRegex(live.EpicLiveError, "patient context"):
            live.fetch_patient_bundle(setup.new_config(CLIENT_ID), token(), resources=["MedicationRequest"], transport=remote)

    def test_failure_in_an_added_resource_prevents_a_successful_run(self):
        remote = mock.Mock(side_effect=[resources()[0], live.EpicLiveError("Epic HTTP 403.")])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            with self.assertRaises(live.EpicLiveError):
                live.run_pipeline(setup.new_config(CLIENT_ID), token(), path, "run", ["vital-signs"],
                                  resources=["Condition"], transport=remote)
            self.assertEqual(list(path.iterdir()), [])


class MappingAuditTests(unittest.TestCase):
    def test_code_only_blood_pressure_maps_to_systolic_diastolic(self):
        raw = resources()[1]
        raw.pop("valueQuantity")
        raw["component"] = [
            {"code": {"coding": [{"system": "http://loinc.org", "code": code}]}, "valueQuantity": {"value": value, "unit": "mmHg"}}
            for code, value in (("8462-4", 80), ("8480-6", 120))]
        result = ingest.map_observation(raw)
        self.assertEqual(result["value"], "120/80")
        self.assertEqual(result["unit"], "mmHg")

    def test_top_level_observation_value_is_not_lost_to_other_components(self):
        raw = resources()[1]
        raw["component"] = [{"code": {"text": "Unrelated component"}, "valueString": "other"}]
        self.assertEqual(ingest.map_observation(raw)["value"], "70")

    def test_coded_boolean_and_integer_observation_values_are_preserved(self):
        for key, value, expected in (("valueBoolean", False, "false"), ("valueInteger", 0, "0"),
                                     ("valueCodeableConcept", {"coding": [{}, {"code": "negative"}]}, "negative")):
            raw = resources()[1]
            raw.pop("valueQuantity")
            raw[key] = value
            with self.subTest(key=key):
                self.assertEqual(ingest.map_observation(raw)["value"], expected)

    def test_medication_reference_display_is_used_as_name(self):
        self.assertEqual(ingest.map_medication_request(resources()[3])["name"], "Test medication")

    def test_audit_detects_dropped_resources_and_changed_values(self):
        raw = bundle()
        payload = ingest.normalize_bundle_to_ehr_payload(raw, "test-patient")
        self.assertEqual(reconcile.reconcile_bundle(raw, payload)["status"], "passed")
        payload["medications"] = []
        payload["observations"][0]["value"] = "900"
        report = reconcile.reconcile_bundle(raw, payload)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(len(report["mismatches"]), 2)
        self.assertNotIn("900", json.dumps(report))

    def test_audit_warns_about_unmapped_data(self):
        raw = bundle()
        raw["entry"][1]["resource"].pop("valueQuantity")
        raw["entry"][1]["resource"]["valueRange"] = {"low": {"value": 1}, "high": {"value": 3}}
        raw["entry"][3]["resource"]["medicationReference"].pop("display")
        report = reconcile.reconcile_bundle(raw, ingest.normalize_bundle_to_ehr_payload(raw, "test-patient"))
        self.assertEqual(report["status"], "passed_with_warnings")
        self.assertEqual(len(report["warnings"]), 2)


class SavedEvidenceTests(unittest.TestCase):
    def test_no_result_notice_requires_a_matching_empty_search(self):
        outcome = {"resourceType": "OperationOutcome", "issue": [{
            "severity": "warning", "code": "processing", "details": {
                "text": reports.EPIC_EMPTY_RESULT_TEXT,
                "coding": [{"system": reports.EPIC_COVERAGE_NOTICE_SYSTEM, "code": "4101"}]}}]}
        for populated in (False, True):
            with self.subTest(populated=populated), tempfile.TemporaryDirectory() as directory:
                run_dir = Path(directory)
                patient, observation = resources()[:2]
                entries = [{"resource": outcome}] + ([{"resource": observation}] if populated else [])
                remote = mock.Mock(side_effect=[patient, {"resourceType": "Bundle", "type": "searchset", "entry": entries}])
                live.run_pipeline(setup.new_config(CLIENT_ID), token(), run_dir, "run", ["vital-signs"], transport=remote)
                verified = reports.inspect_run(run_dir)
                self.assertEqual(verified["eligibleForLimitedSandboxAcceptance"], not populated)
                self.assertEqual(verified["apiWarningReview"]["emptyResultNoticeCount"], 0 if populated else 1)
                report = json.loads((run_dir / "report.json").read_text())
                report["queries"] = []
                self.assertEqual(reports.review_api_warnings(run_dir, report)["unresolvedCount"], 1)

    def test_reviewed_coverage_notice_counts_without_clearing_original_warning(self):
        outcome = {"resourceType": "OperationOutcome", "issue": [{
            "severity": "warning", "code": "processing", "details": {
                "text": reports.EPIC_COVERAGE_NOTICE_TEXT,
                "coding": [{"system": reports.EPIC_COVERAGE_NOTICE_SYSTEM, "code": "4119",
                            "display": reports.EPIC_COVERAGE_NOTICE_TEXT}]}}]}
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "notice-run"
            run_dir.mkdir()
            patient, observation = resources()[:2]
            remote = mock.Mock(side_effect=[patient, {"resourceType": "Bundle", "type": "searchset",
                "entry": [{"resource": observation}, {"resource": outcome}]}])
            live.run_pipeline(setup.new_config(CLIENT_ID), token(), run_dir, "run", ["vital-signs"], transport=remote)
            verified = reports.inspect_run(run_dir)
            self.assertEqual(verified["status"], "passed_with_warnings")
            self.assertTrue(verified["eligibleForLimitedSandboxAcceptance"])
            self.assertEqual(reports.summarize(directory)["verifiedLivePatientCount"], 1)
            self.assertEqual(json.loads((run_dir / "report.json").read_text())["status"], "passed_with_warnings")
            (run_dir / "search-outcomes.json").unlink()
            self.assertFalse(reports.inspect_run(run_dir)["eligibleForLimitedSandboxAcceptance"])

    def test_unrecognized_warning_details_and_mapping_warnings_stay_unresolved(self):
        from copy import deepcopy
        template = {"resourceType": "OperationOutcome", "issue": [{
            "severity": "warning", "code": "processing", "details": {
                "text": reports.EPIC_COVERAGE_NOTICE_TEXT,
                "coding": [{"system": reports.EPIC_COVERAGE_NOTICE_SYSTEM, "code": "4119"}]}}]}
        for change in ("code", "system", "text", "diagnostics", "mapping"):
            outcome = deepcopy(template)
            issue = outcome["issue"][0]
            if change in {"code", "system"}:
                issue["details"]["coding"][0][change] = "different"
            elif change == "text":
                issue["details"]["text"] = "Results truncated"
            elif change == "diagnostics":
                issue["diagnostics"] = "Additional problem to review"
            patient, observation = resources()[:2]
            if change == "mapping":
                observation.pop("valueQuantity")
                observation["valueRange"] = {"low": {"value": 1}}
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                run_dir = Path(directory)
                remote = mock.Mock(side_effect=[patient, {"resourceType": "Bundle", "type": "searchset",
                    "entry": [{"resource": observation}, {"resource": outcome}]}])
                live.run_pipeline(setup.new_config(CLIENT_ID), token(), run_dir, "run", ["vital-signs"], transport=remote)
                self.assertFalse(reports.inspect_run(run_dir)["eligibleForLimitedSandboxAcceptance"])

    def test_offline_demo_is_verified_but_never_counts_as_live_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            run_dir = reports.demo(directory)
            verified = reports.inspect_run(run_dir)
            self.assertTrue(verified["digestVerified"])
            self.assertEqual(verified["status"], "passed")
            self.assertEqual(verified["dataSource"], "local-fixture")
            summary = reports.summarize(directory)
            self.assertEqual(summary["verifiedLivePatientCount"], 0)
            self.assertFalse(summary["threePatientBaselineReady"])

    def test_three_distinct_mocked_patients_are_counted_once_each(self):
        with tempfile.TemporaryDirectory() as directory:
            for index, patient in enumerate(("p1", "p1", "p2", "p3")):
                run_dir = Path(directory) / f"run-{index}"
                run_dir.mkdir()
                live.run_pipeline(setup.new_config(CLIENT_ID), token(patient), run_dir, "run", ["vital-signs"],
                                  resources=live.PROFILES["expanded"], transport=transport(patient))
            summary = reports.summarize(directory)
            self.assertEqual(summary["verifiedLivePatientCount"], 3)
            self.assertTrue(summary["threePatientBaselineReady"])
            self.assertTrue(summary["expandedRetrievalObserved"])

    def test_tampered_saved_values_fail_even_when_digest_is_recomputed(self):
        with tempfile.TemporaryDirectory() as directory:
            run_dir = reports.demo(directory)
            record_path = run_dir / "records/offline-demo.json"
            payload = json.loads(record_path.read_text())
            payload["observations"][0]["value"] = "wrong"
            new_reference = ehr_storage.store_ehr_record(payload, "offline-demo", run_dir / "records")
            report_path = run_dir / "report.json"
            report = json.loads(report_path.read_text())
            report["pipeline"]["reference"] = new_reference
            report_path.write_text(json.dumps(report))
            verified = reports.inspect_run(run_dir)
            self.assertTrue(verified["digestVerified"])
            self.assertEqual(verified["status"], "failed")

    def test_summary_reports_incomplete_and_failed_attempts(self):
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "bad-run"
            run_dir.mkdir()
            (run_dir / "failure.json").write_text('{"status":"failed"}')
            self.assertEqual(reports.summarize(directory)["runs"][0]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
