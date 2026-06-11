"""DevGuard lifecycle guardian — pure safety-tier classifiers.

Consolidated from the vendored ``ci-pr-bug-lifecycle-guardian`` and stripped of
all GitHub / argparse / file-I/O coupling. These are *pure functions*: given a
diagnosis they decide HOW MUCH autonomy a proposed remediation may have — they
never perform the remediation, call a provider, or touch the network.

Pipeline::

    blast_radius  → how operationally risky are the touched files?
    confidence    → how safe is the proposed fix?
    authority     → the binding tier (the gate)

Tiers (:class:`Tier`):
    TIER_0 OBSERVE_ONLY   — diagnose / receipt only, no mutation
    TIER_1 SAFE_PATCH     — bounded, reversible, deterministic patch
    TIER_2 GUARDED_REPAIR — needs prior lineage + bounded blast + governance flag
    TIER_3 HUMAN_REQUIRED — no autonomous mutation, escalate to a human

These are pure (no resources, no secrets, no deps), so they are intentionally
*not* behind the production gate — the gate lives where DevGuard actually
acquires resources (the index in :mod:`.index`, the ledger in :mod:`.ledger`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Optional, Sequence


# --------------------------------------------------------------------------- #
# Tiers + confidence levels
# --------------------------------------------------------------------------- #
class Tier(IntEnum):
    OBSERVE_ONLY = 0
    SAFE_PATCH = 1
    GUARDED_REPAIR = 2
    HUMAN_REQUIRED = 3


class Confidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNSAFE = "UNSAFE"


# --------------------------------------------------------------------------- #
# Blast radius
# --------------------------------------------------------------------------- #
CATEGORY_SCORES = {
    "docs_only": 0.1,
    "workflow_only": 0.2,
    "dependency_only": 0.3,
    "ci_pipeline": 0.4,
    "orchestration": 0.6,
    "runtime_topology": 0.7,
    "provider_routing": 0.75,
    "auth_security": 0.9,
    "deployment": 0.95,
}

PATH_CATEGORY_MAP = [
    (r"\.(md|txt|rst)$", "docs_only"),
    (r"\.github/workflows/", "workflow_only"),
    (r"(requirements|package\.json|Gemfile|go\.mod|Cargo\.toml)", "dependency_only"),
    (r"(Dockerfile|docker-compose|\.ci|Jenkinsfile|buildspec)", "ci_pipeline"),
    (r"(orchestrat|dispatch|agent|bus|queue)", "orchestration"),
    (r"(runtime|kernel|substrate|engine|core)", "runtime_topology"),
    (r"(provider|bedrock|anthropic|openai|model_route|llm)", "provider_routing"),
    (r"(auth|security|secret|credential|oauth|token|encrypt|permission)", "auth_security"),
    (r"(deploy|release|prod|staging|terraform|helm|infra|k8s)", "deployment"),
]


@dataclass
class BlastRadius:
    score: float
    category: str
    categories_hit: dict[str, int]
    auto_repair_allowed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "blast_radius_score": self.score,
            "blast_radius_category": self.category,
            "categories_hit": self.categories_hit,
            "auto_repair_allowed": self.auto_repair_allowed,
            "max_category": self.category,
        }


def classify_blast_radius(
    affected_files: Sequence[str], findings: Sequence[dict] | None = None
) -> BlastRadius:
    """Estimate operational mutation risk from the touched files + findings."""
    findings = findings or []
    categories_hit: dict[str, list[str]] = {}

    for filepath in affected_files:
        matched = False
        for pattern, category in PATH_CATEGORY_MAP:
            if re.search(pattern, filepath, re.IGNORECASE):
                categories_hit.setdefault(category, []).append(filepath)
                matched = True
                break
        if not matched:
            categories_hit.setdefault("ci_pipeline", []).append(filepath)

    for finding in findings:
        cat = finding.get("category", "")
        if cat in ("permissions", "auth"):
            categories_hit.setdefault("auth_security", []).append("(from finding)")
        elif cat == "infra":
            categories_hit.setdefault("runtime_topology", []).append("(from finding)")

    if not categories_hit:
        return BlastRadius(
            score=0.0,
            category="unknown",
            categories_hit={},
            auto_repair_allowed=True,
        )

    max_category = max(categories_hit.keys(), key=lambda c: CATEGORY_SCORES.get(c, 0.5))
    max_score = CATEGORY_SCORES.get(max_category, 0.5)
    breadth_penalty = min(0.2, len(categories_hit) * 0.05)
    final_score = min(1.0, max_score + breadth_penalty)

    return BlastRadius(
        score=round(final_score, 3),
        category=max_category,
        categories_hit={k: len(v) for k, v in categories_hit.items()},
        auto_repair_allowed=final_score < 0.6,
    )


# --------------------------------------------------------------------------- #
# Confidence
# --------------------------------------------------------------------------- #
SECURITY_PATHS = {"auth", "security", "secrets", "credentials", "oauth", "token", "encrypt"}
DEPLOYMENT_PATHS = {"deploy", "release", "production", "staging", "infrastructure", "terraform", "helm"}
ORCHESTRATION_PATHS = {"orchestrat", "dispatch", "runtime", "kernel", "governance"}
PROVIDER_PATHS = {"bedrock", "anthropic", "openai", "provider", "aws", "gcp", "azure"}

SAFE_CATEGORIES = {"workflow_syntax", "dependency", "shell"}
RISKY_CATEGORIES = {"auth", "permissions", "infra", "resource"}
UNSAFE_CATEGORIES = {"deployment", "security_mutation"}

SAFE_STRATEGIES = {"lint_and_fix", "yaml_lint_fix", "install_missing_dep", "fix_shell_command", "npm_install"}
RISKY_STRATEGIES = {"fix_permissions", "update_action_ref"}
UNSAFE_STRATEGIES = {"copilot_review", "check_token_secrets", "increase_resources"}


@dataclass
class ConfidenceResult:
    level: Confidence
    score: float
    reasons: list[str] = field(default_factory=list)
    policy: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level.value,
            "score": self.score,
            "reasons": self.reasons,
            "policy": self.policy,
        }


def classify_confidence(
    diagnosis: dict, repo_profile: Optional[dict] = None
) -> ConfidenceResult:
    """Classify remediation safety BEFORE any repair execution (conservative)."""
    findings = diagnosis.get("findings", [])
    affected_files = diagnosis.get("affected_files", [])

    score = 1.0
    reasons: list[str] = []

    all_strategies = [f.get("strategy", "") for f in findings]
    if all_strategies and all(s in SAFE_STRATEGIES for s in all_strategies):
        score += 0.2
        reasons.append("all strategies are TIER_1 safe patches")

    for filepath in affected_files:
        path_lower = filepath.lower()
        if any(s in path_lower for s in SECURITY_PATHS):
            score -= 0.3
            reasons.append(f"security-sensitive path: {filepath}")
        elif any(s in path_lower for s in DEPLOYMENT_PATHS):
            score -= 0.2
            reasons.append(f"deployment path: {filepath}")
        elif any(s in path_lower for s in ORCHESTRATION_PATHS):
            score -= 0.15
            reasons.append(f"orchestration path: {filepath}")
        elif any(s in path_lower for s in PROVIDER_PATHS):
            score -= 0.1
            reasons.append(f"provider-coupled path: {filepath}")

    for finding in findings:
        cat = finding.get("category", "")
        if cat in UNSAFE_CATEGORIES:
            score -= 0.4
            reasons.append(f"unsafe category: {cat}")
        elif cat in RISKY_CATEGORIES:
            score -= 0.15
            reasons.append(f"risky category: {cat}")

    for finding in findings:
        strat = finding.get("strategy", "")
        if strat in UNSAFE_STRATEGIES:
            score -= 0.1
        elif strat in RISKY_STRATEGIES:
            score -= 0.05

    if len(affected_files) > 10:
        score -= 0.15
        reasons.append(f"high file count: {len(affected_files)}")
    if len(findings) > 8:
        score -= 0.1
        reasons.append(f"high finding count: {len(findings)}")

    if repo_profile:
        criticality = repo_profile.get("criticality", "MEDIUM")
        if criticality == "CORE_SUBSTRATE":
            score -= 0.3
            reasons.append("core substrate repo")
        elif criticality == "HIGH":
            score -= 0.15
            reasons.append("high criticality repo")
        mutation_policy = repo_profile.get("mutation_policy", "guarded")
        if mutation_policy == "manual_only":
            score = min(score, 0.0)
            reasons.append("manual_only policy")
        elif mutation_policy == "review_required":
            score = min(score, 0.4)
            reasons.append("review_required policy")

    score = max(0.0, min(1.0, score))

    if score >= 0.55:
        level, policy = Confidence.HIGH, "Auto remediation PR allowed"
    elif score >= 0.35:
        level, policy = Confidence.MEDIUM, "Issue + remediation proposal only (no PR)"
    elif score >= 0.15:
        level, policy = Confidence.LOW, "Diagnostics only"
    else:
        level, policy = Confidence.UNSAFE, "Governance escalation only"

    return ConfidenceResult(level=level, score=round(score, 3), reasons=reasons, policy=policy)


# --------------------------------------------------------------------------- #
# Authority (the binding tier)
# --------------------------------------------------------------------------- #
TIER1_PATCH_CLASSES = {
    "lint_and_fix",
    "yaml_lint_fix",
    "install_missing_dep",
    "npm_install",
    "fix_shell_command",
    "fix_permissions",
}
TIER2_STRATEGIES = {"update_action_ref", "define_before_use"}
TIER3_STRATEGIES = {
    "copilot_review",
    "check_token_secrets",
    "increase_resources",
    "retry_or_increase_timeout",
}
TIER3_FILE_PATTERNS = {
    "secret", "credential", "auth", "oauth", "token",
    "deploy", "terraform", "helm", "k8s",
    "governance", "kernel", "provider",
}


@dataclass
class AuthorityDecision:
    tier: int
    tier_name: str
    reasons: list[str]
    patch_classes: list[str]
    deterministic: bool
    blast_radius_estimate: float
    rollback_confidence: str
    validation_requirements: list[str]
    policy: str

    @property
    def human_required(self) -> bool:
        return self.tier >= Tier.HUMAN_REQUIRED

    @property
    def allows_autonomous_mutation(self) -> bool:
        return Tier.SAFE_PATCH <= self.tier <= Tier.GUARDED_REPAIR

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "tier_name": self.tier_name,
            "reasons": self.reasons,
            "patch_classes": self.patch_classes,
            "deterministic": self.deterministic,
            "blast_radius_estimate": self.blast_radius_estimate,
            "rollback_confidence": self.rollback_confidence,
            "validation_requirements": self.validation_requirements,
            "policy": self.policy,
            "human_required": self.human_required,
        }


def _validation_for_tier(tier: Tier) -> list[str]:
    base = ["syntax_check", "compile_check"]
    if tier <= Tier.SAFE_PATCH:
        return base + ["workflow_schema", "secret_scan"]
    if tier == Tier.GUARDED_REPAIR:
        return base + [
            "workflow_schema", "secret_scan", "lint",
            "portability_scan", "path_contamination",
        ]
    return []


def _policy_for_tier(tier: Tier) -> str:
    return {
        Tier.OBSERVE_ONLY: "Diagnose and receipt only. No mutation.",
        Tier.SAFE_PATCH: "Deterministic patch allowed. Rollback snapshot required.",
        Tier.GUARDED_REPAIR: "Repair allowed with governance flag + prior lineage.",
        Tier.HUMAN_REQUIRED: "No autonomous mutation. Escalate to a human.",
    }[tier]


def classify_authority(
    diagnosis: dict,
    confidence: ConfidenceResult,
    blast_radius: BlastRadius,
    repair_history: Optional[dict] = None,
) -> AuthorityDecision:
    """Classify the maximum allowed repair authority tier (the binding gate)."""
    findings = diagnosis.get("findings", [])
    affected_files = diagnosis.get("affected_files", [])

    max_tier = Tier.SAFE_PATCH
    reasons: list[str] = []
    patch_classes: list[str] = []
    deterministic = True

    # --- file patterns force TIER_3 ---
    for filepath in affected_files:
        path_lower = filepath.lower()
        if any(p in path_lower for p in TIER3_FILE_PATTERNS):
            max_tier = Tier.HUMAN_REQUIRED
            reasons.append(f"TIER_3 forced: sensitive path {filepath}")
            deterministic = False

    # --- strategies ---
    for finding in findings:
        strat = finding.get("strategy", "")
        if strat in TIER3_STRATEGIES:
            max_tier = max(max_tier, Tier.HUMAN_REQUIRED)
            reasons.append(f"TIER_3: strategy {strat}")
            deterministic = False
        elif strat in TIER2_STRATEGIES:
            max_tier = max(max_tier, Tier.GUARDED_REPAIR)
            reasons.append(f"TIER_2: strategy {strat}")
        elif strat in TIER1_PATCH_CLASSES:
            patch_classes.append(strat)

    # --- confidence gates ---
    if confidence.level == Confidence.UNSAFE:
        max_tier = Tier.HUMAN_REQUIRED
        reasons.append("TIER_3: confidence UNSAFE")
    elif confidence.level == Confidence.LOW:
        max_tier = max(max_tier, Tier.HUMAN_REQUIRED)
        reasons.append("TIER_3: confidence LOW")
    elif confidence.level == Confidence.MEDIUM:
        if max_tier == Tier.SAFE_PATCH and patch_classes:
            reasons.append("TIER_1 allowed: MEDIUM confidence + safe patch classes")
        else:
            max_tier = max(max_tier, Tier.GUARDED_REPAIR)
            reasons.append("TIER_2: confidence MEDIUM (non-safe strategies)")

    # --- blast radius gates ---
    if not blast_radius.auto_repair_allowed:
        max_tier = max(max_tier, Tier.HUMAN_REQUIRED)
        reasons.append(f"TIER_3: blast radius blocked (score={blast_radius.score})")
    elif blast_radius.score > 0.5:
        max_tier = max(max_tier, Tier.GUARDED_REPAIR)
        reasons.append(f"TIER_2: elevated blast radius ({blast_radius.score})")

    # --- TIER_2 requires successful prior lineage ---
    if max_tier == Tier.GUARDED_REPAIR:
        prior_successes = (repair_history or {}).get("successful_repairs", 0)
        if prior_successes < 1:
            max_tier = Tier.HUMAN_REQUIRED
            reasons.append("TIER_3: no successful prior lineage for TIER_2")

    # --- confidence decay (repeated failures reduce autonomy) ---
    if repair_history:
        consecutive_failures = repair_history.get("consecutive_failures", 0)
        if consecutive_failures >= 3:
            max_tier = Tier.HUMAN_REQUIRED
            reasons.append(f"TIER_3: confidence decay ({consecutive_failures} consecutive failures)")
        elif consecutive_failures >= 1:
            max_tier = max(max_tier, Tier.GUARDED_REPAIR)
            reasons.append(f"TIER_2: confidence decay ({consecutive_failures} prior failure)")

    # --- no actionable patch classes => observe only ---
    if max_tier == Tier.SAFE_PATCH and not patch_classes:
        max_tier = Tier.OBSERVE_ONLY
        reasons.append("TIER_0: no actionable patch classes found")

    rollback_confidence = (
        "high" if max_tier <= Tier.SAFE_PATCH
        else "medium" if max_tier == Tier.GUARDED_REPAIR
        else "low"
    )

    return AuthorityDecision(
        tier=int(max_tier),
        tier_name=max_tier.name,
        reasons=reasons,
        patch_classes=patch_classes,
        deterministic=deterministic and max_tier <= Tier.SAFE_PATCH,
        blast_radius_estimate=blast_radius.score,
        rollback_confidence=rollback_confidence,
        validation_requirements=_validation_for_tier(max_tier),
        policy=_policy_for_tier(max_tier),
    )


# --------------------------------------------------------------------------- #
# One-shot orchestrator
# --------------------------------------------------------------------------- #
@dataclass
class GuardianAssessment:
    blast: BlastRadius
    confidence: ConfidenceResult
    authority: AuthorityDecision

    @property
    def tier(self) -> int:
        return self.authority.tier

    @property
    def human_required(self) -> bool:
        return self.authority.human_required

    def to_dict(self) -> dict[str, Any]:
        return {
            "blast_radius": self.blast.to_dict(),
            "confidence": self.confidence.to_dict(),
            "authority": self.authority.to_dict(),
        }


def assess(
    diagnosis: dict,
    *,
    repo_profile: Optional[dict] = None,
    repair_history: Optional[dict] = None,
) -> GuardianAssessment:
    """Run the full guardian pipeline and return the combined assessment."""
    blast = classify_blast_radius(
        diagnosis.get("affected_files", []), diagnosis.get("findings", [])
    )
    confidence = classify_confidence(diagnosis, repo_profile)
    authority = classify_authority(diagnosis, confidence, blast, repair_history)
    return GuardianAssessment(blast=blast, confidence=confidence, authority=authority)
