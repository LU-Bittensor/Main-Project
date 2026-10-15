
**Currently Receipted Incidents**

| **Action**                                           | **On-chain event**   | **Receipted today?**                  |
| ---------------------------------------------------- | -------------------- | ------------------------------------- |
| Owner uploads/registers a record                     | `ResourceRegistered` | **Yes**                               |
| Owner **adds** someone (`grantAccess`)               | `AccessGrantSet`     | **No** (skipped)                      |
| Owner **removes** someone (`revokeAccess`)           | `AccessRevoked`      | **No**                                |
| User starts challenge (`requestChallenge`)           | `ChallengeIssued`    | **No**                                |
| User **successfully proves access** (`verifyAccess`) | `AccessGranted`      | **Yes** — this is the access incident |
| User fails verify                                    | `AccessDenied`       | **No** by default                     |


