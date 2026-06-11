"""Advanced schema for the Agent Ledger.

Design (a relational realization of a property-graph "knowledge plane"):

  Taxonomy (3-level hierarchy)
    Layer  1──*  Container  1──*  Function

  Catalog
    Package            (python | node | system dependency)
    Provider           (external/internal capability provider)

  Provider-agnostic abstraction  (governance pillar #2)
    Capability  *──*  Provider     via ProviderCapability (primary / fallback tier)

  Secret separation  (governance pillar #3 — references only, never values)
    Provider  1──*  SecretRequirement

  Placement edges  (the heart: "what layer / container / function they have")
    Placement: (subject_kind, subject_id) -> Layer [-> Container [-> Function]]
               polymorphic over Package | Provider, with evidence + criticality

  Dependency graph
    ComponentDependency: typed edge between any two ledger entities

  Audit (makes it a true *ledger*)
    LedgerEvent: append-only change log
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from agentledger.db import Base


# ---------------------------------------------------------------------------
# Taxonomy: Layer -> Container -> Function
# ---------------------------------------------------------------------------

class Layer(Base):
    __tablename__ = "layers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    slug = Column(String(60), unique=True, nullable=False, index=True)
    name = Column(String(120), nullable=False)
    # runtime | governance | memory | providers | interface | domain | resilience | data | infra
    kind = Column(String(40), nullable=False, default="domain")
    order_index = Column(Integer, default=100)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    containers = relationship("Container", back_populates="layer",
                              cascade="all, delete-orphan")


class Container(Base):
    """A logical module/package within a layer (maps to a real dir or sub-stack)."""
    __tablename__ = "containers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    layer_id = Column(Integer, ForeignKey("layers.id"), nullable=False, index=True)
    slug = Column(String(80), unique=True, nullable=False, index=True)
    name = Column(String(120), nullable=False)
    fs_path = Column(String(255), nullable=True)
    language = Column(String(30), default="python")  # python | typescript | mixed
    status = Column(String(30), default="active")
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    layer = relationship("Layer", back_populates="containers")
    functions = relationship("Function", back_populates="container",
                             cascade="all, delete-orphan")


class Function(Base):
    """A real top-level function/class discovered in a container's source."""
    __tablename__ = "functions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    container_id = Column(Integer, ForeignKey("containers.id"), nullable=False, index=True)
    name = Column(String(160), nullable=False)
    qualname = Column(String(320), nullable=True)  # relpath:name
    signature = Column(String(500), nullable=True)
    # entrypoint | capability | adapter | route | model | class | function | async_function
    kind = Column(String(40), default="function")
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    container = relationship("Container", back_populates="functions")

    __table_args__ = (
        Index("ix_function_container_name", "container_id", "name"),
    )


# ---------------------------------------------------------------------------
# Catalog: Package, Provider
# ---------------------------------------------------------------------------

class Package(Base):
    __tablename__ = "packages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(160), nullable=False, index=True)
    ecosystem = Column(String(20), nullable=False, default="python")  # python | node | system
    version_spec = Column(String(120), nullable=True)
    resolved_version = Column(String(80), nullable=True)
    is_direct = Column(Boolean, default=True)       # declared in a manifest
    is_dev = Column(Boolean, default=False)
    required = Column(Boolean, default=True)
    rationale = Column(Text, nullable=True)
    status = Column(String(30), default="active")    # active | candidate | deprecated
    homepage = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("name", "ecosystem", name="uq_package_name_ecosystem"),
    )


