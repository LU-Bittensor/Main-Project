# Test the per-recipient, per-record contract flow

The frontend now calls the existing DH and Solidity challenge-response flow:

1. Owner selects a recipient and records, then clicks **Grant**.
2. The API derives a separate X25519/HKDF credential for each recipient–record grant using `DHkey_share.py`.
3. In blockchain mode, it deploys a new `DataAccessControl.sol` contract with an owner-only permanent kill switch for each pair and calls `registerCommitment(hash(shared_secret))`.
4. It persists each contract address and both transaction hashes. A grant becomes active only after commitment registration succeeds.
5. On every recipient **Check access**, the API derives the recipient's matching credential and sends `requestChallenge()` and `verifyAccess(compute_response(secret, nonce))` to that record's contract. It requires an `AccessGranted` event before reporting success.

Selecting **All records** creates one grant/contract per current record, not one contract for the entire selection. Sharing the same record with a different recipient deploys another contract. Repeating an existing active/pending pair reuses it. Re-granting after revocation creates a new grant/contract and a new derived credential.

The original hash verification flow and interactive DH CLI are preserved; Solidity now includes the permanent kill switch. Noninteractive helpers were added to `DHkey_share.py` so browser requests do not require terminal password prompts.

## Start the full local flow

Use three terminals. All paths below begin at the repository root unless stated otherwise.

**Terminal 1 — local blockchain**

```sh
cd Main-Project/key-generation/testContractDeployment
npm install
npx hardhat compile
npx hardhat node --hostname 127.0.0.1
```

If a Hardhat node is already running on port 8545, reuse it. You do **not** need to run `scripts/deploy.js`: the API deploys contracts when you click Grant. There is no shared `CONTRACT_ADDRESS` setting for the API anymore.

**Terminal 2 — Python API with blockchain enabled**

```sh
cd Main-Project/key-generation
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

export RPC_URL=http://127.0.0.1:8545
# Public Hardhat account #0 key: local testing ONLY, never use with real assets.
export OWNER_PRIVATE_KEY=0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80
export FUND_LOCAL_WALLETS=1
export GRANT_DATA_DIR="$PWD/.grant-pairs-local"
.venv/bin/python grant_api.py --dev --chain --auto-data-dir
```

Alternatively, run `bash load_API_bc.sh` from this directory. The launcher automatically selects a child directory under `GRANT_DATA_DIR` (default `.grant-pairs-local`) for the current Hardhat instance and contract build, and prints the path. Restarting only the API reuses the same data. Restarting/resetting Hardhat or changing the contract build selects fresh test data; create the grants again. Old directories are preserved. This avoids the configuration-mismatch error without bypassing the database safety check. The `--auto-data-dir` option requires `--dev --chain` and Hardhat chain ID 31337.

The operator pays for deployments and commitment registration. Each enrolled user has a separate encrypted custodial EVM private key; the recipient's key signs their challenge and verification transactions. `FUND_LOCAL_WALLETS=1`, together with `--dev`, uses Hardhat's test-only balance RPC to fund those wallets. Automatic funding is refused outside chain ID 31337. This does not spend or transfer real funds.

**Terminal 3 — frontend**

