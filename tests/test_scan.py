import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import scan


class ScanTests(unittest.TestCase):
    @patch("scan.select_profile", return_value="http-head")
    def test_retrieves_imported_text_and_preserves_exact_target(self, select):
        with tempfile.TemporaryDirectory() as directory:
            corpus = Path(directory)
            (corpus / "imported").mkdir()
            (corpus / "imported" / "manual.txt").write_text(
                "Para cabeçalhos HTTP use curl HEAD", encoding="utf-8")
            target = "https://example.org/a/../{b,c}?x=1&y=2"
            checkpoint = Path("custom.keras")
            result = scan.plan("url", target, "Ver cabeçalhos", checkpoint, corpus)
        select.assert_called_once_with(
            checkpoint, "Ver cabeçalhos",
            [{"source": "imported/manual.txt", "excerpt": "Para cabeçalhos HTTP use curl HEAD"}],
            ("http-head", "http-get"), 4)
        self.assertEqual(result["references"], ["imported/manual.txt"])
        self.assertEqual(result["command"][-2:], ["--", target])

    @patch("scan.select_profile", return_value="http-head --location")
    def test_rejects_profile_outside_allowlist(self, select):
        with self.assertRaisesRegex(scan.ScanError, "Perfil não permitido"):
            scan.plan("url", "https://example.org", "headers")

    def test_missing_checkpoint_has_training_guidance(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.keras"
            with self.assertRaisesRegex(scan.ScanError, "src/train.py"):
                scan.plan("lan", "192.168.1.0/24", "descobrir hosts", missing)

    @patch("scan.select_profile")
    def test_invalid_scope_stops_before_model(self, select):
        for scope, target in (
            ("url", "file:///etc/passwd"), ("url", "https://u:p@example.org"),
            ("url", "https://example.org:65536"), ("url", "https://example.org/#"),
            ("url", "https://example.org\\@other.example"), ("url", "https://example.org\nx"),
            ("lan", "8.8.8.0/24"), ("lan", "100.64.0.0/24"),
            ("lan", "192.168.1.1/24"), ("lan", "192.168.0.0/23"),
            ("lan", "192.168.1.1"), ("lan", "192.168.1.0/255.255.255.0"),
        ):
            with self.subTest(target=target), self.assertRaises(scan.ScanError):
                scan.plan(scope, target, "teste")
        select.assert_not_called()

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
        common = ["--goal", "teste"]
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
