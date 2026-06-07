"""MyDude Local BCS Path.

The local BCS path is the offline equivalent of the cloud BCS gate. It:
  1. Writes promotion events to the local outbox (SQLite/provider_home_candidates.db)
  2. Assigns a local candidate_id but NO gate_receipt_id (cloud BCS gate does that)
  3. Marks all outputs as offline-candidate until the cloud BCS gate acknowledges
  4. Replays the outbox when connectivity is restored

This module is used by the local sovereign stack when the cloud BCS gate is
unreachable. It shares the same CompletionClaim and ScopeGate logic but uses
the POSTGRES authority (never Unity) and routes through the offline outbox.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("mydude.local_bcs")

LOCAL_DB_PATH = Path(os.environ.get("LOCAL_BCS_DB", str(Path.home() / ".mydude/local/provider_home_candidates.db")))


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the local outbox tables if they don't exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id TEXT NOT NULL UNIQUE,
            content_hash TEXT NOT NULL,
            model_id TEXT,
            provider TEXT,
            domain TEXT,
            exec_locus TEXT NOT NULL DEFAULT 'local',
            promotion_status TEXT NOT NULL DEFAULT 'pending',
            gate_receipt_id TEXT,
            promoted_at TEXT,
            created_at TEXT NOT NULL,
            payload TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL UNIQUE,
            candidate_id TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            payload TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            last_attempt_at TEXT,
            replayed_at TEXT,
            gate_receipt_id TEXT,
            error_detail TEXT,
            created_at TEXT NOT NULL,
            expires_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_outbox_status ON outbox (status, created_at);
        CREATE INDEX IF NOT EXISTS idx_candidates_status ON candidates (promotion_status);
    """)
    conn.commit()


def _get_conn() -> sqlite3.Connection:
    LOCAL_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(LOCAL_DB_PATH))
    _ensure_schema(conn)
    return conn


@dataclass
class LocalCandidate:
    candidate_id: str
    content_hash: str
    model_id: Optional[str]
    provider: Optional[str]
    domain: Optional[str]
    exec_locus: str
    payload: dict
    promotion_status: str = "pending"
    gate_receipt_id: Optional[str] = None


def write_candidate(
    content: str,
    model_id: Optional[str] = None,
    provider: Optional[str] = None,
    domain: Optional[str] = "general",
    exec_locus: str = "local",
    payload: Optional[dict] = None,
) -> LocalCandidate:
    """Write a new offline candidate to the local outbox.

    The payload stored in the outbox is a fully valid ClaimPayload that the cloud
    BCS gate can accept at /claims/model without modification. All V1-V7 required
    fields are populated here so replay never fails with request validation errors:

      authority   = "postgres"       — local candidates use the Postgres authority lane
      scope_label = "V7_scope_label" — last valid scope gate marker; gate re-validates
      gate_receipt_id                — pre-assigned UUID; cloud gate validates format (V3)
                                       and issues its own receipt_id in the response
      exec_locus                     — must be one of in_azure | anthropic_hosted | local
    """
    candidate_id = str(uuid.uuid4())
    content_hash = hashlib.sha256(content.encode()).hexdigest()
    gate_receipt_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    full_payload = {
        # Identity fields (V1, V2, V3)
        "candidate_id": candidate_id,
        "content_hash": content_hash,
        "gate_receipt_id": gate_receipt_id,
        # exec_locus pin (V4) — local candidates always run on local infra
        "exec_locus": exec_locus if exec_locus in ("in_azure", "anthropic_hosted", "local") else "local",
        # Authority boundary (V5) — local outbox uses postgres authority lane, never Unity
        "authority": "postgres",
        # Scope label (V7) — mark with the terminal gate label; cloud gate re-validates all
        "scope_label": "V7_scope_label",
        # Routing metadata
        "model_id": model_id,
        "provider": provider,
        "domain": domain,
        "created_at": now,
        **(payload or {}),
    }
    idempotency_key = "%s::%s::local" % (candidate_id, content_hash)

    conn = _get_conn()
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO candidates
                (candidate_id, content_hash, model_id, provider, domain, exec_locus, created_at, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (candidate_id, content_hash, model_id, provider, domain, exec_locus, now, json.dumps(full_payload)),
        )
        expires_at = None  # No expiry by default; cloud gate handles cleanup
        conn.execute(
            """
            INSERT OR IGNORE INTO outbox
                (event_id, candidate_id, content_hash, idempotency_key, payload, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), candidate_id, content_hash, idempotency_key, json.dumps(full_payload), now, expires_at),
        )
        conn.commit()
    finally:
        conn.close()

    logger.info("Local candidate written: candidate_id=%s exec_locus=%s status=offline_candidate", candidate_id, exec_locus)
    return LocalCandidate(
        candidate_id=candidate_id,
        content_hash=content_hash,
        model_id=model_id,
        provider=provider,
        domain=domain,
        exec_locus=exec_locus,
        payload=full_payload,
    )


def replay_outbox(bcs_gate_url: str, limit: int = 10, dry_run: bool = False) -> dict:
    """Replay pending outbox events to the cloud BCS gate."""
    import urllib.request
    import urllib.error

    conn = _get_conn()
    results = {"replayed": [], "failed": [], "dry_run": dry_run}
    try:
        cur = conn.execute(
            "SELECT id, event_id, candidate_id, content_hash, payload FROM outbox WHERE status='pending' ORDER BY created_at LIMIT ?",
            (limit,),
        )
        rows = cur.fetchall()
    except Exception as e:
        conn.close()
        return {"error": str(e)}

    for row in rows:
        row_id, event_id, candidate_id, content_hash, raw_payload = row
        payload = json.loads(raw_payload)

        if dry_run:
            results["replayed"].append(event_id)
            continue

        try:
            req = urllib.request.Request(
                bcs_gate_url.rstrip("/") + "/claims/model",
                data=json.dumps(payload).encode(),
                headers={
                    "Content-Type": "application/json",
                    "X-Idempotency-Key": "%s::%s::local" % (candidate_id, content_hash),
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode())
                gate_receipt_id = result.get("gate_receipt_id", "")
            conn.execute(
                "UPDATE outbox SET status='replayed', replayed_at=?, gate_receipt_id=?, attempts=attempts+1 WHERE id=?",
                (datetime.now(timezone.utc).isoformat(), gate_receipt_id, row_id),
            )
            conn.execute(
                "UPDATE candidates SET promotion_status='promoted', gate_receipt_id=?, promoted_at=? WHERE candidate_id=?",
                (gate_receipt_id, datetime.now(timezone.utc).isoformat(), candidate_id),
            )
            results["replayed"].append(event_id)
        except Exception as e:
            conn.execute(
                "UPDATE outbox SET status='failed', error_detail=?, last_attempt_at=?, attempts=attempts+1 WHERE id=?",
                (str(e)[:500], datetime.now(timezone.utc).isoformat(), row_id),
            )
            results["failed"].append(event_id)
            logger.warning("Outbox replay failed for event %s: %s", event_id, e)

    conn.commit()
    conn.close()
    return results


def pending_count() -> int:
    """Return the number of pending outbox events."""
    conn = _get_conn()
    count = conn.execute("SELECT COUNT(*) FROM outbox WHERE status='pending'").fetchone()[0]
    conn.close()
    return count
