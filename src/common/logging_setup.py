'''
Sets up logging for the application.
Creates a logger that writes to logs/<log_file> with rotation.

'''
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional


def setup_logger(name: str, log_file: str, level: int = logging.INFO) -> logging.Logger:
    """
    Creates/returns a logger that writes to logs/<log_file> with rotation.
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger  # already configured

    logger.setLevel(level)
    logger.propagate = False

    # logs folder at project root (two levels above src/common)
    project_root = Path(__file__).resolve().parents[2]
    logs_dir = project_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    file_path = logs_dir / log_file

    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    fh = RotatingFileHandler(
        filename=str(file_path),
        maxBytes=1_000_000,   # 1MB
        backupCount=3,
        encoding="utf-8"
    )
    fh.setFormatter(fmt)
    fh.setLevel(level)

    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    ch.setLevel(logging.WARNING)  # console shows warnings+ only

    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger
