"""One place to configure logging for the CLI."""

from __future__ import annotations

import logging
from pathlib import Path

LOG = logging.getLogger("itgc_uar")


def setup_logging(verbose: bool = False, log_file: Path | None = None) -> None:
    """Console logging (INFO, or DEBUG with --verbose) and an optional run.log file (always DEBUG)."""
    LOG.handlers.clear()
    LOG.setLevel(logging.DEBUG)
    LOG.propagate = False

    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter("%(levelname)-7s %(message)s"))
    LOG.addHandler(console)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file, mode="w", encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
        LOG.addHandler(fh)
