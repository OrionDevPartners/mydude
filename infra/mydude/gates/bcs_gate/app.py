"""BCS Promotion Gate — Container App (min-1, always-on).

This is the SINGLE TRUTH WRITER for MyDude's governance ledger (Postgres) and
knowledge corpus (Microsoft Fabric / OneLake staging in ADLS Gen2).
It is the only actor that may write to either authority.

Guarantees:
  - Single Entra managed identity (mydude-bcs-gate) is the only writer.
  - Idempotency: enforced by a UNIQUE constraint in Postgres claim_receipt table
    + a Postgres advisory lock per (candidate_id, content_hash) tuple.
    In-memory sets are NOT used — they give false safety under multi-replica / multi-worker deployments.
  - Lease lock: Postgres session advisory lock (pg_try_advisory_lock) instead of an
    in-process threading.Lock, which is meaningless across Container App replicas.
  - Scope-gate V1-V7 must all pass before any write.
  - Governance-ledger write failure raises HTTP 502 — callers must not treat "error" as "promoted".
  - Candidate events from provider_home and offline outbox replays are
    admitted here and nowhere else.

POST /claims/migration  — accept a migration CompletionClaim
POST /claims/model      — accept a model-promotion CompletionClaim
POST /outbox/replay     — replay offline promotion_events from provider_home
GET  /health            — liveness probe (always responds 200)
GET  /status            — gate status (advisory lock counts, throughput)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import struct
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Header, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("bcs_gate")

# ---------------------------------------------------------------------------
# Configuration (from environment / Key Vault refs injected by Container App)
# ---------------------------------------------------------------------------
BCS_LEASE_SECRET = os.environ.get("BCS_LEASE_SECRET", "")
PG_AGENTS_HOME_DSN = os.environ.get("PG_AGENTS_HOME_DSN", "")
PG_PROVIDER_HOME_DSN = os.environ.get("PG_PROVIDER_HOME_DSN", "")
MANAGED_IDENTITY_CLIENT_ID = os.environ.get("MANAGED_IDENTITY_CLIENT_ID", "")
SCOPE_GATE_VERSION = os.environ.get("SCOPE_GATE_VERSION", "V7")
# Knowledge-corpus staging target (Microsoft Fabric / OneLake over ADLS Gen2).
# The governance ledger itself is Postgres (PG_AGENTS_HOME_DSN, above); the
# knowledge corpus is staged to this ADLS Gen2 account's OneLake-staging
# filesystem. The BCS gate managed identity is the sole writer to both.
ADLS_CORPUS_ACCOUNT = os.environ.get("ADLS_CORPUS_ACCOUNT", "")
ADLS_CORPUS_FILESYSTEM = os.environ.get("ADLS_CORPUS_FILESYSTEM", "onelake-staging")


# ---------------------------------------------------------------------------
# Postgres helpers (durable idempotency + advisory lease lock)
# ---------------------------------------------------------------------------

def _advisory_lock_key(candidate_id: str, content_hash: str) -> int:
    """Map (candidate_id, content_hash) → a stable 64-bit advisory lock key.

    Uses the first 8 bytes of SHA-256 so different tuples almost never collide,
    and the key is deterministic across all replicas and workers.
    """
    raw = hashlib.sha256(("%s::%s" % (candidate_id, content_hash)).encode()).digest()
    # interpret as signed 64-bit big-endian (Postgres pg_try_advisory_lock takes bigint)
    return struct.unpack(">q", raw[:8])[0]


@contextmanager
def _pg_advisory_lease(conn, candidate_id: str, content_hash: str):
    """Acquire a session-level advisory lock in Postgres; yield; release on exit.

    Raises RuntimeError if the lock is already held (another replica is promoting
    the same claim). The lock is scoped to the session so it is released on
    connection close even if the process crashes.
    """
    lock_key = _advisory_lock_key(candidate_id, content_hash)
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (lock_key,))
        acquired = cur.fetchone()[0]
    if not acquired:
        raise RuntimeError(
            "V6: advisory lock held by another replica for candidate=%s; retry shortly." % candidate_id
        )
    try:
        yield lock_key
    finally:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (lock_key,))


def _pg_is_duplicate(conn, candidate_id: str, content_hash: str) -> bool:
    """Check the durable claim_receipt table for an already-committed promotion.

    V1 idempotency check: ONLY rows with status='ledger_committed' count as committed.
    A row with status='pending' or 'failed' does NOT block retry — the previous
    attempt did not complete, and the claim is eligible for re-submission.

    The UNIQUE constraint on (candidate_id, content_hash) ensures at most one
    receipt row exists; the state machine column determines whether it is live.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT status FROM governance.claim_receipt
            WHERE candidate_id = %s AND content_hash = %s
            LIMIT 1
            """,
            (candidate_id, content_hash),
        )
        row = cur.fetchone()
        if row is None:
            return False  # no receipt at all — not a duplicate
        return row[0] == "ledger_committed"  # only committed claims block retry


def _pg_upsert_receipt_pending(conn, gate_receipt_id: str, candidate_id: str, content_hash: str,
                                claim_type: str, authority: str, exec_locus: str) -> None:
    """Insert a receipt row with status='pending', or reset a failed row to 'pending'.

    Uses INSERT ... ON CONFLICT to handle the case where a previous attempt left
    a 'failed' row. A 'ledger_committed' conflict is caught by _pg_is_duplicate
    before this call and raises ValueError(V1) instead.

    The UNIQUE constraint on (candidate_id, content_hash) guards against races.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO governance.claim_receipt
                (gate_receipt_id, candidate_id, content_hash, claim_type, authority, exec_locus, status, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, 'pending', now())
            ON CONFLICT (candidate_id, content_hash) DO UPDATE
                SET gate_receipt_id = EXCLUDED.gate_receipt_id,
                    status          = 'pending',
                    failure_reason  = NULL,
                    created_at      = now()
                WHERE governance.claim_receipt.status = 'failed'
            """,
            (gate_receipt_id, candidate_id, content_hash, claim_type, authority, exec_locus),
        )


def _pg_commit_receipt(conn, candidate_id: str, content_hash: str) -> None:
    """Advance receipt from 'pending' → 'ledger_committed' after successful governance-ledger write."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE governance.claim_receipt
            SET status = 'ledger_committed', committed_at = now()
            WHERE candidate_id = %s AND content_hash = %s AND status = 'pending'
            """,
            (candidate_id, content_hash),
        )


def _pg_fail_receipt(conn, candidate_id: str, content_hash: str, reason: str) -> None:
    """Mark receipt as 'failed' so the claim can be retried on next submission."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE governance.claim_receipt
            SET status = 'failed', failure_reason = %s
            WHERE candidate_id = %s AND content_hash = %s AND status = 'pending'
            """,
            (reason[:500], candidate_id, content_hash),
        )


def _get_agents_home_conn():
    """Open a psycopg2 connection to agents_home.  Returns None if DSN absent."""
    if not PG_AGENTS_HOME_DSN:
        return None
    try:
        import psycopg2
        return psycopg2.connect(PG_AGENTS_HOME_DSN)
    except Exception as e:
        logger.error("Cannot connect to agents_home: %s", e)
        return None


# ---------------------------------------------------------------------------
# Scope gate re-implementation (gate-side, authoritative)
# ---------------------------------------------------------------------------
VALID_EXEC_LOCI = ("in_azure", "anthropic_hosted", "local")
VALID_AUTHORITIES = ("fabric", "postgres")
VALID_SCOPES = ("V1_idempotency", "V2_content_hash", "V3_receipt_unique",
                "V4_exec_locus", "V5_authority", "V6_lease_lock", "V7_scope_label")


def _run_scope_gate(payload: dict, conn) -> list[str]:
    """Re-run the V1-V7 gates on the gate side (authoritative check).

    V1 and V6 are now Postgres-backed (durable across replicas):
      V1: claim_receipt table lookup (not in-memory set)
      V6: pg_try_advisory_lock (not threading.Lock)
    """
    candidate_id = payload.get("candidate_id", "")
    content_hash = payload.get("content_hash", "")
    gate_receipt_id = payload.get("gate_receipt_id", "")
    exec_locus = payload.get("exec_locus", "")
    authority = payload.get("authority", "")
    scope_label = payload.get("scope_label", "")

    # V1: durable idempotency check
    if conn is not None and _pg_is_duplicate(conn, candidate_id, content_hash):
        raise ValueError("V1: duplicate — this (candidate_id, content_hash) has already been promoted")

    # V2
    if not content_hash or len(content_hash) != 64:
        raise ValueError("V2: invalid content_hash (must be 64-char SHA-256 hex)")

    # V3
    try:
        uuid.UUID(gate_receipt_id)
    except (ValueError, AttributeError):
        raise ValueError("V3: invalid gate_receipt_id (must be UUID)")

    # V4
    if exec_locus not in VALID_EXEC_LOCI:
        raise ValueError("V4: invalid exec_locus '%s'" % exec_locus)

    # V5
    if authority not in VALID_AUTHORITIES:
        raise ValueError("V5: invalid authority '%s'" % authority)

    # V6: BCS_LEASE_SECRET presence (the actual per-claim lock is the pg advisory lock held by the caller)
    if not BCS_LEASE_SECRET:
        raise ValueError("V6: BCS_LEASE_SECRET not configured — gate cannot authenticate lease")

    # V7
    if scope_label not in VALID_SCOPES:
        raise ValueError("V7: invalid scope_label '%s'" % scope_label)

    return list(VALID_SCOPES)


# ---------------------------------------------------------------------------
# Governance ledger (Postgres) + knowledge corpus (OneLake/ADLS) — sole write path
# ---------------------------------------------------------------------------
def _get_storage_token() -> Optional[str]:
    """Acquire an access token for ADLS Gen2 / OneLake using the managed identity."""
    if not MANAGED_IDENTITY_CLIENT_ID:
        return None
    try:
        import urllib.request
        url = ("http://169.254.169.254/metadata/identity/oauth2/token"
               "?api-version=2018-02-01"
               "&resource=https://storage.azure.com/"
               "&client_id=%s" % MANAGED_IDENTITY_CLIENT_ID)
        req = urllib.request.Request(url, headers={"Metadata": "true"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            return data.get("access_token")
    except Exception as e:
        logger.error("Failed to acquire managed identity storage token: %s", e)
        return None


def _stage_corpus_to_onelake(claim_payload: dict) -> None:
    """Stage a knowledge-corpus claim's manifest to OneLake staging in ADLS Gen2.

    The BCS gate is the sole writer to the knowledge corpus. Corpus claims
    (authority='fabric') carry their manifest in `detail`; this stages that
    manifest JSON to the configured OneLake-staging filesystem via the ADLS Gen2
    DataLake REST API (create file → append → flush) using the gate managed
    identity. Microsoft Fabric lakehouse items surface this staged data via
    OneLake shortcuts; the durable governance record stays in Postgres.

    Fail-loud in production if the corpus target is unconfigured; skip in dev/test.
    RAISES on any staging failure — the caller must not treat a failure as success.
    """
    production_mode = os.environ.get("PRODUCTION_MODE", "false").lower() == "true"
    if not ADLS_CORPUS_ACCOUNT or not ADLS_CORPUS_FILESYSTEM:
        if production_mode:
            raise RuntimeError(
                "ADLS_CORPUS_ACCOUNT / ADLS_CORPUS_FILESYSTEM not configured "
                "(PRODUCTION_MODE=true). Knowledge-corpus claims must be staged to "
                "OneLake/ADLS, not silently dropped."
            )
        logger.warning("ADLS corpus target not configured; corpus staging skipped (dev/test mode).")
        return

    token = _get_storage_token()
    if not token:
        raise RuntimeError(
            "Cannot obtain managed identity token for OneLake/ADLS corpus staging "
            "(candidate_id=%s). Corpus claim NOT staged." % claim_payload.get("candidate_id")
        )

    import urllib.request
    import urllib.error

    candidate_id = claim_payload.get("candidate_id", "unknown")
    rel_path = "corpus/%s/%s.json" % (
        claim_payload.get("migration_name") or "manifest", candidate_id,
    )
    body = json.dumps(claim_payload.get("detail", {})).encode()
    base = "https://%s.dfs.core.windows.net/%s/%s" % (
        ADLS_CORPUS_ACCOUNT, ADLS_CORPUS_FILESYSTEM, rel_path,
    )
    auth = {"Authorization": "Bearer %s" % token, "x-ms-version": "2021-08-06"}

    def _call(url: str, method: str, data: bytes, extra: Optional[dict] = None) -> None:
        headers = dict(auth)
        if extra:
            headers.update(extra)
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()

    try:
        # ADLS Gen2 DataLake REST: create empty file, append the body, then flush.
        _call(base + "?resource=file", "PUT", b"")
        _call(base + "?action=append&position=0", "PATCH", body,
              {"Content-Type": "application/octet-stream"})
        _call(base + "?action=flush&position=%d" % len(body), "PATCH", b"")
        logger.info("Staged corpus manifest to OneLake/ADLS: %s/%s",
                    ADLS_CORPUS_FILESYSTEM, rel_path)
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            "OneLake/ADLS corpus staging failed (HTTP %d): %s"
            % (e.code, e.read().decode(errors="replace"))
        )
    except Exception as e:
        raise RuntimeError("OneLake/ADLS corpus staging error: %s" % e)


def _write_claim_to_ledger(conn, claim_payload: dict) -> dict:
    """Write a promoted claim to the Postgres governance ledger (governance.claim_ledger).

    This is the ONLY function in the system that writes rows to the governance
    ledger. The BCS gate (single Entra managed identity) is the sole truth writer.

    The INSERT is left UNCOMMITTED so the caller can commit it atomically with the
    claim_receipt state advance. For knowledge-corpus claims (authority='fabric')
    the corpus manifest is then staged to OneLake/ADLS via _stage_corpus_to_onelake().

    Returns a dict with status="promoted" on success.
    RAISES on any failure — callers must not swallow errors.
    """
    production_mode = os.environ.get("PRODUCTION_MODE", "false").lower() == "true"

    if conn is None:
        if production_mode:
            raise RuntimeError(
                "PG_AGENTS_HOME_DSN is not configured (PRODUCTION_MODE=true). "
                "BCS gate cannot proceed: claims must be written to the governance "
                "ledger (Postgres), not silently dropped. Set PG_AGENTS_HOME_DSN."
            )
        logger.warning("PG_AGENTS_HOME_DSN not configured; claim recorded locally only (dev/test mode).")
        return {"status": "local_only", "reason": "no_agents_home_dsn"}

    claim_id = str(uuid.uuid4())
    authority = claim_payload.get("authority", "")
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO governance.claim_ledger
                    (claim_id, candidate_id, content_hash, gate_receipt_id, exec_locus,
                     authority, scope_label, migration_name, database,
                     model_id, provider, domain, detail, promoted_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, now())
                ON CONFLICT (candidate_id, content_hash) DO NOTHING
                """,
                (
                    claim_id,
                    claim_payload.get("candidate_id"),
                    claim_payload.get("content_hash"),
                    claim_payload.get("gate_receipt_id"),
                    claim_payload.get("exec_locus"),
                    authority,
                    claim_payload.get("scope_label"),
                    claim_payload.get("migration_name"),
                    claim_payload.get("database"),
                    claim_payload.get("model_id"),
                    claim_payload.get("provider"),
                    claim_payload.get("domain"),
                    json.dumps(claim_payload.get("detail", {})),
                ),
            )
    except Exception as e:
        # Always raise — _process_claim must not return 200 when the ledger write fails.
        raise RuntimeError(
            "Governance ledger INSERT failed for candidate_id=%s: %s"
            % (claim_payload.get("candidate_id"), e)
        )

    # Knowledge-corpus claims also stage their manifest to OneLake/ADLS (gate-only).
    if authority == "fabric":
        _stage_corpus_to_onelake(claim_payload)

    logger.info("Governance ledger INSERT staged for candidate_id=%s claim_id=%s",
                claim_payload.get("candidate_id"), claim_id)
    return {"status": "promoted", "claim_id": claim_id}


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
class ClaimPayload(BaseModel):
    candidate_id: str
    content_hash: str
    gate_receipt_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    exec_locus: str
    authority: str
    scope_label: str
    migration_name: Optional[str] = None
    database: Optional[str] = None
    model_id: Optional[str] = None
    provider: Optional[str] = None
    domain: Optional[str] = None
    detail: dict = Field(default_factory=dict)
    passed_gates: list = Field(default_factory=list)


