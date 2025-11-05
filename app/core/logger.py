import logging
from logging.handlers import RotatingFileHandler
import os
import sys

LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

LOG_FILE = os.path.join(LOG_DIR, "app.log")
LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

file_handler = RotatingFileHandler(
    LOG_FILE,
    maxBytes=5_000_000,
    backupCount=5,
    encoding="utf-8"
)
file_handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))

logger = logging.getLogger("itr_profile_logger")
logger.setLevel(logging.INFO)

if not logger.handlers:
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    logger.propagate = False

# ✅ Added helper for flexible imports
def get_logger(name: str = "itr_profile_logger") -> logging.Logger:
    """
    Returns a configured logger instance.
    Use: logger = get_logger(__name__)
    """
    return logging.getLogger(name)
