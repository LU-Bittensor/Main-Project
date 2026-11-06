"""WSGI entry point for Cloud Run; configuration comes from runtime env/secrets."""
import os

from grant_api import create_app


def create_cloud_app():
    chain = None
    if os.getenv("GRANT_CHAIN_ENABLED", "false").lower() == "true":
        from chain_grants import ChainGrants

        chain = ChainGrants(os.environ["RPC_URL"], os.environ["OWNER_PRIVATE_KEY"],
                            fund_local_wallets=False)
    return create_app(dev=False, chain=chain)
