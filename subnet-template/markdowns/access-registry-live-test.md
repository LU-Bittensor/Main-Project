# AccessRegistry live test (testnet 573)

Date: **2026-09-22**. Goal: emit real AccessRegistry events on Subtensor EVM (chain **945**) and confirm `validatorv1` polls them and `minerv1` stores receipts.

## Environment

| Piece | Value |
|---|---|
| Subnet | testnet **netuid 573** (`medilink`) |
| AccessRegistry | `0xCA8Cb3EbD9D1Be4d555cA5800Edd116D789660E1` |
| EVM RPC | `https://test.chain.opentensor.ai` (chain id **945**) |
| Owner / grantee H160 | `0xb18A75F10500655d9378C5f6f05B0c35611a8e84` (`access-registry` under `AidanColdKey`) |
| Miner | uid **1**, HTTP axon `128.180.243.98:8091` (+ local `127.0.0.1:8091` via `LOCAL_MINER_AXON`) |
| Validator | uid **2**, wallet `test-validator` / hotkey `default` |
| Miner store | `Main-Project/subnet-template/miner_store_v1.db` |
| Demo data CID | `ehr/demo/patient/record/v1/payload.bin` |
| Resource id | `0x244c503c000512b288077903d3d04dde51b0a91ff1460a6bb72161da07e3ae9e` |

Validator polls **`ResourceRegistered`** and **`AccessGranted`** only. It intentionally skips `AccessGrantSet`, `AccessRevoked`, and `ChallengeIssued` (see `Receipts.md` / `validatorv1.py`).

---

## What we did (chronological)

### 1. Confirmed nodes

- Miner health: `GET http://127.0.0.1:8091/health` → `ok`, uid 1.
- Validator was initially running with `--no-mock-events`, registry address set, `--evm-start-block 8063400`, and `LOCAL_MINER_AXON=127.0.0.1:8091`.

### 2. Unlocked the EVM key and ran the registry client

`access_registry_client.py` needs `OWNER_PRIVATE_KEY` (H160). That is **not** the X25519 `ehr_*.pem` keys.

Steps that worked:

1. Decrypt `~/.bittensor/wallets/AidanColdKey/evmkeys/access-registry` (Ethereum keystore V3) with the AidanColdKey password via `eth_account.Account.decrypt`.
2. Set `CHAIN_ID=945`, `RPC_URL=https://test.chain.opentensor.ai`.
3. Run the demo path with DH passphrase `demo` (non-interactive), because the script’s `getpass` fails in a non-TTY agent shell:
   - `registerResource` → **ResourceRegistered**
   - `grantAccess` → **AccessGrantSet**
   - `requestChallenge` + `verifyAccess` → **AccessGranted**

### 3. On-chain results

| Step | Event | Tx / block |
|---|---|---|
| Register | `ResourceRegistered` | tx `e1e306bb…763`, **block 8063560**, 1 log |
| Grant | `AccessGrantSet` | tx `522f4368…e34`, **block 8063561**, 1 log |
| Prove | `AccessGranted` | completed after challenge/verify; client printed `Result: granted` |

Confirmed via `eth_getTransactionReceipt` and a targeted `eth_getLogs` for topic  
`keccak256("ResourceRegistered(bytes32,address,bytes32,string)")`.

---

## What failed (and why)

### A. `btcli evm key export` without a password path

- Piped export returned empty; `--wallet-password-file` is **not** accepted on `btcli evm key export`.
- **Fix:** decrypt the keystore JSON directly with the coldkey password (user-provided for this session).

### B. DH PEM passphrase prompt (`EOFError`)

- `run_demo()` calls `DHUser.from_script(..., overwrite=True)` which uses `getpass` twice.
- Non-interactive shells cannot answer → `EOFError`.
- **Fix:** call `DHUser.from_script(..., passphrase="demo")` (or load existing `owner_private.pem` / `user_private.pem` with that passphrase).

### C. RPC `429 Too Many Requests` on `requestChallenge`

- After register + grant, waiting for the challenge tx receipt hit public RPC rate limits.
- Register/grant had already landed; only the prove path was incomplete.
- **Fix:** retry `request_access` with backoff → `AccessGranted` succeeded.

