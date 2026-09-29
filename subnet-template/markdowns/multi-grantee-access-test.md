# Multi-grantee AccessRegistry test plan (Part B)

Extracted from the incentive + multi-grant plan. Scope: prove **multiple users** can hold simultaneous grants on one resource and prove access independently.

Related code:

- [`../key-generation/AccessRegistry.sol`](../../key-generation/AccessRegistry.sol)
- [`../key-generation/access_registry_client.py`](../../key-generation/access_registry_client.py)
- [`../key-generation/ehr_hippius.py`](../../key-generation/ehr_hippius.py)
- [`Receipts.md`](../../key-generation/markdowns/Receipts.md)
- [`access-registry-live-test.md`](access-registry-live-test.md)

Clarification: “posting new access registry keys” means the **owner posts per-grantee commitments** via `grantAccess` (after off-chain X25519 DH), not publishing PEM material on-chain.

---

## B1. Multi-grantee script / demo

Extend [`access_registry_client.py`](../../key-generation/access_registry_client.py) (or add `multi_grantee_demo.py`) to:

1. Generate **3** DH identities: `owner`, `user_a`, `user_b` (distinct X25519 PEMs).
2. Use **3 distinct H160** wallets (Hardhat accounts or funded test keys) for owner / A / B.
3. Encrypt once → `registerResource(resourceId, dataHash, dataCid)`.
4. For each grantee: DH → wrap DEK sidecar → `grantAccess(resourceId, grantee, keccak256(secret))`.
5. Assert on-chain:
   - `hasGrant(resourceId, A) == true`
   - `hasGrant(resourceId, B) == true`
   - `commitments[resourceId][A] != commitments[resourceId][B]`
6. Prove access separately: A and B each `requestChallenge` + `verifyAccess` → both get `AccessGranted`.
7. Negative check: wallet C with no grant → `requestChallenge` reverts `"Not granted"`.
8. Optional: `revokeAccess` for A → A fails challenge; B still succeeds.

Wire Hippius wraps through [`ehr_hippius.py`](../../key-generation/ehr_hippius.py) when credentials exist; otherwise local ciphertext + wrap files are enough for registry assertions.

---

## B2. Validator observation of multi-user access

With `validatorv1` on live logs (`mock_events: false`):

- One `ResourceRegistered` → WRITE/UPDATE receipt
- Two `AccessGranted` events → two AUDIT receipts from A and B
- Miner store has both AUDIT CIDs; challenge round still scores

Note: `AccessGrantSet` is **skipped** by the validator today (see [`Receipts.md`](../../key-generation/markdowns/Receipts.md)) — grants alone do not drive incentives; successful `verifyAccess` does.

---

## B3. Runbook checklist

| Step | Pass? |
|---|---|
| Distinct owner / A / B H160s funded | |
| `registerResource` emits `ResourceRegistered` | |
| Two `grantAccess` → two `AccessGrantSet` | |
| A and B both `AccessGranted` | |
| Ungranted C cannot `requestChallenge` | |
| Optional revoke A; B still proves | |
| Validator emits WRITE + 2× AUDIT receipts | |

**Success criteria:** two distinct grantees hold simultaneous grants on one `resourceId`, both prove access, stranger cannot, and validator receipts reflect both AUDIT events.

---

## Hospital / RBAC note (design only)

The contract is a **flat per-H160 ACL**. Role-based hospital auth should live off-chain (IAM decides who gets `grantAccess`). Prefer **per-clinician** grants over shared “hospital keys”; shared role wallets collapse audit identity and raise custody risk. See [`Contract_Logic.md`](../../Contract_Logic.md) for the grant model.
