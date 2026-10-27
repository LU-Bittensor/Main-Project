# Epic live sandbox results — October 23, 2026

The three API sprint deliverables are complete for the defined sandbox scope: authenticated retrieval across three distinct test patients, expanded resource retrieval, and reconciliation of saved output. Patient identity was compared using the authorized patient IDs; identifiers are omitted from this summary.

| Patient | Vitals | Labs | Conditions | Medication orders | Encounters | Mapping / digest |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Test patient 1 | 257 | 2 | 1 | 1 | 5 | Passed / verified |
| Test patient 2 | 0 | 0 | 7 | 9 | 0 | Passed / verified |
| Test patient 3 | 1617 | 0 | 1 | 1 | 0 | Passed / verified |

Each run also returned one Patient resource. Zero counts reflect actual empty searches. At least one live query followed pagination; all fetched data passed through normalization and local storage, and saved records were re-read to verify mapped counts/selected fields and SHA-256 digests.

## Acceptance and limitations

Epic returned two reviewed notices: **4119**, which limits coverage to data accessible to the authorized user, and **4101**, which reports an empty result. Empty-result notices matched the corresponding zero-count searches. Original reports retain `passed_with_warnings`; the summary accepts these specific notices only for this limited sandbox demonstration. There were no mapping mismatches, mapping warnings, or unresolved API warnings in the three accepted runs.

The first baseline attempt did not retain warning details and remains excluded from automatic acceptance. Its subsequent expanded run supplied reviewable replacement evidence for that patient. This work uses synthetic records on Epic’s hosted sandbox. Full-chart coverage and production integration are outside this acceptance scope.

## Saved evidence

- [Identifier-free results JSON](evidence/epic/2026-10-23-live-results.json)
- [66 passing targeted tests](evidence/epic/2026-10-23-live-review-tests.txt)
- [Test patient 1 original report](storage/epic_sandbox_runs/20261023T213314Z-25d89d54/report.json)
- [Test patient 2 original report](storage/epic_sandbox_runs/20261023T213651Z-ef1499de/report.json)
- [Test patient 3 original report](storage/epic_sandbox_runs/20261023T213757Z-003737f6/report.json)

## Repeat the demonstration

From this project directory, run:

```sh
python3 epic_sandbox_live.py --profile expanded --observation-category vital-signs --observation-category laboratory
python3 epic_sandbox_report.py summary
```

Authorize a published test-patient account in the opened browser. Each run saves its own raw resources, normalized record, mapping audit, outcome details, and report under ignored `storage/epic_sandbox_runs/`. Use `python3 epic_sandbox_report.py verify --run-dir storage/epic_sandbox_runs/RUN_DIRECTORY` to recheck a saved run.

The separate MediLink frontend and external storage services remain integration follow-ups. All work and saved artifacts are in CapstoneSpring.
