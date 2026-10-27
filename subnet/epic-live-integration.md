# Epic sandbox integration — Main-232–234

Owner: Louis · Updated: October 23, 2026

## October 23 setup update

The [current setup guide](epic-setup.md) covers the expanded five-resource profile, configuration/check commands, mapping reconciliation, saved-run verification, and an offline demo. **60 targeted tests pass**; see [October 23 test results](evidence/epic/2026-10-23-offline-tests.txt). The offline demo passed normalization, reconciliation, and digest verification. Python's macOS certificate setup was corrected with the system CA bundle while retaining certificate/hostname checks. [The current setup check](evidence/epic/2026-10-23-setup-check.json) reaches public SMART discovery and reports the remaining client-ID placeholder.

The app details have now been supplied from its registration screenshots and saved in the ignored local config. [Configured setup checks](evidence/epic/2026-10-23-configured-setup-check.json) pass for the baseline profile. A follow-up fix supports the app's unconstrained FHIR-ID setting.

**The first authenticated sandbox pull completed October 23 at 21:31 UTC.** Epic returned one Patient and 257 vital-sign Observations after successful browser authorization and token exchange. Independent re-reading of the saved bundle and normalized record verified matching counts/selected fields and the SHA-256 digest. One Epic search warning was reported without its diagnostic text; the run remains `passed_with_warnings` pending review. [Baseline evidence](evidence/epic/2026-10-23-live-baseline.json) records the result without patient identifiers. New runs now preserve original search outcomes in an owner-only local file; [all 63 targeted tests pass](evidence/epic/2026-10-23-warning-tests.txt). [Final live results](epic-live-results.md) now verify three distinct patients across the expanded profile. All saved-record audits and digests pass. The expected Epic 4119 coverage and 4101 empty-result notices were reviewed; the original warnings remain visible. All 66 targeted tests pass.

## Task status

| Task | Delivered | Status / remaining acceptance check |
| --- | --- | --- |
| Main-232 — Validate Epic Sandbox OAuth Flow for Non-Production Client | Standalone browser authorization, random state validation, S256 PKCE, local callback, authorization-code exchange, token expiry and patient-context checks. | **Live authorization and token exchange validated October 23.** Expiry/error paths remain covered by local tests. |
| Main-233 — Connect Epic Sandbox Token Flow to FHIR Resource Fetching | Bearer-authenticated Patient.Read and Observation.Search, bounded pagination, patient matching, Bundle output, existing normalization/storage/digest pipeline. | **Live retrieval and saved-data verification succeeded:** one Patient, 257 Observations. The subsequent expanded run captured the warning details: expected access-coverage notice 4119. Three expanded patient runs now satisfy the limited sandbox acceptance checks. |
| Main-234 — Document Epic Sandbox Integration Results and Required Follow-Ups | This runbook, recorded discovery evidence, test output, limitations, acceptance checks, and three next-week tasks. | **Documentation updated with the live baseline evidence**, reviewed notices, and completed expanded/multi-patient checks. |

The original task tracker was supplied as a screenshot; this repository document records the status and follow-ups. No external tracker was edited. The screenshot's numeric columns have not been interpreted as hours or points because their headings were not provided.

## October 18 historical verification

- An unauthenticated GET to Epic's public sandbox SMART discovery endpoint returned **HTTP 200**. Its JSON advertised the expected authorize/token URLs, S256 PKCE, standalone launch, public clients, and patient context. Evidence: [discovery.json](evidence/epic/2026-10-18-discovery.json).
- **40 targeted tests passed**, including 21 new live-runner tests and 19 existing config, fixture, ingestion, validation, and storage tests. The callback test uses a real temporary loopback HTTP listener. OAuth token responses and FHIR responses in the tests are mocked. Evidence: [offline-tests.txt](evidence/epic/offline-tests.txt).
- At the October 18 checkpoint, no registered-client login, real token exchange, or authenticated FHIR data retrieval had been performed. Only the placeholder example config was present then. The October 23 live result above supersedes that limitation.

The discovery response was requested without a client ID. It verifies reachability and advertised capabilities, not the configuration or authorization of this app. Never use these offline results as evidence that the live OAuth acceptance checks passed.

## Configure and run

This runner is for a **public/non-confidential patient-facing sandbox test app** using a standalone launch and HTTP loopback callback. It does not implement a confidential-client secret/JWT flow, an EHR launch, a backend service, or production access. Confirm the app's registration type before using it.

From `Main-Project/subnet`, using Python 3.11 or newer:

```sh
cp -n epic_sandbox.config.example.yml epic_sandbox.config.yml
```

Edit the ignored `epic_sandbox.config.yml` file:

- Set `client_id` to the app's **non-production** client ID.
- Set `redirect_uri` to the **exact** registered callback, such as `http://localhost:8080/callback`. This runner accepts `localhost` or `127.0.0.1` with an explicit port and path. It listens only on IPv4 loopback.
- Confirm the app has Patient.Read (R4) and Observation.Search (Vital Signs) (R4) enabled. Enable Observation.Search (Labs) (R4) before requesting laboratory observations.
- For the initial two-resource run, limit the config's scope list to:

```yaml
scopes:
  - launch/patient
  - patient/Patient.read
  - patient/Observation.read
```

Scopes must match the app's registered APIs and the sandbox's accepted scope syntax. The baseline profile fetches Patient and Observation. The new expanded profile also fetches Condition, MedicationRequest, and Encounter; see the current setup guide for its scope configuration. The runner does not request refresh tokens or use an ID token to establish user identity.

```sh
python3 epic_oauth_config.py --config epic_sandbox.config.yml
python3 epic_sandbox_live.py --config epic_sandbox.config.yml
```

