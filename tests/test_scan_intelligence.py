"""Offline fixtures for local LLM scan interpretation; no scans, downloads or training."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import scan_intelligence as si
import rag


NMAP = b'''<?xml version="1.0"?>
<nmaprun><host><address addr="192.168.1.10" addrtype="ipv4"/>
<ports><port protocol="tcp" portid="80"><state state="open"/>
<service name="http" product="nginx" version="1.18"/></port>
<port protocol="tcp" portid="22"><state state="closed"/></port>
</ports></host><host><address addr="192.168.1.11" addrtype="ipv4"/>
<ports><port protocol="tcp" portid="445"><state state="open"/></port></ports></host></nmaprun>'''


class ScanIntelligenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.xml = Path(self.tmp.name) / "scan.xml"
        self.xml.write_bytes(NMAP)
        self.target = "192.168.1.10"
        self.corpus = Path(self.tmp.name) / "corpus"
        self.corpus.mkdir()
        (self.corpus / "nginx.md").write_text(
            "HTTP nginx 1.18 server version checks are evidence, not proof of vulnerabilities.\n"
            "Verify configurations with safe, reviewed tools.\n", encoding="utf-8")
        self.db = Path(self.tmp.name) / "rag.sqlite3"
        rag.index(self.corpus, self.db)
        load = si.load_config
        patcher = patch.object(
            si, "load_config",
            side_effect=lambda name: {"database": str(self.db)} if name == "rag" else load(name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def model(self, content, seen=None):
        def reply(messages, settings):
            if seen is not None:
                seen.extend(messages)
            return json.dumps(content)
        return reply

    def test_parses_only_matching_open_tcp_ports(self):
        rows = si.parse_nmap(NMAP, self.target)
        self.assertEqual([row["port"] for row in rows], [80])
        self.assertEqual(rows[0]["product"], "nginx")
        self.assertEqual(si.parse_nmap(NMAP, "192.168.1.12"), [])

    def test_blocks_doctype(self):
        with self.assertRaises(si.AnalysisError):
            si.parse_nmap(b'<!DOCTYPE nmaprun [<!ENTITY x SYSTEM "file:///etc/passwd">]><nmaprun/>', self.target)

    def test_nuclei_filters_other_hosts(self):
        payload = "\n".join([
            json.dumps({"matched-at": "http://192.168.1.10/", "template-id": "server-header",
                        "info": {"name": "Server header", "severity": "info"}}),
            json.dumps({"matched-at": "http://8.8.8.8/", "template-id": "foreign"}),
        ]).encode()
        rows = si.parse_nuclei(payload, self.target, 10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "tool_reported_unverified")

    def test_rejects_unauthorized_target_before_model(self):
        with self.assertRaises(si.AnalysisError):
            si.analyze(self.target, nmap_xml=self.xml, client=self.model({}))
        with self.assertRaises(si.security_tools.ToolPlanError):
            si.analyze("8.8.8.8", nmap_xml=self.xml, authorized=True, client=self.model({}))

    def test_returns_review_only_hypotheses_and_validated_check(self):
        messages = []
        response = {"hypotheses": [{"description": "Review HTTP headers.", "sources": [1]}],
                    "checks": [{"tool": "nmap", "args": ["-sT", "-sV", "-p", "80"],
                                "reason": "Confirm the HTTP service version."}]}
        report = si.analyze(self.target, nmap_xml=self.xml, authorized=True,
                            database=self.db,
                            client=self.model(response, messages))
        self.assertEqual(report["observations"]["open_tcp"][0]["port"], 80)
        self.assertFalse(report["hypotheses"][0]["confirmed"])
        self.assertFalse(report["next_checks"][0]["executed"])
        self.assertEqual(report["next_checks"][0]["args"], ["-sT", "-sV", "-p", "80"])
        self.assertIn("observations", messages[1]["content"])

    def test_rejects_unvalidated_flags_and_unknown_sources(self):
        bad = {"hypotheses": [], "checks": [
            {"tool": "nmap", "args": ["--script", "vuln", "-p", "80"], "reason": "Not allowed"}]}
        with self.assertRaises(si.security_tools.ToolPlanError):
            si.analyze(self.target, nmap_xml=self.xml, authorized=True, client=self.model(bad))
        bad = {"hypotheses": [{"description": "Unverified", "sources": [99]}], "checks": []}
        with self.assertRaises(si.AnalysisError):
            si.analyze(self.target, nmap_xml=self.xml, authorized=True, client=self.model(bad))

    def test_rejects_malformed_and_excessive_model_output(self):
        with self.assertRaises(si.AnalysisError):
            si.analyze(self.target, nmap_xml=self.xml, authorized=True,
                       client=lambda messages, settings: "not JSON")
        output = {"hypotheses": [], "checks": []}
        with patch.object(si, "_local_chat", return_value=json.dumps(output)) as mocked:
            si.analyze(self.target, nmap_xml=self.xml, authorized=True)
            mocked.assert_called_once()

    def test_missing_or_empty_rag_fails_closed(self):
        missing = Path(self.tmp.name) / "missing.sqlite3"
        with self.assertRaisesRegex(si.AnalysisError, "RAG index is required"):
            si.analyze(self.target, nmap_xml=self.xml, authorized=True,
                       database=missing, client=self.model({}))
        with patch.object(si.rag, "search", return_value=[]):
            with self.assertRaisesRegex(si.AnalysisError, "no relevant documents"):
                si.analyze(self.target, nmap_xml=self.xml, authorized=True,
                           database=self.db, client=self.model({}))

    def test_hypothesis_requires_valid_document_evidence(self):
        response = {"hypotheses": [{"description": "Unverified.", "sources": []}], "checks": []}
        with self.assertRaises(si.AnalysisError):
            si.analyze(self.target, nmap_xml=self.xml, authorized=True,
                       database=self.db, client=self.model(response))
        response["hypotheses"][0]["sources"] = [True]
        with self.assertRaises(si.AnalysisError):
            si.analyze(self.target, nmap_xml=self.xml, authorized=True,
                       database=self.db, client=self.model(response))

    def test_input_limits_and_symlinks(self):
        symlink = Path(self.tmp.name) / "link.xml"
        symlink.symlink_to(self.xml)
        with self.assertRaises(si.AnalysisError):
            si.analyze(self.target, nmap_xml=symlink, authorized=True, client=self.model({}))
        limited = dict(si.SETTINGS, max_input_bytes=20)
        with self.assertRaises(si.AnalysisError):
            si.analyze(self.target, nmap_xml=self.xml, settings=limited,
                       authorized=True, client=self.model({}))


if __name__ == "__main__":
    unittest.main()
