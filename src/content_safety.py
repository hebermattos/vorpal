"""Shared content filters used by import and dataset preparation."""

import re

try:
    from .config import load_config
except ImportError:  # Script invocation.
    from config import load_config

SETTINGS = load_config("content_safety")

SECRET_CONTENT = re.compile(
    SETTINGS["secret_content"]
)
