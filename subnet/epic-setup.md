# MediLink Epic sandbox setup

Updated October 23, 2026. All code, configuration, and output live in CapstoneSpring.

## Live sandbox integration verified across three patients

The local setup includes OAuth with PKCE, a browser callback, five resource types, pagination, normalization, local storage, digest verification, a mapping audit, and a saved-run summary. The offline demonstration passed. The live runner's HTTPS connection to Epic's public discovery endpoint also succeeded after adding the macOS system CA bundle to Python's trust store; certificate and hostname verification remain enabled.

The ignored local config now contains the non-production client ID supplied from the app registration and the registered callback `http://localhost:8080/callback`. Screenshots confirm a non-confidential R4 client, SMART v1 scopes, and unconstrained FHIR IDs. The runner accepts longer FHIR IDs while rejecting path/query characters. The local configuration, callback-port, and client-specific discovery checks pass; see [configured setup evidence](evidence/epic/2026-10-23-configured-setup-check.json).

[The final live results](epic-live-results.md) verify all three distinct test patients, all five supported resource types, pagination, normalization, and saved-record integrity. The three API sprint deliverables are complete for this defined sandbox scope.

The first live baseline completed on October 23 at 21:31 UTC: authorization-code exchange succeeded, and authenticated requests returned one Patient and 257 vital-sign Observations. Re-reading the saved files confirmed matching mapped fields/counts and a valid digest. Epic returned one search warning; its diagnostic text was not retained by that version of the runner, so the run remains `passed_with_warnings`. See [baseline evidence](evidence/epic/2026-10-23-live-baseline.json).

The local configuration now uses the **expanded** profile. Live runs have returned all five supported resource types and passed saved-data reconciliation and digest checks. New runs retain search outcome details separately so warnings can be investigated. [All 66 targeted tests pass](evidence/epic/2026-10-23-live-review-tests.txt). The other registered callback, `http://127.0.0.1:5173/external-import/callback`, belongs to the separate frontend flow and is not used by this runner.

## One-time Epic registration

