# MediLink subnet template

Bittensor subnet that turns **AccessRegistry** access events into a signed, challengeable audit ledger and miner weights. Encrypted EHR bytes stay off-chain (Hippius / local); the subnet stores **receipts**, not charts.

| Doc | Contents |
|-----|----------|
| [`markdowns/ARCHITECTURE.md`](markdowns/ARCHITECTURE.md) | How the system works (EVM → receipts → HTTP challenges → scoring) |
| [`markdowns/OPERATOR_GUIDE.md`](markdowns/OPERATOR_GUIDE.md) | Testnet 573 setup, Hardhat/945 deploy, e2e prove checklist |
| [`markdowns/INTEGRATIONS.md`](markdowns/INTEGRATIONS.md) | Epic SMART/FHIR, Hippius CLI, off-chain hash workflow |

**Run these binaries:** `miner.py` + `validator.py`  
**Config:** [`validator.config.yml`](validator.config.yml)  
**Registry / DH / deploy:** [`../key-generation/`](../key-generation/)

## Quick start

```bash
source /Users/aidanosullivan/YumaBittensor/bt_venv/bin/activate
cd /Users/aidanosullivan/YumaBittensor/Main-Project
pip install -r subnet/requirements.txt

# Full operator path (wallets, registry, miner, validator, unlock):
# → markdowns/OPERATOR_GUIDE.md
```

Offline incentive tests:

```bash
cd subnet
../../bt_venv/bin/python -m unittest tests.test_incentive_scoring tests.test_incentive_smoke -v
```

## Layout

```
subnet/
  validator.py        # EVM poll → receipts → challenges → SetWeights
  miner.py            # FastAPI axon: /receipt /batch /challenge/* /index
  protocol.py         # MessageSynapse + medical Synapse shapes
  validator.config.yml
  tests/
  markdowns/          # ARCHITECTURE · OPERATOR_GUIDE · INTEGRATIONS
```

## License

See repository root / project license terms.

## Epic sandbox API integration

Start with the [Epic setup guide](epic-setup.md). `epic_sandbox_setup.py` configures and checks the app, `epic_sandbox_live.py --profile expanded` retrieves Patient, Observation, Condition, MedicationRequest, and Encounter resources, and `epic_sandbox_report.py` verifies stored data and summarizes runs. The OAuth client uses PKCE and the existing ingestion/storage/digest pipeline.

See [implementation evidence and task status](epic-live-integration.md) and [the three tasks for October 19–25](epic-next-week-tasks.md). Registered-client live validation is complete for the defined sandbox scope. The offline demo and mocked tests are labeled separately from live API evidence.

Epic sandbox validation completed across three distinct test patients on October 23, 2026. See [live results and repeatable demonstration](epic-live-results.md) for counts, saved evidence, and the reviewed coverage limitations.
