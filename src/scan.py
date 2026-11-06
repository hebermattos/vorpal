"""Plan an authorized scan with local retrieval and Vorpal's trained Keras model."""
import argparse
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit

from network_scanners import HOSTNAME, ScanError

PROFILES = {
    "url": ("http-head", "http-get"),
    "lan": ("discover", "tcp-common"),
}
PRIVATE_NETWORKS = tuple(map(ipaddress.IPv4Network, ("10.0.0.0/8", "172.16.0.0/12",
                                                    "192.168.0.0/16")))
CORPUS = Path(__file__).resolve().parents[1] / "corpus"
DEFAULT_CHECKPOINT = Path("checkpoints/latest.keras")


def validate_scope(scope, target):
    if scope == "lan":
        try:
            network = ipaddress.IPv4Network(target, strict=True)
        except ValueError as exc:
            raise ScanError("Use um CIDR IPv4 canônico, como 192.168.1.0/24") from exc
        if (target != str(network) or network.num_addresses > 256 or
                not any(network.subnet_of(block) for block in PRIVATE_NETWORKS)):
            raise ScanError("Use um CIDR privado RFC 1918 com até 256 endereços")
        return target
    if scope != "url":
        raise ScanError("Escopo desconhecido")
    if (not re.fullmatch(r"[!-~]{1,2048}", target) or
            any(c in target for c in "#\\") or
            not target.startswith(("http://", "https://"))):
        raise ScanError("Use uma única URL HTTP(S) ASCII sem fragmento")
    try:
        url = urlsplit(target)
        if not re.fullmatch(r"(?:\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9.-]+)(?::[0-9]{1,5})?",
                            url.netloc) or url.port == 0:
            raise ValueError
        if url.netloc.startswith("["):
            ipaddress.IPv6Address(url.hostname)
        elif not HOSTNAME.fullmatch(url.hostname):
            raise ValueError
    except ValueError as exc:
        raise ScanError("Host ou porta inválidos; credenciais não são aceitas") from exc
    return target


def retrieve(root, goal, scope):
    """Select bounded, relevant excerpts from bundled and imported text."""
    terms = set(re.findall(r"\w{3,}", goal.lower()))
    terms.update(("http", "url", "curl") if scope == "url"
                 else ("nmap", "tcp", "cidr", "rede"))
    excerpts = []
    for path in sorted(root.rglob("*"))[:2000]:
        if (path.suffix.lower() not in {".txt", ".md", ".rst"} or
                path.is_symlink() or not path.is_file()):
            continue
        with path.open("rb") as handle:
            content = handle.read(64 * 1024).decode("utf-8", errors="replace")
        for line_number, line in enumerate(content.splitlines()):
            chunk = line.strip()[:800]
            score = len(terms.intersection(re.findall(r"\w{3,}", chunk.lower())))
            if score:
                excerpts.append((score, str(path.relative_to(root)), line_number, chunk))
    excerpts.sort(key=lambda entry: (-entry[0], entry[1], entry[2]))
    return [{"source": source, "excerpt": chunk} for _, source, _, chunk in excerpts[:4]]