class OutboxReplayRequest(BaseModel):
    limit: int = 10
    dry_run: bool = False


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "BCS Gate starting. Governance ledger DSN: %s, corpus account: %s, managed identity: %s",
        "configured" if PG_AGENTS_HOME_DSN else "[MISSING — V1 idempotency + ledger degraded]",
        ADLS_CORPUS_ACCOUNT or "[not configured]",
        MANAGED_IDENTITY_CLIENT_ID or "[not configured]",
    )
    if not BCS_LEASE_SECRET:
        logger.warning("BCS_LEASE_SECRET not set — V6 gate will reject all claims.")
    if not PG_PROVIDER_HOME_DSN:
        logger.warning("PG_PROVIDER_HOME_DSN not set — /outbox/replay will be disabled.")
    yield
    logger.info("BCS Gate shutting down.")


app = FastAPI(title="MyDude BCS Promotion Gate", version="2.0.0", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "gate_version": SCOPE_GATE_VERSION}


@app.get("/status")
async def status():
    conn = _get_agents_home_conn()
    receipt_count = None
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM governance.claim_receipt")
                receipt_count = cur.fetchone()[0]
        except Exception:
            pass
        finally:
            conn.close()
    return {
        "governance_ledger_configured": bool(PG_AGENTS_HOME_DSN),
        "corpus_staging_configured": bool(ADLS_CORPUS_ACCOUNT),
        "managed_identity_configured": bool(MANAGED_IDENTITY_CLIENT_ID),
        "agents_home_dsn_configured": bool(PG_AGENTS_HOME_DSN),
        "provider_home_dsn_configured": bool(PG_PROVIDER_HOME_DSN),
        "scope_gate_version": SCOPE_GATE_VERSION,
        "promoted_claims_total": receipt_count,
        "idempotency_backend": "postgres" if PG_AGENTS_HOME_DSN else "degraded-no-dsn",
        "lease_backend": "postgres-advisory-lock",
    }


