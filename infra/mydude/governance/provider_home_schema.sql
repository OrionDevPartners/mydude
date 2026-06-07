-- =============================================================================
-- provider_home schema — private candidate cognition and outbox
-- Database: provider_home
-- Migration lineage: independent from agents_home (separate Flyway baseline)
-- Roles: provider_home_writer (app), provider_home_reader (BCS gate only)
-- =============================================================================
-- AUTHORITY RULE: provider_home stores raw candidates and the outbox queue.
-- It is NOT a routing authority. Outputs here are offline-candidate until the
-- BCS gate promotes them by writing to Unity Catalog.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Roles (idempotent guard)
-- ---------------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'provider_home_writer') THEN
    CREATE ROLE provider_home_writer LOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'provider_home_reader') THEN
    CREATE ROLE provider_home_reader LOGIN;
  END IF;
END $$;

GRANT CONNECT ON DATABASE provider_home TO provider_home_writer;
GRANT CONNECT ON DATABASE provider_home TO provider_home_reader;

-- ---------------------------------------------------------------------------
-- Extensions (required before any table uses gen_random_uuid())
-- ---------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS pgcrypto;     -- provides gen_random_uuid() on PG < 13
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";  -- uuid_generate_v4() fallback

-- ---------------------------------------------------------------------------
-- Schemas
-- ---------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS candidates;
CREATE SCHEMA IF NOT EXISTS outbox;

GRANT USAGE ON SCHEMA candidates TO provider_home_writer, provider_home_reader;
GRANT USAGE ON SCHEMA outbox TO provider_home_writer, provider_home_reader;

-- ---------------------------------------------------------------------------
-- candidates.model_candidate
-- Raw model evaluation results before BCS promotion.
-- Every row here is an offline-candidate until gate_receipt_id is populated
-- by the BCS gate after successful promotion to Unity Catalog.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS candidates.model_candidate (
    id                  BIGSERIAL PRIMARY KEY,
    candidate_id        UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    content_hash        TEXT NOT NULL,            -- SHA-256 of the evaluated artifact
    model_id            TEXT NOT NULL,
    provider            TEXT NOT NULL,
    domain              TEXT NOT NULL,
    exec_locus          TEXT NOT NULL CHECK (exec_locus IN ('in_azure', 'anthropic_hosted', 'local')),

    -- Eval signals (continuous-eval from Foundry + Azure Monitor)
    benchmark_score     NUMERIC(8,4),
    cost_per_1k_tokens  NUMERIC(12,6),
    latency_p50_ms      INT,
    latency_p99_ms      INT,
    modality            TEXT[],                   -- e.g. ['text', 'vision', 'code']

    -- BCS gate outcome (null = not yet promoted)
    gate_receipt_id     UUID,                     -- populated by BCS gate on promotion
    promoted_at         TIMESTAMPTZ,
    promotion_status    TEXT NOT NULL DEFAULT 'pending'
                        CHECK (promotion_status IN ('pending', 'promoted', 'rejected', 'expired')),
    rejection_reason    TEXT,

    -- Audit
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    source              TEXT DEFAULT 'continuous_eval'
);

