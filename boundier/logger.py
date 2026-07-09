import os
import logging
from logging.handlers import RotatingFileHandler

def setup_logging(log_dir: str = "logs", log_filename: str = "boundier.log") -> logging.Logger:
    """Sets up a centralized rotating logger outputting to console and file."""
    if not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)
        
    logger = logging.getLogger("boundier")
    logger.setLevel(logging.DEBUG)
    
    # Avoid duplicate handlers if setup is called multiple times
    if logger.handlers:
        logger.handlers.clear()
        
    log_format = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s"
    )
    
    # Console handler for clean standard output (INFO and above)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(log_format)
    logger.addHandler(console_handler)
    
    # Rotating file handler for verbose troubleshooting (DEBUG and above)
    file_path = os.path.join(log_dir, log_filename)
    file_handler = RotatingFileHandler(
        file_path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(log_format)
    logger.addHandler(file_handler)
    
    return logger
