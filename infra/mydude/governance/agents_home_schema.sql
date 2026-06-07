-- =============================================================================
-- agents_home schema — MyDude routing authority
-- Database: agents_home
-- Migration lineage: independent from provider_home (separate Flyway baseline)
-- Roles: agents_home_writer (app), agents_home_reader (readonly projections)
-- =============================================================================
-- AUTHORITY RULE: agents_home is the ONLY governance authority for routing.
-- All policy decisions, model-team assignments, exec_locus pins, and cloud_shift
-- state live here. No other database or service writes routing decisions.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Roles (created by postgres_migrator before DDL; idempotent guard shown here)
-- ---------------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agents_home_writer') THEN
    CREATE ROLE agents_home_writer LOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agents_home_reader') THEN
    CREATE ROLE agents_home_reader LOGIN;
  END IF;
END $$;

GRANT CONNECT ON DATABASE agents_home TO agents_home_writer;
GRANT CONNECT ON DATABASE agents_home TO agents_home_reader;

-- ---------------------------------------------------------------------------
-- Extensions (required before any table uses gen_random_uuid())
-- ---------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS pgcrypto;     -- provides gen_random_uuid() on PG < 13
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";  -- uuid_generate_v4() fallback

-- ---------------------------------------------------------------------------
-- Schemas
-- ---------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS routing;
CREATE SCHEMA IF NOT EXISTS policy;
CREATE SCHEMA IF NOT EXISTS governance;

GRANT USAGE ON SCHEMA routing TO agents_home_writer, agents_home_reader;
GRANT USAGE ON SCHEMA policy TO agents_home_writer, agents_home_reader;
GRANT USAGE ON SCHEMA governance TO agents_home_writer, agents_home_reader;

