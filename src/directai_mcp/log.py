"""File + stderr logging. Never stdout (MCP STDIO rule, SPEC 3.6)."""

from __future__ import annotations

import logging
import sys
from pathlib import Path


def setup_logging(data_dir: Path, level: int = logging.INFO) -> Path:
    """Configure root logger: file + stderr only. Returns log file path."""
    log_file = data_dir / "logs" / "directai.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    eh = logging.StreamHandler(sys.stderr)
    eh.setFormatter(fmt)
    root.addHandler(fh)
    root.addHandler(eh)
    return log_file