Sign in to [Epic on FHIR — My Apps](https://fhir.epic.com/Developer/Apps). Use a separate **sandbox-only test app** for this local runner. Suggested name: **MediLink Sandbox**.

| Setting | Value for this runner |
| --- | --- |
| User type | Patients / patient-facing |
| Launch workflow | Standalone patient authorization |
| Client authentication | Public/non-confidential; no client secret |
| FHIR version | R4 |
| Redirect URI | `http://localhost:8080/callback` — register this exact value, or supply your existing compatible registered URI |
| Client ID to copy | **Non-production** client ID |
| Release state | Save and make ready for sandbox testing; do not activate this local test app for production |

The website may phrase these fields differently. If your existing app requires a client secret or private-key JWT, tell me before using this runner; that registration needs a different token-exchange flow. Do not send account passwords or token values in chat.

Enable these APIs (select the **R4 Search** versions where listed):

| API | Used for |
| --- | --- |
| [Patient.Read](https://fhir.epic.com/Specifications?api=931) | The patient selected during authorization |
| [Observation.Search — Vital Signs](https://fhir.epic.com/Specifications?api=973) | Baseline vital-sign observations |
| [Observation.Search — Labs](https://fhir.epic.com/Specifications?api=999) | Optional laboratory observations |
| [Condition.Search — Problems](https://fhir.epic.com/Specifications?api=953) | Conditions with `category=problem-list-item` |
| [MedicationRequest.Search](https://fhir.epic.com/Specifications?api=997) | Medication orders; select the general R4 API, not Order Template Medication |
| [Encounter.Search — Patient Chart](https://fhir.epic.com/Specifications?api=909) | Patient encounters |

Epic registration/API changes can take time to reach the sandbox. Check its [app-creation and OAuth guidance](https://fhir.epic.com/Documentation?docId=oauth2) if a newly saved app is rejected. The form and enabled APIs must be checked in your own signed-in account; local configuration cannot register the application for you.

## Save configuration

From the project:

```sh
cd /Users/louisscalise/Desktop/Spring26/CapstoneSpring/Main-Project/subnet
python3 epic_sandbox_setup.py configure \
  --client-id YOUR_NON_PRODUCTION_CLIENT_ID \
  --redirect-uri http://localhost:8080/callback
python3 epic_sandbox_setup.py check --online --check-port
```

Replace the client-ID placeholder with the actual UUID. The setup helper writes `epic_sandbox.config.yml` with owner-only permissions and six scopes for the expanded profile. That file is ignored by Git. `init` creates a placeholder config only when none exists; repeating it preserves your settings. `configure` deliberately updates the client, callback, and chosen profile. To use a separate config file, place `--config PATH` before the subcommand.

The checker verifies local settings, the requested scope set, callback-port availability when requested, and public SMART discovery. `ready_for_login` does **not** mean the client ID is registered or that patient access is authorized; those are verified during login and API requests. macOS system certificates are loaded automatically unless you explicitly supply `SSL_CERT_FILE` or `SSL_CERT_DIR`. TLS checks are never disabled.

## First authenticated pull

```sh
python3 epic_sandbox_live.py --profile baseline
```

Sign in using an Epic-published sandbox test-patient account in the browser window and authorize the app. Credentials are entered only on Epic's page. The runner keeps the authorization code, PKCE verifier, and token in memory and saves only resource data and reports.

After the first successful pull, test the expanded profile:

```sh
python3 epic_sandbox_setup.py configure \
  --client-id YOUR_NON_PRODUCTION_CLIENT_ID \
  --redirect-uri http://localhost:8080/callback --profile expanded
python3 epic_sandbox_live.py --profile expanded \
  --observation-category vital-signs \
  --observation-category laboratory
```

The expanded profile fetches Patient, Observation, Condition, MedicationRequest, and Encounter. Every search is scoped to the patient ID returned by the token exchange. A failed resource request stops the run; it is not silently replaced with fixture data. Missing names, unmapped value shapes, or API warnings remain visible in reports.

MedicationRequest searches by patient without a category filter; live sandbox runs confirmed this request returns medication orders. Conditions are limited to the problem list; other Condition categories and detailed Medication resources are not fetched. This is a defined five-resource integration, not a full-chart export.

For the three-patient sprint deliverable, repeat the login and pull for three distinct documented sandbox patients. Sign out or use a separate browser session between patients when needed. A fresh command alone does not guarantee a different browser account. `--no-browser` prints a login URL for manual use in another browser session on the same computer. At least one patient should have observations in a selected category.

## Verify the output

Each run goes into a unique directory under `storage/epic_sandbox_runs/`, regardless of the terminal's current directory. These data files are ignored by Git.

| File | Purpose |
| --- | --- |
| `bundle.json` | Raw resources fetched during this run |
| `records/epic-sandbox-live.json` | Normalized EHR summary |
| `reconciliation.json` | Counts, source-ID-to-array-position mapping, selected field comparisons, and mapping warnings |
| `report.json` | Source, timestamp, query/page counts, digest result, and reconciliation result |
| `search-outcomes.json` | Original search OperationOutcome details, when present; retained locally for warning/error review, including before a later request failure |
| `failure.json` | Safe failure summary if the run could not complete |

```sh
python3 epic_sandbox_report.py verify --run-dir storage/epic_sandbox_runs/RUN_DIRECTORY
python3 epic_sandbox_report.py summary
```

`verify` rereads the raw bundle and stored record, compares selected mapped fields, and recomputes the record digest. `summary` reports distinct verified live patients and whether the three-patient baseline and expanded retrieval have evidence. Runs with unresolved warnings require review and are excluded from automatic readiness. Summary flags describe local evidence, not independent proof from Epic; keep the original reports and review them before marking sprint work complete.

Two exact notices were reviewed in the live October 23 responses. Epic code **4119** limits coverage to information available to the authorized user; code **4101** reports no results. The verifier recognizes their exact text, severity, and Epic coding system in the saved outcome file. An empty-result notice also requires a matching single-page search with zero returned entries. These notices qualify only for the limited sandbox demonstration; `passed_with_warnings` remains visible in the original reports. Other API warnings, mapping warnings, missing outcome details, or failed checks remain unresolved.

The normalized model intentionally omits many FHIR fields. It keeps patient ID rather than all demographics, condition labels rather than full Condition resources, and only selected medication/encounter fields. Preserve the raw Bundle for full returned content. Files are owner-only plaintext sandbox artifacts, not an encrypted production EHR store.

## Offline demo and tests

```sh
python3 epic_sandbox_report.py demo
python3 -m unittest discover -s tests -p 'test_epic*.py' -v
python3 -m unittest discover -s tests -p 'test_ehr_storage.py' -v
```

The demo uses existing synthetic fixture files, writes `dataSource: local-fixture` and `oauthValidated: false`, and is never counted as live evidence. Tests mock remote OAuth/FHIR responses; one test uses a real loopback callback listener.

## Ownership boundary

These commands implement the local Epic integration and data pipeline. The MediLink frontend, access-control contracts, Bittensor processes, and Hippius services are separate components; this setup does not start or deploy them. Once the live sandbox connection works, the existing normalization output is the handoff point for integrating the frontend and storage services.
