"""Read Epic sandbox FHIR resources through standalone OAuth.

Public/non-confidential test clients only. Tokens and PKCE verifiers remain in
memory. This command does not authenticate users to the application via OIDC.
"""

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections import Counter
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import epic_oauth_config
import epic_sandbox_validate
import epic_fhir_reconcile


ROOT = Path(__file__).resolve().parent
PROFILES = {
    "baseline": ("Observation",),
    "expanded": ("Observation", "Condition", "MedicationRequest", "Encounter"),
}


def scopes_for_profile(profile):
    return ["launch/patient", "patient/Patient.read"] + [
        f"patient/{resource}.read" for resource in PROFILES[profile]
    ]


def validate_profile_scopes(config, profile):
    scopes = set(config["scopes"])
    missing = [scope for scope in scopes_for_profile(profile)
               if scope not in scopes and scope.replace(".read", ".rs") not in scopes]
    if missing:
        raise EpicLiveError("Configuration lacks scopes for this profile: " + ", ".join(missing))


class EpicLiveError(ValueError):
    """A safe-to-display error; never includes tokens or remote response bodies."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def tls_context():
    context = ssl.create_default_context()
    # python.org macOS builds may lack a default CA file even though macOS has
    # a system bundle. Honor explicit trust-store overrides without modification.
    if (sys.platform == "darwin" and not os.getenv("SSL_CERT_FILE")
            and not os.getenv("SSL_CERT_DIR") and Path("/etc/ssl/cert.pem").is_file()):
        context.load_verify_locations(cafile="/etc/ssl/cert.pem")
    return context


def request_json(url, *, headers=None, form=None):
    """Send a bounded JSON request without forwarding credentials on redirects."""
    request_headers = {"Accept": "application/fhir+json, application/json"}
    request_headers.update(headers or {})
    data = None
    if form is not None:
        request_headers["Content-Type"] = "application/x-www-form-urlencoded"
        data = urllib.parse.urlencode(form).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=request_headers)
    try:
        https = urllib.request.HTTPSHandler(context=tls_context())
        with urllib.request.build_opener(NoRedirect, https).open(request, timeout=30) as response:
            raw = response.read(10_000_001)
    except urllib.error.HTTPError as exc:
        hints = {
            400: "Check the request parameters and app configuration.",
            401: "Authorization expired or was rejected; restart the OAuth flow.",
            403: "Check the app's enabled APIs, granted scopes, and sandbox sync.",
            429: "Rate limited; stop and retry later according to Epic's guidance.",
        }
        status = exc.code
        exc.close()
        raise EpicLiveError(f"Epic HTTP {status}. {hints.get(status, 'Request failed; no data was accepted.')}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise EpicLiveError("Epic request failed due to a network or TLS error.") from None
    if len(raw) > 10_000_000:
        raise EpicLiveError("Epic response exceeded the 10 MB per-page limit.")
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeError):
        raise EpicLiveError("Epic did not return valid JSON.") from None
    if not isinstance(body, dict):
        raise EpicLiveError("Expected a JSON object from Epic.")
    if body.get("resourceType") == "OperationOutcome":
        raise EpicLiveError("Epic returned an OperationOutcome instead of the requested resource.")
    return body


def validate_live_config(config):
    epic_oauth_config.validate_epic_oauth_config(config)
    # Keep this test harness restricted to the documented Epic public sandbox.
    for name, expected in (
        ("authorization_url", epic_oauth_config.DEFAULT_AUTHORIZATION_URL),
        ("token_url", epic_oauth_config.DEFAULT_TOKEN_URL),
        ("fhir_base_url", epic_oauth_config.DEFAULT_FHIR_BASE_URL),
    ):
        if config[name].rstrip("/") != expected:
            raise EpicLiveError(f"This harness requires the official sandbox {name}.")
    redirect = urllib.parse.urlsplit(config["redirect_uri"])
    if (redirect.scheme != "http" or redirect.hostname not in {"localhost", "127.0.0.1"}
            or not redirect.port or not redirect.path or redirect.query or redirect.fragment
            or redirect.username or redirect.password):
        raise EpicLiveError("Use a registered http://localhost:PORT/callback or 127.0.0.1 callback for this local test client.")
    if "launch/patient" not in config["scopes"]:
        raise EpicLiveError("Standalone patient selection requires launch/patient in this harness.")


def create_authorization_request(config):
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    url = epic_oauth_config.build_authorization_url(config, state=state)
    url += "&" + urllib.parse.urlencode({"code_challenge": challenge, "code_challenge_method": "S256"})
    return url, state, verifier


def parse_callback(query, expected_state):
    params = urllib.parse.parse_qs(query, keep_blank_values=True)
    if any(len(values) != 1 for values in params.values()):
        raise EpicLiveError("OAuth callback contains duplicate parameters.")
    state = params.get("state", [""])[0]
    if not secrets.compare_digest(state.encode(), expected_state.encode()):
        raise EpicLiveError("OAuth state mismatch; callback rejected.")
    if "error" in params:
        raise EpicLiveError("Epic authorization was denied or failed; restart the flow.")
    code = params.get("code", [""])[0]
    if not code:
        raise EpicLiveError("OAuth callback did not include an authorization code.")
    return code


def receive_authorization_code(config, authorization_url, state, *, timeout=300, launch_browser=True):
    redirect = urllib.parse.urlsplit(config["redirect_uri"])
    result = {}

    class CallbackHandler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, *args):
            pass  # Default HTTP logging would expose the authorization code.

        def do_GET(self):
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path != redirect.path:
                self.send_error(404)
                return
            try:
                result["code"] = parse_callback(parsed.query, state)
                status, message = 200, "Authorization received. You can close this tab."
            except EpicLiveError as exc:
                result["error"] = exc
                status, message = 400, "Authorization failed. Check the terminal."
            body = message.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            try:
                self.wfile.write(body)
            except OSError:
                pass

    with HTTPServer(("127.0.0.1", redirect.port), CallbackHandler) as server:
        server.timeout = 1
        print("Sign in with an Epic sandbox test patient in your browser.", flush=True)
        print(authorization_url, flush=True)
        if launch_browser:
            webbrowser.open(authorization_url)
        deadline = time.monotonic() + timeout
        while not result and time.monotonic() < deadline:
            server.handle_request()
    if "error" in result:
        raise result["error"]
    if "code" not in result:
        raise EpicLiveError("Timed out waiting for Epic authorization; restart the flow.")
    return result["code"]


def exchange_code(config, code, verifier, *, transport=request_json):
    started = time.monotonic()
    result = transport(config["token_url"], form={
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": config["redirect_uri"], "client_id": config["client_id"],
        "code_verifier": verifier,
    })
    if not isinstance(result.get("access_token"), str) or not result["access_token"].strip():
        raise EpicLiveError("Token response is missing an access token.")
    if str(result.get("token_type", "")).lower() != "bearer":
        raise EpicLiveError("Expected a Bearer access token.")
    try:
        lifetime = int(result["expires_in"])
    except (KeyError, TypeError, ValueError, OverflowError):
        raise EpicLiveError("Token response is missing a valid expires_in.") from None
    if lifetime <= 5:
        raise EpicLiveError("Token is expired or has too little time remaining.")
    patient = result.get("patient")
    # Epic's unconstrained-ID scheme can return identifiers over 64 characters.
    if (not isinstance(patient, str) or not re.fullmatch(r"[A-Za-z0-9.-]+", patient)
            or patient in {".", ".."}):
        raise EpicLiveError("Token response has no valid patient context; check launch/patient and the patient-facing app registration.")
    return {"access_token": result["access_token"], "patient": patient,
            "expires_at": started + lifetime - 5, "scope": result.get("scope", "")}


def checked_fhir_url(base, url):
    resolved = urllib.parse.urljoin(base.rstrip("/") + "/", url)
    parsed, expected = urllib.parse.urlsplit(resolved), urllib.parse.urlsplit(base)
    path = urllib.parse.unquote(parsed.path)
    if (parsed.scheme != "https" or parsed.netloc != expected.netloc
            or parsed.fragment or parsed.username or parsed.password
            or not path.startswith(expected.path.rstrip("/") + "/")
            or any(part in {".", ".."} for part in path.split("/"))
            or "\\" in path or "%" in path):
        raise EpicLiveError("Rejected a pagination link outside the configured FHIR base.")
    return resolved


def fetch_patient_bundle(config, token, categories=("vital-signs",), *, transport=request_json,
                         max_pages=50, resources=("Observation",), outcomes=None):
    if not categories or any(c not in {"vital-signs", "laboratory"} for c in categories):
        raise EpicLiveError("Choose vital-signs and/or laboratory Observation categories.")
    if max_pages < 1:
        raise EpicLiveError("max_pages must be positive.")
    if not resources or any(resource not in PROFILES["expanded"] for resource in resources):
        raise EpicLiveError("Unsupported FHIR search resource.")
    base, patient_id = config["fhir_base_url"].rstrip("/"), token["patient"]

    def get(url):
        url = checked_fhir_url(base, url)
        if time.monotonic() >= token["expires_at"]:
            raise EpicLiveError("Access token expired; restart OAuth before fetching more data.")
        return transport(url, headers={"Authorization": "Bearer " + token["access_token"],
                                       "Epic-Client-ID": config["client_id"]})

    patient = get(base + "/Patient/" + urllib.parse.quote(patient_id, safe=""))
    if patient.get("resourceType") != "Patient" or patient.get("id") != patient_id:
        raise EpicLiveError("Patient response does not match the authorized patient context.")
    entries = [{"resource": patient}]
    seen_resources, queries, warnings = set(), [], []
    search_plan = []
    for resource_type in dict.fromkeys(resources):
        if resource_type == "Observation":
            search_plan.extend((resource_type, category) for category in dict.fromkeys(categories))
        else:
            search_plan.append((resource_type, "problem-list-item" if resource_type == "Condition" else None))
    for resource_type, category in search_plan:
        params = {"patient": patient_id}
        if category:
            params["category"] = category
        url = base + "/" + resource_type + "?" + urllib.parse.urlencode(params)
        visited, count = set(), 0
        while url:
            url = checked_fhir_url(base, url)
            if url in visited or len(visited) >= max_pages:
                raise EpicLiveError("Pagination repeated or exceeded the page limit; run is incomplete.")
            visited.add(url)
            bundle = get(url)
            if bundle.get("resourceType") != "Bundle" or bundle.get("type") != "searchset":
                raise EpicLiveError("Expected a FHIR searchset Bundle.")
            page_entries = bundle.get("entry", [])
            if not isinstance(page_entries, list):
                raise EpicLiveError("FHIR Bundle.entry must be a list.")
            for entry in page_entries:
                resource = entry.get("resource") if isinstance(entry, dict) else None
                if not isinstance(resource, dict):
                    raise EpicLiveError("FHIR Bundle contains an invalid entry.")
                if resource.get("resourceType") == "OperationOutcome":
                    if outcomes is not None:
                        outcomes.append({"resourceType": resource_type, "category": category,
                                         "page": len(visited), "outcome": resource})
                    issues = resource.get("issue")
                    if not isinstance(issues, list) or not issues:
                        raise EpicLiveError("Search returned an invalid OperationOutcome.")
                    for issue in issues:
                        if not isinstance(issue, dict) or issue.get("severity") not in {"warning", "information"}:
                            raise EpicLiveError("Search returned an error OperationOutcome; run is incomplete.")
                        warnings.append({"resourceType": resource_type, "category": category, "severity": issue["severity"]})
                    continue
                if (resource.get("resourceType") != resource_type
                        or not isinstance(resource.get("id"), str) or not resource["id"]):
                    raise EpicLiveError(f"{resource_type} search returned an unexpected resource.")
                subject = resource.get("subject")
                reference = subject.get("reference") if isinstance(subject, dict) else None
                if reference not in {"Patient/" + patient_id, base + "/Patient/" + patient_id}:
                    raise EpicLiveError(f"{resource_type} does not match the authorized patient context.")
                count += 1
                key = (resource["resourceType"], resource["id"])
                if key not in seen_resources:
                    seen_resources.add(key)
                    entries.append({"resource": resource})
            links = bundle.get("link", [])
            if not isinstance(links, list) or any(not isinstance(link, dict) for link in links):
                raise EpicLiveError("FHIR Bundle.link must contain link objects.")
            next_links = [link.get("url") for link in links if link.get("relation") == "next"]
            if len(next_links) > 1 or any(not isinstance(link, str) or not link for link in next_links):
                raise EpicLiveError("FHIR Bundle contains invalid next links.")
            url = urllib.parse.urljoin(url, next_links[0]) if next_links else None
        queries.append({"resourceType": resource_type, "category": category,
                        "pages": len(visited), "returnedEntries": count})
    return {"resourceType": "Bundle", "type": "collection", "entry": entries}, {
        "queries": queries, "warnings": warnings,
        "rawResourceCounts": dict(Counter(entry["resource"]["resourceType"] for entry in entries)),
    }


def write_json(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(value, output, indent=2, sort_keys=True)
        output.write("\n")


def run_pipeline(config, token, run_dir, record_id, categories, *, transport=request_json,
                 resources=("Observation",)):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", record_id):
        raise EpicLiveError("record-id must use letters, digits, hyphens, or underscores.")
    outcomes = []
    try:
        bundle, fetched = fetch_patient_bundle(config, token, categories, transport=transport,
                                               resources=resources, outcomes=outcomes)
    finally:
        # Keep diagnostic text in a separate owner-only sandbox artifact, even if
        # a later request fails. Never include it in terminal output or reports.
        if outcomes:
            write_json(run_dir / "search-outcomes.json", outcomes)
    write_json(run_dir / "bundle.json", bundle)
    storage = run_dir / "records"
    storage.mkdir(mode=0o700)
    validation = epic_sandbox_validate.validate_epic_bundle_pipeline(
        run_dir / "bundle.json", patient_id=token["patient"], record_id=record_id, storage_root=storage)
    (storage / f"{record_id}.json").chmod(0o600)
    payload = json.loads((storage / f"{record_id}.json").read_text(encoding="utf-8"))
    reconciliation = epic_fhir_reconcile.reconcile_bundle(bundle, payload)
    write_json(run_dir / "reconciliation.json", reconciliation)
    if reconciliation["status"] == "failed":
        raise EpicLiveError("Raw FHIR data does not match the normalized record; review reconciliation.json.")
    report = {
        "status": "passed_with_warnings" if fetched["warnings"] or reconciliation["warnings"] else "passed",
        "environment": "epic-public-sandbox", "dataSource": "authenticated-fhir-api",
        "completedAt": datetime.now(UTC).isoformat(), "oauthValidated": True,
        "fhirBaseUrl": config["fhir_base_url"], **fetched, "pipeline": validation,
        "reconciliation": reconciliation,
    }
    write_json(run_dir / "report.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(ROOT / epic_oauth_config.DEFAULT_CONFIG_PATH))
    parser.add_argument("--profile", choices=PROFILES, default="baseline")
    parser.add_argument("--observation-category", choices=("vital-signs", "laboratory"), action="append")
    parser.add_argument("--output-root", default=str(ROOT / "storage/epic_sandbox_runs"))
    parser.add_argument("--record-id", default="epic-sandbox-live")
    parser.add_argument("--no-browser", action="store_true", help="Print the login URL without opening it.")
    args = parser.parse_args(argv)
    run_dir = None
    try:
        config = epic_oauth_config.resolve_epic_oauth_config(args.config)
        validate_live_config(config)
        validate_profile_scopes(config, args.profile)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", args.record_id):
            raise EpicLiveError("record-id must use letters, digits, hyphens, or underscores.")
        run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(4)
        run_dir = Path(args.output_root) / run_id
        run_dir.mkdir(mode=0o700, parents=True)
        url, state, verifier = create_authorization_request(config)
        code = receive_authorization_code(config, url, state, launch_browser=not args.no_browser)
        token = exchange_code(config, code, verifier)
        run_pipeline(config, token, run_dir, args.record_id, args.observation_category or ["vital-signs"],
                     resources=PROFILES[args.profile])
        print(f"Sandbox API run completed. Review {run_dir / 'report.json'}")
        return 0
    except (EpicLiveError, epic_oauth_config.EpicOAuthConfigError, OSError, ValueError) as exc:
        if isinstance(exc, (EpicLiveError, epic_oauth_config.EpicOAuthConfigError)):
            message = str(exc)
        elif isinstance(exc, FileNotFoundError):
            message = "A required local file is missing. Run python3 epic_sandbox_setup.py init, then configure the app."
        else:
            message = "Configuration or local pipeline failed; check the config and output paths."
        if run_dir is not None:
            try:
                write_json(run_dir / "failure.json", {"status": "failed", "error": message,
                    "completedAt": datetime.now(UTC).isoformat()})
            except OSError:
                pass
        print(message)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
