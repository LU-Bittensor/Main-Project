"""WSGI entry point for Cloud Run; configuration comes from runtime env/secrets."""
import os

import time
from pathlib import Path
import requests

# Public Hardhat account #0; only used for the explicitly enabled disposable chain.
TEST_OPERATOR = '0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80'


def configure_sidecar():
    config = None
    for _ in range(120):
        try:
            response = requests.get('http://127.0.0.1:8546/ready', timeout=2)
            response.raise_for_status()
            config = response.json()
            break
        except (requests.RequestException, ValueError):
            time.sleep(1)
    if not config or config.get('chain_id') != 31337:
        raise RuntimeError('Hardhat sidecar did not become ready')
    from web3 import Web3
    if not all(Web3.is_address(config.get(k, '')) for k in ('registry', 'faucet')):
        raise RuntimeError('Invalid sidecar deployment addresses')
    os.environ.update(GRANT_CHAIN_ENABLED='true', RPC_URL='http://127.0.0.1:8545',
                      CHAIN_ID='31337', OWNER_PRIVATE_KEY=TEST_OPERATOR,
                      FAUCET_OPERATOR_PRIVATE_KEY=TEST_OPERATOR,
                      REGISTRY_ADDRESS=config['registry'], FAUCET_ADDRESS=config['faucet'])
    # A new chain must never reuse old registry/grant state after an API restart.
    instance = config.get('instance', '')
    if not instance.startswith('0x') or any(c not in '0123456789abcdefABCDEF' for c in instance[2:]):
        raise RuntimeError('Invalid Hardhat instance identity')
    base = Path(os.getenv('GRANT_DATA_DIR', '/tmp/medilink'))
    os.environ['GRANT_DATA_DIR'] = str(base / instance)



def create_cloud_app():
    sidecar = os.getenv('HARDHAT_SIDECAR', 'false').lower() == 'true'
    if sidecar:
        configure_sidecar()
    # Import after bootstrap: faucet_client reads environment at import time.
    from grant_api import create_app
    chain = None
    if os.getenv("GRANT_CHAIN_ENABLED", "false").lower() == "true":
        from chain_grants import ChainGrants

        chain = ChainGrants(os.environ["RPC_URL"], os.environ["OWNER_PRIVATE_KEY"],
                            fund_local_wallets=False)
    app = create_app(dev=False, chain=chain)
    if sidecar:
        from test_rpc import install_rpc
        app.config['HARDHAT_RPC_ENABLED'] = True
        install_rpc(app)
    return app
