"""Run an explicitly offline demo, verify saved runs, and summarize live evidence."""

import argparse
import hashlib
import json
import re
import secrets
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import ehr_storage
import epic_fhir_reconcile
import epic_fhir_ingest
import epic_sandbox_live as live
import epic_sandbox_validate

ROOT = Path(__file__).resolve().parent
EPIC_COVERAGE_NOTICE_SYSTEM = "urn:oid:1.2.840.114350.1.13.0.1.7.2.657369"
EPIC_COVERAGE_NOTICE_TEXT = (
    "This response includes information available to the authorized user at the time of the request. "
    "It may not contain the entire record available in the system."
)
EPIC_EMPTY_RESULT_TEXT = "Resource request returns no results."


def review_api_warnings(run_dir, report):
    """Recognize the exact coverage/empty-result notices reviewed live.

    Preserve the original warning status. This accepts a limited authorized-data
    demonstration, never a claim that the entire chart was returned.
    """
    warnings = report.get("warnings", [])
    if not warnings:
        return {"status": "none", "coverageNoticeCount": 0, "emptyResultNoticeCount": 0, "unresolvedCount": 0}
    unresolved = {"status": "unresolved", "coverageNoticeCount": 0,
                  "emptyResultNoticeCount": 0, "unresolvedCount": len(warnings)}
    try:
        outcomes = json.loads((Path(run_dir) / "search-outcomes.json").read_text())
        actual, coverage, empty = [], 0, 0
        for entry in outcomes:
            for issue in entry["outcome"]["issue"]:
                actual.append({"resourceType": entry["resourceType"], "category": entry["category"],
                               "severity": issue["severity"]})
                details = issue.get("details", {})
                coding = details.get("coding", [])
                code = coding[0].get("code") if len(coding) == 1 else None
                expected_text = {"4119": EPIC_COVERAGE_NOTICE_TEXT, "4101": EPIC_EMPTY_RESULT_TEXT}.get(code)
                if (issue.get("severity") == "warning" and issue.get("code") == "processing"
                        and expected_text and details.get("text") == expected_text
                        and coding[0].get("system") == EPIC_COVERAGE_NOTICE_SYSTEM
                        and coding[0].get("display", expected_text) == expected_text
                        and not issue.get("diagnostics")):
                    if code == "4119":
                        coverage += 1
                    else:
                        matches = [query for query in report.get("queries", [])
                                   if query["resourceType"] == entry["resourceType"]
                                   and query["category"] == entry["category"]]
                        if len(matches) == 1 and matches[0]["returnedEntries"] == 0 and matches[0]["pages"] == 1:
                            empty += 1
        if actual != warnings:
            return unresolved
        remaining = len(warnings) - coverage - empty
        return {"status": "unresolved" if remaining else "reviewed_expected_notices",
                "coverageNoticeCount": coverage, "emptyResultNoticeCount": empty, "unresolvedCount": remaining}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return unresolved


def demo(output_root):
    bundle = epic_fhir_ingest.load_fhir_bundle(ROOT / "fixtures/epic/patient_bundle.json")
    patient = next(entry["resource"] for entry in bundle["entry"] if entry["resource"]["resourceType"] == "Patient")
    run_dir = Path(output_root) / ("offline-demo-" + secrets.token_hex(4))
    run_dir.mkdir(mode=0o700, parents=True)
    live.write_json(run_dir / "bundle.json", bundle)
    storage = run_dir / "records"
    storage.mkdir(mode=0o700)
    pipeline = epic_sandbox_validate.validate_epic_bundle_pipeline(
        run_dir / "bundle.json", patient["id"], "offline-demo", storage)
    (storage / "offline-demo.json").chmod(0o600)
    payload = json.loads((storage / "offline-demo.json").read_text())
    audit = epic_fhir_reconcile.reconcile_bundle(bundle, payload)
    live.write_json(run_dir / "reconciliation.json", audit)
    live.write_json(run_dir / "report.json", {
        "status": audit["status"], "environment": "local-offline-demo",
        "dataSource": "local-fixture", "oauthValidated": False,
        "completedAt": datetime.now(UTC).isoformat(), "pipeline": pipeline,
        "reconciliation": audit, "queries": [], "warnings": [],
    })
    return run_dir


