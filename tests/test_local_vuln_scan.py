import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local_vuln_scan import ScanError, check_cves, validate_target, run_scan, resolve_service_hint


GENERATE_MODULE = "src.generate" if resolve_service_hint.__module__.startswith("src.") else "generate"


class LocalVulnScanTests(unittest.TestCase):
    def setUp(self):
        checkpoint = patch.object(sys.modules[run_scan.__module__], "_resolve_checkpoint", return_value=None)
        checkpoint.start()
        self.addCleanup(checkpoint.stop)

    def test_service_hint_without_model_is_unknown(self):
        service, note = resolve_service_hint(22, "SSH-2.0-test")
        self.assertEqual(service, "unknown")
        self.assertIn("no trained checkpoint", note)

    @patch(f"{GENERATE_MODULE}.generate")
    def test_service_hint_uses_model_output(self, generate):
        generate.side_effect = lambda model, prompt, **kwargs: prompt + json.dumps({
            "service": "custom-service", "note": "Review access controls.",
        })
        model = object()
        self.assertEqual(resolve_service_hint(12345, "test banner", model=model),
                         ("custom-service", "Review access controls."))
        self.assertIs(generate.call_args.args[0], model)
        self.assertIn('"port": 12345', generate.call_args.args[1])
        self.assertIn("test banner", generate.call_args.args[1])

    @patch(f"{GENERATE_MODULE}.generate")
    def test_invalid_model_service_hint_is_unknown(self, generate):
        for output in ('invalid', '[]', '{"service": 22, "note": "test"}',
                       '{"service": "ssh", "note": ""}'):
            with self.subTest(output=output):
                generate.side_effect = lambda model, prompt, **kwargs: prompt + output
                service, note = resolve_service_hint(22, "", model=object())
                self.assertEqual(service, "unknown")
                self.assertIn("invalid or failed model response", note)

    @patch(f"{GENERATE_MODULE}.generate")
    def test_cve_check_uses_model_and_marks_suggestions_unverified(self, generate):
        generate.side_effect = lambda model, prompt, **kwargs: prompt + json.dumps({
            "cves": [{"id": "CVE-2024-0001", "description": "Example suggestion."}],
        })
        model = object()
        result = check_cves("example banner", model=model)
        self.assertEqual(result["status"], "unverified_suggestions")
        self.assertEqual(result["source"], "model")
        self.assertFalse(result["verified"])
        self.assertEqual(result["cves"][0]["id"], "CVE-2024-0001")
        self.assertIs(generate.call_args.args[0], model)
        self.assertIn("example banner", generate.call_args.args[1])

    @patch(f"{GENERATE_MODULE}.generate")
    def test_cve_check_without_model_or_banner_skips_generation(self, generate):
        for banner, model in (("example", None), ("", object())):
            result = check_cves(banner, model=model)
            self.assertEqual(result["status"], "not_checked")
            self.assertEqual(result["cves"], [])
        generate.assert_not_called()

    @patch(f"{GENERATE_MODULE}.generate")
    def test_cve_check_rejects_invalid_output(self, generate):
        for output in ('invalid', '[]', '{"cves": null}',
                       '{"cves": [{"id": "fake", "description": "test"}]}'):
            with self.subTest(output=output):
                generate.side_effect = lambda model, prompt, **kwargs: prompt + output
                result = check_cves("example", model=object())
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["cves"], [])

    @patch(f"{GENERATE_MODULE}.generate")
    def test_empty_model_cves_do_not_claim_no_vulnerabilities(self, generate):
        generate.side_effect = lambda model, prompt, **kwargs: prompt + '{"cves": []}'
        result = check_cves("example", model=object())
        self.assertEqual(result["status"], "unknown")
        self.assertFalse(result["verified"])

    def test_validate_target_accepts_local_private_ip(self):
        self.assertEqual(validate_target("192.168.0.254"), "192.168.0.254")

    def test_validate_target_rejects_non_local_ip(self):
        with self.assertRaises(ScanError):
            validate_target("8.8.8.8")

    @patch("local_vuln_scan.socket.create_connection")
    def test_run_scan_writes_report(self, create_connection):
        mock_socket = create_connection.return_value.__enter__.return_value
        mock_socket.recv.return_value = b"HTTP/1.1 200 OK\r\nServer: test\r\n"

        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "report.json"
            report = run_scan("192.168.0.10", output_path=output_path, ports=(80, 443), timeout=0.2, authorized=True)

            self.assertTrue(output_path.exists())
            self.assertEqual(report["target"], "192.168.0.10")
            self.assertIn("findings", report)
            self.assertTrue(report["findings"])
            self.assertEqual(report["findings"][0]["cve_check"]["status"], "not_checked")

    @patch("local_vuln_scan.socket.create_connection")
    def test_run_scan_logs_verification_types(self, create_connection):
        mock_socket = create_connection.return_value.__enter__.return_value
        mock_socket.recv.return_value = b"HTTP/1.1 200 OK\r\nServer: test\r\n"

        with patch("sys.stdout", new_callable=io.StringIO) as buffer:
            run_scan("192.168.0.10", ports=(80,), timeout=0.2, authorized=True, verbose=True)

        output = buffer.getvalue()
        self.assertIn("Verification type: target validation", output)
        self.assertIn("Verification type: TCP connection check", output)
        self.assertIn("Verification type: banner inspection", output)
        self.assertIn("Verification type: model risk assessment", output)
        self.assertIn("VORPAL LOCAL NETWORK SCAN", output)
        self.assertIn("Authorization: confirmed by --authorized", output)
        self.assertIn("[PORT 1/1] 192.168.0.10:80", output)
        self.assertIn("Port processing elapsed:", output)
        self.assertIn("SCAN SUMMARY", output)
        self.assertIn("Overall risk level:", output)

    @patch("src.local_vuln_scan._resolve_checkpoint", return_value=None)
    @patch("local_vuln_scan.socket.create_connection")
    def test_http_500_response_is_reported_for_open_port(self, create_connection, _checkpoint):
        mock_socket = create_connection.return_value.__enter__.return_value
        mock_socket.recv.return_value = b"HTTP/1.1 500 Internal Server Error\r\nContent-Length: 0\r\n\r\n"
        with tempfile.TemporaryDirectory() as tmp:
            report = run_scan(
                "192.168.0.10", ports=(80,), authorized=True,
                output_path=Path(tmp) / "report.json", verbose=False,
            )
        finding = report["findings"][0]
        self.assertEqual(finding["status"], "open")
        self.assertEqual(finding["http_status"], 500)
        mock_socket.sendall.assert_called_once()
        self.assertIn(b"HEAD / HTTP/1.1", mock_socket.sendall.call_args.args[0])

    @patch("src.local_vuln_scan._resolve_checkpoint", return_value=None)
    @patch("local_vuln_scan.socket.create_connection")
    def test_tcp_open_port_is_reported_when_peer_resets(self, create_connection, _checkpoint):
        mock_socket = create_connection.return_value.__enter__.return_value
        mock_socket.recv.side_effect = ConnectionResetError("peer reset")
        with tempfile.TemporaryDirectory() as tmp:
            report = run_scan(
                "192.168.0.10", ports=(8080,), authorized=True,
                output_path=Path(tmp) / "report.json", verbose=False,
            )
        finding = report["findings"][0]
        self.assertEqual(finding["status"], "open")
        self.assertEqual(finding["banner_status"], "read_error")
        self.assertIn("ConnectionResetError", finding["banner_error"])


if __name__ == "__main__":
    unittest.main()
