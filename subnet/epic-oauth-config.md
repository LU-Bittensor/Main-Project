# Epic Sandbox OAuth Config

For the current registration, setup checker, and live run commands, start with [Epic setup](epic-setup.md). The notes below describe the original configuration module.

Main-231 adds the configuration layer needed before live Epic sandbox ingestion can run.

## Purpose

The project should not hardcode Epic sandbox credentials. Instead, the live ingestion layer should load OAuth and FHIR endpoint settings from a local config file or environment variables.

## Setup

Copy the example config:

```sh
cp epic_sandbox.config.example.yml epic_sandbox.config.yml
```

Fill in the real non-production client ID from the Epic on FHIR sandbox app:

```yaml
client_id: your-real-non-production-client-id
```

The local `epic_sandbox.config.yml` file is ignored by git.

## Validate Config

From `subnet`, run:

```sh
python3 epic_oauth_config.py --config epic_sandbox.config.yml
```

The command prints a safe public summary without exposing the raw client ID.

## Build Authorization URL

After the real client ID is set, run:

```sh
python3 epic_oauth_config.py --config epic_sandbox.config.yml --authorization-url
```

That prints an OAuth authorization URL with:

- `response_type=code`
- `redirect_uri`
- `client_id`
- `state`
- `aud`
- `scope`

## Environment Overrides

These environment variables can override the config file:

- `EPIC_SANDBOX_CLIENT_ID`
- `EPIC_SANDBOX_REDIRECT_URI`
- `EPIC_SANDBOX_AUTHORIZATION_URL`
- `EPIC_SANDBOX_TOKEN_URL`
- `EPIC_SANDBOX_FHIR_BASE_URL`
- `EPIC_SANDBOX_SCOPES`

## How This Connects To Ingestion

The flow after live Epic sandbox access is available:

```text
Epic OAuth config
OAuth authorization code
Access token
Epic FHIR resources
FHIR Bundle JSON
epic_sandbox_validate.py
```

The config loader prepares the OAuth side without changing the ingestion, storage, or digest verification code.

## Run the standalone OAuth flow

The new `epic_sandbox_live.py` runner handles state validation, PKCE, the callback, token exchange, and authenticated Patient/Observation retrieval. See [Epic live integration](epic-live-integration.md) for compatible app registration, commands, and remaining live validation. The authorization URL printed by this configuration utility alone does not retain a state/PKCE session; use the runner for an end-to-end attempt.
