"""One-time migrations for persisted pipeline contracts.

These migrations only rewrite exact, previously valid persisted component
contracts. They are not runtime aliases and do not broaden the current API.
"""
from __future__ import annotations

import sqlite3

from alphalab.strategy.python_runtime import python_source_sha256


_EXECUTION_ENTRYPOINT_MIGRATION = "execution-entrypoint-configure-v1"


def migrate_pipeline_contracts(connection: sqlite3.Connection) -> None:
    """Bring persisted components onto the current six-stage contract once."""

    if connection.execute(
        "SELECT 1 FROM pipeline_contract_migrations WHERE name = ?",
        (_EXECUTION_ENTRYPOINT_MIGRATION,),
    ).fetchone():
        return

    rows = connection.execute(
        """SELECT v.component_id, v.version, v.source
           FROM pipeline_component_versions v
           JOIN pipeline_components c ON c.id = v.component_id
           WHERE c.stage = 'execution' AND v.entrypoint = 'create_orders'"""
    ).fetchall()
    for row in rows:
        source = str(row["source"])
        marker = "def create_orders("
        if source.count(marker) != 1:
            raise ValueError(
                "persisted execution component cannot be migrated automatically: "
                f"{row['component_id']}@{row['version']}"
            )
        migrated = source.replace(marker, "def configure_execution(", 1)
        connection.execute(
            """UPDATE pipeline_component_versions
               SET entrypoint = 'configure_execution', source = ?, source_sha256 = ?
               WHERE component_id = ? AND version = ?""",
            (
                migrated,
                python_source_sha256(migrated),
                row["component_id"],
                row["version"],
            ),
        )
    connection.execute(
        "INSERT INTO pipeline_contract_migrations (name) VALUES (?)",
        (_EXECUTION_ENTRYPOINT_MIGRATION,),
    )


__all__ = ["migrate_pipeline_contracts"]
