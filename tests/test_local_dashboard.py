from __future__ import annotations

import json
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
            "charts.js",
            "app.js",
            "styles.css",
            "api.test.js",
            "charts.test.js",
            "application-api.js",
            "application-api.test.js",
            "manual-entry.js",
            "manual-entry.css",
            "transactions.js",
            "transactions.css",
        }
        self.assertEqual(
            {path.name for path in DASHBOARD.iterdir() if path.is_file()},
            expected,
        )

    def test_html_uses_only_local_assets(self) -> None:
        html = (DASHBOARD / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="./styles.css"', html)
        self.assertIn('href="./transactions.css"', html)
        self.assertIn('href="./manual-entry.css"', html)
        self.assertIn('src="../node_modules/chart.js/dist/chart.umd.js"', html)
        self.assertIn('src="./api.js"', html)
        self.assertIn('src="./charts.js"', html)
        self.assertIn('src="./app.js"', html)
        self.assertIn('src="./application-api.js"', html)
        self.assertIn('src="./transactions.js"', html)
        self.assertIn('src="./manual-entry.js"', html)
        self.assertIsNone(re.search(r"https?://", html, flags=re.IGNORECASE))

    def test_api_reads_formal_statistics_and_schema_v2(self) -> None:
        source = (DASHBOARD / "api.js").read_text(encoding="utf-8")
        self.assertIn(
            'const DEFAULT_DATA_URL = "/data/reports/spending_statistics.json";',
            source,
        )
        self.assertIn("const SUPPORTED_SCHEMA_VERSION = 2;", source)
        self.assertIn("summary.shown_data", source)
        self.assertIn("month.show", source)
        self.assertNotIn("mock-data", source)
        self.assertNotIn("wx.", source)

    def test_transaction_workspace_uses_application_api_contract(self) -> None:
        html = (DASHBOARD / "index.html").read_text(encoding="utf-8")
        api_source = (DASHBOARD / "application-api.js").read_text(encoding="utf-8")
        workspace_source = (DASHBOARD / "transactions.js").read_text(encoding="utf-8")
        self.assertIn("data-transactions-workspace", html)
        self.assertIn("data-enrichment-merchant", html)
        self.assertIn("data-enrichment-category", html)
        self.assertIn("data-enrichment-note", html)
        self.assertIn('const DEFAULT_API_BASE = "http://127.0.0.1:8765/api";', api_source)
        self.assertIn('request("/categories")', api_source)
        self.assertIn('request("/transactions")', api_source)
        self.assertIn('method: "PATCH"', api_source)
        self.assertIn("service.updateEnrichment", workspace_source)
        self.assertIn("单笔 Enrichment 例外", html)
        self.assertIn("不修改 Mapping", html)
        self.assertIn("elements.dashboardReload.click()", workspace_source)

    def test_manual_entry_uses_application_api_without_copying_reconciliation(self) -> None:
        html = (DASHBOARD / "index.html").read_text(encoding="utf-8")
        api_source = (DASHBOARD / "application-api.js").read_text(encoding="utf-8")
        entry_source = (DASHBOARD / "manual-entry.js").read_text(encoding="utf-8")
        self.assertIn("data-manual-entry-form", html)
        self.assertIn("data-manual-type", html)
        self.assertIn("data-manual-date", html)
        self.assertIn("data-manual-amount", html)
        self.assertIn("data-manual-description", html)
        self.assertIn("data-manual-description-suggestions", html)
        self.assertNotIn("data-manual-merchant", html)
        self.assertNotIn("data-manual-category", html)
        self.assertIn('request("/manual-descriptions")', api_source)
        self.assertIn('request("/manual-inputs", { method: "POST", body })', api_source)
        self.assertIn("service.createManualInput", entry_source)
        self.assertIn("service.getManualDescriptions", entry_source)
        self.assertIn("findSimilarManualDescriptions", entry_source)
        self.assertNotIn("service.getCategories", entry_source)
        self.assertNotIn("Reconciliation", entry_source)

    def test_chart_runtime_has_no_remote_url(self) -> None:
        source = (DASHBOARD / "charts.js").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"https?://", source, flags=re.IGNORECASE))
        self.assertIn("createChartRegistry", source)
        self.assertIn("categoryStackedBar", source)
        self.assertIn("categoryStackedArea", source)
        self.assertIn("categoryDoughnut", source)

    def test_dashboard_does_not_embed_statistics_payload(self) -> None:
        html = (DASHBOARD / "index.html").read_text(encoding="utf-8")
        self.assertNotIn('"total_spending_minor"', html)
        self.assertNotIn('type="application/json"', html)

    def test_chart_js_dependency_is_pinned(self) -> None:
        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(package["dependencies"]["chart.js"], "4.5.1")
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertRegex(gitignore, r"(?m)^node_modules/$")


if __name__ == "__main__":
    unittest.main()
