"""One-time legacy-to-canonical migration tooling for the rebuild workspace."""

from rebuild.migration.execute import MigrationExecutionResult, execute_migration
from rebuild.migration.plan import MigrationPlan, build_migration_plan

__all__ = [
    "MigrationExecutionResult",
    "MigrationPlan",
    "build_migration_plan",
    "execute_migration",
]