-- ---------------------------------------------------------------------------
-- routing.jurisdiction_decision
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS routing.jurisdiction_decision (
    id                  BIGSERIAL PRIMARY KEY,
    request_id          UUID NOT NULL DEFAULT gen_random_uuid(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Where does this request run?
    exec_locus          TEXT NOT NULL CHECK (exec_locus IN ('in_azure', 'anthropic_hosted', 'local')),
    -- Which tier of the 5-tier fallback resolved it?
    fallback_tier       SMALLINT NOT NULL CHECK (fallback_tier BETWEEN 1 AND 5),
    -- Which model-team handled it?
    model_team          TEXT,
    -- Which provider?
    resolved_provider   TEXT,
    -- Was cloud_shift active?
    cloud_shift_active  BOOLEAN NOT NULL DEFAULT TRUE,
    -- Was egress forced local?
    local_only          BOOLEAN NOT NULL DEFAULT FALSE,
    -- Domain (for exec_locus pin checking)
    domain              TEXT,
    -- Outcome
    outcome             TEXT NOT NULL CHECK (outcome IN ('executed', 'refused', 'queued', 'degraded')),
    detail              JSONB
);

CREATE INDEX IF NOT EXISTS idx_jurisdiction_decision_created ON routing.jurisdiction_decision (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jurisdiction_decision_locus ON routing.jurisdiction_decision (exec_locus);

GRANT SELECT, INSERT ON routing.jurisdiction_decision TO agents_home_writer;
GRANT SELECT ON routing.jurisdiction_decision TO agents_home_reader;
GRANT USAGE ON SEQUENCE routing.jurisdiction_decision_id_seq TO agents_home_writer;

-- ---------------------------------------------------------------------------
-- routing.cloud_shift
-- Single-row table; the BCS gate and policy engine read this as the egress
-- kill switch. When enabled=false, all cloud egress is blocked and the routing
-- ladder falls to local_degraded or refuse/queue.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS routing.cloud_shift (
    id              INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),  -- enforces singleton
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    reason          TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by      TEXT
);

INSERT INTO routing.cloud_shift (id, enabled, reason, updated_by)
VALUES (1, TRUE, 'default: egress enabled', 'bootstrap')
ON CONFLICT (id) DO NOTHING;

GRANT SELECT, UPDATE ON routing.cloud_shift TO agents_home_writer;
GRANT SELECT ON routing.cloud_shift TO agents_home_reader;

-- ---------------------------------------------------------------------------
-- routing.db_fallback_route
-- Static fallback routes when preferred providers are unavailable.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS routing.db_fallback_route (
    id              BIGSERIAL PRIMARY KEY,
    domain          TEXT NOT NULL,
    exec_locus      TEXT NOT NULL CHECK (exec_locus IN ('in_azure', 'anthropic_hosted', 'local')),
    fallback_tier   SMALLINT NOT NULL CHECK (fallback_tier BETWEEN 1 AND 5),
    provider        TEXT NOT NULL,
    model_id        TEXT NOT NULL,
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    priority        INT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_db_fallback_route_unique
    ON routing.db_fallback_route (domain, exec_locus, fallback_tier, priority);

GRANT SELECT, INSERT, UPDATE, DELETE ON routing.db_fallback_route TO agents_home_writer;
GRANT SELECT ON routing.db_fallback_route TO agents_home_reader;
GRANT USAGE ON SEQUENCE routing.db_fallback_route_id_seq TO agents_home_writer;

-- ---------------------------------------------------------------------------
-- policy.model_team_policy
-- Defines which models each team/domain may use. The Foundry Model Router
-- ONLY sees models approved here — never the full provider catalog.
-- exec_locus_pin enforces that a model on the wrong infra can never satisfy
-- a domain's exec_locus requirement.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS policy.model_team_policy (
    id                  BIGSERIAL PRIMARY KEY,
    team                TEXT NOT NULL,
    domain              TEXT NOT NULL,
    model_id            TEXT NOT NULL,
    provider            TEXT NOT NULL,
    exec_locus_pin      TEXT NOT NULL CHECK (exec_locus_pin IN ('in_azure', 'anthropic_hosted', 'local', 'any')),
    allowed             BOOLEAN NOT NULL DEFAULT TRUE,
    cost_cap_usd        NUMERIC(12,6),
    latency_budget_ms   INT,
    priority            INT NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_model_team_policy_unique
    ON policy.model_team_policy (team, domain, model_id, provider);
CREATE INDEX IF NOT EXISTS idx_model_team_policy_locus ON policy.model_team_policy (exec_locus_pin);

GRANT SELECT, INSERT, UPDATE, DELETE ON policy.model_team_policy TO agents_home_writer;
GRANT SELECT ON policy.model_team_policy TO agents_home_reader;
GRANT USAGE ON SEQUENCE policy.model_team_policy_id_seq TO agents_home_writer;

-- Seed: initial MyDude-granted model set
INSERT INTO policy.model_team_policy
    (team, domain, model_id, provider, exec_locus_pin, allowed, priority)
VALUES
    ('default', 'general',   'gpt-4.1-mini',            'openai',     'in_azure',        TRUE, 10),
    ('default', 'general',   'claude-sonnet-4-20250514', 'anthropic',  'anthropic_hosted', TRUE, 20),
    ('default', 'general',   'gemini-2.0-flash',         'gemini',     'in_azure',        TRUE, 30),
    ('default', 'general',   'grok-2-latest',            'grok',       'in_azure',        TRUE, 40),
    ('default', 'local',     'qwen3:14b',                'ollama',     'local',           TRUE, 10),
    ('default', 'local',     'llama3.2:3b',              'ollama',     'local',           TRUE, 20)
ON CONFLICT (team, domain, model_id, provider) DO NOTHING;

-- ---------------------------------------------------------------------------
-- policy.index_policy
-- Controls which indexes (LanceDB L1/L2, AI Search) the routing layer uses.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS policy.index_policy (
    id          BIGSERIAL PRIMARY KEY,
    index_name  TEXT NOT NULL UNIQUE,
    index_type  TEXT NOT NULL CHECK (index_type IN ('lancedb_l1', 'lancedb_l2', 'ai_search', 'duckdb')),
    location    TEXT NOT NULL CHECK (location IN ('local', 'azure')),
    authority   BOOLEAN NOT NULL DEFAULT FALSE,  -- always FALSE; indexes are projections
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    notes       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO policy.index_policy (index_name, index_type, location, authority, notes)
VALUES
    ('lancedb_l1_hot', 'lancedb_l1', 'local', FALSE, 'Hot vector store, local sovereign stack'),
    ('lancedb_l2_warm', 'lancedb_l2', 'azure', FALSE, 'Warm vector store, ADLS Gen2, rebuildable'),
    ('ai_search_main', 'ai_search', 'azure', FALSE, 'Azure AI Search, rebuildable projection')
ON CONFLICT (index_name) DO NOTHING;

GRANT SELECT, INSERT, UPDATE ON policy.index_policy TO agents_home_writer;
GRANT SELECT ON policy.index_policy TO agents_home_reader;
GRANT USAGE ON SEQUENCE policy.index_policy_id_seq TO agents_home_writer;

-- ---------------------------------------------------------------------------
-- policy.governed_threshold
-- Compliance and hallucination risk thresholds per domain.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS policy.governed_threshold (
    id                  BIGSERIAL PRIMARY KEY,
    domain              TEXT NOT NULL UNIQUE,
    min_compliance_score NUMERIC(5,4) NOT NULL DEFAULT 0.7,
    max_hallucination_risk NUMERIC(5,4) NOT NULL DEFAULT 0.3,
    require_consensus   BOOLEAN NOT NULL DEFAULT TRUE,
    min_providers       SMALLINT NOT NULL DEFAULT 2,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO policy.governed_threshold
    (domain, min_compliance_score, max_hallucination_risk, require_consensus, min_providers)
VALUES
    ('general', 0.70, 0.30, TRUE, 2),
    ('local',   0.65, 0.35, FALSE, 1)
ON CONFLICT (domain) DO NOTHING;

GRANT SELECT, INSERT, UPDATE ON policy.governed_threshold TO agents_home_writer;
GRANT SELECT ON policy.governed_threshold TO agents_home_reader;
GRANT USAGE ON SEQUENCE policy.governed_threshold_id_seq TO agents_home_writer;

-- ---------------------------------------------------------------------------
-- governance.claim_receipt
-- Durable idempotency log: every claim promoted by the BCS gate is recorded
-- here BEFORE the Unity Catalog write.  The UNIQUE constraint on
-- (candidate_id, content_hash) is the hard idempotency guarantee that works
-- across every BCS gate replica and worker process.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS governance.claim_receipt (
    id              BIGSERIAL PRIMARY KEY,
    gate_receipt_id UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    candidate_id    TEXT NOT NULL,
    content_hash    TEXT NOT NULL,          -- 64-char SHA-256 hex
    claim_type      TEXT NOT NULL CHECK (claim_type IN ('migration', 'model', 'outbox_replay')),
    authority       TEXT NOT NULL CHECK (authority IN ('unity', 'postgres', 'unknown')),
    exec_locus      TEXT NOT NULL CHECK (exec_locus IN ('in_azure', 'anthropic_hosted', 'local', 'unknown')),
    -- State machine: pending → unity_committed (success) | failed (transient Unity error)
    -- Idempotency check (V1) ONLY triggers on 'unity_committed' rows.
    -- Failed rows allow retry — the claim is not stuck.
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'unity_committed', 'failed')),
    failure_reason  TEXT,                   -- populated when status='failed'
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    committed_at    TIMESTAMPTZ,            -- set when status→unity_committed
    UNIQUE (candidate_id, content_hash)     -- cross-replica duplicate guard (advisory lock enforces ordering)
);

CREATE INDEX IF NOT EXISTS idx_claim_receipt_candidate  ON governance.claim_receipt (candidate_id);
CREATE INDEX IF NOT EXISTS idx_claim_receipt_created    ON governance.claim_receipt (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_claim_receipt_committed  ON governance.claim_receipt (committed_at DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_claim_receipt_status     ON governance.claim_receipt (status);

GRANT SELECT, INSERT, UPDATE ON governance.claim_receipt TO agents_home_writer;
GRANT SELECT ON governance.claim_receipt TO agents_home_reader;
GRANT USAGE ON SEQUENCE governance.claim_receipt_id_seq TO agents_home_writer;

-- ---------------------------------------------------------------------------
-- governance.schema_manifest
-- Self-describing manifest of all schema versions in this database.
-- ---------------------------------------------------------------------------
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
VALUES ('agents_home', 'V001', 'Initial schema: routing, policy, governance tables')
ON CONFLICT (schema_name, version) DO NOTHING;

GRANT SELECT, INSERT ON governance.schema_manifest TO agents_home_writer;
GRANT SELECT ON governance.schema_manifest TO agents_home_reader;
GRANT USAGE ON SEQUENCE governance.schema_manifest_id_seq TO agents_home_writer;

-- ---------------------------------------------------------------------------
-- governance.budget_cap
-- Per-domain cost caps enforced by the routing layer before dispatch.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS governance.budget_cap (
    id              BIGSERIAL PRIMARY KEY,
    domain          TEXT NOT NULL UNIQUE,
    daily_usd_cap   NUMERIC(12,2),
    monthly_usd_cap NUMERIC(12,2),
    alert_at_pct    NUMERIC(5,2) DEFAULT 80.0,
    hard_stop       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO governance.budget_cap (domain, daily_usd_cap, monthly_usd_cap, alert_at_pct, hard_stop)
VALUES ('general', 50.00, 1000.00, 80.0, TRUE)
ON CONFLICT (domain) DO NOTHING;

GRANT SELECT, INSERT, UPDATE ON governance.budget_cap TO agents_home_writer;
GRANT SELECT ON governance.budget_cap TO agents_home_reader;
GRANT USAGE ON SEQUENCE governance.budget_cap_id_seq TO agents_home_writer;

-- ---------------------------------------------------------------------------
-- Default grants on future tables
-- ---------------------------------------------------------------------------
ALTER DEFAULT PRIVILEGES IN SCHEMA routing
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO agents_home_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA routing
    GRANT SELECT ON TABLES TO agents_home_reader;

ALTER DEFAULT PRIVILEGES IN SCHEMA policy
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO agents_home_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA policy
    GRANT SELECT ON TABLES TO agents_home_reader;

ALTER DEFAULT PRIVILEGES IN SCHEMA governance
    GRANT SELECT, INSERT, UPDATE ON TABLES TO agents_home_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA governance
    GRANT SELECT ON TABLES TO agents_home_reader;