CREATE INDEX IF NOT EXISTS idx_model_candidate_status ON candidates.model_candidate (promotion_status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_model_candidate_hash ON candidates.model_candidate (content_hash);
CREATE INDEX IF NOT EXISTS idx_model_candidate_domain ON candidates.model_candidate (domain, exec_locus);

GRANT SELECT, INSERT, UPDATE ON candidates.model_candidate TO provider_home_writer;
GRANT SELECT ON candidates.model_candidate TO provider_home_reader;
GRANT USAGE ON SEQUENCE candidates.model_candidate_id_seq TO provider_home_writer;

-- ---------------------------------------------------------------------------
-- candidates.cognition_record
-- Private intermediate reasoning steps from provider_home — not exported
-- until BCS promotion. Separate from the claim ledger in agents_home.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS candidates.cognition_record (
    id              BIGSERIAL PRIMARY KEY,
    candidate_id    UUID NOT NULL REFERENCES candidates.model_candidate (candidate_id),
    round           SMALLINT NOT NULL,
    role            TEXT NOT NULL,
    provider        TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    content_preview TEXT,                         -- first 500 chars, for diagnostics
    compliance_score NUMERIC(5,4),
    hallucination_risk NUMERIC(5,4),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_cognition_record_candidate ON candidates.cognition_record (candidate_id, round);

GRANT SELECT, INSERT ON candidates.cognition_record TO provider_home_writer;
GRANT SELECT ON candidates.cognition_record TO provider_home_reader;
GRANT USAGE ON SEQUENCE candidates.cognition_record_id_seq TO provider_home_writer;

-- ---------------------------------------------------------------------------
-- outbox.promotion_event
-- Events queued for replay into the BCS gate (cloud sync path).
-- The local BCS path writes here when offline; the outbox worker replays
-- these into the cloud BCS gate when connectivity is restored.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS outbox.promotion_event (
    id              BIGSERIAL PRIMARY KEY,
    event_id        UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    candidate_id    UUID NOT NULL,
    content_hash    TEXT NOT NULL,
    payload         JSONB NOT NULL,               -- full promotion payload for BCS gate
    idempotency_key TEXT NOT NULL UNIQUE,         -- candidate_id::content_hash::local_seq
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'replayed', 'failed', 'expired')),
    attempts        SMALLINT NOT NULL DEFAULT 0,
    last_attempt_at TIMESTAMPTZ,
    replayed_at     TIMESTAMPTZ,
    gate_receipt_id UUID,                         -- set when BCS gate acknowledges
    error_detail    TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ DEFAULT (now() + INTERVAL '7 days')
);

CREATE INDEX IF NOT EXISTS idx_promotion_event_status ON outbox.promotion_event (status, created_at);
CREATE INDEX IF NOT EXISTS idx_promotion_event_candidate ON outbox.promotion_event (candidate_id);

GRANT SELECT, INSERT, UPDATE ON outbox.promotion_event TO provider_home_writer;
GRANT SELECT ON outbox.promotion_event TO provider_home_reader;
GRANT USAGE ON SEQUENCE outbox.promotion_event_id_seq TO provider_home_writer;

-- ---------------------------------------------------------------------------
-- governance.schema_manifest (provider_home copy — separate lineage)
-- ---------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS governance;
GRANT USAGE ON SCHEMA governance TO provider_home_writer, provider_home_reader;

CREATE TABLE IF NOT EXISTS governance.schema_manifest (
    id              BIGSERIAL PRIMARY KEY,
    schema_name     TEXT NOT NULL,
    version         TEXT NOT NULL,
    applied_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    applied_by      TEXT NOT NULL DEFAULT current_user,
    checksum        TEXT,
    description     TEXT,
    UNIQUE (schema_name, version)
);

INSERT INTO governance.schema_manifest (schema_name, version, description)
VALUES ('provider_home', 'V001', 'Initial schema: candidates, outbox tables')
ON CONFLICT (schema_name, version) DO NOTHING;

GRANT SELECT, INSERT ON governance.schema_manifest TO provider_home_writer;
GRANT SELECT ON governance.schema_manifest TO provider_home_reader;
GRANT USAGE ON SEQUENCE governance.schema_manifest_id_seq TO provider_home_writer;

-- ---------------------------------------------------------------------------
-- Default grants on future tables
-- ---------------------------------------------------------------------------
ALTER DEFAULT PRIVILEGES IN SCHEMA candidates
    GRANT SELECT, INSERT, UPDATE ON TABLES TO provider_home_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA candidates
    GRANT SELECT ON TABLES TO provider_home_reader;

ALTER DEFAULT PRIVILEGES IN SCHEMA outbox
    GRANT SELECT, INSERT, UPDATE ON TABLES TO provider_home_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA outbox
    GRANT SELECT ON TABLES TO provider_home_reader;
