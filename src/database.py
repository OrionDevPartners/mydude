import os
import logging
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "")

if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL environment variable is not set or is empty. "
        "Please configure a valid PostgreSQL connection string."
    )

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


def _sync_missing_columns():
    inspector = inspect(engine)
    existing_tables = inspector.get_table_names()
    preparer = engine.dialect.identifier_preparer
    for table_name, table in Base.metadata.tables.items():
        if table_name not in existing_tables:
            continue
        db_cols = {c["name"] for c in inspector.get_columns(table_name)}
        for col in table.columns:
            if col.name not in db_cols:
                col_type = col.type.compile(engine.dialect)
                safe_table = preparer.quote_identifier(table_name)
                safe_col = preparer.quote_identifier(col.name)
                default_clause = ""
                params = {}
                if col.default is not None and col.default.arg is not None:
                    val = col.default.arg
                    if isinstance(val, bool):
                        default_clause = " DEFAULT TRUE" if val else " DEFAULT FALSE"
                    elif isinstance(val, (int, float)):
                        default_clause = " DEFAULT :default_val"
                        params["default_val"] = val
                    elif isinstance(val, str):
                        default_clause = " DEFAULT :default_val"
                        params["default_val"] = val
                sql = text(
                    f"ALTER TABLE {safe_table} ADD COLUMN IF NOT EXISTS"
                    f" {safe_col} {col_type}{default_clause}"
                )
                try:
                    with engine.begin() as conn:
                        conn.execute(sql, params)
                    logger.info("Added missing column %s.%s", table_name, col.name)
                except Exception as e:
                    logger.warning("Failed to add column %s.%s: %s", table_name, col.name, e)


def init_db():
    from src import models
    Base.metadata.create_all(bind=engine)
    _sync_missing_columns()