class Provider(Base):
    __tablename__ = "providers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    slug = Column(String(60), unique=True, nullable=False, index=True)
    name = Column(String(120), nullable=False)
    # llm | local_llm | memory | graph | finance | browser | voice | avatar |
    # emotion | ssh | optimizer | search | telephony
    kind = Column(String(40), nullable=False, default="llm")
    capability_summary = Column(Text, nullable=True)
    status = Column(String(30), default="active")  # active | candidate | planned | deprecated
    is_external = Column(Boolean, default=True)
    homepage = Column(String(255), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    capabilities = relationship("ProviderCapability", back_populates="provider",
                                cascade="all, delete-orphan")
    secrets = relationship("SecretRequirement", back_populates="provider",
                           cascade="all, delete-orphan")


# ---------------------------------------------------------------------------
# Provider-agnostic abstraction
# ---------------------------------------------------------------------------

class Capability(Base):
    """An abstract capability (e.g. llm.chat) that many providers can fulfil."""
    __tablename__ = "capabilities"

    id = Column(Integer, primary_key=True, autoincrement=True)
    slug = Column(String(60), unique=True, nullable=False, index=True)
    name = Column(String(120), nullable=False)
    description = Column(Text, nullable=True)
    interface_ref = Column(String(255), nullable=True)  # where the agnostic seam lives
    created_at = Column(DateTime, default=datetime.utcnow)

    providers = relationship("ProviderCapability", back_populates="capability",
                             cascade="all, delete-orphan")


class ProviderCapability(Base):
    __tablename__ = "provider_capabilities"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider_id = Column(Integer, ForeignKey("providers.id"), nullable=False, index=True)
    capability_id = Column(Integer, ForeignKey("capabilities.id"), nullable=False, index=True)
    is_primary = Column(Boolean, default=False)
    fallback_tier = Column(Integer, default=0)  # 0 = primary path, 1+ = fallback order
    notes = Column(Text, nullable=True)

    provider = relationship("Provider", back_populates="capabilities")
    capability = relationship("Capability", back_populates="providers")

    __table_args__ = (
        UniqueConstraint("provider_id", "capability_id", name="uq_provider_capability"),
    )


# ---------------------------------------------------------------------------
# Secret separation (references only — NEVER store secret values)
# ---------------------------------------------------------------------------

class SecretRequirement(Base):
    __tablename__ = "secret_requirements"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider_id = Column(Integer, ForeignKey("providers.id"), nullable=False, index=True)
    env_var = Column(String(120), nullable=True)
    vault_key = Column(String(120), nullable=True)
    required = Column(Boolean, default=True)
    # connector_proxy | vault | env  (runtime sourcing order, pillar #3)
    sourced_via = Column(String(40), default="connector_proxy")
    description = Column(Text, nullable=True)

    provider = relationship("Provider", back_populates="secrets")


# ---------------------------------------------------------------------------
# Placement edges (polymorphic: package | provider -> taxonomy node)
# ---------------------------------------------------------------------------

class Placement(Base):
    __tablename__ = "placements"

    id = Column(Integer, primary_key=True, autoincrement=True)
    subject_kind = Column(String(20), nullable=False)  # package | provider
    subject_id = Column(Integer, nullable=False)
    layer_id = Column(Integer, ForeignKey("layers.id"), nullable=True, index=True)
    container_id = Column(Integer, ForeignKey("containers.id"), nullable=True, index=True)
    function_id = Column(Integer, ForeignKey("functions.id"), nullable=True, index=True)
    role = Column(String(120), nullable=True)
    # critical | high | normal | low
    criticality = Column(String(20), default="normal")
    rationale = Column(Text, nullable=True)
    evidence = Column(String(320), nullable=True)  # e.g. "ast-import-scan: src/coach/providers.py"
    status = Column(String(30), default="active")
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_placement_subject", "subject_kind", "subject_id"),
        Index("ix_placement_container", "container_id"),
    )


# ---------------------------------------------------------------------------
# Generic typed dependency edge between any two ledger entities
# ---------------------------------------------------------------------------

class ComponentDependency(Base):
    __tablename__ = "component_dependencies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    from_kind = Column(String(20), nullable=False)  # layer|container|function|package|provider
    from_id = Column(Integer, nullable=False)
    to_kind = Column(String(20), nullable=False)
    to_id = Column(Integer, nullable=False)
    # imports | wraps | calls | fulfilled_by | placed_in | depends_on
    relation = Column(String(40), nullable=False, default="depends_on")
    weight = Column(Float, default=1.0)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_dep_from", "from_kind", "from_id"),
        Index("ix_dep_to", "to_kind", "to_id"),
    )


# ---------------------------------------------------------------------------
# Append-only audit ledger
# ---------------------------------------------------------------------------

class LedgerEvent(Base):
    __tablename__ = "ledger_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ts = Column(DateTime, default=datetime.utcnow, index=True)
    actor = Column(String(60), default="seeder")   # seeder | agent | <agent-name>
    action = Column(String(40), nullable=False)    # seed | insert | update | delete | note
    entity_kind = Column(String(40), nullable=True)
    entity_ref = Column(String(160), nullable=True)
    summary = Column(Text, nullable=True)
    payload_json = Column(Text, nullable=True)
