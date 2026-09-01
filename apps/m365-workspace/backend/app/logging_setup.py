"""Minimal stdlib logging setup.

Edwin backs these call sites with the shared ``app-logger`` package
(console + Azure Blob sinks, batching, request-context enrichment).
The scaffold keeps the same call-site shape (``get_logger`` /
``init_logging``) but uses plain stdlib logging so there is nothing
extra to install — swap in a richer sink later without touching any
importer.
"""

from __future__ import annotations

import logging


def init_logging(level: str = "INFO") -> None:
    """Configure the root logger once at app boot.

    ``force=True`` replaces handlers installed by anything that ran
    earlier (uvicorn configures logging before importing the app), so
    the format below actually takes effect.
    """
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )


def get_logger(name: str) -> logging.Logger:
    """Named logger accessor — same call shape as Edwin's app_logger."""
    return logging.getLogger(name)
