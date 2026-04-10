"""
Logger — Logging profissional com JSON estruturado e rotação.
Substitui todos os print() do projeto.
"""

import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler
from pythonjsonlogger import jsonlogger


LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)


def get_logger(name: str, level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Console handler — human-readable
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console_fmt = logging.Formatter(
        "%(asctime)s │ %(levelname)-5s │ %(name)-18s │ %(message)s",
        datefmt="%H:%M:%S",
    )
    console.setFormatter(console_fmt)
    logger.addHandler(console)

    # File handler — JSON structured (para parsing e alertas)
    json_handler = RotatingFileHandler(
        LOG_DIR / f"{name}.jsonl",
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding="utf-8",
    )
    json_handler.setLevel(logging.DEBUG)
    json_fmt = jsonlogger.JsonFormatter(
        "%(asctime)s %(name)s %(levelname)s %(message)s",
        timestamp=True,
    )
    json_handler.setFormatter(json_fmt)
    logger.addHandler(json_handler)

    return logger


# Loggers pré-configurados
log_main = get_logger("sdr.main")
log_discovery = get_logger("sdr.discovery")
log_enrichment = get_logger("sdr.enrichment")
log_scoring = get_logger("sdr.scoring")
log_outreach = get_logger("sdr.outreach")
log_webhook = get_logger("sdr.webhook")
log_scheduler = get_logger("sdr.scheduler")
log_scraper = get_logger("sdr.scraper")
