import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


DEFAULT_STORAGE_ROOT = Path("storage") / "ehr_records"


def canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def compute_sha256_digest(payload_bytes: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload_bytes).hexdigest()}"


def build_local_storage_uri(record_path: Path, storage_root: Path) -> str:
    relative_path = record_path.relative_to(storage_root)
    return f"local://ehr-records/{relative_path.as_posix()}"


def resolve_local_storage_uri(storage_uri: str, storage_root: str | Path) -> Path:
    prefix = "local://ehr-records/"
    if not storage_uri.startswith(prefix):
        raise ValueError(f"Unsupported storage URI: {storage_uri}")

    relative_path = storage_uri.removeprefix(prefix)
    return Path(storage_root) / relative_path


def store_ehr_record(
    payload: dict[str, Any],
    record_id: str,
    storage_root: str | Path = DEFAULT_STORAGE_ROOT,
    source_system: str = "epic-fhir",
    created_at: str | None = None,
) -> dict[str, Any]:
    storage_root = Path(storage_root)
    storage_root.mkdir(parents=True, exist_ok=True)

    payload_bytes = canonical_json_bytes(payload)
    digest = compute_sha256_digest(payload_bytes)
    record_path = storage_root / f"{record_id}.json"
    record_path.write_bytes(payload_bytes)

    return {
        "recordId": record_id,
        "patientId": payload["patientId"],
        "sourceSystem": source_system,
        "recordType": "ehr-summary",
        "storageUri": build_local_storage_uri(record_path, storage_root),
        "digest": digest,
        "contentType": "application/json",
        "byteLength": len(payload_bytes),
        "createdAt": created_at or datetime.now(UTC).isoformat(),
    }


def load_stored_record(
    reference: dict[str, Any],
    storage_root: str | Path = DEFAULT_STORAGE_ROOT,
) -> dict[str, Any]:
    record_path = resolve_local_storage_uri(reference["storageUri"], storage_root)
    return json.loads(record_path.read_text(encoding="utf-8"))


def verify_record_digest(
    reference: dict[str, Any],
    storage_root: str | Path = DEFAULT_STORAGE_ROOT,
) -> bool:
    record_path = resolve_local_storage_uri(reference["storageUri"], storage_root)
    payload_bytes = record_path.read_bytes()
    return compute_sha256_digest(payload_bytes) == reference["digest"]


def load_payload(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as payload_file:
        payload = json.load(payload_file)

    if not isinstance(payload, dict):
        raise ValueError("Expected normalized EHR payload to be a JSON object.")
    if not payload.get("patientId"):
        raise ValueError("Expected normalized EHR payload to include patientId.")

    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Store a normalized EHR payload locally and create a digest reference."
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to a normalized EHR JSON payload from epic_fhir_ingest.py.",
    )
    parser.add_argument(
        "--record-id",
        required=True,
        help="Internal record ID for the stored EHR payload.",
    )
    parser.add_argument(
        "--storage-root",
        default=str(DEFAULT_STORAGE_ROOT),
        help="Directory for local off-chain EHR record files.",
    )
    parser.add_argument(
        "--reference-output",
        help="Optional path for the generated reference JSON. Prints to stdout when omitted.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    payload = load_payload(args.input)
    reference = store_ehr_record(
        payload,
        record_id=args.record_id,
        storage_root=args.storage_root,
    )
    output = json.dumps(reference, indent=2, sort_keys=True)

    if args.reference_output:
        Path(args.reference_output).write_text(output + "\n", encoding="utf-8")
    else:
        print(output)


if __name__ == "__main__":
    main()
