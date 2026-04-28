import argparse
import json
import sys
from typing import Any

def filter_none(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}


def build_metadata_synapse(args) -> dict[str, Any]:
    return filter_none(
        {
            "patientId": args.patient_id,
            "requestId": args.request_id,
            "recordType": args.record_type,
        }
    )


def build_access_synapse(args) -> dict[str, Any]:
    return filter_none(
        {
            "requesterId": args.requester_id,
            "patientId": args.patient_id,
            "recordId": args.record_id,
            "purposeOfUse": args.purpose_of_use,
            "dateRangeStart": args.date_range_start,
            "dateRangeEnd": args.date_range_end,
        }
    )


def build_retrieval_synapse(args) -> dict[str, Any]:
    return filter_none(
        {
            "requesterId": args.requester_id,
            "patientId": args.patient_id,
            "recordId": args.record_id,
            "chunkRangeStart": args.chunk_range_start,
            "chunkRangeEnd": args.chunk_range_end,
        }
    )


def build_integrity_synapse(args) -> dict[str, Any]:
    return {
        "recordId": args.record_id,
        "providedDigest": args.provided_digest,
    }


def build_ehr_synapse(args) -> dict[str, Any]:
    return {
        "patientId": args.patient_id,
    }


BUILDERS = {
    "metadata": build_metadata_synapse,
    "access": build_access_synapse,
    "retrieve": build_retrieval_synapse,
    "integrity": build_integrity_synapse,
    "ehr": build_ehr_synapse,
}


def build_request_envelope(args) -> dict[str, Any]:
    synapse_payload = BUILDERS[args.command](args)
    return {
        "synapseType": args.command,
        "payload": synapse_payload,
    }


def render_request_text(envelope: dict[str, Any]) -> str:
    lines = [f"Synapse type: {envelope['synapseType']}"]
    for key, value in envelope["payload"].items():
        lines.append(f"{key}: {value}")
    return "\n".join(lines)


def build_mock_response(envelope: dict[str, Any]) -> dict[str, Any]:
    payload = envelope["payload"]
    synapse_type = envelope["synapseType"]

    if synapse_type == "metadata":
        response_payload = {
            "patientId": payload["patientId"],
            "requestId": payload["requestId"],
            "availableRecords": [
                {
                    "recordId": "record-001",
                    "recordType": "ehr",
                    "description": "Patient longitudinal health summary",
                },
                {
                    "recordId": "record-002",
                    "recordType": "lab-results",
                    "description": "Recent laboratory panel results",
                },
            ],
        }
    elif synapse_type == "access":
        approved = payload["purposeOfUse"].lower() in {"treatment", "operations"}
        response_payload = {
            "accessGranted": approved,
            "contractDecision": "approved" if approved else "denied",
            "keyReference": (
                f"mock-key://{payload['patientId']}/{payload['recordId']}"
                if approved
                else None
            ),
        }
    elif synapse_type == "retrieve":
        start = payload.get("chunkRangeStart", 0)
        end = payload.get("chunkRangeEnd", start)
        response_payload = {
            "encryptedPayload": (
                f"mock-encrypted-payload:{payload['recordId']}:chunks-{start}-{end}"
            ),
            "digestReference": f"digest::{payload['recordId']}",
        }
    elif synapse_type == "integrity":
        blockchain_digest = f"digest::{payload['recordId']}"
        response_payload = {
            "blockchainDigest": blockchain_digest,
            "isValid": payload["providedDigest"] == blockchain_digest,
        }
    elif synapse_type == "ehr":
        response_payload = {
            "patientId": payload["patientId"],
            "allergies": [
                {
                    "substance": "Penicillin",
                    "reaction": "Rash",
                    "severity": "moderate",
                }
            ],
            "immunizations": [
                {
                    "name": "Influenza",
                    "dateAdministered": "2026-01-12",
                    "provider": "Campus Health Clinic",
                }
            ],
            "problems": ["Hypertension", "Seasonal allergies"],
        }
    else:
        raise ValueError(f"Unsupported synapse type for mock response: {synapse_type}")

    return {
        "status": "success",
        "synapseType": synapse_type,
        "payload": filter_none(response_payload),
        "error": None,
    }


