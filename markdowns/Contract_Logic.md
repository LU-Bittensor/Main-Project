# Contract_Logic

How MediLink’s **AccessRegistry** is created, how access works on-chain, how you change who can access what, and how **subnet validators** poll the logs those interactions leave on the chain.

Related code:

- [`key-generation/AccessRegistry.sol`](key-generation/AccessRegistry.sol)
- [`key-generation/deploy_access_registry.py`](key-generation/deploy_access_registry.py)
- [`key-generation/access_registry_client.py`](key-generation/access_registry_client.py)
- [`key-generation/ehr_hippius.py`](key-generation/ehr_hippius.py)
- [`Hippius_EHR_Storage.md`](Hippius_EHR_Storage.md)
- [`subnet/validator.py`](../subnet/validator.py)
- [`subnet/markdowns/ARCHITECTURE.md`](../subnet/markdowns/ARCHITECTURE.md)
- [`subnet/markdowns/OPERATOR_GUIDE.md`](../subnet/markdowns/OPERATOR_GUIDE.md)

---

## 1. Is a contract generated per user?

**No.** There is **one** `AccessRegistry` contract for the whole system (per deployment / network).

| What | One per… | Where |
|---|---|---|
| `AccessRegistry` bytecode + address | **network deployment** (e.g. Hardhat once, or testnet once) | Deployed with `deploy_access_registry.py` |
| Resource entry | **`resourceId`** (one health record), not a person | `resources[resourceId]` |
| Grantee commitment | **`(resourceId, grantee H160)`** | `commitments[resourceId][grantee]` |
| Pending challenge | **`(resourceId, user H160)`** for ~5 minutes | ephemeral storage |
| X25519 keypairs + shared secret | **owner–grantee relationship** off-chain | PEM files / app secrets — **never** stored on-chain |

Validators poll **a single contract address**. That keeps `eth_getLogs` cheap and avoids deploying a new contract every time a user or record appears.

---

## 2. How the contract is created (deploy flow)

```
AccessRegistry.sol
       │
       ▼
deploy_access_registry.py   (solc compile → deploy tx)
       │
       ▼
EVM chain (Hardhat 31337 / Subtensor EVM 945 / …)
       │
       ▼
deployment.json  →  CONTRACT_ADDRESS / ACCESS_REGISTRY
```

1. Compile `AccessRegistry.sol` (empty constructor — no constructor args).
2. Send a deploy transaction from a funded **H160** key (`OWNER_PRIVATE_KEY`).
3. Save the resulting address (e.g. `key-generation/deployment.json`).
4. Point apps and validators at that address.

After deploy, the contract is empty of resources. Metadata via `registerResource`; grants via `grantAccess`.

**Breaking change:** multi-grantee layout requires a **redeploy** (old single-commitment storage is incompatible).

---

## 3. End-to-end access flow (off-chain + on-chain)

```
Owner                     User                      AccessRegistry                 Gateway / Hippius
  │                         │                              │                              │
  │◄──── X25519 DH ────────►│  (off-chain; shared_secret)  │                              │
  │                         │                              │                              │
  │ PutObject ciphertext    │                              │                              │
  │ + wrap sidecar for User │──────────────────────────────┼─────────────────────────────►│
  │                         │                              │                              │
  │ registerResource(       │                              │                              │
  │   resourceId,           │                              │                              │
  │   dataHash, dataCid)    │─────────────────────────────►│ emit ResourceRegistered      │
  │                         │                              │                              │
  │ grantAccess(resourceId, │                              │                              │
  │   user, commitment)     │─────────────────────────────►│ emit AccessGrantSet          │
  │                         │                              │                              │
  │                         │ requestChallenge(resourceId) │                              │
  │                         │─────────────────────────────►│ emit ChallengeIssued         │
  │                         │                              │                              │
  │                         │ verifyAccess(resourceId,     │                              │
  │                         │   keccak256(commit‖nonce))   │                              │
  │                         │─────────────────────────────►│ emit AccessGranted / Denied  │
  │                         │                              │                              │
  │                         │◄──── only if AccessGranted ──┼── Presign payload + wrap ────┤
  │                         │      unwrap DEK, decrypt     │                              │
```

### Off-chain (key agreement + envelope)

1. Owner and user each have an X25519 keypair (`access_registry_client.py`).
2. They exchange **public** keys and derive the same `shared_secret` (DH + HKDF).
3. Owner encrypts EHR with a random **DEK**; wraps DEK for the user with `KEK_u = HKDF(secret, info=resourceId‖grantee)` (`ehr_hippius.py`).
4. Neither private keys nor the raw shared secret are written to the chain.

### On-chain (authorization + audit logs)

1. **Owner** calls `registerResource(resourceId, dataHash, dataCid)` — metadata only → **`ResourceRegistered`**
2. **Owner** calls `grantAccess(resourceId, grantee, commitment)` where `commitment = keccak256(sharedSecret)` → **`AccessGrantSet`**
3. **User** calls `requestChallenge(resourceId)` (requires `commitments[id][msg.sender] != 0`) → **`ChallengeIssued`**
4. **User** calls `verifyAccess(resourceId, response)` against **that user’s** commitment → **`AccessGranted`** / **`AccessDenied`**

