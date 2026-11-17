# Disposable API + Hardhat Cloud Run service

Build both images from Main-Project. Replace YOUR_REGION with your Cloud Run region. Create the Artifact Registry repository once (skip if it already exists):

```sh
gcloud artifacts repositories create medilink --repository-format=docker --location=YOUR_REGION --project=peaceful-access-513919-e6
gcloud builds submit . --config cloud/cloudbuild.yaml --substitutions=_REGION=YOUR_REGION --project=peaceful-access-513919-e6
```

In Cloud Run, edit the existing service and deploy a new revision:

1. Set the ingress container name to `api`, image to `YOUR_REGION-docker.pkg.dev/peaceful-access-513919-e6/medilink/api:hardhat`, and port to 8080. Preserve all Supabase, Hippius, Epic and GRANT_MASTER_KEY settings described in ../CLOUD_RUN.md.
2. Add `HARDHAT_SIDECAR=true` to **api**. Bootstrap automatically sets GRANT_CHAIN_ENABLED, RPC_URL, CHAIN_ID, OWNER_PRIVATE_KEY, REGISTRY_ADDRESS, FAUCET_ADDRESS and FAUCET_OPERATOR_PRIVATE_KEY for this test mode. Remove overrides for those values to avoid confusion. Do not use real-funded keys.
3. Add a second container named `hardhat` using `YOUR_REGION-docker.pkg.dev/peaceful-access-513919-e6/medilink/hardhat:demo`. Do not make it an ingress container. Allocate 1 CPU and 1 GiB memory to each container.
4. Add a startup probe to **hardhat**: HTTP GET `/ready`, port **8546**, period 2 seconds, timeout 1 second, failure threshold 120. It succeeds only after both contracts are deployed and the faucet is funded.
5. Set **api** to depend on **hardhat** in container startup order. Give api an HTTP startup probe on `/api/health`, port 8080, period 2 seconds, timeout 1 second, failure threshold 120.
6. Use instance-based billing (CPU always allocated), minimum instances 1, maximum instances 1, and concurrency 8. Allow public invocation so browser preflights and the restricted RPC bridge work; document APIs still require Supabase authentication. Send 100% of traffic to this revision; do not split traffic.

The sidecar binds Hardhat RPC to loopback 8545. Deployment metadata/health is available internally on 8546. Only the API is public. `/rpc` permits transaction reads, contract calls, estimation, and signed raw transactions; it rejects node administration, account enumeration, impersonation and unsigned transactions. This is a public test chain, not a production RPC service. Browser-origin checks are not authentication or rate limiting.

## Netlify production build variables

```dotenv
VITE_GRANT_API_URL=https://YOUR-CLOUD-RUN-SERVICE.run.app
VITE_EVM_RPC_URL=https://YOUR-CLOUD-RUN-SERVICE.run.app/rpc
VITE_EVM_CHAIN_ID=31337
VITE_GRANT_DEMO=false
```

Keep VITE_SUPABASE_URL and VITE_SUPABASE_PUBLISHABLE_KEY set to the same project as the API. Remove VITE_ACCESS_REGISTRY for this mode: the current frontend resolves the live registry through authenticated `/api/registry/resolve` on chain 31337. No private/operator/Hippius secrets belong in Netlify VITE variables. Rebuild and redeploy Netlify after updating them.

## Check

```sh
curl --fail https://YOUR-CLOUD-RUN-SERVICE.run.app/api/health
curl --fail https://YOUR-CLOUD-RUN-SERVICE.run.app/rpc -H 'Content-Type: application/json' \
  --data '{"jsonrpc":"2.0","id":1,"method":"eth_chainId","params":[]}'
```

Expect health chain=true and RPC result `0x7a69`. Sign in with two browser profiles, enroll/unlock both vaults, share a harmless document, preview/download it according to permission, and revoke. The faucet supplies test gas. Verify a `hardhat_reset` request to public `/rpc` returns 400.

Every sidecar replacement resets the chain. API bootstrap uses the Hardhat instance identity for a fresh local data directory; API memory/local SQLite and Epic jobs are disposable too. Saved Supabase/Hippius objects remain, but enrollments/grants/permission levels may need recreating. A sidecar restart while API stays alive requires redeploying the revision; do not continue using a stale API against a reset chain. Single-instance settings do not prevent replacements or temporary revision overlap. This setup is for controlled demonstrations, not durable production use.
