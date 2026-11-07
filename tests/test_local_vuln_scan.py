import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local_vuln_scan import ScanError, validate_target, run_scan


class LocalVulnScanTests(unittest.TestCase):
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
        self.assertIn("[PORT 1/1] 192.168.0.10:80 (http)", output)
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
