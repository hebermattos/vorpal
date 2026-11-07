"""Shared content filters used by import and dataset preparation."""
import re

SECRET_CONTENT = re.compile(
    r'-----BEGIN [A-Z ]*PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9_]{20,}|'
    r'AKIA[0-9A-Z]{16}|(?i:api[_-]?key|password|secret|token)\s*[:=]\s*["\x27]?[^\s"\x27]{8,}'
)