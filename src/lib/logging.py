from __future__ import annotations

import logging
import sys
from typing import Final, cast

import structlog

DEFAULT_LOG_LEVEL: Final[str] = "INFO"


def configure_logging(log_level: str = DEFAULT_LOG_LEVEL) -> None:
    """Configure structlog en JSON pour rendre les runs cron facilement auditables."""

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level.upper(),
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(log_level.upper()),
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Crée un logger nommé pour conserver la source dans chaque événement."""

    return cast(structlog.stdlib.BoundLogger, structlog.get_logger(name))
