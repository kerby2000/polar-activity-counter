"""Capture backend connection events without filling the normal console with debug logs."""

import logging
import time
from pathlib import Path


class ConnectionLog:
    def __init__(self, path: Path):
        self.handler = logging.FileHandler(path, encoding="utf-8")
        formatter = logging.Formatter("%(asctime)sZ %(levelname)s %(name)s: %(message)s")
        formatter.converter = time.gmtime
        self.handler.setFormatter(formatter)
        self.saved = []
        for name in ("bleak.backends.winrt.client", "polar_activity.device"):
            logger = logging.getLogger(name)
            self.saved.append((logger, logger.level, logger.propagate))
            verbose = logger.isEnabledFor(logging.DEBUG)
            logger.addHandler(self.handler)
            logger.setLevel(logging.DEBUG)
            logger.propagate = verbose

    def close(self) -> None:
        for logger, level, propagate in self.saved:
            logger.removeHandler(self.handler)
            logger.setLevel(level)
            logger.propagate = propagate
        self.saved.clear()
        self.handler.close()
