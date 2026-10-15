import argparse
import json
import os
import secrets
import urllib.parse
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_PATH = "epic_sandbox.config.yml"
DEFAULT_AUTHORIZATION_URL = (
    "https://fhir.epic.com/interconnect-fhir-oauth/oauth2/authorize"
)
DEFAULT_TOKEN_URL = "https://fhir.epic.com/interconnect-fhir-oauth/oauth2/token"
DEFAULT_FHIR_BASE_URL = "https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4"
REQUIRED_FIELDS = (
    "client_id",
    "redirect_uri",
    "authorization_url",
    "token_url",
    "fhir_base_url",
)
PLACEHOLDER_VALUES = {
    "",
    "your-epic-non-production-client-id",
    "your-client-id",
    "TODO",
    "todo",
}
DEFAULT_SCOPES = (
    "launch/patient",
    "openid",
    "fhirUser",
    "patient/Patient.read",
    "patient/Condition.read",
    "patient/Observation.read",
    "patient/DiagnosticReport.read",
    "patient/MedicationRequest.read",
    "patient/Encounter.read",
    "patient/DocumentReference.read",
)


class EpicOAuthConfigError(ValueError):
    """Raised when Epic sandbox OAuth configuration cannot be used."""


def _coerce_scalar(value: str) -> Any:
    normalized = value.strip().strip("\"'")
    if normalized.lower() in {"true", "false"}:
        return normalized.lower() == "true"
    return normalized


def parse_simple_yaml(raw_config: str) -> dict[str, Any]:
    config: dict[str, Any] = {}
    active_list_key: str | None = None

    for line_number, raw_line in enumerate(raw_config.splitlines(), start=1):
        line_without_comment = raw_line.split("#", 1)[0].rstrip()
        if not line_without_comment.strip():
            continue

        stripped = line_without_comment.strip()
        if stripped.startswith("- "):
            if not active_list_key:
                raise EpicOAuthConfigError(
                    f"Invalid config line {line_number}: list item without key."
                )
            config[active_list_key].append(_coerce_scalar(stripped[2:]))
            continue

        if ":" not in stripped:
            raise EpicOAuthConfigError(
                f"Invalid config line {line_number}: missing ':'."
            )

        key, raw_value = stripped.split(":", 1)
        value = raw_value.strip()
        if value == "":
            config[key] = []
            active_list_key = key
        elif key == "scopes":
            config[key] = [
                scope.strip()
                for scope in value.split(",")
                if scope.strip()
            ]
            active_list_key = None
        else:
            config[key] = _coerce_scalar(value)
            active_list_key = None

    return config


def load_config_file(config_path: str | Path) -> dict[str, Any]:
    path = Path(config_path)
    raw_config = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        config = json.loads(raw_config)
    else:
        config = parse_simple_yaml(raw_config)

    if not isinstance(config, dict):
        raise EpicOAuthConfigError("Epic OAuth config must be a JSON/YAML object.")
    return config


def _env_overrides() -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            "client_id": os.getenv("EPIC_SANDBOX_CLIENT_ID"),
            "redirect_uri": os.getenv("EPIC_SANDBOX_REDIRECT_URI"),
            "authorization_url": os.getenv("EPIC_SANDBOX_AUTHORIZATION_URL"),
            "token_url": os.getenv("EPIC_SANDBOX_TOKEN_URL"),
            "fhir_base_url": os.getenv("EPIC_SANDBOX_FHIR_BASE_URL"),
            "scopes": os.getenv("EPIC_SANDBOX_SCOPES"),
        }.items()
        if value
    }


def normalize_scopes(scopes: Any) -> list[str]:
    if scopes is None:
        return list(DEFAULT_SCOPES)
    if isinstance(scopes, str):
        normalized = scopes.replace(",", " ")
        return [scope.strip() for scope in normalized.split() if scope.strip()]
    if isinstance(scopes, list):
        return [str(scope).strip() for scope in scopes if str(scope).strip()]
    raise EpicOAuthConfigError("Epic OAuth scopes must be a list or string.")


def resolve_epic_oauth_config(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    require_real_client_id: bool = True,
) -> dict[str, Any]:
    config = load_config_file(config_path)
    config = {
        "authorization_url": DEFAULT_AUTHORIZATION_URL,
        "token_url": DEFAULT_TOKEN_URL,
        "fhir_base_url": DEFAULT_FHIR_BASE_URL,
        "scopes": list(DEFAULT_SCOPES),
        **config,
        **_env_overrides(),
    }
    config["scopes"] = normalize_scopes(config.get("scopes"))
    validate_epic_oauth_config(config, require_real_client_id=require_real_client_id)
    return config


def validate_epic_oauth_config(
    config: dict[str, Any],
    require_real_client_id: bool = True,
) -> None:
    missing_fields = [
        field_name
        for field_name in REQUIRED_FIELDS
        if not str(config.get(field_name, "")).strip()
    ]
    if missing_fields:
        raise EpicOAuthConfigError(
            f"Missing Epic OAuth config fields: {', '.join(missing_fields)}"
        )

    if require_real_client_id and config["client_id"] in PLACEHOLDER_VALUES:
        raise EpicOAuthConfigError(
            "Epic OAuth config requires a real non-production client_id."
        )

    if not config["redirect_uri"].startswith(("http://", "https://")):
        raise EpicOAuthConfigError("Epic OAuth redirect_uri must be an HTTP(S) URL.")

    for field_name in ("authorization_url", "token_url", "fhir_base_url"):
        if not config[field_name].startswith("https://"):
            raise EpicOAuthConfigError(
                f"Epic OAuth {field_name} must be an HTTPS URL."
            )

    if not config["scopes"]:
        raise EpicOAuthConfigError("Epic OAuth config requires at least one scope.")


def scope_string(config: dict[str, Any]) -> str:
    return " ".join(config["scopes"])


def build_authorization_url(
    config: dict[str, Any],
    state: str | None = None,
) -> str:
    state = state or secrets.token_urlsafe(24)
    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "redirect_uri": config["redirect_uri"],
            "client_id": config["client_id"],
            "state": state,
            "aud": config["fhir_base_url"],
            "scope": scope_string(config),
        }
    )
    return f"{config['authorization_url']}?{query}"


def public_config_summary(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "clientIdConfigured": config["client_id"] not in PLACEHOLDER_VALUES,
        "redirectUri": config["redirect_uri"],
        "authorizationUrl": config["authorization_url"],
        "tokenUrl": config["token_url"],
        "fhirBaseUrl": config["fhir_base_url"],
        "scopeCount": len(config["scopes"]),
        "scopes": config["scopes"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Load and validate Epic sandbox OAuth configuration."
    )
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG_PATH,
        help="Path to the Epic sandbox OAuth config file.",
    )
    parser.add_argument(
        "--allow-placeholder-client-id",
        action="store_true",
        help="Allow placeholder client_id values when validating example configs.",
    )
    parser.add_argument(
        "--authorization-url",
        action="store_true",
        help="Print the OAuth authorization URL instead of the config summary.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        config = resolve_epic_oauth_config(
            args.config,
            require_real_client_id=not args.allow_placeholder_client_id,
        )
    except (OSError, json.JSONDecodeError, EpicOAuthConfigError) as error:
        parser.exit(1, f"Epic OAuth config error: {error}\n")

    if args.authorization_url:
        print(build_authorization_url(config))
    else:
        print(json.dumps(public_config_summary(config), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
