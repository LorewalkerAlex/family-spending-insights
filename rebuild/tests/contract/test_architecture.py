from __future__ import annotations

import ast
import unittest
from pathlib import Path

import family_spending


REBUILD_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REBUILD_ROOT / "src" / "family_spending"
DOMAIN_FORBIDDEN_STDLIB = {
    "csv",
    "email",
    "http",
    "imaplib",
    "json",
    "os",
    "pathlib",
    "sqlite3",
    "tomllib",
    "urllib",
}
LEGACY_INTERNAL_PREFIXES = (
    "family_spending.backend",
    "family_spending.ingestion",
    "family_spending.enrichment_store",
    "family_spending.feedback",
    "family_spending.financial_projection",
    "family_spending.manual_source",
    "family_spending.mapping_review",
    "family_spending.month_coverage",
    "family_spending.refund_reconciliation",
    "family_spending.scheduled_input",
    "family_spending.settings",
    "family_spending.source_link_store",
    "family_spending.source_records",
    "family_spending.spending_projection",
    "family_spending.spending_statistics",
    "family_spending.statistics_serialization",
    "family_spending.transaction_resolution",
    "family_spending.transactions",
)


def imported_modules(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.append(node.module)
    return tuple(modules)


class ArchitectureContractTests(unittest.TestCase):
    def test_test_process_imports_rebuild_package_not_current_backend(self) -> None:
        module_path = Path(family_spending.__file__).resolve()
        self.assertTrue(module_path.is_relative_to(REBUILD_ROOT / "src"), module_path)

    def test_domain_has_no_infrastructure_or_serialization_dependencies(self) -> None:
        violations: list[str] = []
        for path in sorted((PACKAGE_ROOT / "domain").glob("*.py")):
            for module in imported_modules(path):
                root = module.split(".", 1)[0]
                if root in DOMAIN_FORBIDDEN_STDLIB or root in {"yaml"}:
                    violations.append(f"{path.name}: {module}")
                if module.startswith("family_spending.") and not module.startswith(
                    "family_spending.domain"
                ):
                    violations.append(f"{path.name}: {module}")
        self.assertEqual(violations, [])

    def test_new_backend_never_imports_current_backend_modules(self) -> None:
        violations: list[str] = []
        for path in sorted(PACKAGE_ROOT.rglob("*.py")):
            for module in imported_modules(path):
                if module.startswith(LEGACY_INTERNAL_PREFIXES):
                    violations.append(f"{path.relative_to(PACKAGE_ROOT)}: {module}")
        self.assertEqual(violations, [])

    def test_application_ports_do_not_depend_on_concrete_persistence(self) -> None:
        violations: list[str] = []
        for path in sorted((PACKAGE_ROOT / "application").rglob("*.py")):
            for module in imported_modules(path):
                if module.startswith("family_spending.persistence"):
                    violations.append(f"{path.relative_to(PACKAGE_ROOT)}: {module}")
        self.assertEqual(violations, [])

    def test_sources_do_not_depend_on_concrete_persistence(self) -> None:
        violations: list[str] = []
        for path in sorted((PACKAGE_ROOT / "sources").rglob("*.py")):
            for module in imported_modules(path):
                if module.startswith("family_spending.persistence"):
                    violations.append(f"{path.relative_to(PACKAGE_ROOT)}: {module}")
        self.assertEqual(violations, [])

    def test_projections_do_not_depend_on_sources_persistence_runtime_or_interfaces(self) -> None:
        """Keep projections as rebuildable consumers of Domain/Application state, never mutation owners."""
        forbidden_prefixes = (
            "family_spending.sources",
            "family_spending.persistence",
            "family_spending.runtime",
            "family_spending.interfaces",
        )
        violations: list[str] = []
        for path in sorted((PACKAGE_ROOT / "projections").rglob("*.py")):
            for module in imported_modules(path):
                if module.startswith(forbidden_prefixes):
                    violations.append(f"{path.relative_to(PACKAGE_ROOT)}: {module}")
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
