// Keep the compiled registry identical to the authoritative backend source.
const fs = require("fs");
const path = require("path");
fs.copyFileSync(path.join(__dirname, "../../contracts/AccessRegistry.sol"),
                path.join(__dirname, "contracts/AccessRegistry.sol"));
require("@nomicfoundation/hardhat-toolbox");

/** @type import('hardhat/config').HardhatUserConfig */
module.exports = {
  solidity: "0.8.28",
};
