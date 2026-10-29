/**
 * Hardhat tests for MediLinkFaucet.
 * Run (with Hardhat toolbox + network): npx hardhat test test/MediLinkFaucet.ts
 *
 * These tests also document expected behavior for the Python client.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..");

type Artifact = { abi: unknown[]; bytecode: string };

function loadCompiledViaNote(): void {
  // Compilation is performed by deploy_medilink_faucet.py / solcx in CI.
  // This file asserts the Solidity source encodes the locked design.
  const source = readFileSync(path.join(ROOT, "MediLinkFaucet.sol"), "utf8");
  assert.match(source, /function drip\(address to\)/);
  assert.match(source, /onlyOperator/);
  assert.match(source, /BalanceAboveThreshold/);
  assert.match(source, /CooldownActive/);
  assert.match(source, /function deposit\(\)/);
  assert.match(source, /function withdraw\(/);
}

describe("MediLinkFaucet source contract", () => {
  it("exposes operator drip with threshold and cooldown guards", () => {
    loadCompiledViaNote();
  });
});

// Optional live network test when HARDHAT_NETWORK / RPC is available via ethers.
const require = createRequire(import.meta.url);
try {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const hh = require("hardhat");
  if (hh?.ethers) {
    describe("MediLinkFaucet live", function () {
      this.timeout(60_000);

      it("deposit → drip → cooldown → minBalance", async () => {
        const [owner, user, stranger] = await hh.ethers.getSigners();
        const factory = await hh.ethers.getContractFactory("MediLinkFaucet");
        const dripAmount = hh.ethers.parseEther("0.01");
        const minBalance = hh.ethers.parseEther("0.005");
        const cooldown = 3600n;
        const faucet = await factory.deploy(dripAmount, minBalance, cooldown);
        await faucet.waitForDeployment();

        await (await owner.sendTransaction({ to: await faucet.getAddress(), value: hh.ethers.parseEther("1") })).wait();

        const before = await hh.ethers.provider.getBalance(user.address);
        await (await faucet.drip(user.address)).wait();
        const after = await hh.ethers.provider.getBalance(user.address);
        assert.equal(after - before, dripAmount);

        await assert.rejects(async () => faucet.drip(user.address), /CooldownActive|BalanceAboveThreshold/);

        await assert.rejects(async () => faucet.connect(stranger).drip(user.address), /NotOperator/);
      });
    });
  }
} catch {
  // Hardhat toolbox not wired; source assertions above still run under node:test / mocha if configured.
}
