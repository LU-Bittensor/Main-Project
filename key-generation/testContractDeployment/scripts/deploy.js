const hre = require("hardhat");

async function main() {
  const [deployer] = await hre.ethers.getSigners();
  console.log("Deploying with account:", deployer.address);

  const Contract = await hre.ethers.getContractFactory("DataAccessControl");
  const contract = await Contract.deploy("medilink-record-001"); // dataIdentifier
  await contract.waitForDeployment();

  console.log("CONTRACT_ADDRESS=" + await contract.getAddress());
  // Hardhat account #0 private key is always this in local dev:
  console.log("OWNER_PRIVATE_KEY=0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80");
}

main().catch((e) => { console.error(e); process.exit(1); });
