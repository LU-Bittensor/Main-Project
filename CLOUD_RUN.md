# MediLink API on Cloud Run

The frontend stays on Netlify. Build from **Main-Project**, not backend/ or the outer Hippius SDK repository. The root Dockerfile and backend/Dockerfile both require this same context. The image includes Flask, Gunicorn, Epic's shared Python modules and the compiled AccessRegistry artifact. It excludes local databases, .env files and frontend assets, and runs as a non-root user. Do not run start_backend.sh inside the container: that starts a local test blockchain.

## Runtime configuration

Configure these in Cloud Run Variables & Secrets before deployment. Store secret values in Secret Manager and grant the runtime service account access. Local .env files are deliberately not loaded/copied.

- SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY: same project as the frontend.
- SUPABASE_SECRET_KEY: server-only Supabase key.
- GRANT_MASTER_KEY: existing persistent key, 64 hex characters; never generate a new key on each restart.
- FRONTEND_ORIGINS=https://med1link.netlify.app
- DOCUMENT_STORAGE_BACKEND=hippius
- HIPPIUS_ACCESS_KEY_ID, HIPPIUS_SECRET_ACCESS_KEY, HIPPIUS_PATIENT_HASH_KEY, HIPPIUS_STORAGE_MASTER_KEY, HIPPIUS_NAMESPACE: reuse your existing values. Apply the repository's Supabase document/storage/client-encryption migrations first.
- EPIC_CLIENT_ID, EPIC_REDIRECT_URI: registered Epic client and exact frontend callback URL (e.g. https://med1link.netlify.app/external-import/callback). Register that URL with Epic.
- GRANT_CHAIN_ENABLED defaults to false. For chain grants set true, RPC_URL to a remotely reachable EVM endpoint, OWNER_PRIVATE_KEY to the operator key, and REGISTRY_ADDRESS to the deployed registry. Set the appropriate CHAIN_ID and faucet settings when using those features. Localhost RPC and public Hardhat test keys are not a remote blockchain deployment.

## Build and test locally

Start Docker Desktop, then from Main-Project:

```sh
docker build --platform linux/amd64 -t medilink-api:cloud .
docker run --rm --name medilink-api -p 8080:8080 \
  --env-file /absolute/path/to/cloud-runtime.env medilink-api:cloud
```

The env file must contain plain KEY=value entries without shell quotes or expansions. Keep it outside the repository. The keys listed above must be supplied, including those the local launcher normally generates/exports.

From another terminal:

```sh
curl --fail http://localhost:8080/api/health
curl -i -X OPTIONS http://localhost:8080/api/records \
  -H 'Origin: https://med1link.netlify.app' \
  -H 'Access-Control-Request-Method: GET' \
  -H 'Access-Control-Request-Headers: authorization,content-type'
```

Expect 200 and 204 respectively. Health is a startup check, not proof of Epic, S3 or blockchain connectivity.

## Deploy a prototype

From Main-Project, after configuring runtime values/secrets on the service:

```sh
gcloud run deploy mvp-backend --source . \
  --project peaceful-access-513919-e6 --region YOUR_REGION \
  --port 8080 --cpu 1 --memory 1Gi --concurrency 8 \
  --min-instances 1 --max-instances 1 --no-cpu-throttling \
  --allow-unauthenticated
```

Public invocation is required for browser CORS preflight; protected API routes still enforce Supabase authentication. Instance-based CPU permits background Epic fetching after the start request returns. Keeping an instance running incurs ongoing charges. Replace YOUR_REGION with your actual region. Existing runtime environment/secret settings are preserved by this command; it does not upload your local .env.

Set Netlify VITE_GRANT_API_URL to the returned HTTPS origin (no /api or trailing slash), then rebuild/redeploy the frontend. Repeat the health and preflight checks against that URL, followed by a real-account upload, Epic import, sharing and revocation check.

## Persistence limitation

This is a runnable prototype container, not a durable production migration. SQLite contains grants, download levels, enrolled users, Epic OAuth/job state; fallback vault files and pending Epic handoffs also depend on local state. Cloud Run's writable filesystem is ephemeral. One worker/instance reduces state fragmentation but does not preserve data through replacement, deployment or overlapping revisions. Download levels fail closed if their rows disappear, and unfinished Epic imports may need restarting.

Move SQLite-backed state to a durable transactional database and pending imports to an encrypted shared coordinator before relying on Cloud Run for persistent accounts/sharing workflows. Hippius object storage and Supabase catalog rows already persist independently, but they do not replace this local state. Do not put SQLite on a Cloud Storage FUSE mount as a workaround.

References: https://docs.cloud.google.com/run/docs/container-contract and https://docs.cloud.google.com/run/docs/configuring/billing-settings
