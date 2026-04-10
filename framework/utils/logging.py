from __future__ import annotations

import logging


def get_framework_logger() -> logging.Logger:
    logger = logging.getLogger("device_framework")
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)s [%(name)s] %(message)s"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger


LOGGER = get_framework_logger()