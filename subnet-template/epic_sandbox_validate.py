import argparse
import json
from pathlib import Path
from typing import Any

import ehr_storage
import epic_fhir_ingest


REQUIRED_NORMALIZED_FIELDS = (
    "patientId",
    "allergies",
    "immunizations",
    "problems",
    "labResults",
    "medications",
    "encounters",
    "documents",
    "observations",
)


def validate_epic_bundle_pipeline(
    bundle_path: str | Path,
    patient_id: str,
    record_id: str,
    storage_root: str | Path,
) -> dict[str, Any]:
    payload = epic_fhir_ingest.normalize_fixture_file(
        bundle_path,
        patient_id=patient_id,
    )
    missing_fields = [
        field_name
        for field_name in REQUIRED_NORMALIZED_FIELDS
        if field_name not in payload
    ]
    if missing_fields:
        raise ValueError(f"Normalized payload missing fields: {missing_fields}")

    reference = ehr_storage.store_ehr_record(
        payload,
        record_id=record_id,
        storage_root=storage_root,
        source_system="epic-sandbox-fhir",
    )
    digest_valid = ehr_storage.verify_record_digest(reference, storage_root)
    if not digest_valid:
        raise ValueError("Stored Epic sandbox payload failed digest verification.")

    return {
        "status": "passed",
        "resourceCounts": {
            "allergies": len(payload["allergies"]),
            "immunizations": len(payload["immunizations"]),
            "problems": len(payload["problems"]),
            "labResults": len(payload["labResults"]),
            "medications": len(payload["medications"]),
            "encounters": len(payload["encounters"]),
            "documents": len(payload["documents"]),
            "observations": len(payload["observations"]),
        },
        "reference": reference,
        "digestVerified": digest_valid,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate an Epic sandbox FHIR Bundle through ingestion, local "
            "off-chain storage, and digest verification."
        )
    )
    parser.add_argument(
        "--bundle",
        required=True,
        help="Path to an Epic sandbox FHIR Bundle JSON export.",
    )
    parser.add_argument(
        "--patient-id",
        default="patient-001",
        help="Internal project patient ID for the normalized payload.",
    )
    parser.add_argument(
        "--record-id",
        default="epic-sandbox-record-001",
        help="Internal record ID for local off-chain storage.",
    )
    parser.add_argument(
        "--storage-root",
        default=str(ehr_storage.DEFAULT_STORAGE_ROOT),
        help="Directory for local off-chain EHR record files.",
    )
    parser.add_argument(
        "--report-output",
        help="Optional path for the validation report JSON. Prints to stdout when omitted.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    report = validate_epic_bundle_pipeline(
        bundle_path=args.bundle,
        patient_id=args.patient_id,
        record_id=args.record_id,
        storage_root=args.storage_root,
    )
    output = json.dumps(report, indent=2, sort_keys=True)

    if args.report_output:
        Path(args.report_output).write_text(output + "\n", encoding="utf-8")
    else:
        print(output)


if __name__ == "__main__":
    main()
