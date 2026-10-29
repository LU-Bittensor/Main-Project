const { readFileSync } = require("fs");
const path = require("path");
const { expect } = require("chai");

describe("MediLinkFaucet source contract", function () {
  it("exposes operator drip with threshold and cooldown guards", function () {
    const source = readFileSync(
      path.join(__dirname, "../../../contracts/MediLinkFaucet.sol"),
      "utf8",
    );
    expect(source).to.match(/function drip\(address to\)/);
    expect(source).to.match(/onlyOperator/);
    expect(source).to.match(/BalanceAboveThreshold/);
    expect(source).to.match(/CooldownActive/);
    expect(source).to.match(/function deposit\(\)/);
    expect(source).to.match(/function withdraw\(/);
  });
});
