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


_BOOL_DEFAULTS = {True: "TRUE", False: "FALSE"}


def _compile_default_clause(col):
    if col.default is None or col.default.arg is None:
        return "", {}
    val = col.default.arg
    if callable(val):
        return "", {}
    if isinstance(val, bool):
        return " DEFAULT " + _BOOL_DEFAULTS[val], {}
    if isinstance(val, (int, float)):
        return " DEFAULT :default_val", {"default_val": val}
    if isinstance(val, str):
        return " DEFAULT :default_val", {"default_val": val}
    return "", {}


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
                # SECURITY: identifiers come only from static SQLAlchemy model
                # metadata (Base.metadata), never from user input, and are quoted
                # via the dialect's identifier_preparer. No injection surface.
                safe_table = preparer.quote_identifier(table_name)
                safe_col = preparer.quote_identifier(col.name)
                col_type = col.type.compile(engine.dialect)
                default_clause, params = _compile_default_clause(col)
                stmt = text(
                    "ALTER TABLE " + safe_table
                    + " ADD COLUMN IF NOT EXISTS " + safe_col
                    + " " + str(col_type) + default_clause
                )
                try:
                    with engine.begin() as conn:
                        conn.execute(stmt, params)
                    logger.info("Added missing column %s.%s", table_name, col.name)
                except Exception as e:
                    logger.warning("Failed to add column %s.%s: %s", table_name, col.name, e)


def init_db():
    from src import models
    Base.metadata.create_all(bind=engine)
    _sync_missing_columns()