```sh
cd Main-Project/frontend
npm install
VITE_GRANT_DEMO=true npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

Skip dependency installation if already installed. Open <http://127.0.0.1:5173/>.
`--strictPort` prevents Vite switching to 5174, which the API's default CORS settings do not allow. If 5173 is occupied, stop the old frontend or reuse it. Keep the Python terminal running too.

## Browser walkthrough

1. In the header, select **Test as → Demo patient**. The **Records** page shows **Demo lipid panel** (`demo-lab`) and **Demo chest X-ray** (`demo-imaging`) from the API. Open **Access ledger** in the navigation (`/access`) and confirm it says **Blockchain connected · one contract per recipient and record**.
2. Select **Demo clinician**, choose **All records**, and click **Grant**.
3. Expect two grant cards: **Demo lipid panel** and **Demo chest X-ray**, each active with a **different contract address**. Expand each transaction detail to inspect its deployment and commitment hashes.
4. Select **Test as → Demo clinician**. Open **Records** to see the two shared records, then return to **Access ledger**. The selected account persists across navigation and refreshes in this browser tab. Click **Check access** on the lab card. Expect **AccessGranted confirmed on-chain** and a verification transaction hash.
5. Click it again. Expect a **different verification transaction hash**: every check submits a new challenge and response.
6. Check the imaging card. Its transactions target the other contract.
7. Switch back to the patient and grant just **Lab report** again. It reuses the existing active lab pair rather than deploying another contract.
8. Share **Lab report** with **Unrelated user**. This creates a third contract, distinct from the clinician's lab contract.
9. Revoke the clinician's lab grant. Expect **Status: revoked**, **Contract permanently disabled**, and a **Revocation transaction** hash. The lab contract now rejects challenges and verification directly; the clinician's next API lab check is also denied. Their imaging check still succeeds. Switch to the clinician and open **Records**: only the imaging record remains (use **Refresh records** if the page was already open).
10. Restart the API with the same data directory and key configuration: the grant addresses and wallets persist.

**Revocation blocks the API and permanently disables the pair contract.** The owner clicks Revoke; the API first persists `revoking` (access denied), then the operator sends `killContract()`. Only a confirmed `isKilled == true` marks a deployed contract `revoked`. The UI displays the kill transaction hash. Deactivation prevents new challenges, commitment updates, and verification of challenges issued before the kill. It does not erase the contract, its history, or previously downloaded files.

A timeout or RPC failure leaves the grant **revoking**, with API access blocked. **Refresh** checks status without sending transactions. **Retry revocation** submits the kill if no previous kill transaction is still pending. Failed/reverted kills never restore API access. If the deployment itself is pending, retry revocation after it mines; the API will disable that contract without finishing setup. If deployment reverted or never started, there is no contract to kill, so API revocation completes alone. Off-chain mode also has no contract to disable.

**Existing contracts require redeployment.** The old contracts have no kill method and cannot gain one by recompiling. Run `npm install` in `testContractDeployment` to install the pinned OpenZeppelin dependency, then `npx hardhat compile`. Start the API with a fresh data directory such as `GRANT_DATA_DIR="$PWD/.grant-pairs-kill-chain"` and create new grants. Existing data is left untouched; the API rejects a database whose recorded deployment bytecode differs. The new SQLite columns migrate automatically for compatible databases.

The API operator is the contract owner. Keep that ownership while using API-managed revocation: transferring or renouncing it prevents the configured operator from killing the contract. The permanent flag remains set across ownership changes; there is no reactivation function. Re-granting creates a new contract.

The Records page and access ledger use the same two backend fixtures (`demo-lab`, `demo-imaging`) in local mode. The old mock records, provider patient list, and pretend upload form have been removed. Recipients see only records with active grants. This displays record metadata and verifies access; it does not download or encrypt medical files.

## Off-chain demo

To test only DH, persistence, and API authorization without Hardhat:

```sh
cd Main-Project/key-generation
.venv/bin/python grant_api.py --dev
```

Use a terminal without the blockchain data directory setting, or choose a separate directory. The frontend explicitly says **Off-chain demo** and access results say no contract was called. This mode is not a blockchain test.

## Data, resets, and interrupted setup

- This version uses `pair-grants.sqlite3` and defaults to `.grant-pairs-dev` in demo mode or `.grant-pairs-data` otherwise. Older `grants.sqlite3` data is left untouched; prior aggregate grants are not imported into the new per-pair model.
- Back up the SQLite database and its matching master key together. In local mode the key is generated once in `master.key` under the data directory. Private DH keys, EVM wallet keys, and shared credentials are encrypted at rest and never returned to the browser.
- Use different data directories for off-chain and blockchain modes. The service rejects a database with a different recorded auth/chain/operator/bytecode configuration.
- Restarting Hardhat resets its contracts. Recompile if necessary and start the API with a fresh directory such as `GRANT_DATA_DIR="$PWD/.grant-pairs-chain-2"`. Old SQLite addresses cannot restore deleted blockchain state.
- If setup times out, the grant remains **pending**. Click **Refresh** to check receipts. If only deployment completed, click **Resume setup** to register the commitment. Persisted transaction hashes prevent duplicate deployments or registrations during retry/restart.
- Refresh never submits new blockchain transactions. Deployment alone never activates a grant. A reverted setup is marked **failed**; Grant can create a new attempt.
- A permanently dropped transaction may need manual operator reconciliation. The API will not silently replace an unknown transaction or report it as confirmed.
- A revoking or revoked grant can never resume setup or regain access. If its deployment later mines, retry revocation to kill it. New grants for that pair wait until revocation completes.

## Command-line walkthrough

With the API running:

```sh
curl -s http://127.0.0.1:5001/api/health

curl -s http://127.0.0.1:5001/api/grants \
  -H 'Authorization: Bearer demo-patient' \
  -H 'Content-Type: application/json' \
  -d '{"recipient_id":"clinician","record_ids":["demo-lab","demo-imaging"]}'
