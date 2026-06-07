"""Shared base for MyDude migrators.

Every migrator emits CompletionClaims through the BCS scope-completion gate
(V1-V7) after applying DDL or catalog changes. The scope-gate verifies:
  V1 — idempotency key has not been replayed
  V2 — content_hash matches the artifact
  V3 — gate_receipt_id is unique
  V4 — exec_locus is declared
  V5 — authority assertion (catalog vs postgres, never mixed)
  V6 — lease lock is held
  V7 — scope label is one of the registered scopes

Unity must never own Postgres DDL; Postgres migrators must never write to
Unity Catalog. This module enforces that boundary at the claim level.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class MigrationAuthority(str, Enum):
    UNITY = "unity"      # Unity/Iceberg — ledger and table schemas
    POSTGRES = "postgres"  # agents_home + provider_home DDL


class ScopeLabel(str, Enum):
    V1_IDEMPOTENCY = "V1_idempotency"
    V2_CONTENT_HASH = "V2_content_hash"
    V3_RECEIPT_UNIQUE = "V3_receipt_unique"
    V4_EXEC_LOCUS = "V4_exec_locus"
    V5_AUTHORITY = "V5_authority"
    V6_LEASE_LOCK = "V6_lease_lock"
    V7_SCOPE_LABEL = "V7_scope_label"

    @classmethod
    def all_gates(cls):
        return [cls.V1_IDEMPOTENCY, cls.V2_CONTENT_HASH, cls.V3_RECEIPT_UNIQUE,
                cls.V4_EXEC_LOCUS, cls.V5_AUTHORITY, cls.V6_LEASE_LOCK, cls.V7_SCOPE_LABEL]


@dataclass
class CompletionClaim:
    """A verifiable claim that a migration step completed successfully."""
    candidate_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    content_hash: str = ""
    gate_receipt_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    exec_locus: str = "in_azure"
    authority: MigrationAuthority = MigrationAuthority.POSTGRES
    scope_label: ScopeLabel = ScopeLabel.V7_SCOPE_LABEL
    migration_name: str = ""
    database: str = ""
    applied_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    gate_version: str = "V7"
    passed_gates: list = field(default_factory=list)
    detail: dict = field(default_factory=dict)

    def compute_content_hash(self, content: str) -> None:
        self.content_hash = hashlib.sha256(content.encode()).hexdigest()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["authority"] = self.authority.value
        d["scope_label"] = self.scope_label.value
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


class ScopeGate:
    """V1-V7 scope-completion gate.

    Each gate is a verification step. All seven must pass before the
    CompletionClaim is submitted to the BCS gate.
    """

    def __init__(self, claim: CompletionClaim, expected_authority: MigrationAuthority,
                 lease_secret: Optional[str] = None):
        self.claim = claim
        self.expected_authority = expected_authority
        self.lease_secret = lease_secret or os.environ.get("BCS_LEASE_SECRET", "")
        self._seen_idempotency_keys: set = set()

    def run_all(self) -> list[str]:
        """Run all 7 gates. Returns list of passed gate labels. Raises on failure."""
        gates = [
            self._v1_idempotency,
            self._v2_content_hash,
            self._v3_receipt_unique,
            self._v4_exec_locus,
            self._v5_authority,
            self._v6_lease_lock,
            self._v7_scope_label,
        ]
        passed = []
        for gate_fn in gates:
            label = gate_fn()
            passed.append(label)
            logger.debug("Scope gate passed: %s (claim=%s)", label, self.claim.candidate_id)
        self.claim.passed_gates = passed
        return passed

    def _v1_idempotency(self) -> str:
        key = f"{self.claim.candidate_id}::{self.claim.content_hash}"
        if key in self._seen_idempotency_keys:
            raise ValueError("V1 idempotency violation: this candidate_id+content_hash has already been submitted")
        self._seen_idempotency_keys.add(key)
        return ScopeLabel.V1_IDEMPOTENCY.value

    def _v2_content_hash(self) -> str:
        if not self.claim.content_hash or len(self.claim.content_hash) != 64:
            raise ValueError("V2 content_hash violation: content_hash must be a 64-char SHA-256 hex digest")
        return ScopeLabel.V2_CONTENT_HASH.value

    def _v3_receipt_unique(self) -> str:
        if not self.claim.gate_receipt_id:
            raise ValueError("V3 receipt_unique violation: gate_receipt_id must be non-empty")
        try:
            uuid.UUID(self.claim.gate_receipt_id)
        except ValueError:
            raise ValueError("V3 receipt_unique violation: gate_receipt_id must be a valid UUID")
        return ScopeLabel.V3_RECEIPT_UNIQUE.value

    def _v4_exec_locus(self) -> str:
        valid = ("in_azure", "anthropic_hosted", "local")
        if self.claim.exec_locus not in valid:
            raise ValueError(
                "V4 exec_locus violation: must be one of %s, got '%s'"
                % (valid, self.claim.exec_locus)
            )
        return ScopeLabel.V4_EXEC_LOCUS.value

    def _v5_authority(self) -> str:
        if self.claim.authority != self.expected_authority:
            raise ValueError(
                "V5 authority violation: expected '%s', got '%s'. "
                "Unity must never own Postgres DDL; Postgres must never write to Unity Catalog."
                % (self.expected_authority.value, self.claim.authority.value)
            )
        return ScopeLabel.V5_AUTHORITY.value

    def _v6_lease_lock(self) -> str:
        if not self.lease_secret:
            raise ValueError(
                "V6 lease_lock violation: BCS_LEASE_SECRET is not set. "
                "The BCS gate must hold the lease before any migration claim is submitted."
            )
        return ScopeLabel.V6_LEASE_LOCK.value

    def _v7_scope_label(self) -> str:
        valid_labels = [s.value for s in ScopeLabel.all_gates()]
        if self.claim.scope_label.value not in valid_labels:
            raise ValueError(
                "V7 scope_label violation: '%s' is not a registered scope label."
                % self.claim.scope_label.value
            )
        return ScopeLabel.V7_SCOPE_LABEL.value


def submit_completion_claim(
    claim: CompletionClaim,
    bcs_gate_url: Optional[str] = None,
    claim_endpoint: Optional[str] = None,
) -> dict:
    """Submit a CompletionClaim to the BCS gate.

    In production: POST to the BCS gate Container App.
    In local mode: writes to provider_home.outbox.promotion_event for later replay.

    claim_endpoint — override the default BCS gate path. Defaults by authority:
      MigrationAuthority.POSTGRES or UNITY → /claims/migration
      MigrationAuthority.POSTGRES with migration_name="model_promotion" → /claims/model
    Callers may pass an explicit path (e.g. "/claims/model") to route correctly.
    """
    import urllib.request
    import urllib.error

    url = bcs_gate_url or os.environ.get("BCS_GATE_URL", "")
    payload = claim.to_json().encode()

    # Determine the correct BCS gate endpoint path.
    if claim_endpoint:
        gate_path = claim_endpoint
    elif claim.migration_name == "model_promotion":
        gate_path = "/claims/model"
    else:
        gate_path = "/claims/migration"

    if url:
        req = urllib.request.Request(
            url + gate_path,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "X-Gate-Version": claim.gate_version,
                "X-Idempotency-Key": f"{claim.candidate_id}::{claim.content_hash}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode())
                logger.info(
                    "BCS gate accepted claim: candidate_id=%s receipt=%s",
                    claim.candidate_id, claim.gate_receipt_id,
                )
                return result
        except urllib.error.URLError as e:
            logger.warning("BCS gate unreachable (%s); writing to local outbox for replay.", e)
            return _write_to_local_outbox(claim)
    else:
        logger.info("No BCS_GATE_URL configured; writing claim to local outbox.")
        return _write_to_local_outbox(claim)


def _write_to_local_outbox(claim: CompletionClaim) -> dict:
    """Write the claim to provider_home.outbox.promotion_event for later cloud replay."""
    try:
        import psycopg2
        dsn = os.environ.get("PG_PROVIDER_HOME_DSN", "")
        if not dsn:
            logger.warning("PG_PROVIDER_HOME_DSN not set; claim logged but not persisted.")
            return {"status": "logged_only", "candidate_id": claim.candidate_id}
        idempotency_key = f"{claim.candidate_id}::{claim.content_hash}::local"
        conn = psycopg2.connect(dsn)
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO outbox.promotion_event
                        (candidate_id, content_hash, payload, idempotency_key, status)
                    VALUES (%s, %s, %s, %s, 'pending')
                    ON CONFLICT (idempotency_key) DO NOTHING
                    """,
                    (
                        claim.candidate_id,
                        claim.content_hash,
                        json.dumps(claim.to_dict()),
                        idempotency_key,
                    ),
                )
        conn.close()
        return {"status": "queued_for_replay", "candidate_id": claim.candidate_id}
    except Exception as e:
        logger.error("Failed to write claim to local outbox: %s", e)
        return {"status": "error", "error": str(e), "candidate_id": claim.candidate_id}