The subnet observes logs; the gateway only serves data after `AccessGranted`.

---

## 4. What lives on-chain (and what does not)

### Storage

```solidity
struct Resource {
    address owner;
    bytes32 dataHash;    // keccak256(payload ciphertext)
    string  dataCid;     // Hippius payload key for WRITE receipts
    bool    exists;
}

mapping(bytes32 => Resource) public resources;
mapping(bytes32 => mapping(address => bytes32)) public commitments; // resourceId => grantee => keccak256(secret)
mapping(bytes32 => mapping(address => bytes32)) private pendingChallenge;
mapping(bytes32 => mapping(address => uint256)) private challengeExpiry;
```

### Events (what validators poll)

| Event | When | Indexed fields | Validator receipt? |
|---|---|---|---|
| `ResourceRegistered` | Owner registers/updates metadata | — | Yes → usually `WRITE` / `UPDATE` |
| `AccessGrantSet` | Owner grants a grantee | `resourceId`, `grantee` | **Skip** by default (optional AUDIT later) |
| `AccessRevoked` | Owner clears a grant | `resourceId`, `grantee` | Skip or AUDIT |
| `ChallengeIssued` | User asks for a nonce | `resourceId`, `requester` | **No** |
| `AccessGranted` | User proves their secret | `resourceId`, `requester` | Yes → `AUDIT` |
| `AccessDenied` | Wrong response | `resourceId`, `requester` | Optional (default skip) |

---

## 5. How to modify access controls per user

### Grant / revoke

| Goal | What to do |
|---|---|
| **Authorize a user for a record** | DH with that user → wrap DEK sidecar on Hippius → `grantAccess(resourceId, grantee, commitment)` |
| **Revoke that user** | `revokeAccess(resourceId, grantee)` + delete/overwrite their wrap object (ciphertext unchanged) |
| **Authorize another user** | Same as grant for the new H160 — **no** overwrite of other commitments |
| **Many users for one record** | Supported: `commitments[resourceId][grantee]` per user; one `resourceId` / one payload |
| **Change data pointer** | Owner re-registers with new `dataHash` / `dataCid` |

Owner gate: first `registerResource` sets `owner`; later metadata updates and all grants require that owner.

### What users do *not* configure on-chain

- Users cannot grant themselves; only the resource owner calls `grantAccess`.
- `requestChallenge` requires an existing commitment for `msg.sender`.

---

## 6. How subnet validators poll these events

Design goal: **contracts emit logs → validators poll one address → build MediLink receipts.**

```
User txs on AccessRegistry
        │
        │  logs: ResourceRegistered / AccessGranted (/ AccessDenied)
        ▼
EVM RPC  eth_getLogs({
           address: ACCESS_REGISTRY,   // one address
           fromBlock: last_evm_block + 1,
           toBlock:   tip - REORG_DEPTH,
           topics: [ ResourceRegistered | AccessGranted | … ]
         })
        │
        ▼
validator.poll_evm_commitments()
        │  decode → CommitmentEvent (actor, data_cid, op_type_hint, …)
        ▼
build_receipt() → sign → broadcast to miners → batch / challenge / weights
```

`AccessGrantSet` / `AccessRevoked` are **not** in the default topic filter (skip noise). Document a future knob if richer grant audit is needed.

### Mapping log → MediLink receipt

| Chain event | Typical `op_type` | Actor | `data_cid` |
|---|---|---|---|
| `ResourceRegistered` | `WRITE` or `UPDATE` | owner | event `dataCid` (Hippius payload key) or hash→CID |
| `AccessGranted` | `AUDIT` | requester | from `dataHash` |
| `AccessDenied` | skipped by default | — | — |
| `ChallengeIssued` | never | — | — |
| `AccessGrantSet` / `AccessRevoked` | skipped by default | — | — |

### Why this matches “easy to poll”

- **One filter address** — no discovery of per-user contracts.
- **Indexed `resourceId` / `requester`** on access events.
- **Rich enough data in the log** (`dataHash` on `AccessGranted`) so validators need not `eth_call` per event for basic AUDIT receipts.
- **Terminal outcomes only** for receipts — avoids doubling load on every challenge or grant-set.

---

## 7. Mental model (summary)

1. **Deploy once** → one `AccessRegistry` address.
2. **Per health record** → one `resourceId` + Hippius payload ciphertext.
3. **Per authorized user** → off-chain DH + wrap sidecar + on-chain commitment.
4. **Per access attempt** → challenge/verify txs that emit logs.
5. **Validators** → `eth_getLogs` on that address every ~12s → immutable audit receipts on the subnet.

That is the intended loop: **user interaction → on-chain log → subnet poll → monitored ledger.** EHR bytes stay on Hippius encrypted; see [`Hippius_EHR_Storage.md`](Hippius_EHR_Storage.md).
