"""Initialize, configure, and check a local Epic sandbox test app."""

import argparse
import json
import os
import socket
import sys
import tempfile
import urllib.parse
import uuid
from pathlib import Path

import epic_oauth_config as oauth
import epic_sandbox_live as live

ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / oauth.DEFAULT_CONFIG_PATH


def new_config(client_id="your-epic-non-production-client-id", redirect_uri="http://localhost:8080/callback", profile="expanded"):
    return {"client_id": client_id, "redirect_uri": redirect_uri,
            "authorization_url": oauth.DEFAULT_AUTHORIZATION_URL, "token_url": oauth.DEFAULT_TOKEN_URL,
            "fhir_base_url": oauth.DEFAULT_FHIR_BASE_URL, "scopes": live.scopes_for_profile(profile)}


def save_config(path, config, *, replace=False):
    if path.suffix not in {".yml", ".yaml", ".json"}:
        raise live.EpicLiveError("Choose a .yml, .yaml, or .json config file.")
    if path.suffix == ".json":
        text = json.dumps(config, indent=2) + "\n"
    else:
        # Quote scalar values. The simple config reader supports these strings.
        text = "\n".join(f'{key}: {json.dumps(value)}' for key, value in config.items() if key != "scopes")
        text += "\nscopes:\n" + "".join(f"  - {scope}\n" for scope in config["scopes"])
    if not replace:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as output:
            output.write(text)
        return
    # Replace only after a complete new config exists; no partial writes.
    with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=".epic-config-", delete=False) as output:
        temporary = Path(output.name)
        output.write(text)
    try:
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def check_setup(path, profile, *, online=False, check_port=False, transport=live.request_json):
    report = {"profile": profile, "checks": [], "blockers": [], "liveOAuthValidated": False}
    if sys.version_info < (3, 11):
        report["blockers"].append("Python 3.11 or newer is required.")
    config = None
    try:
        config = oauth.resolve_epic_oauth_config(path)
        live.validate_live_config(config)
        live.validate_profile_scopes(config, profile)
        report["checks"].append("Local config and requested profile are valid.")
        report["redirectUri"] = config["redirect_uri"]
    except (OSError, ValueError) as exc:
        report["blockers"].append(str(exc) if isinstance(exc, ValueError) else "Local config is missing or unreadable. Run the init command.")
        config = None
    if check_port and config:
        port = urllib.parse.urlsplit(config["redirect_uri"]).port
        try:
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", port))
            report["checks"].append("Callback port is available now.")
        except OSError:
            report["blockers"].append("Callback port is unavailable or this process cannot bind it.")
    if online:
        headers = {"Epic-Client-ID": config["client_id"]} if config else {}
        try:
            discovery = transport(oauth.DEFAULT_FHIR_BASE_URL + "/.well-known/smart-configuration", headers=headers)
            for field, expected in (("authorization_endpoint", oauth.DEFAULT_AUTHORIZATION_URL),
                                    ("token_endpoint", oauth.DEFAULT_TOKEN_URL)):
                if discovery.get(field) != expected:
                    raise live.EpicLiveError("Sandbox discovery advertises unexpected OAuth endpoints.")
            if "S256" not in discovery.get("code_challenge_methods_supported", []):
                raise live.EpicLiveError("Sandbox discovery does not advertise S256 PKCE.")
            report["checks"].append("Public sandbox discovery is reachable and advertises expected endpoints and PKCE.")
            report["discoveryClientIdProvided"] = bool(config)
        except (live.EpicLiveError, ValueError) as exc:
            report["blockers"].append(str(exc))
    report["status"] = "blocked" if report["blockers"] else "ready_for_login"
    report["nextStep"] = "Complete browser authorization to validate the registered app and enabled APIs."
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="Create an ignored placeholder config; preserve an existing config.")
    init.add_argument("--profile", choices=live.PROFILES, default="expanded")
    configure = sub.add_parser("configure", help="Save the app's non-production client ID and exact callback.")
    configure.add_argument("--client-id", required=True)
    configure.add_argument("--redirect-uri", required=True)
    configure.add_argument("--profile", choices=live.PROFILES, default="expanded")
    check = sub.add_parser("check", help="Check configuration; optionally check discovery and callback availability.")
    check.add_argument("--profile", choices=live.PROFILES, default="expanded")
    check.add_argument("--online", action="store_true")
    check.add_argument("--check-port", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            if args.config.exists():
                print("Existing configuration preserved.")
            else:
                save_config(args.config, new_config(profile=args.profile))
                print("Created local placeholder configuration. Register the app, then run configure.")
        elif args.command == "configure":
            try:
                uuid.UUID(args.client_id)
            except ValueError:
                raise live.EpicLiveError("Use the non-production client ID UUID shown in your Epic app registration.") from None
            config = new_config(args.client_id, args.redirect_uri, args.profile)
            live.validate_live_config(config)
            save_config(args.config, config, replace=args.config.exists())
            print("Saved local sandbox configuration. Run check --online --check-port next.")
        else:
            report = check_setup(args.config, args.profile, online=args.online, check_port=args.check_port)
            print(json.dumps(report, indent=2))
            return 1 if report["blockers"] else 0
        return 0
    except (OSError, ValueError) as exc:
        print(str(exc) if isinstance(exc, live.EpicLiveError) else "Cannot configure the sandbox; check the input and local config path.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
