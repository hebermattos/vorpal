"""Backward-compatible URL import interface backed by the canonical dataset importer."""
from pathlib import Path
import sys
from import_dataset import (
    NoRedirect,
    TextHTML as HTMLText,
    ExtractError,
    extract_text,
    fetch_url,
    import_one,
    validate_url,
)
from import_dataset import main as importer_main


def fetch(url):
    name, raw, final_url = fetch_url(url)
    return final_url, extract_text(name, raw)


def import_url(url, destination='corpus/imported'):
    name, raw, final_url = fetch_url(url)
    accepted, rejected = import_one(name, raw, final_url, destination)
    if not accepted:
        reason = rejected[0]['reason'] if rejected else 'No supported content was imported'
        raise ExtractError(reason)
    return Path(accepted[0]['output'])


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    return importer_main(['--url', *arguments])


if __name__ == '__main__':
    main()

# Keep this adapter's imported names available to legacy callers.

