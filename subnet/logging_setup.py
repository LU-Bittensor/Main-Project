"""Logging helpers: subnet app logs only; silence Bittensor/websocket RPC noise."""

from __future__ import annotations

import logging
from typing import Iterable

# Libraries that flood DEBUG with JSON-RPC / websocket traffic.
_NOISY_LOGGERS: tuple[str, ...] = (
    "websockets",
    "websockets.client",
    "websockets.server",
    "asyncio",
    "urllib3",
    "httpx",
    "httpcore",
    "bittensor",
    "bittensor.transport",
    "bittensor.transport.raw_websocket",
    "bittensor.core",
    "substrateinterface",
    "scalecodec",
)


def silence_noisy_loggers(level: int = logging.WARNING) -> None:
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(level)


def configure_app_logging(
    app_logger_names: Iterable[str],
    *,
    level: int = logging.INFO,
    fmt: str = "%(asctime)s %(levelname)s %(name)s: %(message)s",
) -> None:
    """Root at WARNING; named app loggers at `level`; third-party RPC quiet."""
    logging.basicConfig(level=logging.WARNING, format=fmt, force=True)
    silence_noisy_loggers(logging.WARNING)
    for name in app_logger_names:
        lg = logging.getLogger(name)
        lg.setLevel(level)
        lg.propagate = True
