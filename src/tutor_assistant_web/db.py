from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

if TYPE_CHECKING:
    from tutor_assistant_web.config import Settings


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(
        self,
        url: str,
        *,
        pool_size: int = 10,
        max_overflow: int = 20,
        pool_timeout: int = 30,
        pool_recycle: int = 1800,
        statement_timeout_ms: int = 30_000,
        lock_timeout_ms: int = 5000,
    ) -> None:
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        engine_options: dict = {
            "pool_pre_ping": True,
            "connect_args": connect_args,
        }
        if not url.startswith("sqlite"):
            engine_options.update(
                pool_size=pool_size,
                max_overflow=max_overflow,
                pool_timeout=pool_timeout,
                pool_recycle=pool_recycle,
            )
        self.engine = create_engine(url, **engine_options)
        self.dialect_name = self.engine.dialect.name
        if self.dialect_name == "postgresql":
            self._configure_postgresql_timeouts(statement_timeout_ms, lock_timeout_ms)
        elif self.dialect_name == "sqlite":
            self._configure_sqlite()
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)

    @classmethod
    def from_settings(cls, settings: Settings) -> Database:
        return cls(
            settings.database_url,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_timeout=settings.database_pool_timeout,
            pool_recycle=settings.database_pool_recycle,
            statement_timeout_ms=settings.database_statement_timeout_ms,
            lock_timeout_ms=settings.database_lock_timeout_ms,
        )

    def _configure_postgresql_timeouts(
        self, statement_timeout_ms: int, lock_timeout_ms: int
    ) -> None:
        @event.listens_for(self.engine, "connect")
        def set_timeouts(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute(
                    "SELECT set_config('statement_timeout', %s, false)",
                    (str(statement_timeout_ms),),
                )
                cursor.execute(
                    "SELECT set_config('lock_timeout', %s, false)",
                    (str(lock_timeout_ms),),
                )
                dbapi_connection.commit()
            finally:
                cursor.close()

    def _configure_sqlite(self) -> None:
        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
            finally:
                cursor.close()

    def create_schema(self) -> None:
        """Compatibility helper for isolated model tests.

        Runtime startup uses :meth:`migrate`, so deployed databases always have
        an explicit schema revision.
        """
        from tutor_assistant_web import models  # noqa: F401

        Base.metadata.create_all(self.engine)

    def migrate(self, revision: str = "head") -> None:
        config = Config()
        migrations = Path(__file__).with_name("migrations")
        config.set_main_option("script_location", str(migrations))
        config.set_main_option("sqlalchemy.url", self.engine.url.render_as_string(False))
        tables = set(inspect(self.engine).get_table_names())
        # Releases <= 0.2 created tables with metadata.create_all. Mark that
        # known layout as the pilot revision before applying tenant migration.
        if "students" in tables and "alembic_version" not in tables:
            command.stamp(config, "0001_pilot")
        command.upgrade(config, revision)

    def session(self) -> Iterator[Session]:
        with self.sessions() as session:
            yield session

    def healthcheck(self) -> None:
        from sqlalchemy import text

        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))

    def dispose(self) -> None:
        self.engine.dispose()
