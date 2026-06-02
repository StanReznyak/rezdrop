from __future__ import annotations

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from app.config import settings

engine_kwargs = {"pool_pre_ping": True}
if settings.database_url.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(settings.database_url, echo=False, future=True, **engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _column_exists(table_name: str, column_name: str) -> bool:
    inspector = inspect(engine)
    if table_name not in inspector.get_table_names():
        return False
    return any(col["name"] == column_name for col in inspector.get_columns(table_name))


def _add_column_if_missing(table_name: str, column_name: str, column_sql: str) -> None:
    if _column_exists(table_name, column_name):
        return
    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}"))


def apply_lightweight_migrations() -> None:
    """Small compatibility helper for local SQLite databases.

    Docker/PostgreSQL usage should rely on Alembic. This helper keeps the
    Windows local start from crashing when an existing local database is reused.
    """
    _add_column_if_missing("batches", "session_id", "session_id VARCHAR(64)")
    _add_column_if_missing("batches", "ip_address", "ip_address VARCHAR(64)")
    _add_column_if_missing("batches", "user_agent", "user_agent TEXT")
    _add_column_if_missing("batches", "total_size_bytes", "total_size_bytes INTEGER DEFAULT 0")
    _add_column_if_missing("batches", "files_count", "files_count INTEGER DEFAULT 0")
    _add_column_if_missing("batches", "deleted_by_admin", "deleted_by_admin BOOLEAN DEFAULT FALSE")
    _add_column_if_missing("batches", "user_id", "user_id INTEGER")
    _add_column_if_missing("files", "scan_status", "scan_status VARCHAR(32) DEFAULT 'clean'")
    _add_column_if_missing("files", "storage_backend", "storage_backend VARCHAR(32) DEFAULT 'local'")
    _add_column_if_missing("files", "storage_key", "storage_key VARCHAR(1024)")
    _add_column_if_missing("download_logs", "user_id", "user_id INTEGER")


def init_db() -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    apply_lightweight_migrations()


def check_database() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
