from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "local_dashboard"


class LocalDashboardContractTests(unittest.TestCase):
    def test_dashboard_files_exist(self) -> None:
        expected = {
            "index.html",
            "api.js",
            "app.js",
            "styles.css",
            "api.test.js",
        }
        self.assertEqual(
            {path.name for path in DASHBOARD.iterdir() if path.is_file()},
            expected,
        )

    def test_html_uses_only_local_assets(self) -> None:
        html = (DASHBOARD / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="./styles.css"', html)
        self.assertIn('src="./api.js"', html)
        self.assertIn('src="./app.js"', html)
        self.assertIsNone(re.search(r"https?://", html, flags=re.IGNORECASE))

    def test_api_reads_the_formal_derived_statistics_path(self) -> None:
        source = (DASHBOARD / "api.js").read_text(encoding="utf-8")
        self.assertIn(
            'const DEFAULT_DATA_URL = "/data/reports/spending_statistics.json";',
            source,
        )
        self.assertIn("const SUPPORTED_SCHEMA_VERSION = 1;", source)
        self.assertNotIn("mock-data", source)
        self.assertNotIn("wx.", source)

    def test_dashboard_does_not_embed_statistics_payload(self) -> None:
        html = (DASHBOARD / "index.html").read_text(encoding="utf-8")
        self.assertNotIn('"total_spending_minor"', html)
        self.assertNotIn('type="application/json"', html)


if __name__ == "__main__":
    unittest.main()
