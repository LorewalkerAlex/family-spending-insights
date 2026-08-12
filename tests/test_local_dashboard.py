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
            "mapping-review.js",
            "mapping-review.css",
            "mapping-review-api.test.js",
            "scheduled-input.js",
            "scheduled-input.css",
            "financial-summary-api.js",
            "financial-summary-api.test.js",
            "financial-summary.js",
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
        self.assertIn('href="./mapping-review.css"', html)
        self.assertIn('href="./scheduled-input.css"', html)
        self.assertIn('src="../node_modules/chart.js/dist/chart.umd.js"', html)
        self.assertIn('src="./api.js"', html)
        self.assertIn('src="./charts.js"', html)
        self.assertIn('src="./app.js"', html)
        self.assertIn('src="./application-api.js"', html)
        self.assertIn('src="./financial-summary-api.js"', html)
        self.assertIn('src="./financial-summary.js"', html)
        self.assertIn('src="./transactions.js"', html)
        self.assertIn('src="./manual-entry.js"', html)
        self.assertIn('src="./mapping-review.js"', html)
        self.assertIn('src="./scheduled-input.js"', html)
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

    def test_financial_summary_is_a_separate_sidecar_contract(self) -> None:
        html = (DASHBOARD / "index.html").read_text(encoding="utf-8")
        api_source = (DASHBOARD / "financial-summary-api.js").read_text(encoding="utf-8")
        view_source = (DASHBOARD / "financial-summary.js").read_text(encoding="utf-8")
        self.assertIn("data-financial-summary", html)
        self.assertIn("data-financial-month-select", html)
        self.assertIn("累计净现金流", html)
        self.assertIn('const DEFAULT_DATA_URL = "/data/reports/financial_summary.json";', api_source)
        self.assertIn("const SUPPORTED_SCHEMA_VERSION = 1;", api_source)
        self.assertIn("net_cash_flow_minor", api_source)
        self.assertIn("spending_data_complete", api_source)
        self.assertIn("createFinancialSummaryService", view_source)
        self.assertIn("消费侧覆盖完整", view_source)
        self.assertNotIn("aggregate", view_source.lower())

    def test_transaction_workspace_uses_application_api_contract(self) -> None:
        html = (DASHBOARD / "index.html").read_text(encoding="utf-8")
        api_source = (DASHBOARD / "application-api.js").read_text(encoding="utf-8")
        workspace_source = (DASHBOARD / "transactions.js").read_text(encoding="utf-8")
        self.assertIn("data-transactions-workspace", html)
        self.assertIn("data-enrichment-merchant", html)
        self.assertIn("data-enrichment-category", html)
        self.assertIn("data-enrichment-note", html)
        self.assertIn('const DEFAULT_API_BASE = "http://127.0.0.1:8765/api";', api_source)
        self.assertIn('"income_default"', api_source)
        self.assertIn('request("/categories")', api_source)
        self.assertIn('request("/transactions")', api_source)
        self.assertIn('method: "PATCH"', api_source)
        self.assertIn("service.updateEnrichment", workspace_source)
        self.assertIn('transaction.type === "income"', workspace_source)
        self.assertIn("收入不使用 Merchant Mapping", workspace_source)
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
        self.assertIn('request("/manual-inputs")', api_source)
        self.assertIn('request("/manual-inputs", { method: "POST", body })', api_source)
        self.assertIn("/corrections", api_source)
        self.assertIn('method: "DELETE"', api_source)
        self.assertIn("service.createManualInput", entry_source)
        self.assertIn("service.getManualDescriptions", entry_source)
        self.assertIn("service.getManualInputs", entry_source)
        self.assertIn("service.correctManualInput", entry_source)
        self.assertIn("service.deleteManualInput", entry_source)
        self.assertIn("findSimilarManualDescriptions", entry_source)
        self.assertNotIn("service.getCategories", entry_source)
        self.assertNotIn("Reconciliation", entry_source)
        self.assertIn("Income 不进入 Merchant Mapping", html)

    def test_manual_input_management_keeps_source_correction_separate_from_enrichment(self) -> None:
        html = (DASHBOARD / "index.html").read_text(encoding="utf-8")
        entry_source = (DASHBOARD / "manual-entry.js").read_text(encoding="utf-8")
        self.assertIn("data-manual-input-management", html)
        self.assertIn("data-manual-input-list", html)
        self.assertIn("data-manual-correction-form", html)
        self.assertIn("data-action=\"correct-manual-input\"", html)
        self.assertIn("data-action=\"delete-manual-input\"", html)
        self.assertIn("生成新的 Source ID", html)
        self.assertIn("保留原系统 Transaction identity", html)
        self.assertIn("Merchant / 消费 Category 不在这里修改", html)
        self.assertIn("支出稳定规则走 Mapping Review", html)
        self.assertIn("真实单笔例外走 Transaction Workspace", html)
        self.assertIn("Note 属于当前 Enrichment", html)
        self.assertIn("item.transaction.enrichment.note", entry_source)

    def test_scheduled_input_is_orchestration_over_manual_source(self) -> None:
        html = (DASHBOARD / "index.html").read_text(encoding="utf-8")
        scheduled_source = (DASHBOARD / "scheduled-input.js").read_text(encoding="utf-8")
        manual_source = (DASHBOARD / "manual-entry.js").read_text(encoding="utf-8")
        api_source = (DASHBOARD / "application-api.js").read_text(encoding="utf-8")

        self.assertIn("data-scheduled-input", html)
        self.assertIn("data-scheduled-create-form", html)
        self.assertIn("data-scheduled-input-list", html)
        self.assertIn('data-action="run-scheduled-inputs"', html)
        self.assertIn("不是新的 Source", html)
        self.assertIn("1–28", html)
        self.assertIn("历史 Manual Source / Transaction 不会被修改或删除", html)
        self.assertIn('request("/scheduled-inputs")', api_source)
        self.assertIn('request("/scheduled-inputs/run-due"', api_source)
        self.assertIn("service.createScheduledInput", scheduled_source)
        self.assertIn("service.updateScheduledInput", scheduled_source)
        self.assertIn("service.deleteScheduledInput", scheduled_source)
        self.assertIn("service.runDueScheduledInputs", scheduled_source)
        self.assertIn("family-spending:manual-source-changed", scheduled_source)
        self.assertIn("family-spending:manual-source-changed", manual_source)
        self.assertNotIn("Reconciliation", scheduled_source)

    def test_dashboard_html_has_unique_ids(self) -> None:
        html = (DASHBOARD / "index.html").read_text(encoding="utf-8")
        ids = re.findall(r'\bid="([^"]+)"', html)
        self.assertEqual(len(ids), len(set(ids)))

    def test_mapping_review_uses_application_api_and_keeps_single_transaction_edits_separate(self) -> None:
        html = (DASHBOARD / "index.html").read_text(encoding="utf-8")
        api_source = (DASHBOARD / "application-api.js").read_text(encoding="utf-8")
        review_source = (DASHBOARD / "mapping-review.js").read_text(encoding="utf-8")
        self.assertIn("data-mapping-review", html)
        self.assertIn("data-mapping-review-list", html)
        self.assertIn("data-mapping-review-merchant", html)
        self.assertIn("data-mapping-review-category", html)
        self.assertIn("支出待分类 description 审核", html)
        self.assertIn("Income 不进入 Merchant Mapping", html)
        self.assertIn("更新 Mapping 并应用", html)
        self.assertIn("仅修改这一笔", html)
        self.assertIn('request("/mapping-reviews")', api_source)
        self.assertIn('request("/mapping-reviews/preview"', api_source)
        self.assertIn('request("/mapping-reviews/apply"', api_source)
        self.assertIn("service.previewMappingReview", review_source)
        self.assertIn("service.applyMappingReview", review_source)
        self.assertIn("findSimilarMerchantNames", review_source)
        self.assertNotIn("Reconciliation", review_source)

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