### D. Validator process died (`exit_code=1`) before seeing events

- First live validator (started ~15:07) crashed ~15:23 while still chunk-walking from cursor **8063400** (128-block `eth_getLogs` windows).
- At crash it was still around block **~8063528**; demo txs were at **8063560–8063561**.
- So even though events were on-chain, that process never reached them.
- **Fix:** restart validator with `--evm-start-block 8063555` (just before the demo blocks).

### E. Event-type mismatch (expected, not a bug)

- Client `grantAccess` emits **`AccessGrantSet`**.
- Validator does **not** receipt that event.
- Receipts come from **`ResourceRegistered`** (register) and **`AccessGranted`** (successful verify). That is by design.

### F. Agent sandbox / approval friction (ops only)

- Exporting raw private keys from the agent shell requires elevated approval; password-in-chat was used once for this test. Prefer local terminal + clipboard/`Passfile` for future runs; rotate the coldkey password if it was shared in chat.

---

## How it works now

```text
Owner H160  --registerResource-->  AccessRegistry (945)
            --grantAccess-------->  (AccessGrantSet; not receipted)
User H160   --requestChallenge-->
            --verifyAccess------->  AccessGranted log

validatorv1  --eth_getLogs------>  ResourceRegistered + AccessGranted
             --build_receipt---->  signed receipt JSON
             --HTTP POST-------->  miner /receipt (and later /batch, challenges)

minerv1      --verify sig------->  insert receipts / actor_tips / batches
             SQLite: miner_store_v1.db
```

Current processes (after restart):

- **Miner:** still on axon `8091`, health OK.
- **Validator:** restarted with live registry + `LOCAL_MINER_AXON=127.0.0.1:8091` + start block `8063555`.
- Within ~1s of EVM poller ready, validator posted receipts; miner returned **200 OK** on `/receipt`. Later cycles also hit `/batch`, `/challenge/pin`, `/challenge/merkle`, `/index` successfully.

Duplicate POSTs to both `127.0.0.1:8091` and `128.180.243.98:8091` are expected: local override plus the metagraph axon IP (same miner process).

---

## What the validator saw

On restart (`cursor=8063554`):

1. **`ResourceRegistered`** (block **8063560**)
   - `owner` / actor: `0xb18A75F10500655d9378C5f6f05B0c35611a8e84`
   - `dataCid`: `ehr/demo/patient/record/v1/payload.bin`
   - `resourceId`: `0x244c503c000512b288077903d3d04dde51b0a91ff1460a6bb72161da07e3ae9e`
   - Mapped to commitment event with `hippius_status=SUCCESS`, no op hint → first tip for actor → **`op_type=WRITE`**

2. **`AccessGranted`** (after verify; polled shortly after)
   - `requester` / actor: same H160
   - `op_type_hint=AUDIT` → receipt **`op_type=AUDIT`**
   - `hippius_status=SUCCESS`
   - `data_cid` derived from on-chain `dataHash` (not the string CID)

3. **`AccessGrantSet`** — present on chain at block **8063561**, **not** consumed by the poller.

Fanout (from validator logs):

- `POST http://127.0.0.1:8091/receipt` → 200
- `POST http://128.180.243.98:8091/receipt` → 200  
  (repeated for each receipt / axon target)

Receipt envelope fields built by `build_receipt` include: `spec_version`, `tx_id` (UUID), `timestamp_ns`, `actor_evm_addr`, `op_type`, `data_cid`, `prev_cid`, `hippius_status`, `validator_hotkey`, `validator_sig`, and `resource_id` when present.

---

## What the miner stored

Miner logs: `in handle_receipt` for each accepted POST. Persistence is SQLite at:

`Main-Project/subnet-template/miner_store_v1.db`

### `receipts` (2 rows)

