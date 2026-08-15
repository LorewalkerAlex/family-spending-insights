from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from family_spending.config import ConfigurationError, load_app_config, load_email_credentials


class ConfigTests(unittest.TestCase):
    def test_relative_data_root_is_anchored_to_config_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "family-spending.toml"
            config_path.write_text(
                "[storage]\ndata_root = './sandbox/household'\n",
                encoding="utf-8",
            )
            config = load_app_config(config_path)

        self.assertEqual(config.storage.data_root, (root / "sandbox" / "household").resolve())
        self.assertEqual(config.server.port, 8765)
        self.assertEqual(config.runtime.email_poll_interval_seconds, 900)

    def test_config_loads_explicit_runtime_and_source_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "family-spending.toml"
            config_path.write_text(
                """[storage]\ndata_root = './data'\n\n[server]\nhost = '0.0.0.0'\nport = 9000\n\n[runtime]\nemail_poll_interval_seconds = 60\n\n[sources.cmb_email]\nenabled = false\nhost = 'imap.example.com'\nport = 1993\nmailbox = 'Bills'\nsubject_keyword = 'Statement'\n""",
                encoding="utf-8",
            )
            config = load_app_config(config_path)

        self.assertEqual(config.server.host, "0.0.0.0")
        self.assertEqual(config.server.port, 9000)
        self.assertFalse(config.sources.cmb_email.enabled)
        self.assertEqual(config.sources.cmb_email.mailbox, "Bills")

    def test_rebuild_config_points_to_rebuild_local_runtime_sandbox(self) -> None:
        rebuild_root = Path(__file__).resolve().parents[2]
        config = load_app_config(rebuild_root / "rebuild.toml")
        self.assertEqual(
            config.storage.data_root,
            (rebuild_root / ".runtime" / "household").resolve(),
        )

    def test_storage_data_root_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "family-spending.toml"
            path.write_text("[server]\nport = 8765\n", encoding="utf-8")
            with self.assertRaisesRegex(ConfigurationError, "storage.data_root"):
                load_app_config(path)

    def test_email_credentials_stay_out_of_toml_contract(self) -> None:
        credentials = load_email_credentials(
            {"EMAIL_ADDR": "user@example.com", "EMAIL_AUTH_CODE": "secret"}
        )
        self.assertEqual(credentials.address, "user@example.com")
        self.assertEqual(credentials.auth_code, "secret")
        with self.assertRaisesRegex(ConfigurationError, "EMAIL_AUTH_CODE"):
            load_email_credentials({"EMAIL_ADDR": "user@example.com"})


if __name__ == "__main__":
    unittest.main()