The second command opens the authorization URL and waits up to five minutes for the callback. Sign in on Epic's page using a published sandbox test-patient account and authorize the app. Test-user credentials belong on Epic's login page; they are not a client ID or a token and are not sent directly to the FHIR API. Use `--no-browser` to print the URL for manual opening in a browser on the same machine.

After the baseline succeeds, request both supported observation categories:

```sh
python3 epic_sandbox_live.py --config epic_sandbox.config.yml \
  --observation-category vital-signs \
  --observation-category laboratory
```

The runner uses the patient ID from the token response. It does not accept a separate patient ID that could accidentally mix records. A failed or expired token requires a fresh login.

## Data path and output

```text
Epic login → validated callback → authorization-code exchange
          → GET Patient/{authorized patient}
          → GET Observation?patient={authorized patient}&category=vital-signs
          → next pages → combined FHIR Bundle
          → existing EHR normalization → local storage → SHA-256 verification
```

Each invocation creates a unique directory beneath ignored `storage/epic_sandbox_runs/`:

- `bundle.json`: the selected Patient and unique Observation resources, written only after all selected searches finish.
- `records/epic-sandbox-live.json`: the existing project's normalized EHR payload.
- `report.json`: timestamp, environment, fetched counts, queries/pages, warnings, pipeline counts, storage reference, and digest-verification result.
- `failure.json`: a safe error summary when a run fails after its directory is created. A failed run does not produce a successful `report.json`.

Patient demographics remain in the raw Bundle; the existing normalizer keeps the patient ID and mapped clinical arrays, not the full Patient resource. Unrequested clinical arrays will be empty. This initial run is a limited data pull, not a full-chart export. Observation categories can be empty for a particular test patient; zero is recorded and is not automatically an error. For a meaningful ingestion demo, select a documented sandbox patient with data in at least one requested category.

Tokens, authorization codes, and PKCE verifiers are not written to disk. Callback request logging is suppressed. Generated JSON files have mode 0600 and each run directory has mode 0700. The prototype's saved sandbox data is plaintext; these file permissions do not constitute encryption. Keep this harness on synthetic sandbox data.

Pagination follows only HTTPS links within the configured Epic FHIR base and rejects repeats, excessive pages, and automatic redirects. Resources for a different patient are rejected. Error OperationOutcomes stop the run; warning/information outcomes are summarized without diagnostic text in `report.json`. New runs retain their original search outcomes separately in `search-outcomes.json` for local investigation. A run with warnings is reported as `passed_with_warnings` and requires review before closure.

## Live acceptance checklist

1. Verify the registered app type, non-production client ID, exact redirect URI, and enabled APIs. Newly changed registration/API settings may need time to sync; consult Epic's current guidance before retrying a 403.
2. **Complete October 23:** real test-patient login, callback, token exchange, and patient context. Do not copy the code or token into the tracker.
3. **Complete October 23:** Patient.Read matched that context and Observation.Search returned 257 resources.
4. **Complete for the three expanded runs:** saved-record counts, mapped fields, and digests passed re-verification. Original outcome details confirm expected access-coverage and empty-result notices. The first baseline remains excluded because it lacked warning details; the later expanded run supplies accepted replacement evidence. See the final results for run dates and report locations.

## Failure handling

| Symptom | Next action |
| --- | --- |
| Placeholder config / missing config | Set the registered non-production app details in the ignored config file. |
| Login/callback fails | Check app type and exact redirect URI, then restart the runner and login. |
| State mismatch / missing patient context | Reject the run; use a fresh authorization attempt and check standalone patient configuration. |
| HTTP 400 | Review the request, registered callback, selected category, and enabled API specifications. |
| HTTP 401 / local expiry | Reauthorize. The runner does not silently reuse or refresh expired tokens. |
| HTTP 403 | Review app API selections, granted access, and sandbox configuration sync. |
| HTTP 429 | Stop and retry later according to the service's guidance. Automatic retry is not implemented. |
| Empty search | Compare against the documented test patient's available data; retain the zero count. |
| Pagination, patient mismatch, or OperationOutcome error | Treat the pull as incomplete and investigate before rerunning. |

Error-path tests use mocks rather than deliberately inducing failures against Epic. There was no production access, performance testing, or security testing of Epic's service.

## Repeat the targeted tests

```sh
python3 -m unittest discover -s tests -p 'test_epic*.py' -v
python3 -m unittest discover -s tests -p 'test_ehr_storage.py' -v
```

The callback test requires permission to bind a temporary loopback port. All remote OAuth/FHIR responses in this suite are mocked. No extra Python packages are required by the new runner.

## Next week

See [the three tasks for October 19–25](epic-next-week-tasks.md). Complete the pending live acceptance checks above before treating these follow-ups as an extension of a proven live connection.

## Sources

- [Epic OAuth 2.0 specification](https://fhir.epic.com/Documentation?docId=oauth2&section=standaloneOauth2Launch): authorization-code exchange, non-production client use, and PKCE.
- [Epic developer testing guide](https://fhir.epic.com/Documentation?docId=cds_playbook): app/API registration, sandbox testing, and customer-environment testing.
- [Observation.Search — Vital Signs (R4)](https://fhir.epic.com/Specifications?api=973): patient- and category-specific search.
- [Epic sandbox FAQ](https://fhir.epic.com/FAQ/Index?Category=1016&Question=137): sandbox and client-registration context.
- [Live public SMART discovery endpoint](https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4/.well-known/smart-configuration): captured separately in the evidence file.

Epic's published test accounts use synthetic sandbox data. Making authenticated calls to Epic's hosted sandbox is the next step beyond local fixtures. Customer production data requires a separately authorized customer integration.