def select_profile(checkpoint, goal, excerpts, profiles, threads):
    """Rank the allowed continuations by mean byte loss under the local model."""
    if not checkpoint.is_file():
        raise ScanError(f"Checkpoint não encontrado: {checkpoint}. Execute src/train.py primeiro")
    try:
        import tensorflow as tf
        from model import as_inputs, configure_cpu
    except ImportError as exc:
        raise ScanError("Instale as dependências do Vorpal com pip install -r requirements.txt") from exc

    try:
        configure_cpu(threads)
        model = tf.keras.models.load_model(checkpoint, compile=False, safe_mode=True)
        context = model.get_layer("position_embedding").input_dim
        candidates = {profile: list((profile + "\n").encode()) for profile in profiles}
        budget = context - max(map(len, candidates.values())) + 1
        goal_label, profile_label = b"Objetivo: ", b"\nPerfil: "
        text_budget = budget - len(goal_label) - len(profile_label)
        if context < 32:
            raise ScanError("O scan assistido exige um checkpoint treinado com --context de pelo menos 32")
        goal_bytes = goal.encode("utf-8")[:text_budget // 2 if excerpts else text_budget]
        reference_budget = text_budget - len(goal_bytes)
        per_excerpt = reference_budget // max(1, len(excerpts))
        reference = b"".join(item["excerpt"].encode("utf-8")[:per_excerpt - 1] + b"\n"
                             for item in excerpts if per_excerpt > 1)
        prompt = list(reference + goal_label + goal_bytes + profile_label)

        losses = {}
        for profile, candidate in candidates.items():
            inputs = as_inputs([prompt + candidate[:-1]])
            logits = model(inputs, training=False)[0, -len(candidate):]
            loss = tf.keras.losses.sparse_categorical_crossentropy(
                tf.constant(candidate), logits, from_logits=True)
            losses[profile] = float(tf.reduce_mean(loss))
    except ScanError:
        raise
    except (OSError, ValueError, TypeError, AttributeError, tf.errors.OpError) as exc:
        raise ScanError(f"Checkpoint Keras incompatível ou ilegível: {checkpoint}") from exc

    if not all(math.isfinite(loss) for loss in losses.values()):
        raise ScanError("O modelo retornou pontuações inválidas")
    return min(losses, key=losses.get)


def build_command(scope, target, profile):
    target = validate_scope(scope, target)
    if not isinstance(profile, str) or profile not in PROFILES[scope]:
        raise ScanError("Perfil não permitido para o escopo")
    if scope == "url":
        method = ["--head"] if profile == "http-head" else []
        return ["curl", "--disable", "--globoff", "--noproxy", "*", "--proto", "=http,https",
                "--path-as-is", "--silent", "--show-error", "--connect-timeout", "5",
                "--max-time", "15", "--max-filesize", "1048576", *method,
                "--dump-header", "-", "--output", os.devnull, "--", target]
    flags = ["-sn"] if profile == "discover" else [
        "-sT", "-Pn", "-p", "22,53,80,135,139,443,445,1080,3389,8009,8080,8888",
        "--max-rate", "50",
    ]
    return ["nmap", "-n", "--max-retries", "1", "--host-timeout", "10s",
            "-oG", "-", *flags, target]


def plan(scope, target, goal, checkpoint=DEFAULT_CHECKPOINT, root=CORPUS, *, threads=4):
    target = validate_scope(scope, target)
    if not goal.strip() or len(goal) > 1000:
        raise ScanError("Informe um objetivo com até 1000 caracteres")
    if threads < 1:
        raise ScanError("--threads deve ser pelo menos 1")
    excerpts = retrieve(root, goal, scope)
    profile = select_profile(Path(checkpoint), goal, excerpts, PROFILES[scope], threads)
    command = build_command(scope, target, profile)
    return {"scope": scope, "target": target, "profile": profile, "command": command,
            "references": list(dict.fromkeys(item["source"] for item in excerpts))}


def execute(scan_plan, *, authorized=False):
    if not authorized:
        raise ScanError("A execução exige --authorized")
    # Rebuild from validated fields: never execute a command received as data.
    scope, target, profile = (scan_plan[key] for key in ("scope", "target", "profile"))
    command = build_command(scope, target, profile)
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                                timeout=180, check=False,
                                env={"PATH": os.environ.get("PATH", os.defpath), "LC_ALL": "C"})
    except subprocess.TimeoutExpired as exc:
        raise ScanError("Tempo limite do scan excedido") from exc
    except OSError as exc:
        raise ScanError(f"Não foi possível iniciar {command[0]}: {exc}") from exc
    output = result.stdout.decode("utf-8", errors="replace")
    if scope == "url":
        statuses = re.findall(r"^HTTP/\S+ ([1-5][0-9]{2})(?:[ \r]|$)", output, re.M)
        summary = {"http_status": int(statuses[-1]) if statuses else None,
                   "redirect_followed": False}
    else:
        summary = {
            "hosts_responding": re.findall(r"^Host: (\S+) [^\n]*\tStatus: Up\s*$",
                                           output, re.M) if profile == "discover" else [],
            "open_tcp_ports": [
                {"host": match[1], "ports": [int(port) for port in
                 re.findall(r"(?:^|, )(\d+)/open/tcp/", match[2])]}
                for match in re.finditer(r"^Host: (\S+) [^\n]*?\tPorts: ([^\t\n]+)",
                                         output, re.M)
            ],
        }
    return {**scan_plan, "command": command, "exit_code": result.returncode, "summary": summary,
            "stdout": output, "stderr": result.stderr.decode("utf-8", errors="replace")}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--url", help="Uma URL HTTP(S) exata")
    scope.add_argument("--lan", help="Um CIDR IPv4 privado, por exemplo 192.168.1.0/24")
    parser.add_argument("--goal", required=True, help="Objetivo da análise em linguagem natural")
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT,
                        help="Checkpoint .keras produzido pelo treinamento do Vorpal")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--corpus", type=Path, default=CORPUS)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--authorized", action="store_true")
    args = parser.parse_args(argv)
    if args.execute and not args.authorized:
        parser.error("--execute exige --authorized")
    try:
        scope = "url" if args.url is not None else "lan"
        result = plan(scope, args.url if scope == "url" else args.lan,
                      args.goal, args.checkpoint, args.corpus, threads=args.threads)
        if args.execute:
            result = execute(result, authorized=args.authorized)
    except (ScanError, OSError) as exc:
        parser.exit(2, f"Erro: {exc}\n")
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 1 if result.get("exit_code") else 0


if __name__ == "__main__":
    raise SystemExit(main())
