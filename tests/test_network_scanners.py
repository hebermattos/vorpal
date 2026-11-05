import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from network_scanners import ScanError, TOOLS, build_command, execute, validate_target


class ScannerTests(unittest.TestCase):
    def test_five_tools_have_fixed_commands(self):
        targets = {
            "naabu": "192.0.2.1",
            "rustscan": "192.0.2.1",
            "nuclei": "https://example.org",
            "httpx": "https://example.org",
            "arp-scan": "192.168.1.2",
        }
        self.assertEqual(set(targets), set(TOOLS))
        for tool, target in targets.items():
            with self.subTest(tool=tool):
                command = build_command(tool, target, "eth0" if tool == "arp-scan" else None)
                self.assertEqual(command[0], tool)
                self.assertIn(target, command)
                self.assertNotIn("sh", command)
        self.assertIn("-g", build_command("rustscan", "127.0.0.1"))

    def test_reject_injection_and_multi_targets(self):
        inputs = ["-h", "example.org;id", "host one", "127.0.0.1,127.0.0.2",
                  "127.0.0.0/24", "../../targets", "example.org\n--help", ""]
        for target in inputs:
            for tool in ("naabu", "rustscan", "arp-scan"):
                with self.subTest(tool=tool, target=target):
                    with self.assertRaises(ScanError):
                        validate_target(tool, target)
        for target in ("file:///etc/passwd", "https://u:p@example.com",
                       "http://example.com/#fragment", "https://example.org\n--help"):
            with self.assertRaises(ScanError):
                validate_target("nuclei", target)

    def test_interface_validation(self):
        for interface in (None, "-bad", "en0;id", "bad iface"):
            with self.subTest(interface=interface):
                with self.assertRaises(ScanError):
                    build_command("arp-scan", "192.168.1.2", interface)
        with self.assertRaises(ScanError):
            build_command("naabu", "127.0.0.1", "eth0")
        with self.assertRaises(ScanError):
            build_command("rustscan", "example.org")

    def test_explicit_authorization_and_timeout(self):
        with self.assertRaises(ScanError):
            execute("naabu", "127.0.0.1")
        with self.assertRaises(ScanError):
            execute("naabu", "127.0.0.1", authorized=True, timeout=999)

    @patch("network_scanners.subprocess.run")
    @patch("network_scanners.shutil.which", return_value="/usr/bin/naabu")
    def test_execution_records_json_without_shell(self, which, run):
        run.return_value = SimpleNamespace(returncode=0,
            stdout=b'{"ip":"127.0.0.1","port":80}\n', stderr=b"")
        with tempfile.TemporaryDirectory() as tmp:
            output, data = execute("naabu", "127.0.0.1", authorized=True,
                                   output_dir=tmp, timeout=3)
            saved = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(saved["tool"], "naabu")
            self.assertEqual(saved["exit_code"], 0)
            self.assertEqual(saved["schema_version"], 1)
            self.assertEqual(data["target"], "127.0.0.1")
            self.assertTrue(run.called)
            self.assertIsInstance(run.call_args.args[0], list)
            self.assertEqual(run.call_args.kwargs["timeout"], 3)

    @patch("network_scanners.subprocess.run")
    @patch("network_scanners.shutil.which", return_value="/usr/bin/httpx")
    def test_reject_oversized_output(self, which, run):
        run.return_value = SimpleNamespace(returncode=0, stdout=b"x" * (1024 * 1024 + 1),
                                           stderr=b"")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ScanError):
                execute("httpx", "https://example.org", authorized=True, output_dir=tmp)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    @patch("network_scanners.shutil.which", return_value=None)
    def test_missing_binary(self, which):
        with self.assertRaisesRegex(ScanError, "não instalada"):
            execute("naabu", "127.0.0.1", authorized=True)


if __name__ == "__main__":
    unittest.main()