@app.post("/claims/migration")
async def accept_migration_claim(
    payload: ClaimPayload,
    x_idempotency_key: Optional[str] = Header(None, alias="X-Idempotency-Key"),
):
    return await _process_claim(payload, claim_type="migration")


@app.post("/claims/model")
async def accept_model_claim(payload: ClaimPayload):
    return await _process_claim(payload, claim_type="model")


async def _process_claim(payload: ClaimPayload, claim_type: str) -> JSONResponse:
    """Core promotion logic.

    Failure contract (strict):
      - 400  scope gate rejection (V1-V7)
      - 503  advisory lock busy (another replica is promoting this same claim)
      - 502  governance-ledger write failed — claim is NOT promoted; caller must retry or alert
      - 200  ONLY when the claim is committed to BOTH the Postgres claim_receipt AND the
             governance ledger (governance.claim_ledger) — and, for authority='fabric'
             claims, staged to the knowledge corpus (OneLake/ADLS)
             (or PG_AGENTS_HOME_DSN is absent, meaning local/dev mode)
    """
    conn = _get_agents_home_conn()
    try:
        payload_dict = payload.model_dump()

        # Acquire per-claim Postgres advisory lock (cross-replica exclusivity)
        with _pg_advisory_lease(conn, payload.candidate_id, payload.content_hash):
            # V1-V7 gates (V1 checks only ledger_committed rows — failed/pending allow retry)
            passed = _run_scope_gate(payload_dict, conn)

            # Write receipt as 'pending' (state machine step 1 of 3).
            # ON CONFLICT resets 'failed' rows to 'pending' for retry.
            # If receipt is already 'ledger_committed', _pg_is_duplicate() raised V1 above.
            if conn is not None:
                try:
                    _pg_upsert_receipt_pending(
                        conn,
                        gate_receipt_id=payload.gate_receipt_id,
                        candidate_id=payload.candidate_id,
                        content_hash=payload.content_hash,
                        claim_type=claim_type,
                        authority=payload.authority,
                        exec_locus=payload.exec_locus,
                    )
                    conn.commit()
                except Exception as e:
                    conn.rollback()
                    raise ValueError("V1: receipt upsert failed: %s" % e)

            # Write to the governance ledger (state machine step 2 of 3) — RAISES on any failure.
            # The INSERT is left uncommitted so it commits atomically with the receipt advance.
            # On failure: roll back, mark receipt 'failed' (retryable), return 502.
            try:
                ledger_result = _write_claim_to_ledger(conn, payload_dict)
            except RuntimeError as ledger_err:
                # Roll back the uncommitted ledger INSERT, then mark receipt failed so next
                # retry can proceed (not stuck in 'pending').
                if conn is not None:
                    try:
                        conn.rollback()
                        _pg_fail_receipt(conn, payload.candidate_id, payload.content_hash, str(ledger_err))
                        conn.commit()
                    except Exception:
                        pass  # best-effort; don't mask the original error
                raise  # re-raise for the outer except to convert to 502

            # Advance receipt to 'ledger_committed' and commit the ledger row atomically
            # (state machine step 3 of 3). Only now is the claim durably promoted.
            if conn is not None:
                try:
                    _pg_commit_receipt(conn, payload.candidate_id, payload.content_hash)
                    conn.commit()
                except Exception as e:
                    conn.rollback()
                    raise RuntimeError("Governance ledger commit failed: %s" % e)

    except RuntimeError as e:
        err_msg = str(e)
        if "advisory lock held" in err_msg:
            raise HTTPException(503, err_msg)
        # Governance-ledger write failure
        logger.error("Governance-ledger write failure (claim NOT promoted, receipt marked failed): %s", e)
        raise HTTPException(502, "Governance-ledger write failed — claim not promoted, retry eligible: %s" % e)
    except ValueError as e:
        logger.warning("Scope gate rejection: %s (candidate=%s)", e, payload.candidate_id)
        raise HTTPException(400, str(e))
    finally:
        if conn is not None:
            conn.close()

    logger.info(
        "Claim promoted: candidate_id=%s authority=%s type=%s ledger=%s",
        payload.candidate_id, payload.authority, claim_type, ledger_result.get("status"),
    )
    return JSONResponse({
        "status": "promoted",
        "gate_receipt_id": payload.gate_receipt_id,
        "candidate_id": payload.candidate_id,
        "scope_gates_passed": passed,
        "ledger_result": ledger_result,
    })


