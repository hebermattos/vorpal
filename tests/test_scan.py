import argparse
import io
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import scan


class ScanTests(unittest.TestCase):
    def test_retrieves_imported_text_and_preserves_exact_target(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus = Path(directory)
            (corpus / "imported").mkdir()
            (corpus / "imported" / "manual.txt").write_text(
                "Para cabeçalhos HTTP use curl HEAD", encoding="utf-8")
            generate = Mock(return_value='{"profile":"http-head"}')
            target = "https://example.org/a/../{b,c}?x=1&y=2"
            result = scan.plan("url", target, "Ver cabeçalhos", "local", corpus,
                               generate=generate)
        self.assertIn("curl HEAD", generate.call_args.args[1])
        self.assertEqual(result["references"], ["imported/manual.txt"])
        self.assertEqual(result["command"][-2:], ["--", target])

    def test_rejects_model_attempts_to_supply_scope_command_or_options(self):
        for response in ('[]', 'curl --location', '{"profile":"discover"}',
                         '{"profile":"http-head","target":"https://other.example"}',
                         '{"profile":"http-head","command":["sh"]}',
                         '{"profile":"http-head --location"}', '{"profile":[]}'):
            with self.subTest(response=response), self.assertRaises(scan.ScanError):
                scan.plan("url", "https://example.org", "headers", "local",
                          generate=Mock(return_value=response))

    @patch("scan.build_opener")
    def test_model_request_uses_loopback_and_scope_specific_schema(self, build_opener):
        opener = build_opener.return_value
        opener.open.return_value = io.BytesIO(json.dumps({"response": '{"profile":"discover"}'}).encode())
        result = scan.plan("lan", "192.168.1.0/24", "descobrir hosts", "local")
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:11434/api/generate")
        payload = json.loads(request.data)
        self.assertFalse(payload["stream"])
        self.assertEqual(payload["format"]["properties"]["profile"]["enum"],
                         ["discover", "tcp-common"])
        self.assertEqual(result["profile"], "discover")
        self.assertIn("-sn", result["command"])

    def test_invalid_scope_stops_before_model(self):
        generate = Mock()
        for scope, target in (
            ("url", "file:///etc/passwd"), ("url", "https://u:p@example.org"),
            ("url", "https://example.org:65536"), ("url", "https://example.org/#"),
            ("url", "https://example.org\\@other.example"), ("url", "https://example.org\nx"),
            ("lan", "8.8.8.0/24"), ("lan", "100.64.0.0/24"),
            ("lan", "192.168.1.1/24"), ("lan", "192.168.0.0/23"),
            ("lan", "192.168.1.1"), ("lan", "192.168.1.0/255.255.255.0"),
        ):
            with self.subTest(target=target), self.assertRaises(scan.ScanError):
                scan.plan(scope, target, "teste", "local", generate=generate)
        generate.assert_not_called()

    @patch("scan.subprocess.run")
    def test_rebuilds_command_only_after_authorization_and_summarizes_ports(self, run):
        proposed = {"scope": "lan", "target": "192.168.1.0/24", "profile": "tcp-common",
                    "command": ["sh", "-c", "other"]}
        with self.assertRaises(scan.ScanError):
            scan.execute(proposed)
        run.assert_not_called()
        run.return_value = subprocess.CompletedProcess([], 0, stdout=(
            b"Host: 192.168.1.10 ()\tPorts: 80/open/tcp//http///, "
            b"443/closed/tcp//https///, 1080/open/tcp//socks///\n"), stderr=b"")
        report = scan.execute(proposed, authorized=True)
        self.assertEqual(run.call_args.args[0][0], "nmap")
        self.assertEqual(run.call_args.args[0][-1], "192.168.1.0/24")
        self.assertEqual(report["summary"]["open_tcp_ports"],
                         [{"host": "192.168.1.10", "ports": [80, 1080]}])

    @patch.object(argparse.ArgumentParser, "error", side_effect=SystemExit(2))
    @patch("scan.plan")
    def test_cli_requires_one_scope_and_authorization_before_planning(self, plan, error):
        common = ["--goal", "teste", "--model", "local"]
        for extra in (["--url", "https://example.org", "--execute"],
                      ["--url", "https://example.org", "--lan", "192.168.1.0/24"], []):
            with self.assertRaises(SystemExit):
                scan.main(common + extra)
        plan.assert_not_called()

    @unittest.skipUnless(shutil.which("curl"), "curl não instalado")
    def test_real_curl_does_not_follow_redirect_or_expand_url(self):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                self.send_response(302)
                self.send_header("Location", "/fora-do-escopo")
                self.end_headers()

            def log_message(self, *args):
                pass

        with HTTPServer(("127.0.0.1", 0), Handler) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                path = "/a/../{b,c}?x=1"
                report = scan.execute({
                    "scope": "url", "profile": "http-get",
                    "target": f"http://127.0.0.1:{server.server_port}{path}",
                }, authorized=True)
                self.assertEqual(report["exit_code"], 0)
                self.assertEqual(report["summary"]["http_status"], 302)
                self.assertEqual(requests, [path])
            finally:
                server.shutdown()
                worker.join()
