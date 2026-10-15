import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

DEFAULT_TIMEOUT_SECONDS = 30


class HippiusCliError(Exception):
    """User-facing error raised when the CLI cannot complete a Hippius request."""


def deep_merge(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def coerce_config_value(value: str) -> Any:
    normalized = value.strip()
    if normalized.lower() in {"true", "false"}:
        return normalized.lower() == "true"
    try:
        return int(normalized)
    except ValueError:
        pass
    try:
        return float(normalized)
    except ValueError:
        pass
    return normalized.strip("\"'")


def parse_simple_yaml(raw_config: str) -> dict[str, Any]:
    config: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, config)]

    for line_number, raw_line in enumerate(raw_config.splitlines(), start=1):
        line_without_comment = raw_line.split("#", 1)[0].rstrip()
        if not line_without_comment.strip():
            continue
        if ":" not in line_without_comment:
            raise ValueError(f"Invalid config line {line_number}: missing ':'.")

        indent = len(line_without_comment) - len(line_without_comment.lstrip(" "))
        key, raw_value = line_without_comment.strip().split(":", 1)
        value = raw_value.strip()

        while stack and indent <= stack[-1][0]:
            stack.pop()
        current = stack[-1][1]

        if value == "":
            child: dict[str, Any] = {}
            current[key] = child
            stack.append((indent, child))
        else:
            current[key] = coerce_config_value(value)

    return config


def load_config_file(config_path: str | None) -> dict[str, Any]:
    if not config_path:
        return {}
    with open(config_path, "r", encoding="utf-8") as file_handle:
        raw_config = file_handle.read()
    if config_path.endswith(".json"):
        loaded = json.loads(raw_config)
    else:
        loaded = parse_simple_yaml(raw_config)
    if not isinstance(loaded, dict):
        raise ValueError("Configuration file must contain a JSON/YAML object.")
    return loaded


def resolve_hippius_config(args) -> dict[str, Any]:
    file_config = load_config_file(getattr(args, "config", None))
    hippius_config = file_config.get("hippius", file_config)
    if not isinstance(hippius_config, dict):
        raise ValueError("Hippius configuration must be an object.")

    env_config = filter_none(
        {
            "endpoint": os.getenv("HIPPIUS_CLI_ENDPOINT"),
            "timeout": os.getenv("HIPPIUS_CLI_TIMEOUT"),
            "api_key": os.getenv("HIPPIUS_CLI_API_KEY"),
        }
    )
    cli_config = filter_none(
        {
            "endpoint": getattr(args, "endpoint", None),
            "timeout": getattr(args, "timeout", None),
            "api_key": getattr(args, "api_key", None),
        }
    )

    resolved = deep_merge(hippius_config, env_config)
    resolved = deep_merge(resolved, cli_config)
    resolved["timeout"] = float(resolved.get("timeout", DEFAULT_TIMEOUT_SECONDS))
    return resolved


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
    try:
        envelope = json.loads(raw_response)
    except json.JSONDecodeError as error:
        raise ValueError(f"Response body is not valid JSON: {error.msg}.") from error
    if not isinstance(envelope, dict):
        raise ValueError("Response must be a JSON object.")
    if "status" not in envelope or "synapseType" not in envelope:
        raise ValueError("Response envelope must include 'status' and 'synapseType'.")
    if "payload" not in envelope:
        raise ValueError("Response envelope must include 'payload'.")
    return envelope


def normalize_response_envelope(
    response_data: dict[str, Any],
    request_envelope: dict[str, Any],
) -> dict[str, Any]:
    if {"status", "synapseType", "payload"}.issubset(response_data):
        return response_data

    status = response_data.get("status", "success")
    if status == "error":
        payload = response_data.get("payload", {})
    else:
        payload = response_data.get("payload", response_data)

    return {
        "status": status,
        "synapseType": response_data.get("synapseType", request_envelope["synapseType"]),
        "payload": payload if isinstance(payload, dict) else {"value": payload},
        "error": response_data.get("error"),
    }


def submit_hippius_request(
    envelope: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    endpoint = config.get("endpoint")
    if not endpoint:
        raise HippiusCliError(
            "Missing Hippius endpoint. Set --endpoint, HIPPIUS_CLI_ENDPOINT, or hippius.endpoint in --config."
        )

    request_body = json.dumps(envelope).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    api_key = config.get("api_key")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    request = urllib.request.Request(
        endpoint,
        data=request_body,
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=float(config["timeout"]),
        ) as response:
            response_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        error_body = error.read().decode("utf-8", errors="replace")
        try:
            error_envelope = normalize_response_envelope(
                json.loads(error_body),
                envelope,
            )
        except json.JSONDecodeError:
            error_envelope = {
                "status": "error",
                "synapseType": envelope["synapseType"],
                "payload": {},
                "error": f"Hippius HTTP {error.code}: {error_body or error.reason}",
            }
        return error_envelope
    except urllib.error.URLError as error:
        raise HippiusCliError(f"Could not reach Hippius endpoint: {error.reason}") from error
    except TimeoutError as error:
        raise HippiusCliError("Timed out waiting for Hippius response.") from error

    try:
        response_data = json.loads(response_body)
    except json.JSONDecodeError as error:
        raise HippiusCliError(f"Hippius returned non-JSON response: {error.msg}.") from error
    if not isinstance(response_data, dict):
        raise HippiusCliError("Hippius response must be a JSON object.")
    return normalize_response_envelope(response_data, envelope)


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
    parser.add_argument(
        "--config",
        help="Path to a JSON/YAML config file containing Hippius CLI connection settings.",
    )
    parser.add_argument(
        "--endpoint",
        help="Hippius HTTP endpoint for live request submission.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        help=f"Hippius request timeout in seconds. Defaults to {DEFAULT_TIMEOUT_SECONDS}.",
    )
    parser.add_argument(
        "--api-key",
        help="Bearer token for Hippius endpoint authentication.",
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

    try:
        if args.command == "parse-response":
            envelope = parse_response_envelope(read_response_input(args))
            if args.output == "json":
                print(json.dumps(envelope, indent=2))
            else:
                print(render_response_text(envelope))
            return 0

        envelope = build_request_envelope(args)

        if args.dry_run:
            if args.output == "json":
                print(json.dumps(envelope, indent=2))
            else:
                print(render_request_text(envelope))
            return 0

        if args.mock_response:
            response_envelope = build_mock_response(envelope)
        else:
            response_envelope = submit_hippius_request(
                envelope,
                resolve_hippius_config(args),
            )

        if args.output == "json":
            print(json.dumps(response_envelope, indent=2))
        else:
            print(render_response_text(response_envelope))
        return 0
    except (HippiusCliError, ValueError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