| Field | Receipt 1 (WRITE) | Receipt 2 (AUDIT) |
|---|---|---|
| `receipt_cid` | `bafydc59437c2c9e995b95314feee16788a63f6e2175731df4d58c26` | `bafya17b34d7abb7f296c7cdcecbc0538327477f9a5a9f4a72a1f5a1` |
| `miner_cid` | same as `receipt_cid` | same as `receipt_cid` |
| `actor_evm_addr` | `0xb18A75F10500655d9378C5f6f05B0c35611a8e84` | same |
| `op_type` | **WRITE** | **AUDIT** |
| `hippius_status` | SUCCESS | SUCCESS |
| `prev_cid` | (empty — chain start) | `bafydc59437c2c9e995b95314feee16788a63f6e2175731df4d58c26` |
| `batch_id` | `bafya40048596c03679b60695769aeea5105c01a36987ac0626b635e` | `bafy2762c051874e0befc098206d039f37e770c7341b6ee5fcade4a8` |

`data_cid` from the validator payload is **not** a column on `receipts`; it lives inside the content-addressed blob keyed by `receipt_cid` / `miner_cid`. The chain tip metadata is in `actor_tips`.

### `actor_tips`

| Field | Value |
|---|---|
| `actor_evm_addr` | `0xb18A75F10500655d9378C5f6f05B0c35611a8e84` |
| `tip_cid` | `bafya17b34d7abb7f296c7cdcecbc0538327477f9a5a9f4a72a1f5a1` (AUDIT tip) |
| `receipt_count` | **2** |
| `oldest_cid` | `bafydc59437c2c9e995b95314feee16788a63f6e2175731df4d58c26` |

### `batches` (2 rows after fanout)

| `batch_index` | `batch_cid` | `receipt_count` | `merkle_root` | `prev_batch_cid` |
|---|---|---|---|---|
| 0 | `bafya40048596c03679b60695769aeea5105c01a36987ac0626b635e` | 1 | `31cd2f01693e31c63c38035d7c7ae62245098b9574028e367ea458babfc749e2` | (none) |
| 1 | `bafy2762c051874e0befc098206d039f37e770c7341b6ee5fcade4a8` | 1 | `6650d558ef427cc144654b9c7557107e3fe8d9a7e2f12a429084e599908875c1` | batch 0 |

`batch_tips.scope=global` tip points at batch 1.

Miner also answered follow-up validator challenges (`/challenge/pin`, `/challenge/merkle`, `/index`) with HTTP 200 after batching.

---

## Re-run checklist

```bash
source /Users/aidanosullivan/YumaBittensor/bt_venv/bin/activate

# Miner (if needed)
cd Main-Project/subnet-template
python minerv1.py --netuid 573 --wallet.name test-miner --wallet.hotkey default \
  --subtensor.network test --axon.port 8091 --axon.ip 128.180.243.98 --logging.info

# Validator — set --evm-start-block near tip (or last known good) to avoid long catch-up
LOCAL_MINER_AXON=127.0.0.1:8091 python validatorv1.py \
  --config validator.config.yml --netuid 573 --network test \
  -w test-validator -H default --no-mock-events \
  --access-registry 0xCA8Cb3EbD9D1Be4d555cA5800Edd116D789660E1 \
  --evm-start-block <near_current_block>

# Emit events (in another shell; unlock access-registry key yourself)
cd Main-Project/key-generation
export OWNER_PRIVATE_KEY=0x...   # from keystore decrypt or btcli export --show
export USER_PRIVATE_KEY=$OWNER_PRIVATE_KEY
export CHAIN_ID=945 RPC_URL=https://test.chain.opentensor.ai
# Prefer a small Python driver that passes passphrase= to DHUser if no TTY
```

Inspect miner DB:

```bash
sqlite3 Main-Project/subnet-template/miner_store_v1.db \
  "SELECT receipt_cid, op_type, actor_evm_addr, prev_cid, batch_id FROM receipts;"
```

---

## Bottom line

Live registry events on chain **945** were produced; after restarting the validator near those blocks, it decoded **`ResourceRegistered` → WRITE** and **`AccessGranted` → AUDIT**, POSTed signed receipts to the miner, and the miner persisted **2 receipts**, **1 actor tip**, and **2 merkle batches** in `miner_store_v1.db`.
