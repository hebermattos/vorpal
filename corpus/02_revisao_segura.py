from pathlib import Path

def safe_read(base: Path, relative_name: str) -> str:
    """Constrain reading to a known directory, with canonical path checking."""
    root = base.resolve(strict=True)
    target = (root / relative_name).resolve(strict=True)
    if not target.is_relative_to(root) or not target.is_file():
        raise ValueError('arquivo fora do diretório autorizado')
    return target.read_text(encoding='utf8')


def validate_port(port: int) -> int:
    if isinstance(port, bool) or not isinstance(port, int):
        raise TypeError('port deve ser inteiro')
    if not 1 <= port <= 65535:
        raise ValueError('porta fora do intervalo')
    return port