@app.post("/outbox/replay")
async def replay_outbox(req: OutboxReplayRequest):
    """Replay pending offline promotion_events from provider_home outbox.

    Reads from PG_PROVIDER_HOME_DSN (provider_home database, outbox schema).
    Each event is replayed through the full scope gate + governance-ledger write.
    Events that fail are marked 'failed' with an error_detail; they are NOT silently dropped.
    """
    if not PG_PROVIDER_HOME_DSN:
        raise HTTPException(503, "PG_PROVIDER_HOME_DSN not configured; cannot read outbox.")
    try:
        import psycopg2
        ph_conn = psycopg2.connect(PG_PROVIDER_HOME_DSN)
        ah_conn = _get_agents_home_conn()
        replayed = []
        failed = []
        with ph_conn:
            with ph_conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT event_id, candidate_id, content_hash, payload, idempotency_key
                    FROM outbox.promotion_event
                    WHERE status = 'pending' AND (expires_at IS NULL OR expires_at > now())
                    ORDER BY created_at
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                    """,
                    (req.limit,),
                )
                rows = cur.fetchall()
                for row in rows:
                    event_id, candidate_id, content_hash, raw_payload, idempotency_key = row
                    payload_dict = raw_payload if isinstance(raw_payload, dict) else json.loads(raw_payload)
                    try:
                        with _pg_advisory_lease(ah_conn, candidate_id, content_hash):
                            if not req.dry_run:
                                _run_scope_gate(payload_dict, ah_conn)
                                receipt_id = payload_dict.get("gate_receipt_id") or str(uuid.uuid4())
                                if ah_conn is not None:
                                    # Step 1: write receipt as 'pending' (retryable if ledger write fails)
                                    _pg_upsert_receipt_pending(
                                        ah_conn,
                                        gate_receipt_id=receipt_id,
                                        candidate_id=candidate_id,
                                        content_hash=content_hash,
                                        claim_type="outbox_replay",
                                        authority=payload_dict.get("authority", "unknown"),
                                        exec_locus=payload_dict.get("exec_locus", "unknown"),
                                    )
                                    ah_conn.commit()
                                # Step 2: write to the governance ledger — raises on failure
                                _write_claim_to_ledger(ah_conn, payload_dict)
                                if ah_conn is not None:
                                    # Step 3: advance receipt to ledger_committed (+commit ledger row)
                                    _pg_commit_receipt(ah_conn, candidate_id, content_hash)
                                    ah_conn.commit()
                                cur.execute(
                                    "UPDATE outbox.promotion_event SET status='replayed', replayed_at=now(), gate_receipt_id=%s WHERE event_id=%s",
                                    (receipt_id, event_id),
                                )
                        replayed.append(str(event_id))
                    except Exception as e:
                        if ah_conn is not None:
                            try:
                                ah_conn.rollback()
                            except Exception:
                                pass
                        cur.execute(
                            "UPDATE outbox.promotion_event SET status='failed', error_detail=%s, attempts=attempts+1 WHERE event_id=%s",
                            (str(e)[:500], event_id),
                        )
                        logger.error("Outbox replay failed event_id=%s: %s", event_id, e)
                        failed.append(str(event_id))
        if ah_conn is not None:
            ah_conn.close()
        ph_conn.close()
        return {"replayed": replayed, "failed": failed, "dry_run": req.dry_run}
    except Exception as e:
        raise HTTPException(500, "Outbox replay error: %s" % e)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
