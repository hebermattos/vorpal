"""Shared local corpus eligibility and metadata exclusions for import and retrieval."""
import re
try:
    from .config import load_config
except ImportError:
    from config import load_config
SETTINGS = load_config('corpus')
EXT = set(SETTINGS['ext'])
SKIP_DIRS = set(SETTINGS['skip_dirs'])
SKIP_NAME = re.compile(SETTINGS['skip_name'], re.I)
def is_internal_file(name):
    return name in SETTINGS['skip_files']
