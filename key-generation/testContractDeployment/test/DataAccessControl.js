const { expect } = require("chai");
const { ethers } = require("hardhat");
const { time } = require("@nomicfoundation/hardhat-network-helpers");

describe("DataAccessControl original per-pair challenge flow", function () {
  async function setup() {
    const [owner, recipient, outsider] = await ethers.getSigners();
    const contract = await ethers.deployContract("DataAccessControl", ["pair-record-identifier"]);
    const secret = ethers.id("fixture-shared-secret");
    const commitment = ethers.keccak256(secret);
    await contract.registerCommitment(commitment);
    return { owner, recipient, outsider, contract, commitment };
  }
  async function challenge(contract, signer) {
    const receipt = await (await contract.connect(signer).requestChallenge()).wait();
    return contract.interface.parseLog(receipt.logs[0]).args.nonce;
  }
  it("accepts a correct response once and rejects replay", async function () {
    const { contract, recipient, commitment } = await setup();
    const nonce = await challenge(contract, recipient);
    const response = ethers.solidityPackedKeccak256(["bytes32", "bytes32"], [commitment, nonce]);
    await expect(contract.connect(recipient).verifyAccess(response)).to.emit(contract, "AccessGranted").withArgs(recipient.address);
    expect(await contract.hasPendingChallenge(recipient.address)).to.equal(false);
    await expect(contract.connect(recipient).verifyAccess(response)).to.be.revertedWith("No pending challenge");
  });
  it("denies an incorrect response and expires old challenges", async function () {
    const { contract, recipient } = await setup();
    await challenge(contract, recipient);
    await expect(contract.connect(recipient).verifyAccess(ethers.ZeroHash)).to.emit(contract, "AccessDenied");
    await challenge(contract, recipient);
    await time.increase(301);
    await expect(contract.connect(recipient).verifyAccess(ethers.ZeroHash)).to.be.revertedWith("Challenge expired");
  });
  it("keeps commitments independent between pair contracts", async function () {
    const { contract, outsider, commitment } = await setup();
    const other = await ethers.deployContract("DataAccessControl", ["other-pair"]);
    await other.registerCommitment(ethers.id("different-secret"));
    expect(await contract.commitment()).to.equal(commitment);
    expect(await other.commitment()).not.to.equal(commitment);
    await expect(contract.connect(outsider).registerCommitment(commitment)).to.be.revertedWithCustomError(contract, "OwnableUnauthorizedAccount").withArgs(outsider.address);
  });
  it("documents the accepted prototype limitation: public values can forge a response", async function () {
    const { contract, outsider } = await setup();
    const nonce = await challenge(contract, outsider);
    const response = ethers.solidityPackedKeccak256(["bytes32", "bytes32"], [await contract.commitment(), nonce]);
    await expect(contract.connect(outsider).verifyAccess(response)).to.emit(contract, "AccessGranted");
  });
  it("only the owner can permanently kill; old and new challenges cannot grant access", async function () {
    const { contract, owner, recipient, outsider, commitment } = await setup();
    const nonce = await challenge(contract, recipient);
    const response = ethers.solidityPackedKeccak256(["bytes32", "bytes32"], [commitment, nonce]);
    await expect(contract.connect(outsider).killContract()).to.be.revertedWithCustomError(contract, "OwnableUnauthorizedAccount").withArgs(outsider.address);
    await expect(contract.killContract()).to.emit(contract, "ContractKilled").withArgs(owner.address);
    expect(await contract.isKilled()).to.equal(true);
    expect(await contract.hasPendingChallenge(recipient.address)).to.equal(false);
    await expect(contract.connect(recipient).verifyAccess(response)).to.be.revertedWith("Contract is deactivated");
    await expect(contract.connect(recipient).requestChallenge()).to.be.revertedWith("Contract is deactivated");
    await expect(contract.registerCommitment(commitment)).to.be.revertedWith("Contract is deactivated");
    await expect(contract.killContract()).not.to.emit(contract, "ContractKilled");
    // Ownership changes never reset the kill flag.
    await contract.transferOwnership(recipient.address);
    await expect(contract.connect(recipient).registerCommitment(commitment)).to.be.revertedWith("Contract is deactivated");
    const other = await ethers.deployContract("DataAccessControl", ["another-pair"]);
    await other.registerCommitment(commitment);
    await expect(other.connect(recipient).requestChallenge()).to.emit(other, "ChallengeIssued");
  });
  it("can be killed before any commitment is registered", async function () {
    const contract = await ethers.deployContract("DataAccessControl", ["uninitialized-pair"]);
    await contract.killContract();
    await expect(contract.registerCommitment(ethers.id("secret"))).to.be.revertedWith("Contract is deactivated");
  });

});
