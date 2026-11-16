# MAIN-268 — Research Healthcare Record Tagging Standards

Completed November 10, 2026. Research and decision support only; no dropdown implemented.

Deliverables:

- [Sourced standards review and decision brief](../research/healthcare-record-tagging-standards.md)
- [Proposed local category catalog](../research/medilink-tag-catalog.proposed.json)

Reviewed HL7 FHIR R4, published US Core clinical-note guidance, IHE document-sharing
metadata, LOINC Document Ontology and specific term pages, ONC/USCDI references,
SNOMED CT, RxNorm and ICD-10-CM. Distinguished ordinary organizational tags,
clinical document types, content categories, source and security labels.

Recommended thirteen local navigation categories, alias search, a multi-select
picker preserving the current string-tag contract, and separation from existing
kind/source/security behavior. Verified four illustrative LOINC document codes;
none is automatically assigned to broad categories. Recorded the current API's
tag limits and the fact that tag metadata is outside document ciphertext.

Suggested sprint-report wording:

Researched healthcare record classification and tagging using official HL7,
LOINC, IHE, ONC, NLM, SNOMED and CDC sources. Produced a candidate category catalog,
example terminology mappings, implementation constraints, and a decision checklist
for the next dropdown component. Preserved legacy-tag compatibility and separated
organization tags from document types, provenance and security controls.
