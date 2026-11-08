"""Offline scan interpretation with local llama.cpp + required RAG and strict review.

This module NEVER invokes a scanner or executes suggested commands. Model text
is treated as untrusted proposals and checked against the security-tool policy.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

try:
    from .config import load_config
    from . import rag, security_tools
except ImportError:
    from config import load_config
    import rag
    import security_tools

SETTINGS = load_config("scan_intelligence")


class AnalysisError(ValueError):
    pass


def _read(path, size):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > size:
        raise AnalysisError("Input must be a regular file within the configured byte limit.")
    return path.read_bytes()


def parse_nmap(raw, target):
    """Extract only bounded service facts from Nmap XML, never scripts or commands."""
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise AnalysisError("DTD and XML entities are not accepted.")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise AnalysisError(f"Invalid Nmap XML: {exc}") from exc
    if root.tag != "nmaprun":
        raise AnalysisError("Expected an Nmap nmaprun XML document.")
    found = []
    for host in root.findall("host"):
        addresses = {value.get("addr") for value in host.findall("address")
                     if value.get("addrtype") == "ipv4"}
        if target not in addresses:
            continue
        for port in host.findall("./ports/port"):
            if port.get("protocol") != "tcp":
                continue
            state = port.find("state")
            if state is None or state.get("state") != "open":
                continue
            try:
                number = int(port.get("portid", ""))
            except ValueError:
                continue
            if not 1 <= number <= 65535:
                continue
            service = port.find("service")
            attrs = service.attrib if service is not None else {}
            found.append({"port": number, "protocol": "tcp", "state": "open",
                          "service": str(attrs.get("name", ""))[:64],
                          "product": str(attrs.get("product", ""))[:100],
                          "version": str(attrs.get("version", ""))[:64]})
    return sorted({item["port"]: item for item in found}.values(), key=lambda x: x["port"])


def parse_nuclei(raw, target, maximum):
    findings = []
    for line in raw.decode("utf-8").splitlines():
        if not line.strip():
            continue
        if len(line) > 65536:
            raise AnalysisError("Nuclei JSONL line exceeds the limit.")
        try:
            value = json.loads(line)
        except ValueError as exc:
            raise AnalysisError("Invalid Nuclei JSONL record.") from exc
        if not isinstance(value, dict):
            raise AnalysisError("Nuclei records must be JSON objects.")
        # Nuclei reports must refer to this exact approved host.
        from urllib.parse import urlsplit
        matched = str(value.get("matched-at", ""))
        try:
            host = urlsplit(matched).hostname
        except ValueError:
            host = None
        if host != target:
            continue
        info = value.get("info") or {}
        if not isinstance(info, dict):
            info = {}
        findings.append({"template_id": str(value.get("template-id", ""))[:100],
                         "name": str(info.get("name", ""))[:160],
                         "severity": str(info.get("severity", "unknown"))[:24],
                         "matched_at": matched[:256],
                         "status": "tool_reported_unverified"})
        if len(findings) >= maximum:
            break
    return findings


def _local_chat(messages, settings):
    """Use a loopback-only llama.cpp OpenAI-compatible server, no remote API."""
    host = settings.get('host', '127.0.0.1')
    if host not in {'127.0.0.1', 'localhost'}:
        raise AnalysisError('Local inference requires a loopback host.')
    endpoint = f'http://{host}:{settings["port"]}/v1/chat/completions'
    payload = json.dumps({"model": settings["model"], "messages": messages,
                          "stream": False, "temperature": settings["temperature"],
                          "max_tokens": settings["max_tokens"],
                          "response_format": {"type": "json_object"}}).encode("utf-8")
    request = urllib.request.Request(endpoint, data=payload,
                                     headers={"Content-Type": "application/json"}, method="POST")
    try:
        # Explicitly disable proxy use and redirects for the local endpoint.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        with opener.open(request, timeout=settings["timeout_seconds"]) as response:
            data = response.read(settings["max_response_bytes"] + 1)
        if len(data) > settings["max_response_bytes"]:
            raise AnalysisError("Local LLM response exceeds size limit.")
        result = json.loads(data)
        return result["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError, urllib.error.URLError, TimeoutError) as exc:
        raise AnalysisError(f"Local LLM unavailable or invalid response: {exc}") from exc


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise AnalysisError("Local LLM redirects are not allowed.")


def _validate_proposal(data, target, references, settings):
    if not isinstance(data, dict):
        raise AnalysisError("The model response must be a JSON object.")
    hypotheses, checks = data.get("hypotheses"), data.get("checks")
    if not isinstance(hypotheses, list) or not isinstance(checks, list):
        raise AnalysisError("Expected hypotheses and checks arrays.")
    if len(hypotheses) > settings["max_hypotheses"] or len(checks) > settings["max_checks"]:
        raise AnalysisError("The model exceeded the analysis item limits.")
    supported = {item["id"] for item in references}
    cleaned = []
    for item in hypotheses:
        if not isinstance(item, dict):
            raise AnalysisError("Invalid hypothesis.")
        note = item.get("description")
        ids = item.get("sources")
        if (not isinstance(note, str) or not 1 <= len(note) <= settings["max_note_chars"]
                or not note.isprintable() or not isinstance(ids, list) or not ids
                or any(type(i) is not int or i not in supported for i in ids)):
            raise AnalysisError("Invalid hypothesis text or source references.")
        cleaned.append({"description": note, "sources": sorted(set(ids)),
                        "status": "unverified_hypothesis", "confirmed": False})
    reviewed = []
    for item in checks:
        if not isinstance(item, dict):
            raise AnalysisError("Invalid verification proposal.")
        tool, args, reason = item.get("tool"), item.get("args"), item.get("reason")
        if tool not in ("nmap", "nuclei") or not isinstance(reason, str) or not 1 <= len(reason) <= settings["max_note_chars"] or not reason.isprintable():
            raise AnalysisError("Unsupported verification proposal.")
        security_tools.validate_target(tool, target if tool == "nmap" else f"http://{target}")
        normalized = security_tools.validate_arguments(tool, args)
        reviewed.append({"tool": tool, "args": normalized, "reason": reason,
                         "status": "review_required", "executed": False})
    return cleaned, reviewed


def analyze(target, *, nmap_xml=None, nuclei_jsonl=None, database=None, authorized=False,
            settings=None, client=None):
    settings = settings or SETTINGS
    if authorized is not True:
        raise AnalysisError("Explicit authorization is required for this target.")
    target = security_tools.validate_target("nmap", target)
    if nmap_xml is None and nuclei_jsonl is None:
        raise AnalysisError("Provide at least one saved scan result.")
    ports = parse_nmap(_read(nmap_xml, settings["max_input_bytes"]), target) if nmap_xml else []
    nuclei = parse_nuclei(_read(nuclei_jsonl, settings["max_input_bytes"]), target,
                          settings["max_findings"]) if nuclei_jsonl else []
    ports = ports[:settings["max_findings"]]
    observed = {"target": target, "open_tcp": ports, "nuclei": nuclei}
    terms = " ".join(f'{p["service"]} {p["product"]} {p["version"]}' for p in ports)
    query = (terms + " " + " ".join(n["template_id"] for n in nuclei)).strip()
    path = Path(database or load_config("rag")["database"])
    if not path.is_file():
        raise AnalysisError("RAG index is required. Run: python vorpal.py knowledge update")
    if not query:
        raise AnalysisError("No service or finding evidence to query the RAG index.")
    docs = rag.search(query[:settings["max_query_chars"]], path,
                      limit=settings["rag_results"])
    if not docs:
        raise AnalysisError("RAG returned no relevant documents. Review and update the corpus before analysis.")
    sources = [{"id": idx, "type": "document", "source": doc["source"],
                "line": doc["line"], "sha256": doc["sha256"]} for idx, doc in enumerate(docs, 1)]
    context = [{"id": source["id"], "text": doc["text"][:settings["max_excerpt_chars"]]}
               for source, doc in zip(sources, docs)]
    system = settings["system_prompt"]
    user = json.dumps({"observations": observed, "references": context},
                      ensure_ascii=True)
    output = (client or _local_chat)([{"role": "system", "content": system},
                                      {"role": "user", "content": user}], settings)
    if not isinstance(output, str):
        raise AnalysisError("The LLM must return text containing one JSON object.")
    try:
        response = json.loads(output.strip())
    except ValueError as exc:
        raise AnalysisError("The LLM returned invalid JSON.") from exc
    hypotheses, proposals = _validate_proposal(response, target, sources, settings)
    report = {"target": target, "backend": "local_openai_compatible", "rag_required": True,
              "observations": observed, "sources": sources,
              "hypotheses": hypotheses, "next_checks": proposals,
              "generated_at": datetime.now(timezone.utc).isoformat(),
              "status": "review_required", "automatic_execution": False,
              "caveat": "All model hypotheses and tool-reported findings require independent verification."}
    return report


def main():
    parser = argparse.ArgumentParser(description="Review saved scan data with a local LLM (no scanning)")
    parser.add_argument("--target", required=True, help="Single authorized local IPv4 address")
    parser.add_argument("--nmap-xml")
    parser.add_argument("--nuclei-jsonl")
    parser.add_argument("--rag-db")
    parser.add_argument("--authorized", action="store_true")
    parser.add_argument("--output", help="JSON report destination (optional)")
    args = parser.parse_args()
    try:
        report = analyze(args.target, nmap_xml=args.nmap_xml, nuclei_jsonl=args.nuclei_jsonl,
                         database=args.rag_db, authorized=args.authorized)
        if args.output:
            destination = Path(args.output)
            if destination.is_symlink():
                raise AnalysisError("Output must not be a symbolic link.")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2, ensure_ascii=False))
    except (AnalysisError, security_tools.ToolPlanError, OSError, UnicodeError) as exc:
        parser.exit(2, f"Analysis error: {exc}\n")


if __name__ == "__main__":
    main()
