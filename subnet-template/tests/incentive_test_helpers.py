"""Shared stubs so incentive tests import under Bittensor v11 (no Synapse)."""

from __future__ import annotations

import sys
import types


def ensure_import_env() -> None:
    """Patch missing Synapse and light stubs before importing protocol / minerv1."""
    try:
        import bittensor as bt
    except ImportError:
        bt = types.ModuleType("bittensor")
        sys.modules["bittensor"] = bt

    if not hasattr(bt, "Synapse"):

        class Synapse:
            def __init__(self, **kwargs):
                for k, v in kwargs.items():
                    setattr(self, k, v)

        bt.Synapse = Synapse

    if "bittensor.wallet" not in sys.modules:
        wallet_mod = types.ModuleType("bittensor.wallet")

        class Wallet:
            def __init__(self, *args, **kwargs):
                pass

        wallet_mod.Wallet = getattr(bt, "Wallet", Wallet)
        sys.modules["bittensor.wallet"] = wallet_mod
        if not hasattr(bt, "Wallet"):
            bt.Wallet = Wallet

    # minerv1 imports fastapi/uvicorn at module level; stub if absent.
    if "fastapi" not in sys.modules:
        try:
            import fastapi  # noqa: F401
        except ImportError:
            fastapi_mod = types.ModuleType("fastapi")

            class FastAPI:
                def __init__(self, *a, **k):
                    pass

                def get(self, *a, **k):
                    def deco(fn):
                        return fn

                    return deco

                def post(self, *a, **k):
                    def deco(fn):
                        return fn

                    return deco

            class Request:
                pass

            fastapi_mod.FastAPI = FastAPI
            fastapi_mod.Request = Request
            sys.modules["fastapi"] = fastapi_mod

            resp_mod = types.ModuleType("fastapi.responses")

            class JSONResponse:
                def __init__(self, *a, **k):
                    pass

            resp_mod.JSONResponse = JSONResponse
            sys.modules["fastapi.responses"] = resp_mod

    if "uvicorn" not in sys.modules:
        try:
            import uvicorn  # noqa: F401
        except ImportError:
            uvicorn_mod = types.ModuleType("uvicorn")

            class Config:
                def __init__(self, *a, **k):
                    pass

            class Server:
                def __init__(self, *a, **k):
                    pass

                def run(self):
                    pass

            uvicorn_mod.Config = Config
            uvicorn_mod.Server = Server
            sys.modules["uvicorn"] = uvicorn_mod
