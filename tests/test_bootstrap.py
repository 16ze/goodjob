from __future__ import annotations

from src.lib.logging import configure_logging, get_logger


def test_logging_bootstrap() -> None:
    configure_logging()
    logger = get_logger("tests.bootstrap")

    assert logger is not None
