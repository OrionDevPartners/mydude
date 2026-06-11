"""Query interface for the Agent Ledger.

For agents working on this project. Import the helpers, or use the CLI:

  python -m agentledger.query summary
  python -m agentledger.query layers
  python -m agentledger.query containers [layer_slug]
  python -m agentledger.query providers [kind]
  python -m agentledger.query packages [python|node]
  python -m agentledger.query capability <slug>        # who fulfils it (primary + fallbacks)
  python -m agentledger.query where <provider|package> <name>   # placements
  python -m agentledger.query container <slug>          # full detail of one container
  python -m agentledger.query search <text>             # fuzzy across functions/containers/providers
"""
from __future__ import annotations

import sys
from typing import List, Optional

from sqlalchemy import func

from agentledger.db import SessionLocal
from agentledger.models import (
    Capability,
    Container,
    Function,
    Layer,
    Package,
    Placement,
    Provider,
    ProviderCapability,
    SecretRequirement,
)


def summary() -> dict:
    db = SessionLocal()
    try:
        return {
            "layers": db.query(func.count(Layer.id)).scalar(),
            "containers": db.query(func.count(Container.id)).scalar(),
            "functions": db.query(func.count(Function.id)).scalar(),
            "packages_python": db.query(func.count(Package.id)).filter(Package.ecosystem == "python").scalar(),
            "packages_node": db.query(func.count(Package.id)).filter(Package.ecosystem == "node").scalar(),
            "providers": db.query(func.count(Provider.id)).scalar(),
            "providers_active": db.query(func.count(Provider.id)).filter(Provider.status == "active").scalar(),
            "capabilities": db.query(func.count(Capability.id)).scalar(),
            "placements": db.query(func.count(Placement.id)).scalar(),
        }
    finally:
        db.close()


def _print_summary():
    s = summary()
    print("Agent Ledger summary:")
    for k, v in s.items():
        print(f"  {k:18} {v}")


def _print_layers():
    db = SessionLocal()
    try:
        for L in db.query(Layer).order_by(Layer.order_index).all():
            n = db.query(func.count(Container.id)).filter(Container.layer_id == L.id).scalar()
            print(f"[{L.slug:11}] {L.name:24} ({L.kind}) — {n} containers")
            print(f"             {L.description or ''}")
    finally:
        db.close()


def _print_containers(layer_slug: Optional[str]):
    db = SessionLocal()
    try:
        q = db.query(Container).join(Layer)
        if layer_slug:
            q = q.filter(Layer.slug == layer_slug)
        for c in q.order_by(Container.slug).all():
            fn = db.query(func.count(Function.id)).filter(Function.container_id == c.id).scalar()
            print(f"{c.slug:22} [{c.layer.slug:10}] {c.fs_path or '':22} {fn} fns — {c.description or ''}")
    finally:
        db.close()


def _print_providers(kind: Optional[str]):
    db = SessionLocal()
    try:
        q = db.query(Provider)
        if kind:
            q = q.filter(Provider.kind == kind)
        for p in q.order_by(Provider.kind, Provider.slug).all():
            caps = db.query(Capability.slug, ProviderCapability.is_primary, ProviderCapability.fallback_tier)\
                .join(ProviderCapability, ProviderCapability.capability_id == Capability.id)\
                .filter(ProviderCapability.provider_id == p.id).all()
            capstr = ", ".join(f"{s}{'*' if pr else ''}(t{t})" for (s, pr, t) in caps)
            secs = db.query(SecretRequirement).filter(SecretRequirement.provider_id == p.id).all()
            secstr = ", ".join(filter(None, [s.env_var or s.vault_key for s in secs])) or "—"
            print(f"{p.slug:14} [{p.kind:10}] {p.status:8} caps: {capstr}")
            print(f"               secret: {secstr}  via: {secs[0].sourced_via if secs else '—'}")
    finally:
        db.close()


def _print_packages(ecosystem: Optional[str]):
    db = SessionLocal()
    try:
        q = db.query(Package)
        if ecosystem:
            q = q.filter(Package.ecosystem == ecosystem)
        for p in q.order_by(Package.ecosystem, Package.name).all():
            placed = db.query(func.count(Placement.id))\
                .filter(Placement.subject_kind == "package", Placement.subject_id == p.id).scalar()
            dev = " (dev)" if p.is_dev else ""
            print(f"{p.name:28} [{p.ecosystem:6}] {p.version_spec or '':12} used in {placed} containers{dev}")
    finally:
        db.close()


