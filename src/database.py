"""Routed, per-domain database layer for MyDude.io.

Each logical business domain (finance, coach, fleet, telephony, sales, avatar,
subscriptions, browser) gets its OWN physical Postgres database, plus a shared
``core`` database for cross-cutting tables (auth, tasks, swarm runtime,
governance). A single SQLAlchemy ``Session`` API is preserved: ``SessionLocal``
returns a routing session whose ``get_bind`` sends each model to the engine that
owns its table (default: core). Subsystems that replicate a table across every
domain database (the memory substrate, the vector store) bypass routing and bind
a session directly to a domain engine via ``domain_session(domain)``.

Connection resolution is provider-agnostic (Governance Pillar #2/#3):

  * core            -> ``DATABASE_URL``
  * domain ``<X>``  -> ``DOMAIN_DATABASE_URL_<X>`` if set, else a sibling DB on the
                       SAME server derived from the core URL (``<coredb>_<X>``).

In dev (a superuser on the built-in server) the sibling databases are created with
``CREATE DATABASE`` automatically. In a locked-down Azure deployment the per-domain
URLs are pre-provisioned and the create step is a graceful no-op.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Dict, Optional, Set
from urllib.parse import urlsplit, urlunsplit

from contextlib import contextmanager

from sqlalchemy import ForeignKeyConstraint, create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker, declarative_base

from src.domains.registry import (
    CORE_DOMAIN,
    DOMAIN_CONTAINERS,
    DOMAIN_OWNED_TABLES,
    VECTOR_TABLE,
    all_containers,
    domain_table_names,
    replicated_table_names,
    resolve_domain,
    table_domain,
)

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "")

if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL environment variable is not set or is empty. "
        "Please configure a valid PostgreSQL connection string."
    )

Base = declarative_base()


# --------------------------------------------------------------------------- #
# Per-domain connection-string resolution
# --------------------------------------------------------------------------- #
def _derive_sibling_url(core_url: str, slug: str) -> str:
    """Derive a sibling-database URL on the SAME server as the core URL.

    The path component (``/<dbname>``) is replaced with ``/<dbname>_<slug>``.
    Everything else (driver, credentials, host, port, query) is preserved, so the
    sibling lives on the same server with the same credentials.
    """
    parts = urlsplit(core_url)
    db = parts.path.lstrip("/") or "postgres"
    new_path = "/%s_%s" % (db, slug)
    return urlunsplit((parts.scheme, parts.netloc, new_path, parts.query, parts.fragment))


def resolve_domain_url(slug: str) -> str:
    """Resolve the connection string for a domain.

    Order (provider-agnostic, secrets sourced by name from the environment which
    the connector proxy / vault populates at boot):

      1. core             -> ``DATABASE_URL``
      2. ``DOMAIN_DATABASE_URL_<SLUG>`` explicit override (Azure / custom)
      3. derived sibling database on the same server as ``DATABASE_URL``
    """
    if slug == CORE_DOMAIN:
        return DATABASE_URL
    override = os.environ.get("DOMAIN_DATABASE_URL_%s" % slug.upper(), "").strip()
    if override:
        return override
    return _derive_sibling_url(DATABASE_URL, slug)


def _maintenance_url() -> str:
    """A URL pointing at the server's default ``postgres`` maintenance database.

    Used to issue ``CREATE DATABASE`` (which cannot run inside the target DB or a
    transaction). Derived from the core URL so it always targets the same server.
    """
    parts = urlsplit(DATABASE_URL)
    return urlunsplit((parts.scheme, parts.netloc, "/postgres", parts.query, parts.fragment))


def _database_name(url: str) -> str:
    return urlsplit(url).path.lstrip("/") or "postgres"


# --------------------------------------------------------------------------- #
# Engine + sessionmaker registries (lazy, thread-safe)
# --------------------------------------------------------------------------- #
_engines: Dict[str, Engine] = {}
_domain_sessionmakers: Dict[str, sessionmaker] = {}
_engine_lock = threading.Lock()


def _build_engine(url: str) -> Engine:
    # Modest pools — up to 9 engines share one dev server; pre_ping survives the
    # built-in server recycling idle connections.
    return create_engine(
        url,
        pool_size=2,
        max_overflow=5,
        pool_pre_ping=True,
        pool_recycle=1800,
        future=True,
    )


def get_engine(domain: Optional[str] = None) -> Engine:
    """Return the SQLAlchemy engine for *domain* (resolved to a known slug)."""
    slug = resolve_domain(domain)
    eng = _engines.get(slug)
    if eng is not None:
        return eng
    with _engine_lock:
        eng = _engines.get(slug)
        if eng is None:
            url = resolve_domain_url(slug)
            eng = _build_engine(url)
            _engines[slug] = eng
            logger.info("DB engine ready: domain=%s db=%s", slug, _database_name(url))
        return eng


def domain_session(domain: Optional[str] = None) -> Session:
    """Open a plain Session bound DIRECTLY to *domain*'s engine.

    Use this for tables that are replicated across every domain database (memory
    entries, audit log, vectors) where the owning domain is decided by the caller,
    not by the model's table. Business-model code should keep using
    ``SessionLocal`` (routing) instead.
    """
    slug = resolve_domain(domain)
    sm = _domain_sessionmakers.get(slug)
    if sm is None:
        with _engine_lock:
            sm = _domain_sessionmakers.get(slug)
            if sm is None:
                sm = sessionmaker(bind=get_engine(slug), future=True)
                _domain_sessionmakers[slug] = sm
    return sm()


# --------------------------------------------------------------------------- #
# Routing session — preserves the single SessionLocal() API across 40+ callers
# --------------------------------------------------------------------------- #
class RoutingSession(Session):
    """Session that binds each model to the engine owning its table.

    Business-domain models route to their domain database; everything else
    (and any raw/text statement) routes to core.
    """

    def get_bind(self, mapper=None, clause=None, **kw):  # type: ignore[override]
        if mapper is not None:
            try:
                tablename = mapper.persist_selectable.name
            except Exception:
                tablename = getattr(getattr(mapper, "local_table", None), "name", None)
            return get_engine(table_domain(tablename))
        return get_engine(CORE_DOMAIN)


def _make_session_local() -> sessionmaker:
    return sessionmaker(class_=RoutingSession, future=True, bind=get_engine(CORE_DOMAIN))


# Public session factory (kept named ``SessionLocal`` for the existing call sites).
SessionLocal = _make_session_local()


def get_session(domain: Optional[str] = None) -> Session:
    """Explicit accessor.

    ``domain=None`` -> the routing session (model-owned binds). A concrete domain
    -> a session bound directly to that domain's engine.
    """
    if domain is None:
        return SessionLocal()
    return domain_session(domain)


# Backwards-compat module attribute: some modules referenced ``engine`` directly.
# It points at the core engine (built lazily on first access via __getattr__).
def __getattr__(name):  # pragma: no cover - thin compat shim
    if name == "engine":
        return get_engine(CORE_DOMAIN)
    raise AttributeError(name)


# --------------------------------------------------------------------------- #
# Database provisioning (dev = CREATE DATABASE; Azure = graceful no-op)
# --------------------------------------------------------------------------- #
def ensure_databases() -> None:
    """Create each domain's sibling database if it does not yet exist.

    Only runs the create path when connecting as a privileged role on the same
    server (the dev built-in Postgres). When the role lacks ``CREATEDB`` or the
    per-domain URL points at a pre-provisioned managed database (Azure), the
    failure is logged and skipped — the database is expected to already exist.
    """
    targets = []
    for container in all_containers():
        if container.slug == CORE_DOMAIN:
            continue
        url = resolve_domain_url(container.slug)
        targets.append((container.slug, url))
    if not targets:
        return

    # If a domain URL points at a *different* server than core (explicit override),
    # we cannot create it from core's maintenance connection — skip it (managed).
    core_parts = urlsplit(DATABASE_URL)
    try:
        maint = create_engine(_maintenance_url(), isolation_level="AUTOCOMMIT", future=True)
    except Exception as exc:
        logger.warning("DB provisioning: maintenance engine unavailable (%s)", exc)
        return

    try:
        with maint.connect() as conn:
            for slug, url in targets:
                parts = urlsplit(url)
                if parts.netloc != core_parts.netloc:
                    logger.info(
                        "DB provisioning: domain=%s on external server %s — assumed managed/pre-provisioned",
                        slug, parts.hostname,
                    )
                    continue
                dbname = _database_name(url)
                try:
                    exists = conn.execute(
                        text("SELECT 1 FROM pg_database WHERE datname = :n"),
                        {"n": dbname},
                    ).scalar()
                    if exists:
                        continue
                    # dbname comes from static config (core db name + registry slug),
                    # never user input; quote it for the identifier position anyway.
                    safe = maint.dialect.identifier_preparer.quote_identifier(dbname)
                    conn.execute(text("CREATE DATABASE %s" % safe))
                    logger.info("DB provisioning: created domain database %s (domain=%s)", dbname, slug)
                except Exception as exc:
                    logger.warning(
                        "DB provisioning: could not create %s for domain=%s "
                        "(assuming pre-provisioned / managed): %s",
                        dbname, slug, exc,
                    )
    finally:
        maint.dispose()


# --------------------------------------------------------------------------- #
# pgvector bootstrap + per-domain raw vector table
# --------------------------------------------------------------------------- #
def _ensure_vector_extension(engine: Engine) -> bool:
    """Best-effort ``CREATE EXTENSION vector``. Returns True if available."""
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        return True
    except Exception as exc:
        logger.info(
            "pgvector extension unavailable on %s (vector search falls back to "
            "TF-IDF): %s", _database_name(str(engine.url)), exc,
        )
        return False


def _ensure_vector_table(engine: Engine) -> None:
    """Create the per-domain ``vector_entries`` table (raw, pgvector-typed).

    Defined as raw DDL rather than an ORM model so no Python ``pgvector`` package
    is required. The ``embedding`` column uses the variable-length ``vector`` type
    so embedding models of different dimensions can coexist; queries filter by
    ``dim`` to compare only same-dimension vectors.
    """
    ddl = (
        "CREATE TABLE IF NOT EXISTS %s ("
        "  id BIGSERIAL PRIMARY KEY,"
        "  domain TEXT NOT NULL,"
        "  memory_id TEXT NOT NULL,"
        "  content TEXT NOT NULL,"
        "  model_name TEXT NOT NULL DEFAULT '',"
        "  dim INTEGER NOT NULL,"
        "  embedding vector NOT NULL,"
        "  created_at DOUBLE PRECISION NOT NULL DEFAULT 0,"
        "  CONSTRAINT uq_%s_domain_mem UNIQUE (domain, memory_id)"
        ")" % (VECTOR_TABLE, VECTOR_TABLE)
    )
    try:
        with engine.begin() as conn:
            conn.execute(text(ddl))
    except Exception as exc:
        logger.info("vector_entries table not created on %s: %s",
                    _database_name(str(engine.url)), exc)


# --------------------------------------------------------------------------- #
# Schema sync (per-engine, over that engine's table subset)
# --------------------------------------------------------------------------- #
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


def _sync_missing_columns(engine: Engine, table_names: Set[str]) -> None:
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    preparer = engine.dialect.identifier_preparer
    for table_name in table_names:
        table = Base.metadata.tables.get(table_name)
        if table is None or table_name not in existing_tables:
            continue
        db_cols = {c["name"] for c in inspector.get_columns(table_name)}
        for col in table.columns:
            if col.name not in db_cols:
                # SECURITY: identifiers come only from static SQLAlchemy model
                # metadata (Base.metadata), never user input, quoted via the
                # dialect's identifier_preparer. No injection surface.
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
                    logger.info("Added missing column %s.%s (domain db %s)",
                                table_name, col.name, _database_name(str(engine.url)))
                except Exception as e:
                    logger.warning("Failed to add column %s.%s: %s",
                                   table_name, col.name, e)


def _core_table_names() -> Set[str]:
    """Every ORM table that is NOT domain-owned lives in the core database."""
    return {t for t in Base.metadata.tables.keys() if t not in DOMAIN_OWNED_TABLES}


def _tables_for_domain(slug: str) -> Set[str]:
    if slug == CORE_DOMAIN:
        return _core_table_names()
    # domain-owned tables + replicated memory tables that exist in metadata
    names = domain_table_names(slug)
    return {t for t in names if t in Base.metadata.tables}


@contextmanager
def _strip_cross_domain_fks(table_objs, local_table_names: Set[str]):
    """Temporarily drop FK constraints that reference a table NOT physically
    present in the current domain's database.

    Each domain lives in its own physical Postgres database, so a foreign key
    can never span two domains (Postgres cannot enforce a cross-database FK).
    Where a domain table references a table owned by a different domain (e.g.
    telephony ``call_sessions.bot_id`` -> fleet ``bots``) the physical constraint
    is omitted and referential integrity for that edge is enforced at the
    application layer instead. The in-memory metadata is restored afterwards so
    ORM relationships and other domains' DDL are unaffected (fail-loud: a missing
    *local* FK target would still raise during create_all)."""
    removed = []  # (table, constraint)
    for tbl in table_objs:
        for con in list(tbl.constraints):
            if not isinstance(con, ForeignKeyConstraint):
                continue
            try:
                referred = con.referred_table.name
            except Exception:
                # Unresolvable reference target — leave it to fail loud.
                continue
            if referred not in local_table_names:
                tbl.constraints.discard(con)
                removed.append((tbl, con))
    try:
        yield
    finally:
        for tbl, con in removed:
            tbl.constraints.add(con)


def init_db() -> None:
    """Provision every domain database, create its table subset, sync columns,
    and bootstrap its pgvector store. Idempotent and fail-loud per-domain
    (a single domain failure is logged but does not abort the others)."""
    from src import models  # noqa: F401 — ensure all tables are registered

    ensure_databases()

    for container in all_containers():
        slug = container.slug
        try:
            engine = get_engine(slug)
            tables = _tables_for_domain(slug)
            table_objs = [Base.metadata.tables[t] for t in tables if t in Base.metadata.tables]
            with _strip_cross_domain_fks(table_objs, tables):
                Base.metadata.create_all(bind=engine, tables=table_objs)
            _sync_missing_columns(engine, tables)
            _ensure_vector_extension(engine)
            _ensure_vector_table(engine)
            logger.info("DB init complete: domain=%s tables=%d", slug, len(table_objs))
        except Exception as exc:
            logger.error("DB init FAILED for domain=%s: %s", slug, exc)
            if slug == CORE_DOMAIN:
                # core must succeed — fail loud rather than run half-initialized.
                raise
