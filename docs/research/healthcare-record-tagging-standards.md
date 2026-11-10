# MAIN-268 — Healthcare record tagging standards and MediLink decision brief

Research date: November 10, 2026. Scope: patient-facing record organization and
future interchange, grounded in published standards and the current repository.
**Research only: the proposed dropdown/catalog is not implemented.**

## Recommendation

Use a small, searchable, multi-select **Record categories** picker for finding
records. Keep source, document type, workflow, and sharing permissions separate.
For the next sprint component, preserve the existing string-tag API and add a
curated catalog with aliases. Do not replace `kind`, infer diagnoses from file
names, or advertise that this UI makes MediLink FHIR-compliant.

The taxonomy below is a MediLink product recommendation, not a mandated universal
list or an empirically measured ranking of hospital tags. The standards reviewed
provide different classification systems for different purposes, so there is no
single combined dropdown to copy faithfully. This is a scoped standards review,
not an exhaustive survey of every healthcare specialty or EHR implementation.

## What the standards actually distinguish

| Mechanism | What the source establishes | Implication for MediLink |
| --- | --- | --- |
| FHIR R4 DocumentReference | `type` identifies a particular document kind, with LOINC preferred; `category` is broader classification. | A patient-facing category and a precise clinical document type should not share one field. [Specification](https://hl7.org/fhir/R4/documentreference.html) |
| FHIR R4 resource metadata | `meta.tag` supports workflow/operational organization; `meta.source` and security metadata serve different purposes. Tag identity uses the coding system plus code. | Keep a local catalog distinct from source badges, clinical interpretation, and authorization. [Resource metadata](https://hl7.org/fhir/R4/resource.html#tags) |
| LOINC Document Ontology | Describes documents using five dimensions: document kind, service, setting, subject domain, and author role. | Avoid a flat list mixing lab reports, cardiology, outpatient, and physician as interchangeable kinds. Show friendly labels first; add these facets only when needed. [LOINC guide](https://loinc.org/kb/users-guide/document-ontology/document-type-codes) |
| IHE document-sharing metadata | Separates broad class, precise type, practice setting, facility, format, and confidentiality. | This independently supports keeping facets separate. Do not treat MIME format, clinical type, and sensitivity as synonyms. [IHE ITI TF 3, section 4.2](https://profiles.ihe.net/ITI/TF/Volume3/ch-4.2.html) |
| US Core clinical-note guidance | Broad narrative notes and coded diagnostic reports have overlapping but different resource uses. | A narrative report is not the same thing as each discrete result inside it. [US Core STU7 guidance](https://www.hl7.org/fhir/us/core/STU7/clinical-notes.html) |
| FHIR observation categories | Contains categories such as laboratory, imaging and vital signs specifically for observations. | Useful for browser-side content-category suggestions; these are not interchangeable with all DocumentReference types. [R4 value set](https://hl7.org/fhir/R4/valueset-observation-category.html) |
| USCDI | Organizes exchange data classes and elements; the published-version index lists Version 7, July 2026. | Useful coverage reference, not a ready-made file-tag vocabulary or evidence that a UI meets certification requirements. [Published versions](https://isp.healthit.gov/all-uscdi-versions), [ONC overview](https://healthit.gov/interoperability) |
| Security labels | Their effect depends on the security/consent policy and enforcement infrastructure. | A chip labeled Sensitive or Private cannot substitute for grants, revocation, or an enforcement policy. [FHIR security labels](https://hl7.org/fhir/R4/security-labels.html) |

Version discipline: MediLink currently imports FHIR R4. The cited R4 pages are
intentional. US Core STU7 is used as published explanatory guidance, not asserted
to be the latest release or the app's conformance target. Choose a target profile
with actual exchange partners before making interoperability claims. Published
USCDI versions and certification adoption schedules are separate questions.

### Terminologies we should not pour into the dropdown

SNOMED CT represents detailed clinical concepts; RxNorm normalizes medication
identities; ICD-10-CM classifies diagnoses. A diagnosis code or drug identifier is
not a general document tag. Preserve such codes on imported clinical data rather
than asking patients to select them while uploading a file. [SNOMED overview](https://www.snomed.org/what-is-snomed-ct),
[NLM RxNorm](https://www.nlm.nih.gov/research/umls/rxnorm/overview.html),
[CDC ICD-10-CM](https://www.cdc.gov/nchs/icd/icd-10-cm/index.html).

## Proposed starting categories

These thirteen entries are **local navigation labels**, with twelve substantive
categories plus Other. They intentionally cover both document families and
patient-understandable content areas; they are not exact FHIR category codes.
Patients can select multiple categories for a mixed report or snapshot.
Aliases help search and normalization; they do not guarantee clinical equivalence.

| Local catalog ID | Display label | Example scope / search aliases |
| --- | --- | --- |
| lab-results | Lab results | Laboratory reports, test results; labs, bloodwork |
| imaging | Imaging | Imaging reports and uploaded images; radiology, X-ray, MRI, CT |
| visit-notes | Visit notes | Consultation/progress notes and visit narratives; clinical notes |
| medications | Medications | Medication lists and prescription documents; medicines, prescriptions |
| immunizations | Immunizations | Vaccination records; vaccines, vaccinations |
| allergies | Allergies | Allergy/intolerance documentation; allergy list, intolerances |
| conditions-history | Conditions and history | Problem lists and medical history; conditions, problems, history |
| procedures | Procedures | Procedure/operation documentation; surgery, operative report |
| care-plans | Care plans | Treatment and follow-up plans; plan of care, treatment plan |
| health-summaries | Health summaries | Consolidated summaries and discharge material; discharge summary, care summary |
| vitals-measurements | Vitals and measurements | Measurement summaries; vital signs, blood pressure, pulse |
| forms-consents | Forms and consents | Signed forms and consent documents; consent, authorization form |
| other | Other | Material that does not fit; no forced clinical classification |

This list is informed by the separate models above; the exact labels and grouping
are our recommendation. It is not evidence that every imported snapshot contains
all these topics. No-data-in-response is not equivalent to absence of a condition.

Future optional facets, only if user testing justifies them: care setting
(outpatient/inpatient/emergency/home), specialty, note subtype, and date range.
Billing/insurance/admin records could receive their own category if they become
an actual MediLink use case; do not launch an exhaustive specialty catalogue now.

### Precise document codes: examples, not automatic assignments

| Actual document meaning | Verified LOINC example | Caution |
| --- | --- | --- |
| Laboratory report | [11502-2](https://loinc.org/11502-2/) | Not a replacement for codes of individual analytes/results. |
| Diagnostic imaging study | [18748-4](https://loinc.org/18748-4/) | Do not infer a specific modality from a generic upload. |
| Discharge summary | [18842-5](https://loinc.org/18842-5/) | Use when the document really is a discharge summary. |
| Episode summary / CCD meaning | [34133-9](https://loinc.org/34133-9/) | A MediLink JSON snapshot is not automatically a CCD or this document type. |

All four term pages showed Active when reviewed. These examples demonstrate how
future structured document-type metadata could work. Selecting Lab results as a
broad UI category must not silently assert LOINC 11502-2 for every attached item.
Preserve a supplied coding's system, code, display and version when available;
validate any new mapping against the precise document meaning and chosen release.

### Workflow tags and labels to avoid

If organizational workflow tags are needed later, use a separate group such as
For next appointment, Follow-up, or Personal reference. These would be local
organizational labels, not clinical states. A Reviewed label needs a defined
reviewer, timestamp and meaning before adoption; it must not imply clinician
sign-off merely because a patient clicked it.

Do not offer Public, Private, Shared, Revoked, Encrypted, Epic, or MediLink as
editable ordinary tags. They describe security, access state, or source already
owned by the application. Preserve existing strings for compatibility, but do not
let those strings change source badges or permissions.

## Findings from our code

1. `DocumentDialogs.tsx` collects free text. The API splits it at commas, permits
   at most 20 tags of 50 characters, and caps the submitted raw field at 1,000
   characters. Deduplication currently compares exact strings, so capitalization
   and synonyms can still fragment the vocabulary.
2. Supabase records serialize tags as comma-separated text. Local storage keeps
   the metadata with the ciphertext. The frontend receives an array of strings.
   A structured coding object cannot simply be sent through the current field.
3. `kind` is overloaded: values such as Document, Lab report and Epic import are
   used as record types. Exact Epic import checks also control import completion
   and latest-import discovery. `recordSource()` uses kind/legacy ID to classify
   the source. **Do not replace kind with the new category label.**
4. Source/ownership/type filters and metadata search already exist. There is no
   dedicated selected-tag filter. The next component should support exact tag
   filtering while retaining current search, paging and authorization behavior.
5. Existing encrypted imports add generic tags `Epic` and `encrypted snapshot`.
   Current imports fetch Patient, vital/laboratory Observation, Condition,
   MedicationRequest and Encounter data. Do not promise imported imaging,
   immunizations or allergies without expanding/validating the fetch pipeline.
6. Tags and names are readable metadata. The browser document manifest signs
   owner/record identity, MIME, size and ciphertext digest, not a tags field.
   Document encryption therefore does not make tags end-to-end encrypted or
   signed clinical assertions. Tags must not drive grant decisions. Avoid
   condition-specific defaults; even broad category metadata deserves a clear
   visibility explanation.

Relevant code: `frontend/src/components/DocumentDialogs.tsx`,
`frontend/src/lib/external-import.ts`, `frontend/src/lib/record-source.ts`,
`frontend/src/lib/record-filters.ts`, `frontend/src/lib/document-crypto.ts`,
`backend/key-generation/grant_api.py`, `documents.py`, `local_documents.py`,
and `epic_import.py`.

## Decisions for the next sprint component

| Decision | Recommended default | Reason / tradeoff |
| --- | --- | --- |
| Picker scope | Searchable multi-select Record categories; thirteen local entries | A manageable choice set without turning upload into clinical coding. |
| Storage for this sprint | Keep canonical display strings in the existing tag API; catalog has stable internal IDs | Lowest-risk compatibility. Freeze persisted labels for v1; later renaming needs aliases/migration. |
| Longer-term model | Separate category IDs, precise documentType coding, optional workflow tags and provenance | Better exchange semantics; requires schema/API work and client migration. |
| Existing free text | Read/display all legacy values; offer curated choices for new selections | Avoid deleting patient labels or silently rewriting old records. Custom tag creation can be deferred. |
| Source and security | Read-only app metadata and server policy; not editable tags | Tag selection must never grant access or spoof an import. |
| Epic suggestions | Suggest categories only from present content, in the browser, with owner review | The server cannot inspect encrypted snapshots, and tagging every possible domain is inaccurate. |
| Visibility | Explain that record tags accompany metadata visible to authorized parties/server | Private personal annotations require a separate design; the current tag field is not private notes. |
| Is a tag required? | No; allow an untagged record and Other | Prevent forced misclassification and unwanted metadata disclosure. |
| What does multi-tag filtering mean? | OR among chosen categories, AND with source/ownership/type/search | Fits a broad navigation picker; label the semantics and test them. |

The display-string approach is an MVP compromise, not a claim of coded FHIR
interoperability. An alternative is namespaced stable ID strings in the existing
array, but that requires label resolution, legacy handling and search changes in
every consuming client. Full Coding objects require an actual contract change.
Do not ship one client storing IDs while another renders them as raw labels.

Suggested picker behavior: keyboard navigation, searchable aliases, selected
chips with remove controls, an explicit no-match state, and preservation of
legacy values when editing. Keep the backend's current limits; announce limit
errors and validate on the server as well as in the browser.

## Acceptance criteria for the implementation task

- Selecting several categories survives upload, refresh, preview and authorized
  recipient delivery in both local and cloud-backed storage paths.
- Case/whitespace/synonym handling is deterministic, does not exceed existing
  limits, and does not discard unknown existing tags.
- Tag/source/type/ownership/search filters combine predictably; pagination resets
  on filter changes and clamps after revocation.
- Changing categories cannot alter document bytes, immutable grant scope, owner,
  source classification or security permissions.
- Existing records with no tags, legacy Epic labels and custom strings still
  render and remain searchable. Unknown codes are not silently mapped to an
  unrelated category.
- No automatic clinical-code assignment from filenames. No misleading privacy
  label or clinician verification status. No unsupported claim that the picker
  implements all of FHIR, US Core, IHE, USCDI, or a regulatory requirement.

## Research task outcome

Completed the standards review, thirteen-entry candidate catalog, verified-code
examples, current-code compatibility audit, and decision/acceptance checklist.
The JSON beside this report is a proposal only and is not imported by runtime
code. The dropdown remains the next implementation task. There is no product
behavior change in this research commit.
