# Epic Patient Authorization Flow

This document defines how a patient could authorize the project to ingest selected Epic data through Epic on FHIR.

## Purpose

The project should not directly collect Epic passwords or assume unrestricted chart access. Patient-facing ingestion should use SMART on FHIR / OAuth so the patient authenticates with the Epic organization and grants scoped data access to this project.

## Actors

| Actor | Responsibility |
| --- | --- |
| Patient | Chooses to connect Epic/MyChart data and approves requested access. |
| Epic organization | Authenticates the patient and issues scoped tokens. |
| Project app | Requests authorization and receives OAuth authorization codes/tokens. |
| Ingestion adapter | Uses the token to read allowed FHIR resources. |
| Off-chain storage layer | Stores normalized encrypted payloads. |
| Chain/access layer | Stores hash references and access decisions. |

## Authorization Steps

1. Patient starts the "connect Epic record" flow in the project app.
2. Project app identifies the Epic organization or FHIR issuer URL.
3. Project app redirects the patient to the Epic/MyChart authorization endpoint.
4. Patient signs in through Epic/MyChart.
5. Epic displays the app and requested scopes.
6. Patient approves access.
7. Epic redirects back to the project app with an authorization code.
8. Project backend exchanges the code for an access token.
9. Ingestion adapter calls approved Epic FHIR endpoints.
10. Project normalizes, encrypts, stores, hashes, and references the data.

The project app never handles the patient's Epic password.

## Scope Strategy

Request the minimum scopes needed for the prototype. Start with patient-level read access for:

- demographics or patient identity context
- allergies
- immunizations
- conditions/problem list
- observations or diagnostic reports for labs
- document references if needed for metadata discovery

Avoid broad scopes until there is a specific product need. Broad scopes can make approval and customer enablement harder.

## Token Handling

Access tokens should be treated as sensitive credentials.

Recommended handling:

- exchange authorization codes on the backend, not in a public client
- store tokens only if the prototype needs repeated sync
- encrypt any stored refresh tokens
- associate tokens with the internal patient account
- log token events without logging token values
- expire or revoke local token references when the patient disconnects Epic

## Revocation and Disconnect

Patients should be able to disconnect Epic access from the project.

Disconnect should:

1. mark the Epic connection inactive
2. stop new FHIR syncs
3. remove or invalidate stored access/refresh tokens
4. preserve existing on-chain hash records as immutable audit/integrity artifacts
5. mark off-chain records unavailable or delete them if the project policy requires deletion

Because on-chain data is difficult or impossible to delete, the project should avoid placing patient-identifying data on chain in the first place.

## Consent Separation

Epic OAuth consent and project retrieval authorization are different layers:

- Epic consent lets the project ingest data from Epic.
- Project authorization controls whether other users can access project-stored records.

The system should maintain separate receipts for each layer.

## Failure Cases

| Failure case | Expected behavior |
| --- | --- |
| Patient cancels login | Do not create an Epic connection. |
| Patient denies scopes | Show that ingestion was not authorized. |
| Token exchange fails | Do not store partial connection state. |
| Token expires | Require refresh or reconnection before new sync. |
| Scope is missing for a resource | Skip that resource and log a scope-specific warning. |
| Epic endpoint returns partial data | Store only returned resources and mark metadata as incomplete. |

## Prototype Output

For the capstone prototype, document the OAuth flow and simulate the token-backed FHIR calls using Epic sandbox data or synthetic FHIR fixtures. Production use requires an Epic customer environment and approved app configuration.

## Sources

- Epic on FHIR documentation: https://fhir.epic.com/Documentation
- open.epic FHIR interface overview: https://open.epic.com/interface/FHIR
- SMART App Launch scopes and launch context: https://hl7.org/fhir/smart-app-launch/scopes-and-launch-context.html
