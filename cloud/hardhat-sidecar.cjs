// Disposable Hardhat test chain. Neither RPC nor deployment metadata is ingress.
const { spawn } = require('node:child_process');
const http = require('node:http');
const { JsonRpcProvider, ContractFactory, parseEther } = require('ethers');
const child = spawn(process.execPath, ['node_modules/hardhat/internal/cli/cli.js', 'node', '--hostname', '127.0.0.1'], { stdio: ['ignore', 'ignore', 'inherit'] });
child.on('exit', () => process.exit(1));
for (const signal of ['SIGTERM', 'SIGINT']) process.on(signal, () => { child.kill(signal); process.exit(0); });
let config;
http.createServer((req, res) => {
  if (req.url !== '/ready') { res.writeHead(404); return res.end(); }
  res.writeHead(config ? 200 : 503, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
  res.end(JSON.stringify(config || { ready: false }));
}).listen(8546, '0.0.0.0');
(async () => {
  const provider = new JsonRpcProvider('http://127.0.0.1:8545', 31337, { staticNetwork: true });
  let ready = false;
  for (let i = 0; i < 120; i++) {
    try { await provider.send('eth_chainId', []); ready = true; break; } catch { await new Promise(r => setTimeout(r, 500)); }
  }
  if (!ready) throw new Error('Hardhat startup timeout');
  const signer = await provider.getSigner(0);
  async function deploy(name, args) {
    const artifact = require(require("node:path").resolve(`artifacts/contracts/${name}.sol/${name}.json`));
    const contract = await new ContractFactory(artifact.abi, artifact.bytecode, signer).deploy(...args);
    await contract.waitForDeployment();
    return contract.getAddress();
  }
  const registry = await deploy('AccessRegistry', []);
  const faucet = await deploy('MediLinkFaucet', [parseEther('0.01'), parseEther('0.005'), 86400]);
  await (await signer.sendTransaction({ to: faucet, value: parseEther('100') })).wait();
  const metadata = await provider.send('hardhat_metadata', []);
  config = { chain_id: 31337, registry, faucet, instance: metadata.instanceId };
  console.log('Hardhat contracts deployed and faucet funded.');
})().catch(() => { console.error('Hardhat bootstrap failed'); child.kill(); process.exit(1); });
