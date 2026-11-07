"""Small, educational local-network vulnerability scan.

This script validates a private LAN IPv4 address, tests a limited set of common
ports, and saves a JSON report. It intentionally reports only discoverable
exposure, never executes a payload, and is meant for authorized local testing.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import socket
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Iterable

import numpy as np

try:
    import tensorflow as tf
    from tensorflow import keras
    try:
        from model import as_inputs
    except ImportError:  # pragma: no cover
        from src.model import as_inputs
except Exception:  # pragma: no cover
    tf = None
    keras = None
    as_inputs = None

DEFAULT_PORTS = (22, 80, 443, 3306, 5432, 8080, 3389, 5900)
SERVICE_HINTS = {
    22: ("ssh", "Remote administration service; verify SSH keys and firewall rules."),
    80: ("http", "Web service exposed on the local network; ensure TLS and auth are configured."),
    443: ("https", "TLS endpoint on the local network; verify certificate and access policy."),
    3306: ("mysql", "Database port reachable locally; confirm authorization and firewall rules."),
    5432: ("postgresql", "Database service reachable locally; review authentication and network access."),
    8080: ("http-alt", "Alternate web port; confirm whether the service is intentional and restricted."),
    3389: ("rdp", "Remote desktop port; review firewall rules and MFA/authorization requirements."),
    5900: ("vnc", "VNC service exposed on a local subnet; confirm access requirements and authentication."),
}


class ScanError(ValueError):
    """Validation or scan failure."""


def _resolve_checkpoint(checkpoint: str | Path | None = None) -> Path | None:
    candidates = []
    if checkpoint is not None:
        candidates.append(Path(checkpoint))
    repo_root = Path(__file__).resolve().parent.parent
    candidates.extend([
        repo_root / "checkpoints" / "latest.keras",
        Path.cwd() / "checkpoints" / "latest.keras",
    ])
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _score_string(model, text: str) -> float:
    if not text:
        return 0.0
    if model is None or as_inputs is None:
        return 0.0
    payload = np.frombuffer(text.encode("utf-8"), dtype=np.uint8).astype(np.int32)
    context = model.get_layer("position_embedding").input_dim
    payload = payload[-context:]
    if len(payload) < 2:
        return 0.0
    logits = model(as_inputs(np.array([payload], dtype=np.int32)), training=False).numpy()[0]
    probs = tf.nn.softmax(logits, axis=-1).numpy()
    scores = []
    for idx in range(1, len(payload)):
        target = int(payload[idx])
        scores.append(float(np.log(probs[idx - 1, target] + 1e-9)))
    return float(np.mean(scores)) if scores else 0.0


def assess_service_risk(service: str, banner: str, *, model=None) -> tuple[str, float, str]:
    """Use the trained model to compare risky and safe language patterns."""
    if model is None:
        return ("unknown", 0.0, "No trained checkpoint available; using conservative host-level heuristics only.")

    risk_phrases = [
        f"The {service} service is reachable from the network without authentication or access control.",
        f"This {service} endpoint exposes admin functionality and lacks strong authentication or TLS.",
        f"The {service} daemon appears to allow remote access with a weak or default configuration.",
    ]
    safe_phrases = [
        f"The {service} service requires authentication and restricts access to authorized users.",
        f"The {service} endpoint is protected by access controls, TLS, and least-privilege principles.",
        f"The {service} service is intentionally limited to trusted, private, and well-controlled access.",
    ]

    banner_sample = (banner or "").strip()
    if banner_sample:
        risk_phrases.append(f"Banner: {banner_sample} shows an exposed {service} service with network reachability.")
        safe_phrases.append(f"Banner: {banner_sample} is consistent with a restricted and authenticated {service} deployment.")

    risk_score = float(np.mean([_score_string(model, phrase) for phrase in risk_phrases]))
    safe_score = float(np.mean([_score_string(model, phrase) for phrase in safe_phrases]))
    delta = risk_score - safe_score
    if delta > 0.05:
        return ("high", delta, "Model language analysis suggests a risky exposure pattern in the service configuration.")
    if delta > 0.0:
        return ("medium", delta, "Model language analysis suggests a moderate exposure pattern that should be reviewed.")
    return ("low", delta, "Model language analysis suggests the service wording is more consistent with a restricted configuration.")


def validate_target(target: str) -> str:
    """Allow only private LAN IPv4 addresses for this educational scan."""
    if not target or target != target.strip() or any(ch.isspace() for ch in target):
        raise ScanError("Target must be a single IPv4 address without spaces.")
    try:
        address = ipaddress.ip_address(target)
    except ValueError as exc:  # pragma: no cover - validation path covered by tests
        raise ScanError("Target must be a valid IPv4 address.") from exc

    if address.version != 4:
        raise ScanError("Only IPv4 addresses are supported for local-network checks.")
    if address.is_private or address.is_loopback or address.is_link_local:
        return str(address)
    raise ScanError("This scan only accepts local private or link-local IPv4 addresses.")


def _parse_ports(raw_ports: Iterable[int] | str | None) -> tuple[int, ...]:
    if raw_ports is None:
        return DEFAULT_PORTS
    if isinstance(raw_ports, str):
        if not raw_ports.strip():
            return DEFAULT_PORTS
        values = []
        for part in raw_ports.split(","):
            value = part.strip()
            if not value:
                continue
            try:
                port = int(value)
            except ValueError as exc:
                raise ScanError(f"Invalid port value: {value!r}") from exc
            if not 1 <= port <= 65535:
                raise ScanError(f"Port out of range: {port}")
            values.append(port)
        if not values:
            return DEFAULT_PORTS
        return tuple(values)
    values = tuple(int(port) for port in raw_ports)
    return values


def run_scan(
    target: str,
    *,
    authorized: bool = False,
    ports: Iterable[int] | str | None = None,
    timeout: float = 1.0,
    output_path: str | Path | None = None,
    checkpoint: str | Path | None = None,
    verbose: bool = True,
) -> dict:
    """Run a local host scan and persist a JSON report."""
    if not authorized:
        raise ScanError("Authorization is required to scan a local host.")

    validated = validate_target(target)
    port_list = _parse_ports(ports)
    destination = Path(output_path) if output_path is not None else Path("reports") / f"scan_{validated.replace('.', '_')}.json"
    scan_started = perf_counter()
    started_at = datetime.now(timezone.utc)
    model = None
    checkpoint_path = _resolve_checkpoint(checkpoint)
    if checkpoint_path is not None and keras is not None:
        try:
            model = keras.models.load_model(str(checkpoint_path), compile=False)
        except Exception as exc:  # pragma: no cover - guarded by repo state
            if verbose:
                print(f"Model checkpoint could not be loaded: {exc}")
            model = None
    if verbose:
        print("=" * 72)
        print("VORPAL LOCAL NETWORK SCAN")
        print(f"Started at: {started_at.isoformat()}")
        print(f"Target: {validated}")
        print("Authorization: confirmed by --authorized")
        print(f"Ports: {len(port_list)} selected ({', '.join(str(p) for p in port_list)})")
        print(f"Connection timeout: {timeout:g}s per port")
        print(f"Risk assessment: {'model checkpoint ' + str(checkpoint_path) if model is not None else 'port-based fallback'}")
        if model is not None:
            print(f"Loaded trained model: {checkpoint_path}")
        print(f"Report destination: {destination}")
        print("=" * 72)
        print(f"Verification type: target validation -> checking {validated} as a private local IPv4 address")
        print(f"[1/4] Validating target: {validated}")
        print(f"Verification type: port enumeration -> checking ports: {', '.join(str(p) for p in port_list)}")
        print(f"[2/4] Checking ports: {', '.join(str(p) for p in port_list)}")
        if model is None:
            print("      -> No usable checkpoint loaded; model scoring is unavailable for this run.")
            print("      -> Open-port severity will use conservative port-based defaults.")

    findings = []
    connection_failures = 0
    banner_failures = 0
    for port_index, port in enumerate(port_list, start=1):
        port_started = perf_counter()
        service_hint = SERVICE_HINTS.get(port, ("unknown", "Potentially exposed service; verify whether it is intentional."))[0]
        if verbose:
            print(f"[PORT {port_index}/{len(port_list)}] {validated}:{port} ({service_hint})")
            print(f"Verification type: TCP connection check -> connecting to {validated}:{port}")
        try:
            with socket.create_connection((validated, port), timeout=timeout) as sock:
                if verbose:
                    print("    TCP connection: established")
                banner = ""
                banner_error = None
                try:
                    sock.settimeout(timeout)
                    if port in {80, 8080}:
                        request = (
                            f"HEAD / HTTP/1.1\r\nHost: {validated}\r\n"
                            "Connection: close\r\n\r\n"
                        )
                        sock.sendall(request.encode("ascii"))
                    banner = sock.recv(1024).decode("utf-8", errors="replace").strip()
                except OSError as exc:
                    banner_failures += 1
                    banner_error = f"{type(exc).__name__}: {exc}"
                    if verbose:
                        print(f"Verification type: banner inspection -> failed ({type(exc).__name__}: {exc})")
                        print("    TCP connection remains confirmed open; banner/HTTP response was unavailable")

                service, note = SERVICE_HINTS.get(port, ("unknown", "Potentially exposed service; verify whether it is intentional."))
                http_status = None
                if port in {80, 8080} and banner:
                    status_parts = banner.splitlines()[0].split()
                    if len(status_parts) > 1 and status_parts[0].startswith("HTTP/") and status_parts[1].isdigit():
                        http_status = int(status_parts[1])
                if verbose:
                    print(f"Verification type: banner inspection -> received {len(banner.encode('utf-8'))} banner byte(s) for {service}")
                    if http_status is not None:
                        print(f"    HTTP response status: {http_status}")
                    if not banner:
                        print("    Banner: no data returned by service")
                    if banner_error:
                        print(f"    Banner/HTTP read result: {banner_error}")
                model_level, model_score, model_note = assess_service_risk(service, banner, model=model)
                if verbose:
                    print(f"Verification type: model risk assessment -> scoring service exposure for {service}")
                severity = "medium" if port in {22, 80, 443, 3389, 5900} else "low"
                if model_level == "high":
                    severity = "high"
                elif model_level == "medium":
                    severity = "medium"
                final_note = f"{note} {model_note}"
                finding = {
                    "port": port,
                    "service": service,
                    "status": "open",
                    "severity": severity,
                    "banner": banner,
                    "banner_status": "read_error" if banner_error else "received" if banner else "empty",
                    "banner_error": banner_error,
                    "http_status": http_status,
                    "notes": final_note,
                    "model_score": round(float(model_score), 6),
                    "model_level": model_level,
                }
                findings.append(finding)
                if verbose:
                    print(f"    -> Open port found: {port}/{service} [{severity} risk]")
                    if banner:
                        print(f"    -> Banner: {banner[:120]}")
                    if model is not None:
                        print(f"    -> Model comparison signal: {model_level} (risk-vs-safe delta={model_score:.4f})")
                    else:
                        print("    -> Model comparison signal: unavailable")
                    print(f"    -> Assessment: {model_note}")
                    print(f"    -> Port processing elapsed: {perf_counter() - port_started:.3f}s")
        except OSError as exc:
            connection_failures += 1
            if verbose:
                print(f"    TCP connection: failed ({type(exc).__name__}: {exc})")
                print(f"    -> Port processing elapsed: {perf_counter() - port_started:.3f}s")
            continue

    total_open = len(findings)
    risk_levels = [entry["severity"] for entry in findings]
    if any(level == "high" for level in risk_levels):
        summary_risk = "high"
    elif any(level == "medium" for level in risk_levels):
        summary_risk = "medium"
    elif total_open:
        summary_risk = "low"
    else:
        summary_risk = "low"

    duration = perf_counter() - scan_started
    report = {
        "schema_version": 1,
        "target": validated,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "authorized": authorized,
        "timeout_seconds": timeout,
        "duration_seconds": round(duration, 3),
        "ports_checked": list(port_list),
        "findings": findings,
        "summary": {
            "open_ports": [entry["port"] for entry in findings],
            "total_open": total_open,
            "risk_level": summary_risk,
        },
    }

    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    if verbose:
        severity_counts = {
            level: sum(entry["severity"] == level for entry in findings)
            for level in ("high", "medium", "low")
        }
        print("=" * 72)
        print("SCAN SUMMARY")
        print(f"Target: {validated}")
        print(f"Ports attempted: {len(port_list)}; reported open: {len(findings)}")
        print(f"TCP connection failures: {connection_failures}; banner read failures: {banner_failures}")
        print(f"Open-port severity counts: high={severity_counts['high']}, medium={severity_counts['medium']}, low={severity_counts['low']}")
        print(f"Overall risk level: {summary_risk}")
        print(f"Total elapsed: {duration:.3f}s")
        print(f"[3/4] Assessment complete: {len(findings)} open port(s) detected.")
        print(f"[4/4] JSON report saved to {destination} ({destination.stat().st_size} bytes)")
        print("=" * 72)

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a local private-network vulnerability scan and store a JSON report.")
    parser.add_argument("target", help="Private local IPv4 address, such as 192.168.1.25")
    parser.add_argument("--ports", default="22,80,443,3306,5432,8080,3389,5900", help="Comma-separated list of ports to test.")
    parser.add_argument("--timeout", type=float, default=1.0, help="Socket timeout in seconds.")
    parser.add_argument("--output", default="reports/local_vuln_scan.json", help="JSON output path.")
    parser.add_argument("--checkpoint", default=None, help="Optional trained checkpoint for model-assisted risk scoring.")
    parser.add_argument("--authorized", action="store_true", help="Confirm explicit authorization for this target.")
    args = parser.parse_args(argv)

    try:
        run_scan(
            args.target,
            authorized=args.authorized,
            ports=args.ports,
            timeout=args.timeout,
            output_path=args.output,
            checkpoint=args.checkpoint,
            verbose=True,
        )
    except ScanError as exc:
        parser.exit(2, f"Error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
