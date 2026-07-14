from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, url: str) -> None:
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        self.engine = create_engine(url, pool_pre_ping=True, connect_args=connect_args)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)

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