def inspect_run(run_dir):
    run_dir = Path(run_dir)
    report = json.loads((run_dir / "report.json").read_text())
    bundle = epic_fhir_ingest.load_fhir_bundle(run_dir / "bundle.json")
    reference = report["pipeline"]["reference"]
    record_id = reference["recordId"]
    if not isinstance(record_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", record_id):
        raise ValueError("Invalid record ID in the saved run.")
    if reference["storageUri"] != f"local://ehr-records/{record_id}.json":
        raise ValueError("Unexpected storage URI in the saved run.")
    record_path = run_dir / "records" / f"{record_id}.json"
    if not record_path.resolve().is_relative_to(run_dir.resolve()):
        raise ValueError("Stored record is outside the run directory.")
    payload = json.loads(record_path.read_text())
    audit = epic_fhir_reconcile.reconcile_bundle(bundle, payload)
    digest_ok = ehr_storage.verify_record_digest(reference, run_dir / "records")
    failed = not digest_ok or audit["status"] == "failed" or report.get("status") not in {"passed", "passed_with_warnings"}
    warnings = bool(report.get("warnings") or audit["warnings"] or report.get("status") == "passed_with_warnings")
    warning_review = review_api_warnings(run_dir, report)
    unexplained_status = report.get("status") == "passed_with_warnings" and not (report.get("warnings") or audit["warnings"])
    return {
        "run": run_dir.name, "dataSource": report.get("dataSource"),
        "oauthValidated": report.get("oauthValidated") is True,
        "status": "failed" if failed else ("passed_with_warnings" if warnings else "passed"),
        "digestVerified": digest_ok, "reconciliation": audit,
        "apiWarningReview": warning_review,
        "eligibleForLimitedSandboxAcceptance": not failed and not audit["warnings"]
        and warning_review["unresolvedCount"] == 0 and not unexplained_status,
        "patientFingerprint": hashlib.sha256(str(payload.get("patientId")).encode()).hexdigest(),
        "queriedResourceTypes": sorted({query["resourceType"] for query in report.get("queries", [])}),
        "rawResourceCounts": dict(Counter(
            entry["resource"]["resourceType"] for entry in bundle["entry"])),
    }


def summarize(output_root):
    rows = []
    for report_path in sorted(Path(output_root).glob("*/report.json")):
        try:
            rows.append(inspect_run(report_path.parent))
        except (OSError, ValueError, KeyError, TypeError):
            rows.append({"run": report_path.parent.name, "status": "failed", "error": "Saved run is incomplete or invalid."})
    for failure_path in sorted(Path(output_root).glob("*/failure.json")):
        if not (failure_path.parent / "report.json").exists():
            rows.append({"run": failure_path.parent.name, "status": "failed", "error": "Attempt failed before producing a complete run."})
    live_rows = [row for row in rows if row.get("dataSource") == "authenticated-fhir-api"
                 and row.get("oauthValidated") and row.get("eligibleForLimitedSandboxAcceptance")]
    baseline = [row for row in live_rows if "Observation" in row["queriedResourceTypes"]]
    patients = {row["patientFingerprint"] for row in baseline}
    expanded = [row for row in live_rows if set(live.PROFILES["expanded"]).issubset(row["queriedResourceTypes"])]
    return {
        "runs": rows, "verifiedLivePatientCount": len(patients),
        "threePatientBaselineReady": len(patients) >= 3 and any(row["rawResourceCounts"].get("Observation", 0) for row in baseline),
        "expandedRetrievalObserved": bool(expanded),
        "note": "Offline demos and unresolved warnings do not satisfy live acceptance. Reviewed Epic 4119 coverage and 4101 empty-result notices allow a limited authorized-data demo, not full-chart coverage. Empty notices must match a zero-result search. Original run statuses remain unchanged.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    demo_parser = sub.add_parser("demo", help="Use local synthetic fixtures; no Epic account or network is used.")
    demo_parser.add_argument("--output-root", type=Path, default=ROOT / "storage/epic_sandbox_runs")
    verify = sub.add_parser("verify", help="Re-read and check a saved raw bundle, normalized record, and digest.")
    verify.add_argument("--run-dir", required=True, type=Path)
    summary = sub.add_parser("summary", help="Summarize all saved runs and three-patient acceptance.")
    summary.add_argument("--output-root", type=Path, default=ROOT / "storage/epic_sandbox_runs")
    args = parser.parse_args(argv)
    try:
        if args.command == "demo":
            directory = demo(args.output_root)
            print(f"Offline fixture demo saved to {directory}. No live OAuth or API calls were performed.")
            return 0 if inspect_run(directory)["status"] != "failed" else 1
        if args.command == "verify":
            result = inspect_run(args.run_dir)
            print(json.dumps(result, indent=2))
            return 1 if result["status"] == "failed" else 0
        print(json.dumps(summarize(args.output_root), indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError):
        print("Unable to verify the saved run; required files or report fields are missing or invalid.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
