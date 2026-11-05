/**
 * Deploy AccessRegistry + MediLinkFaucet on the configured Hardhat network.
 * Prefer ../deploy_evm_contracts.sh for multi-chain deploys (writes deployment.json).
 */
const hre = require("hardhat");

async function main() {
  const [deployer] = await hre.ethers.getSigners();
  console.log("Deploying with account:", deployer.address);

  const Registry = await hre.ethers.getContractFactory("AccessRegistry");
  const registry = await Registry.deploy();
  await registry.waitForDeployment();
  const registryAddress = await registry.getAddress();
  console.log("ACCESS_REGISTRY=" + registryAddress);

  const drip = hre.ethers.parseEther("0.01");
  const minBalance = hre.ethers.parseEther("0.005");
  const cooldown = 86400n;
  const Faucet = await hre.ethers.getContractFactory("MediLinkFaucet");
  const faucet = await Faucet.deploy(drip, minBalance, cooldown);
  await faucet.waitForDeployment();
  const faucetAddress = await faucet.getAddress();
  console.log("FAUCET_ADDRESS=" + faucetAddress);

  const fund = hre.ethers.parseEther(process.env.FAUCET_FUND_ETH || "1");
  if (fund > 0n) {
    const tx = await deployer.sendTransaction({ to: faucetAddress, value: fund });
    await tx.wait();
    console.log("Funded faucet with", hre.ethers.formatEther(fund), "ETH");
  }

  console.log("OWNER_PRIVATE_KEY=0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80");
  console.log("VITE_ACCESS_REGISTRY=" + registryAddress);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
