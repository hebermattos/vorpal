from pathlib import Path


def safe_read(base: Path, relative_name: str) -> str:
    """Constrain reading to a known directory, with canonical path checking."""
    root = base.resolve(strict=True)
    target = (root / relative_name).resolve(strict=True)
    if not target.is_relative_to(root) or not target.is_file():
        raise ValueError('file is outside the authorized directory')
    return target.read_text(encoding='utf8')


def validate_port(port: int) -> int:
    if isinstance(port, bool) or not isinstance(port, int):
        raise TypeError('port must be an integer')
    if not 1 <= port <= 65535:
        raise ValueError('port is outside the valid range')
    return port