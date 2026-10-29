// Keep compiled contracts identical to the authoritative backend sources.
const fs = require("fs");
const path = require("path");
const contractsSrc = path.join(__dirname, "../../contracts");
const contractsDst = path.join(__dirname, "contracts");
for (const name of ["AccessRegistry.sol", "MediLinkFaucet.sol"]) {
  const src = path.join(contractsSrc, name);
  if (fs.existsSync(src)) {
    fs.copyFileSync(src, path.join(contractsDst, name));
  }
}
require("@nomicfoundation/hardhat-toolbox");

/** @type import('hardhat/config').HardhatUserConfig */
module.exports = {
  solidity: "0.8.28",
};
