# Local Hardhat (AccessRegistry + MediLinkFaucet)

Hardhat copies authoritative Solidity from `backend/contracts/` on config load. Prefer deploying with the Python wrapper from the parent folder (writes `deployment.json` / exports):

```shell
npx hardhat node --hostname 127.0.0.1
# other terminal:
cd .. && ./deploy_evm_contracts.sh hardhat
```

Or Hardhat-only (prints addresses; does not write `deployment.json`):

```shell
npx hardhat compile
npx hardhat run scripts/deploy.js --network localhost
npx hardhat test
```
