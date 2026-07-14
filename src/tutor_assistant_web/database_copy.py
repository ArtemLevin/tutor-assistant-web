from __future__ import annotations

import argparse
import os
from collections.abc import Sequence

from sqlalchemy import delete, func, insert, select

from tutor_assistant_web import models  # noqa: F401
from tutor_assistant_web.db import Base, Database
from tutor_assistant_web.modules.identity.models import DEFAULT_ORGANIZATION_ID


class DatabaseCopyError(RuntimeError):
    pass


def copy_sqlite_to_postgres(
    source_url: str,
    target_url: str,
    *,
    batch_size: int = 500,
) -> dict[str, int]:
    source = Database(source_url)
    target = Database(target_url)
    try:
        if source.dialect_name != "sqlite":
            raise DatabaseCopyError("Source database must be SQLite")
        if target.dialect_name != "postgresql":
            raise DatabaseCopyError("Target database must be PostgreSQL")
        source.migrate()
        target.migrate()
        tables = list(Base.metadata.sorted_tables)
        copied: dict[str, int] = {}
        with (
            source.engine.connect() as source_connection,
            target.engine.begin() as target_connection,
        ):
            _ensure_empty_target(target_connection, tables)
            organizations = Base.metadata.tables["organizations"]
            target_connection.execute(
                delete(organizations).where(organizations.c.id == DEFAULT_ORGANIZATION_ID)
            )
            for table in tables:
                rows = source_connection.execution_options(stream_results=True).execute(
                    select(table)
                )
                count = 0
                batch: list[dict] = []
                for row in rows.mappings():
                    batch.append(dict(row))
                    if len(batch) >= batch_size:
                        target_connection.execute(insert(table), batch)
                        count += len(batch)
                        batch.clear()
                if batch:
                    target_connection.execute(insert(table), batch)
                    count += len(batch)
                copied[table.name] = count
            for table in tables:
                target_count = target_connection.scalar(select(func.count()).select_from(table))
                if target_count != copied[table.name]:
                    raise DatabaseCopyError(
                        f"Row count mismatch for {table.name}: "
                        f"expected {copied[table.name]}, got {target_count}"
                    )
        return copied
    finally:
        source.dispose()
        target.dispose()


def _ensure_empty_target(connection, tables: Sequence) -> None:
    for table in reversed(tables):
        count = connection.scalar(select(func.count()).select_from(table))
        if not count:
            continue
        if table.name == "organizations" and count == 1:
            ids = list(connection.scalars(select(table.c.id)))
            if ids == [DEFAULT_ORGANIZATION_ID]:
                continue
        raise DatabaseCopyError(
            f"Target table {table.name} contains data; use a new PostgreSQL database"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Copy an upgraded Tutor Assistant SQLite database to empty PostgreSQL",
    )
    parser.add_argument(
        "--confirm-empty-target",
        action="store_true",
        help="confirm that the target PostgreSQL database is dedicated and empty",
    )
    args = parser.parse_args()
    if not args.confirm_empty_target:
        parser.error("--confirm-empty-target is required")
    source_url = os.getenv("SOURCE_DATABASE_URL", "")
    target_url = os.getenv("TARGET_DATABASE_URL", "")
    if not source_url or not target_url:
        parser.error("SOURCE_DATABASE_URL and TARGET_DATABASE_URL are required")
    copied = copy_sqlite_to_postgres(source_url, target_url)
    print({"status": "ok", "tables": copied})


if __name__ == "__main__":
    main()
