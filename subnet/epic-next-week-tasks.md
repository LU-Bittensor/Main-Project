# Epic API work — October 19–25, 2026

Owner: Louis. These are three new backlog entries; assign permanent tracker IDs when entering them in the source tracker. Effort estimates below are proposed hours, not interpretations of the screenshot's numeric columns.

Prerequisite satisfied October 23: the registered client completed real sandbox login and authenticated Patient/Observation retrieval. See [live results](epic-live-results.md).

| Entry | Task | Work and acceptance criteria | Owner | Estimate |
| --- | --- | --- | --- | --- |
| Next-1 | Validate live Epic API retrieval across three sandbox patients | Use three published test-patient accounts, authorizing each separately. Pull Patient plus vital-sign/laboratory Observations with the new runner. Capture one dated report per patient with request categories, resource counts, empty-result cases, and verified digests. At least one selected patient must return actual Observation resources. Keep tokens and passwords out of the report. | Louis | 3 hours |
| Next-2 | Expand live FHIR retrieval to conditions, medications, and encounters | Enable the corresponding R4 APIs on the sandbox app, confirm each API's required search parameters, and extend the fetcher for Condition, MedicationRequest, and Encounter. Retrieve real sandbox responses, follow pagination, and pass the combined Bundle through the existing normalizer. Record populated and empty results explicitly; do not insert fixture resources into a live run. | Louis | 3 hours |
| Next-3 | Reconcile live API data with stored EHR output | Compare raw resource IDs/counts and representative values against the normalized records for the live runs. Document dropped or unsupported fields, fix mapping gaps found in the fetched data, and add focused synthetic regression cases. Verify digest integrity and produce a repeatable demo command plus a concise results table. | Louis | 2 hours |

Suggested order: Next-1 early in the week; Next-2 after the baseline is repeatable; Next-3 after expanded retrieval. Deliverables stay in the repository's integration notes and ignored local run outputs. These tasks target Epic's live hosted sandbox with synthetic patient records.

## Completed — October 23

- **Next-1 complete:** Three distinct published sandbox patients authorized separately and returned Patient plus vital-sign/laboratory search responses. Nonzero and empty results are documented. One vital-sign search followed two pages.
- **Next-2 complete:** All three patients were queried for Condition, MedicationRequest, and Encounter. Actual populated and empty responses passed through normalization; no fixture resources were added.
- **Next-3 complete:** Saved raw resources and normalized records passed mapped-count/selected-field reconciliation and digest re-verification for all three patients. Focused regression tests cover mapping gaps and warning handling; all 66 targeted tests pass. The repeatable demo command and results table are in [live results](epic-live-results.md).

Epic's access-coverage and empty-result notices were reviewed and remain visible as `passed_with_warnings`. These deliverables establish a limited sandbox integration with synthetic data. They do not establish full-chart coverage or production readiness. The original task tracker was not edited.