def _print_capability(slug: str):
    db = SessionLocal()
    try:
        cap = db.query(Capability).filter(Capability.slug == slug).first()
        if not cap:
            print(f"No capability '{slug}'")
            return
        print(f"Capability: {cap.slug} — {cap.name}")
        print(f"  interface: {cap.interface_ref}")
        print(f"  {cap.description or ''}")
        rows = db.query(Provider, ProviderCapability)\
            .join(ProviderCapability, ProviderCapability.provider_id == Provider.id)\
            .filter(ProviderCapability.capability_id == cap.id)\
            .order_by(ProviderCapability.fallback_tier).all()
        print("  fulfilled by (provider-agnostic, by fallback order):")
        for (prov, pc) in rows:
            print(f"    tier {pc.fallback_tier}{' PRIMARY' if pc.is_primary else ''}: {prov.slug} [{prov.status}]")
    finally:
        db.close()


def _print_where(kind: str, name: str):
    db = SessionLocal()
    try:
        if kind == "provider":
            subj = db.query(Provider).filter(Provider.slug == name).first()
        else:
            subj = db.query(Package).filter(Package.name == name).first()
        if not subj:
            print(f"No {kind} '{name}'")
            return
        print(f"Placements for {kind} '{name}':")
        rows = db.query(Placement, Container, Layer)\
            .outerjoin(Container, Placement.container_id == Container.id)\
            .outerjoin(Layer, Placement.layer_id == Layer.id)\
            .filter(Placement.subject_kind == kind, Placement.subject_id == subj.id).all()
        for (pl, cont, layer) in rows:
            print(f"  [{layer.slug if layer else '?':10}] {cont.slug if cont else '?':22} "
                  f"{pl.criticality:8} {pl.role or '':20} ({pl.evidence})")
    finally:
        db.close()


def _print_container(slug: str):
    db = SessionLocal()
    try:
        c = db.query(Container).filter(Container.slug == slug).first()
        if not c:
            print(f"No container '{slug}'")
            return
        print(f"Container: {c.slug}  [layer: {c.layer.slug}]  path: {c.fs_path}")
        print(f"  {c.description or ''}")
        fns = db.query(Function).filter(Function.container_id == c.id).order_by(Function.kind, Function.name).all()
        print(f"  functions ({len(fns)}):")
        for f in fns[:200]:
            print(f"    {f.kind:14} {f.qualname}")
        pls = db.query(Placement).filter(Placement.container_id == c.id).all()
        pk = [p for p in pls if p.subject_kind == "package"]
        pv = [p for p in pls if p.subject_kind == "provider"]
        if pk:
            names = [db.get(Package, p.subject_id).name for p in pk]
            print(f"  packages used: {', '.join(sorted(set(names)))}")
        if pv:
            names = [db.get(Provider, p.subject_id).slug for p in pv]
            print(f"  providers used: {', '.join(sorted(set(names)))}")
    finally:
        db.close()


def _print_search(text: str):
    db = SessionLocal()
    like = f"%{text.lower()}%"
    try:
        print(f"Search '{text}':")
        conts = db.query(Container).filter(func.lower(Container.slug).like(like)).all()
        for c in conts:
            print(f"  [container] {c.slug} ({c.fs_path})")
        provs = db.query(Provider).filter(func.lower(Provider.slug).like(like)).all()
        for p in provs:
            print(f"  [provider]  {p.slug} ({p.kind})")
        pkgs = db.query(Package).filter(func.lower(Package.name).like(like)).all()
        for p in pkgs:
            print(f"  [package]   {p.name} ({p.ecosystem})")
        fns = db.query(Function).filter(func.lower(Function.name).like(like)).limit(40).all()
        for f in fns:
            print(f"  [function]  {f.qualname} ({f.kind})")
    finally:
        db.close()


def main(argv: List[str]) -> None:
    if not argv:
        _print_summary()
        return
    cmd, rest = argv[0], argv[1:]
    if cmd == "summary":
        _print_summary()
    elif cmd == "layers":
        _print_layers()
    elif cmd == "containers":
        _print_containers(rest[0] if rest else None)
    elif cmd == "providers":
        _print_providers(rest[0] if rest else None)
    elif cmd == "packages":
        _print_packages(rest[0] if rest else None)
    elif cmd == "capability":
        _print_capability(rest[0]) if rest else print("usage: capability <slug>")
    elif cmd == "where":
        _print_where(rest[0], rest[1]) if len(rest) >= 2 else print("usage: where <provider|package> <name>")
    elif cmd == "container":
        _print_container(rest[0]) if rest else print("usage: container <slug>")
    elif cmd == "search":
        _print_search(rest[0]) if rest else print("usage: search <text>")
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
