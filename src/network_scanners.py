"""Opt-in wrappers for locally installed network security scanners.

Only scan assets for which you have explicit authorization. No shell execution,
arbitrary scanner flags, or automatic ingestion into the model's training corpus.
"""
import argparse
import ipaddress
import json
from pathlib import Path
import re
import shutil
import subprocess
from datetime import datetime, timezone
from urllib.parse import urlsplit

TOOLS = ("naabu", "rustscan", "nuclei", "httpx", "arp-scan")
HOSTNAME = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$")
INTERFACE = re.compile(r"^[a-zA-Z0-9_.:-]{1,32}$")
MAX_OUTPUT = 1024 * 1024
MAX_DURATION = 180


class ScanError(ValueError):
    """Invalid request or scanner execution failure."""


def validate_target(tool, target):
    """Validate a single target; never accept command switches or shell input."""
    if not target or target != target.strip() or any(c.isspace() for c in target):
        raise ScanError("Alvo vazio ou contendo espaços")
    if tool in ("nuclei", "httpx"):
        parsed = urlsplit(target)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ScanError("Use URL http(s) para nuclei/httpx")
        if parsed.username or parsed.password or parsed.fragment:
            raise ScanError("URL com credenciais ou fragmento não é aceita")
        try:
            port = parsed.port
        except ValueError as exc:
            raise ScanError("Porta inválida") from exc
        if port is not None and not 1 <= port <= 65535:
            raise ScanError("Porta inválida")
        host = parsed.hostname
        if not valid_host(host):
            raise ScanError("Hostname inválido")
        return target
    if tool == "arp-scan":
        try:
            address = ipaddress.ip_address(target)
        except ValueError as exc:
            raise ScanError("arp-scan exige um IPv4 individual") from exc
        if address.version != 4 or address.is_multicast or address.is_unspecified:
            raise ScanError("Endereço IPv4 não permitido")
        return str(address)
    if not valid_host(target):
        raise ScanError("Use um endereço IP individual ou hostname válido")
    return target


def valid_host(value):
    if not value or value.startswith("-"):
        return False
    try:
        address = ipaddress.ip_address(value)
        return not (address.is_multicast or address.is_unspecified)
    except ValueError:
        return bool(HOSTNAME.fullmatch(value))


def build_command(tool, target, interface=None):
    """Build fixed, conservative arguments without passing user-supplied flags."""
    if tool not in TOOLS:
        raise ScanError("Ferramenta desconhecida")
    target = validate_target(tool, target)
    if tool == "arp-scan":
        if not interface or not INTERFACE.fullmatch(interface) or interface.startswith("-"):
            raise ScanError("arp-scan exige --interface com nome válido")
        return ["arp-scan", "--plain", "--interface", interface, target]
    if interface:
        raise ScanError("--interface só é válido para arp-scan")
    if tool == "naabu":
        return ["naabu", "-host", target, "-json", "-rate", "100", "-silent"]
    if tool == "rustscan":
        # RustScan's default Nmap integration is avoided with --no-nmap.
        return ["rustscan", "-a", target, "-b", "50", "--timeout", "1500", "--no-nmap"]
    if tool == "nuclei":
        return ["nuclei", "-u", target, "-jsonl", "-rate-limit", "10", "-concurrency", "2", "-no-color", "-silent"]
    return ["httpx", "-u", target, "-json", "-rate-limit", "10", "-no-color", "-silent"]


def execute(tool, target, *, authorized=False, interface=None, timeout=60,
            output_dir="scans"):
    if not authorized:
        raise ScanError("Informe --authorized apenas para ativos sob sua autorização")
    if not isinstance(timeout, int) or timeout < 1 or timeout > MAX_DURATION:
        raise ScanError("Timeout deve estar entre 1 e 180 segundos")
    command = build_command(tool, target, interface)
    binary = shutil.which(command[0])
    if binary is None:
        raise ScanError(f"Ferramenta não instalada no PATH: {command[0]}")
    command[0] = binary
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                                timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise ScanError(f"Tempo limite excedido ({timeout}s)") from exc
    except OSError as exc:
        raise ScanError(f"Falha ao executar scanner: {exc}") from exc

    # Scanner output is untrusted data; never evaluate it or pass it to a shell.
    if len(result.stdout) > MAX_OUTPUT or len(result.stderr) > MAX_OUTPUT:
        raise ScanError("Saída maior que 1 MiB; descarte e limite o escopo do scan")
    report = {
        "schema_version": 1,
        "tool": tool,
        "target": target,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "exit_code": result.returncode,
        "stdout": result.stdout.decode("utf-8", errors="replace"),
        "stderr": result.stderr.decode("utf-8", errors="replace"),
    }
    destination = Path(output_dir)
    if destination.is_symlink():
        raise ScanError("Diretório de saída não pode ser link simbólico")
    destination.mkdir(parents=True, exist_ok=True)
    # One report per invocation, never overwrite previous results.
    import uuid
    path = destination / f"{tool.replace('-', '_')}_{uuid.uuid4().hex}.json"
    with path.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    return path, report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Scanners externos para auditoria autorizada")
    parser.add_argument("tool", choices=TOOLS)
    parser.add_argument("target", help="Host/IP ou URL (nuclei/httpx)")
    parser.add_argument("--interface", help="Interface de rede obrigatória para arp-scan")
    parser.add_argument("--authorized", action="store_true",
                        help="Confirmo que possuo autorização explícita para testar este alvo")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--output-dir", default="scans")
    args = parser.parse_args(argv)
    try:
        path, report = execute(args.tool, args.target, authorized=args.authorized,
                               interface=args.interface, timeout=args.timeout,
                               output_dir=args.output_dir)
    except ScanError as exc:
        parser.exit(2, f"Erro: {exc}\n")
    print(json.dumps({"report": str(path), "exit_code": report["exit_code"]},
                     ensure_ascii=False))
    return 0 if report["exit_code"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
