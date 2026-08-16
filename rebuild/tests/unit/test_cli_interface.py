from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from family_spending.interfaces.cli.main import build_parser, main


class _FakeServer:
    server_address = ("127.0.0.1", 9876)

    def __init__(self) -> None:
        self.served = False
        self.closed = False

    def serve_forever(self) -> None:
        self.served = True

    def server_close(self) -> None:
        self.closed = True


class CliInterfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        root = Path(self.temp_dir.name).resolve()
        self.config_path = root / "test.toml"
        self.config_path.write_text(
            '[storage]\ndata_root = "./household"\n\n[server]\nhost = "127.0.0.1"\nport = 8765\n',
            encoding="utf-8",
        )

    def _run(self, *args: str) -> str:
        stream = io.StringIO()
        with redirect_stdout(stream):
            main(["--config", str(self.config_path), *args])
        return stream.getvalue()

    def test_parser_preserves_operator_commands_with_explicit_config_boundary(self) -> None:
        parser = build_parser()
        self.assertEqual(parser.parse_args(["sync"]).command, "sync")
        self.assertEqual(parser.parse_args(["jobs", "run-due"]).job, "run-due")
        self.assertEqual(
            parser.parse_args(["rebuild", "projections"]).rebuild_target,
            "projections",
        )
        self.assertEqual(parser.parse_args(["diagnose", "state"]).diagnose_target, "state")

    def test_sync_jobs_rebuild_and_diagnose_use_canonical_composition(self) -> None:
        sync = self._run("sync")
        self.assertIn("Source records: 0", sync)
        self.assertIn("Transactions: 0", sync)

        jobs = self._run("jobs", "run-due", "--as-of", "2026-08-16")
        self.assertIn("Scheduled occurrences generated: 0", jobs)

        rebuild = self._run("rebuild", "projections")
        self.assertIn("Transactions: 0", rebuild)
        self.assertIn("Total net spending: 0", rebuild)

        diagnose = self._run("diagnose", "state")
        self.assertIn("Runtime generation: 1", diagnose)
        self.assertIn("Pending Source records: 0", diagnose)

    def test_serve_uses_configured_application_and_http_transport(self) -> None:
        server = _FakeServer()
        with patch(
            "family_spending.interfaces.cli.main.create_http_server",
            return_value=server,
        ) as create:
            output = self._run("serve", "--port", "9876")
        self.assertTrue(server.served)
        self.assertTrue(server.closed)
        self.assertEqual(create.call_args.args[1:], ("127.0.0.1", 9876))
        self.assertIn("Family Spending API: http://127.0.0.1:9876", output)


if __name__ == "__main__":
    unittest.main()