```

The response is `{ "grants": [...] }`, with one item per record. Copy a grant ID:

```sh
export GRANT_ID='paste-one-grant-id'
curl -s -X POST "http://127.0.0.1:5001/api/grants/$GRANT_ID/access" \
  -H 'Authorization: Bearer demo-clinician'

# Resume a pending setup as its owner.
curl -s -X POST "http://127.0.0.1:5001/api/grants/$GRANT_ID/resume" \
  -H 'Authorization: Bearer demo-patient'

# Revoke in the API and permanently disable the pair contract.
curl -s -X DELETE "http://127.0.0.1:5001/api/grants/$GRANT_ID" \
  -H 'Authorization: Bearer demo-patient'
```

DELETE returns HTTP 202 while disablement is pending, or HTTP 200 when revocation is complete. Grant responses include `kill_tx` and `contract_killed`.

Successful on-chain checks return `chain_verified: true`, `contract_address`, `challenge_tx`, and `verify_tx`. Off-chain checks return `chain_verified: false`.

`operation-flow/owner_setup.py` also calls this API, using `ACCESS_TOKEN`, `RECIPIENT_ID`, and comma-separated `RECORD_IDS`. `user_access.py` uses `ACCESS_TOKEN` and `GRANT_ID`. `GRANT_API_URL` defaults to port 5001. `key_server.py --dev --chain` is an alias for the API entry point.

## Automated tests

From `Main-Project/key-generation`:

```sh
.venv/bin/python -m pytest -q
# With the contract compiled and local Hardhat already running:
RUN_CHAIN_TESTS=1 .venv/bin/python -m pytest -q
```

The live integration test deploys multiple pair contracts, checks independent addresses, duplicate/overlapping selections, repeated recipient-signed challenge transactions, restart persistence, and on-chain plus API revocation. Unit tests also cover kill timeouts/reverts, RPC failure, already-killed contracts, and revocation while deployment is pending.

From `Main-Project/key-generation/testContractDeployment`:

```sh
npx hardhat test
```

From `Main-Project/frontend`:

```sh
npx tsc --noEmit
npx eslint src/components/AccessPanel.tsx src/lib/grant-api.ts
npm run build
```

## Existing Supabase accounts

Start the API without `--dev`. Set `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, and `GRANT_MASTER_KEY` (32 random bytes encoded as hex, saved securely and reused across restarts). Keep `RPC_URL`, `OWNER_PRIVATE_KEY`, and `--chain` to use contracts. The entry point does not automatically load `.env` files.

Start the frontend with `VITE_GRANT_DEMO` unset/false, plus its usual `VITE_SUPABASE_URL` and `VITE_SUPABASE_PUBLISHABLE_KEY`. Optional `VITE_GRANT_API_URL` defaults to `http://127.0.0.1:5001`. `FRONTEND_ORIGINS` on the API is a comma-separated origin allowlist; defaults are localhost/127.0.0.1 port 5173.

The recipient first signs in and opens `/provider` or `/` to enroll. The patient then signs in, opens `/access`, and grants access to records already saved in Supabase with their `owner_id`. The Records page displays API records; uploads are not connected yet, so use existing database test rows. Every request verifies the Supabase token; record ownership queries use that same token and RLS. Grants, keys, and contract addresses are in SQLite, not the legacy Supabase `grants` table.

Without local test funding, the recipient's custodial EVM address must have gas funds. Its public address is shown under **Recipient transaction wallet**. The operator wallet also needs deployment gas. No automatic real-network funding is performed.

## Prototype boundaries

The requested original Solidity verification is preserved. Its commitment and nonce are public, so anyone can compute its accepted response without knowing the DH secret. Separate contracts do not fix that limitation. API authentication, ownership, and revocation checks remain in place; an `AccessGranted` event alone is not secure proof of secret possession. Do not use this prototype for real medical data. Future ZK verification can replace that check while keeping the per-pair deployment model.

The service currently manages both parties' private keys; this is a custodial integration, not device-only key custody. The API operator deploys each contract on behalf of the authenticated owner. It is a single-process prototype; do not run multiple API workers against the same SQLite database/transaction signer.

File encryption, Hippius upload/download, and issuing record-specific URLs remain separate work. The standalone original `DHkey_share.py` CLI still uses its configured `CONTRACT_ADDRESS` and Hippius settings; install `boto3` if using its URL-generation path. The frontend path does not call `run_demo()` or use that CLI's global contract address.

Ownership reference: [OpenZeppelin Ownable](https://docs.openzeppelin.com/contracts/5.x/access-control#ownership-and-ownable). Its constructor sets the owner; `owner()` is a getter, not an assignable state variable in the derived contract.