def render_response_text(envelope: dict[str, Any]) -> str:
    lines = [
        f"Status: {envelope.get('status', 'unknown')}",
        f"Synapse type: {envelope.get('synapseType', 'unknown')}",
    ]
    payload = envelope.get("payload")
    if isinstance(payload, dict):
        for key, value in payload.items():
            lines.append(f"{key}: {value}")
    error = envelope.get("error")
    if error:
        lines.append(f"Error: {error}")
    return "\n".join(lines)


def read_response_input(args) -> str:
    if args.response_file:
        with open(args.response_file, "r", encoding="utf-8") as file_handle:
            return file_handle.read()
    return sys.stdin.read()


def parse_response_envelope(raw_response: str) -> dict[str, Any]:
    envelope = json.loads(raw_response)
    if not isinstance(envelope, dict):
        raise ValueError("Response must be a JSON object.")
    if "status" not in envelope or "synapseType" not in envelope:
        raise ValueError("Response envelope must include 'status' and 'synapseType'.")
    if "payload" not in envelope:
        raise ValueError("Response envelope must include 'payload'.")
    return envelope


def add_shared_output_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--output",
        choices=("text", "json"),
        default="json",
        help="Render the constructed request as human-readable text or JSON.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build the Synapse request and print it without sending it.",
    )
    parser.add_argument(
        "--mock-response",
        action="store_true",
        help="Return a mocked Hippius response envelope for the constructed request.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build Hippius Synapse-formatted CLI requests."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    metadata_parser = subparsers.add_parser(
        "metadata",
        help="Build a record metadata discovery request.",
    )
    metadata_parser.add_argument("--patient-id", required=True)
    metadata_parser.add_argument("--request-id", required=True)
    metadata_parser.add_argument("--record-type")
    add_shared_output_flags(metadata_parser)

    access_parser = subparsers.add_parser(
        "access",
        help="Build an access request payload.",
    )
    access_parser.add_argument("--requester-id", required=True)
    access_parser.add_argument("--patient-id", required=True)
    access_parser.add_argument("--record-id", required=True)
    access_parser.add_argument("--purpose-of-use", required=True)
    access_parser.add_argument("--date-range-start")
    access_parser.add_argument("--date-range-end")
    add_shared_output_flags(access_parser)

    retrieve_parser = subparsers.add_parser(
        "retrieve",
        help="Build a medical data retrieval request.",
    )
    retrieve_parser.add_argument("--requester-id", required=True)
    retrieve_parser.add_argument("--patient-id", required=True)
    retrieve_parser.add_argument("--record-id", required=True)
    retrieve_parser.add_argument("--chunk-range-start", type=int)
    retrieve_parser.add_argument("--chunk-range-end", type=int)
    add_shared_output_flags(retrieve_parser)

    integrity_parser = subparsers.add_parser(
        "integrity",
        help="Build an integrity verification request.",
    )
    integrity_parser.add_argument("--record-id", required=True)
    integrity_parser.add_argument("--provided-digest", required=True)
    add_shared_output_flags(integrity_parser)

    ehr_parser = subparsers.add_parser(
        "ehr",
        help="Build a full EHR request.",
    )
    ehr_parser.add_argument("--patient-id", required=True)
    add_shared_output_flags(ehr_parser)

    parse_parser = subparsers.add_parser(
        "parse-response",
        help="Parse and render a Hippius response envelope from a file or stdin.",
    )
    parse_parser.add_argument(
        "--response-file",
        help="Path to a JSON response envelope. If omitted, stdin is used.",
    )
    parse_parser.add_argument(
        "--output",
        choices=("text", "json"),
        default="text",
        help="Render the parsed response as text or JSON.",
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "parse-response":
        envelope = parse_response_envelope(read_response_input(args))
        if args.output == "json":
            print(json.dumps(envelope, indent=2))
        else:
            print(render_response_text(envelope))
        return 0

    envelope = build_request_envelope(args)

    if args.mock_response:
        response_envelope = build_mock_response(envelope)
        if args.output == "json":
            print(json.dumps(response_envelope, indent=2))
        else:
            print(render_response_text(response_envelope))
        return 0

    if args.output == "json":
        print(json.dumps(envelope, indent=2))
    else:
        print(render_request_text(envelope))

    if not args.dry_run:
        print(
            "Request construction complete. Hippius transport submission is not implemented yet.",
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
