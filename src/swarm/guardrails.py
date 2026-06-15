"""
Substrate Guardrail Classifiers
================================
Provider-agnostic, local-first guardrail layer that wraps the swarm pipeline:

  Ingress (pre-inference):
    - InjectionClassifier  — detects prompt-injection / jailbreak attempts
    - PIIRedactor          — redacts PII and secret patterns before they reach the swarm

  Egress (post-inference):
    - OutputSafetyClassifier — flags unsafe/harmful content in model output
    - CodeShield             — static analysis: flags destructive shell/SQL/file ops

Design pillars (matches replit.md governance pillars):
  P1. No placeholders — every classifier actually classifies.
  P2. Provider-agnostic — classifiers are behind a BaseClassifier interface.
  P4. Fail-loud — a guardrail error defaults to BLOCK, never silent pass.
  P6. Kill-switch — each classifier has an env flag to disable for dev debugging.

All verdicts are audited to GuardrailEvent (src/models.py).
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_TRUTHY = ("1", "true", "yes", "on")


def _env_flag(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name, "")
    if not raw.strip():
        return default
    return raw.strip().lower() in _TRUTHY


# ---------------------------------------------------------------------------
# Core types
# ---------------------------------------------------------------------------

class GuardrailStage(str, Enum):
    INGRESS = "ingress"
    EGRESS = "egress"


class GuardrailAction(str, Enum):
    PASS = "pass"
    BLOCK = "block"
    REDACT = "redact"
    FLAG = "flag"


@dataclass
class GuardrailVerdict:
    """Result produced by a single classifier pass."""
    classifier: str
    stage: GuardrailStage
    action: GuardrailAction
    confidence: float              # 0.0 – 1.0
    reason: str
    redacted_text: Optional[str] = None   # populated when action==REDACT
    patterns_matched: List[str] = field(default_factory=list)
    degraded: bool = False         # True when the classifier fell back due to error
    event_id: str = field(default_factory=lambda: f"GRD-{uuid.uuid4().hex[:8].upper()}")
    timestamp: float = field(default_factory=lambda: datetime.utcnow().timestamp())

    @property
    def blocked(self) -> bool:
        return self.action == GuardrailAction.BLOCK

    @property
    def passed(self) -> bool:
        return self.action == GuardrailAction.PASS


class GuardrailError(Exception):
    """Raised by a classifier when it cannot safely determine a verdict.

    Per the fail-loud pillar, callers must treat this as a BLOCK unless the
    classifier's kill-switch is explicitly active.
    """


# ---------------------------------------------------------------------------
# Base interface (P2 — provider-agnostic)
# ---------------------------------------------------------------------------

class BaseClassifier:
    """Abstract base for all guardrail classifiers.

    Subclasses must implement ``_classify(text) -> GuardrailVerdict``.
    ``classify()`` wraps it with the kill-switch and fail-loud logic.
    """
    name: str = "base"
    stage: GuardrailStage = GuardrailStage.INGRESS
    kill_switch_env: str = ""           # e.g. "DISABLE_INJECTION_CLASSIFIER"

    def _classify(self, text: str) -> GuardrailVerdict:
        raise NotImplementedError

    def classify(self, text: str) -> GuardrailVerdict:
        """Public entry point: applies kill-switch and fail-loud wrapper."""
        # Kill-switch: per-classifier dev-only bypass (disabled=True → skip)
        if self.kill_switch_env and not _env_flag(self.kill_switch_env, default=True):
            return GuardrailVerdict(
                classifier=self.name,
                stage=self.stage,
                action=GuardrailAction.PASS,
                confidence=0.0,
                reason=f"Classifier disabled via {self.kill_switch_env}",
            )
        try:
            return self._classify(text)
        except GuardrailError:
            raise
        except Exception as exc:
            logger.error("Guardrail classifier %s raised unexpected error: %s", self.name, exc)
            # Fail-loud: error → BLOCK (never silent pass)
            return GuardrailVerdict(
                classifier=self.name,
                stage=self.stage,
                action=GuardrailAction.BLOCK,
                confidence=1.0,
                reason=f"Classifier error — blocking by default: {exc}",
                degraded=True,
            )


# ---------------------------------------------------------------------------
# Ingress: InjectionClassifier
# ---------------------------------------------------------------------------

# Patterns indicative of prompt-injection / jailbreak attempts.
# Ordered from highest to lowest severity.
_INJECTION_PATTERNS: List[Tuple[str, str]] = [
    # Direct instruction override
    (r"ignore\s+(all\s+)?previous\s+instructions?", "ignore_previous_instructions"),
    (r"disregard\s+(all\s+)?(prior|previous|above)\s+instructions?", "disregard_instructions"),
    (r"forget\s+(everything|all)\s+(you\s+)?(were\s+)?told", "forget_instructions"),
    (r"you\s+are\s+now\s+(a|an|the)\s+\w+\s+(without|that\s+has\s+no)\s+restrict", "roleplay_no_restrict"),
    # Privilege escalation / DAN / jailbreak
    (r"\bDAN\b", "dan_jailbreak"),
    (r"do\s+anything\s+now", "do_anything_now"),
    (r"developer\s+mode", "developer_mode"),
    (r"jailbreak", "jailbreak_keyword"),
    (r"god\s+mode", "god_mode"),
    (r"unrestricted\s+mode", "unrestricted_mode"),
    (r"no\s+restrictions?", "no_restrictions"),
    (r"bypass\s+(all\s+)?(safety|guardrail|filter|restriction|policy)", "bypass_safety"),
    (r"ignore\s+(safety|guardrail|filter|policy|ethic|content)", "ignore_safety"),
    # System-prompt exfiltration
    (r"(print|repeat|output|reveal|show|dump|display)\s+(your\s+)?(system\s+prompt|instructions?|rules?)", "exfiltrate_system_prompt"),
    (r"what\s+(are\s+your\s+|is\s+your\s+)?(system\s+prompt|initial\s+instructions?)", "probe_system_prompt"),
    # Prompt injection via injection strings
    (r"<\|.*?im_start.*?\|>", "chat_ml_injection"),
    (r"\[INST\].*?\[/INST\]", "llama_instruction_injection"),
    (r"###\s*Human\s*:", "human_delimiter_injection"),
    # Goal hijacking
    (r"new\s+(goal|objective|task)\s*[:=]", "goal_hijack"),
    (r"from\s+now\s+on\s+you\s+(will|must|should|shall)", "from_now_on_override"),
    # Encoded / obfuscated attempts
    (r"base64\s*decode.*then\s*(run|exec|execute)", "encoded_exec"),
]

_INJECTION_RE = [
    (re.compile(p, re.IGNORECASE | re.DOTALL), label)
    for p, label in _INJECTION_PATTERNS
]

# Jailbreak scoring: text over this many pattern matches → BLOCK regardless of threshold
_BLOCK_MATCH_COUNT = 2
_BLOCK_CONFIDENCE_THRESHOLD = 0.5


class InjectionClassifier(BaseClassifier):
    """Local regex/heuristic classifier for prompt injection and jailbreak attempts.

    Kill-switch: DISABLE_INJECTION_CLASSIFIER=1 disables it entirely (dev only).
    Threshold env: INJECTION_BLOCK_THRESHOLD (float 0–1, default 0.5).
    """
    name = "injection"
    stage = GuardrailStage.INGRESS
    kill_switch_env = "DISABLE_INJECTION_CLASSIFIER"

    def _classify(self, text: str) -> GuardrailVerdict:
        if not text or not text.strip():
            return GuardrailVerdict(
                classifier=self.name, stage=self.stage,
                action=GuardrailAction.PASS, confidence=0.0,
                reason="Empty input — nothing to classify",
            )

        threshold = float(os.environ.get("INJECTION_BLOCK_THRESHOLD", "0.5"))
        matched: List[str] = []
        for rx, label in _INJECTION_RE:
            if rx.search(text):
                matched.append(label)

        if not matched:
            return GuardrailVerdict(
                classifier=self.name, stage=self.stage,
                action=GuardrailAction.PASS, confidence=0.05,
                reason="No injection patterns detected",
            )

        # Score: ratio of matched patterns, capped at 1.0
        confidence = min(1.0, len(matched) / max(1, len(_INJECTION_RE) * 0.15))

        if len(matched) >= _BLOCK_MATCH_COUNT or confidence >= threshold:
            return GuardrailVerdict(
                classifier=self.name, stage=self.stage,
                action=GuardrailAction.BLOCK,
                confidence=confidence,
                reason=f"Prompt injection / jailbreak detected ({len(matched)} pattern(s)): {', '.join(matched[:5])}",
                patterns_matched=matched,
            )

        # Single weak match → flag without blocking
        return GuardrailVerdict(
            classifier=self.name, stage=self.stage,
            action=GuardrailAction.FLAG,
            confidence=confidence,
            reason=f"Possible injection indicator ({len(matched)} pattern): {', '.join(matched)}",
            patterns_matched=matched,
        )


# ---------------------------------------------------------------------------
# Ingress: PIIRedactor
# ---------------------------------------------------------------------------

@dataclass
class _PIIPattern:
    label: str
    rx: re.Pattern
    replacement: str


def _pii_pat(label: str, pattern: str, replacement: str) -> _PIIPattern:
    return _PIIPattern(label=label, rx=re.compile(pattern, re.IGNORECASE), replacement=replacement)


_PII_PATTERNS: List[_PIIPattern] = [
    # Credentials / secrets
    _pii_pat("api_key_bearer",      r"Bearer\s+[A-Za-z0-9\-_]{20,}",                       "[REDACTED:API_KEY]"),
    _pii_pat("api_key_sk",          r"\bsk-[A-Za-z0-9]{20,}",                               "[REDACTED:API_KEY]"),
    _pii_pat("api_key_generic",     r"\b(?:api[_\-]?key|apikey)\s*[:=]\s*['\"]?[A-Za-z0-9\-_]{16,}", "[REDACTED:API_KEY]"),
    _pii_pat("password_field",      r"\b(?:password|passwd|secret)\s*[:=]\s*['\"]?[^\s'\"]{6,}", "[REDACTED:PASSWORD]"),
    _pii_pat("token_field",         r"\b(?:token|access[_\-]?token|auth[_\-]?token)\s*[:=]\s*['\"]?[A-Za-z0-9\-_.]{20,}", "[REDACTED:TOKEN]"),
    _pii_pat("private_key",         r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]+?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", "[REDACTED:PRIVATE_KEY]"),
    _pii_pat("aws_secret",          r"\bAWS[_\-]SECRET[_\-]ACCESS[_\-]KEY\s*[:=]\s*[^\s]{16,}", "[REDACTED:AWS_SECRET]"),
    _pii_pat("aws_key_id",          r"\bAWSKEYID\s*[:=]\s*[A-Z0-9]{20}",                   "[REDACTED:AWS_KEY_ID]"),
    # PII
    _pii_pat("email",               r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Z]{2,}\b", "[REDACTED:EMAIL]"),
    _pii_pat("ssn",                 r"\b\d{3}[-\s]\d{2}[-\s]\d{4}\b",                      "[REDACTED:SSN]"),
    _pii_pat("credit_card",         r"\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|3[47][0-9]{13}|6(?:011|5[0-9]{2})[0-9]{12})\b", "[REDACTED:CC]"),
    _pii_pat("phone_us",            r"\b(?:\+1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b", "[REDACTED:PHONE]"),
    _pii_pat("ip_address",          r"\b(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b", "[REDACTED:IP]"),
]


class PIIRedactor(BaseClassifier):
    """Local regex-based PII and secret redactor.

    Operates on ingress text before it reaches the swarm or any outbound call.
    Returns action=REDACT with the sanitized text in ``redacted_text``, or
    action=PASS when nothing is found.

    Kill-switch: DISABLE_PII_REDACTOR=1 disables it entirely (dev only).
    """
    name = "pii_redactor"
    stage = GuardrailStage.INGRESS
    kill_switch_env = "DISABLE_PII_REDACTOR"

    def _classify(self, text: str) -> GuardrailVerdict:
        if not text:
            return GuardrailVerdict(
                classifier=self.name, stage=self.stage,
                action=GuardrailAction.PASS, confidence=0.0,
                reason="Empty input",
            )

        redacted = text
        matched_labels: List[str] = []
        for pat in _PII_PATTERNS:
            new_text, count = pat.rx.subn(pat.replacement, redacted)
            if count:
                matched_labels.extend([pat.label] * count)
                redacted = new_text

        if not matched_labels:
            return GuardrailVerdict(
                classifier=self.name, stage=self.stage,
                action=GuardrailAction.PASS, confidence=0.0,
                reason="No PII / secrets detected",
            )

        return GuardrailVerdict(
            classifier=self.name, stage=self.stage,
            action=GuardrailAction.REDACT,
            confidence=min(1.0, len(matched_labels) / 3),
            reason=f"Redacted {len(matched_labels)} PII/secret instance(s): {', '.join(sorted(set(matched_labels))[:5])}",
            redacted_text=redacted,
            patterns_matched=list(set(matched_labels)),
        )


# ---------------------------------------------------------------------------
# Egress: OutputSafetyClassifier
# ---------------------------------------------------------------------------

_SAFETY_CATEGORIES: Dict[str, List[str]] = {
    "harmful_instruction": [
        r"\bhow\s+to\s+(make|build|create|synthesize|manufacture)\s+(a\s+)?(bomb|explosive|weapon|poison|drug|malware|ransomware|virus|trojan)\b",
        r"\bstep[s]?\s+(to|for)\s+(make|build|create)\s+(a\s+)?(bomb|explosive|drug|weapon)\b",
    ],
    "violence": [
        r"\b(murder|kill|assassinate|shoot|stab|attack)\s+(specific\s+)?(person|individual|target|victim|user)\b",
        r"\bhow\s+to\s+(harm|hurt|injure|kill|murder)\s+(a\s+)?(person|human|individual)\b",
    ],
    "secret_leak": [
        r"\bmy\s+(api[_\-]?key|password|secret|token)\s+is\s+['\"]?[A-Za-z0-9\-_]{8,}",
        r"\bsk-[A-Za-z0-9]{20,}\b",
        r"Bearer\s+[A-Za-z0-9\-_]{20,}",
    ],
    "policy_bypass_claim": [
        r"\b(I\s+have|you\s+have)\s+(been|been\s+given)\s+(special\s+)?(permission|authorization|clearance)\s+(to|for)\s+(ignore|bypass|override)\b",
        r"\boperator\s+(has\s+)?(disabled|removed|turned\s+off)\s+(all\s+)?(safety|guardrail|filter|restriction)\b",
    ],
}

_SAFETY_RE: List[Tuple[str, re.Pattern]] = [
    (cat, re.compile("|".join(pats), re.IGNORECASE | re.DOTALL))
    for cat, pats in _SAFETY_CATEGORIES.items()
]

_OUTPUT_SAFETY_BLOCK_THRESHOLD = 0.6


class OutputSafetyClassifier(BaseClassifier):
    """Local heuristic output-safety classifier (egress).

    Flags or blocks model output that contains harmful instructions, violence
    incitement, secret leakage, or policy-bypass claims.

    Kill-switch: DISABLE_OUTPUT_SAFETY_CLASSIFIER=1 disables it entirely (dev only).
    """
    name = "output_safety"
    stage = GuardrailStage.EGRESS
    kill_switch_env = "DISABLE_OUTPUT_SAFETY_CLASSIFIER"

    def _classify(self, text: str) -> GuardrailVerdict:
        if not text or not text.strip():
            return GuardrailVerdict(
                classifier=self.name, stage=self.stage,
                action=GuardrailAction.PASS, confidence=0.0,
                reason="Empty output",
            )

        threshold = float(os.environ.get("OUTPUT_SAFETY_BLOCK_THRESHOLD", str(_OUTPUT_SAFETY_BLOCK_THRESHOLD)))
        matched_cats: List[str] = []
        for cat, rx in _SAFETY_RE:
            if rx.search(text):
                matched_cats.append(cat)

        if not matched_cats:
            return GuardrailVerdict(
                classifier=self.name, stage=self.stage,
                action=GuardrailAction.PASS, confidence=0.02,
                reason="Output safety check passed",
            )

        confidence = min(1.0, len(matched_cats) / len(_SAFETY_RE) * 2)
        if confidence >= threshold or "secret_leak" in matched_cats or "harmful_instruction" in matched_cats:
            return GuardrailVerdict(
                classifier=self.name, stage=self.stage,
                action=GuardrailAction.BLOCK,
                confidence=confidence,
                reason=f"Unsafe output detected — categories: {', '.join(matched_cats)}",
                patterns_matched=matched_cats,
            )

        return GuardrailVerdict(
            classifier=self.name, stage=self.stage,
            action=GuardrailAction.FLAG,
            confidence=confidence,
            reason=f"Output safety concern (below block threshold): {', '.join(matched_cats)}",
            patterns_matched=matched_cats,
        )


# ---------------------------------------------------------------------------
# Egress: CodeShield
# ---------------------------------------------------------------------------

# Static patterns for destructive operations in generated code snippets.
# Matches shell, Python, SQL, and file operations that are irreversible/destructive.
_CODE_SHIELD_PATTERNS: List[Tuple[str, str]] = [
    # Destructive shell
    (r"\brm\s+-rf?\s+[/~]",                       "shell_rm_rf_root"),
    (r"\brm\s+-rf\b",                             "shell_rm_rf"),
    (r"\bdd\s+if=\S+\s+of=/dev/",                "shell_dd_device"),
    (r"\b(mkfs|format)\s+/dev/",                  "shell_format_device"),
    (r"\bshutdown\s+(-[hHrRnNfFkKcCqQ]+\s+)?(now|\d)",   "shell_shutdown"),
    (r"\b:(\s*)\{(\s*):(\s*)\|(\s*):(\s*)&(\s*)\};",      "shell_fork_bomb"),
    (r"\bchmod\s+(777|000)\s+/",                  "shell_unsafe_chmod"),
    (r"\bchown\s+.+\s+/",                         "shell_chown_root"),
    (r"\b(wget|curl)\s+.+\|\s*(bash|sh|zsh|python)",      "shell_pipe_execute"),
    (r"\beval\s+\$\(",                            "shell_eval_subshell"),
    # Python destructive
    (r"\bos\.system\s*\(\s*['\"]rm\s+-rf",        "python_os_system_rm"),
    (r"\bsubprocess\..*rm\s+-rf",                 "python_subprocess_rm"),
    (r"\bshutil\.rmtree\s*\(\s*['\"]?/",          "python_rmtree_root"),
    (r"\bopen\s*\(\s*['\"]?/etc/",                "python_write_etc"),
    (r"\bos\.remove\s*\(\s*['\"]?/",              "python_os_remove_root"),
    # SQL destructive
    (r"\bDROP\s+(TABLE|DATABASE|SCHEMA|INDEX)\b", "sql_drop"),
    (r"\bTRUNCATE\s+TABLE\b",                     "sql_truncate"),
    (r"\bDELETE\s+FROM\s+\w+\s*(?:;|$)",          "sql_delete_no_where"),
    (r"\bDROP\s+COLUMN\b",                        "sql_drop_column"),
    # File-system destructive
    (r"\bwrite.*\/etc\/(passwd|shadow|sudoers)",   "fs_write_sensitive"),
    (r"\bopen\s*\(.+['\"]w['\"]\s*\)\s*.+\/etc/", "fs_write_etc"),
]

_CODE_SHIELD_RE = [
    (re.compile(p, re.IGNORECASE | re.DOTALL | re.MULTILINE), label)
    for p, label in _CODE_SHIELD_PATTERNS
]

# Inline code-block detection
_CODE_FENCE_RE = re.compile(
    r"(?:```[\w]*\n?)([\s\S]*?)(?:```)|(?:`([^`]+)`)",
    re.MULTILINE,
)


def _extract_code_blocks(text: str) -> str:
    """Extract content of Markdown fenced/inline code blocks; fallback to full text."""
    blocks = _CODE_FENCE_RE.findall(text)
    if blocks:
        return "\n".join(b[0] or b[1] for b in blocks)
    return text


class CodeShield(BaseClassifier):
    """Static analysis pass over generated code for destructive operations (egress).

    Scans Markdown code blocks (and the full text as fallback) for shell, Python,
    SQL, and file operations that are irreversible or catastrophically destructive.
    Matches feed into the existing irreversible-action confirm gate rather than
    auto-remediating (out of scope per task spec).

    Kill-switch: DISABLE_CODE_SHIELD=1 disables it entirely (dev only).
    """
    name = "code_shield"
    stage = GuardrailStage.EGRESS
    kill_switch_env = "DISABLE_CODE_SHIELD"

    def _classify(self, text: str) -> GuardrailVerdict:
        if not text or not text.strip():
            return GuardrailVerdict(
                classifier=self.name, stage=self.stage,
                action=GuardrailAction.PASS, confidence=0.0,
                reason="No code content",
            )

        target = _extract_code_blocks(text)
        matched: List[str] = []
        for rx, label in _CODE_SHIELD_RE:
            if rx.search(target):
                matched.append(label)

        if not matched:
            return GuardrailVerdict(
                classifier=self.name, stage=self.stage,
                action=GuardrailAction.PASS, confidence=0.0,
                reason="No destructive code patterns detected",
            )

        # Any match in the code shield → FLAG requiring confirm gate
        # High-severity patterns → BLOCK immediately
        high_severity = {"shell_rm_rf_root", "shell_dd_device", "shell_format_device",
                         "shell_fork_bomb", "sql_drop", "fs_write_sensitive",
                         "shell_pipe_execute"}
        is_critical = bool(set(matched) & high_severity)

        action = GuardrailAction.BLOCK if is_critical else GuardrailAction.FLAG
        confidence = min(1.0, len(matched) / 3)

        return GuardrailVerdict(
            classifier=self.name, stage=self.stage,
            action=action,
            confidence=confidence,
            reason=f"Destructive code pattern(s) in generated output: {', '.join(matched[:6])}",
            patterns_matched=matched,
        )


# ---------------------------------------------------------------------------
# Audit persistence
# ---------------------------------------------------------------------------

def _persist_verdict(verdict: GuardrailVerdict, context_preview: str = "") -> None:
    """Durably write a guardrail verdict to GuardrailEvent. Never raises."""
    try:
        from src.database import SessionLocal
        from src.models import GuardrailEvent
        db = SessionLocal()
        try:
            db.add(GuardrailEvent(
                event_id=verdict.event_id,
                classifier=verdict.classifier,
                stage=verdict.stage.value,
                action=verdict.action.value,
                confidence=round(verdict.confidence, 4),
                reason=verdict.reason[:1000],
                patterns_json=",".join(verdict.patterns_matched[:20]) if verdict.patterns_matched else "",
                degraded=verdict.degraded,
                context_preview=(context_preview or "")[:300],
            ))
            db.commit()
        except Exception as exc:
            logger.warning("GuardrailEvent persist failed: %s", exc)
            db.rollback()
        finally:
            db.close()
    except Exception as exc:
        logger.warning("GuardrailEvent DB session failed: %s", exc)


def _emit_sentinel_event(verdict: GuardrailVerdict) -> None:
    """Raise a SentinelEvent for BLOCK verdicts so they appear in the governance dashboard."""
    try:
        from src.database import SessionLocal
        from src.models import SentinelEvent
        db = SessionLocal()
        try:
            db.add(SentinelEvent(
                alert_id=verdict.event_id,
                alert_type=f"guardrail_{verdict.classifier}",
                severity="critical" if verdict.action == GuardrailAction.BLOCK else "warning",
                description=(
                    f"[Guardrail:{verdict.classifier}] {verdict.reason[:300]}"
                ),
                recommended_action=(
                    "Review the flagged content and the matched pattern(s). "
                    "If a false positive, adjust the guardrail threshold or pattern via env vars."
                ),
            ))
            db.commit()
        except Exception as exc:
            logger.warning("SentinelEvent persist for guardrail verdict failed: %s", exc)
            db.rollback()
        finally:
            db.close()
    except Exception as exc:
        logger.warning("SentinelEvent DB session failed: %s", exc)


# ---------------------------------------------------------------------------
# GuardrailLayer — main orchestrator
# ---------------------------------------------------------------------------

class GuardrailLayer:
    """Orchestrates all classifiers for both ingress and egress passes.

    Usage:
        gl = GuardrailLayer()

        # Ingress (returns sanitized text or raises on block)
        safe_text = gl.run_ingress(raw_prompt)

        # Egress
        verdicts = gl.run_egress(model_output)
        if gl.egress_blocked(verdicts):
            ... # refuse to surface the output
    """

    def __init__(self) -> None:
        self._ingress: List[BaseClassifier] = [
            InjectionClassifier(),
            PIIRedactor(),
        ]
        self._egress: List[BaseClassifier] = [
            OutputSafetyClassifier(),
            CodeShield(),
        ]

    # -- Ingress -----------------------------------------------------------

    def run_ingress(self, text: str, audit: bool = True) -> Tuple[str, List[GuardrailVerdict]]:
        """Run all ingress classifiers on ``text``.

        Returns (possibly_redacted_text, verdicts).
        Raises GuardrailError on a BLOCK verdict so the caller can surface it
        as an explicit refusal. On classifier error the verdict is already BLOCK
        (fail-loud) and is re-raised.
        """
        verdicts: List[GuardrailVerdict] = []
        current = text

        for clf in self._ingress:
            v = clf.classify(current)
            verdicts.append(v)
            if audit:
                _persist_verdict(v, context_preview=text[:200])
                if v.blocked:
                    _emit_sentinel_event(v)

            if v.blocked:
                logger.warning(
                    "[Guardrail:INGRESS:BLOCK] classifier=%s reason=%s",
                    v.classifier, v.reason[:200],
                )
                raise GuardrailError(
                    f"[{v.classifier}] {v.reason}"
                )

            if v.action == GuardrailAction.REDACT and v.redacted_text is not None:
                logger.info(
                    "[Guardrail:INGRESS:REDACT] classifier=%s patterns=%s",
                    v.classifier, v.patterns_matched,
                )
                current = v.redacted_text

            elif v.action == GuardrailAction.FLAG:
                logger.warning(
                    "[Guardrail:INGRESS:FLAG] classifier=%s reason=%s",
                    v.classifier, v.reason[:200],
                )

        return current, verdicts

    # -- Egress ------------------------------------------------------------

    def run_egress(self, text: str, audit: bool = True) -> List[GuardrailVerdict]:
        """Run all egress classifiers on model output.

        Returns verdicts. Does NOT raise — egress blocks must be checked by the
        caller via ``egress_blocked(verdicts)``. This allows partial surfacing
        decisions (e.g. redacting rather than suppressing the whole output).
        """
        verdicts: List[GuardrailVerdict] = []

        for clf in self._egress:
            v = clf.classify(text)
            verdicts.append(v)
            if audit:
                _persist_verdict(v, context_preview=text[:200])
                if v.blocked or v.action == GuardrailAction.FLAG:
                    _emit_sentinel_event(v)

            if v.blocked:
                logger.warning(
                    "[Guardrail:EGRESS:BLOCK] classifier=%s reason=%s",
                    v.classifier, v.reason[:200],
                )
            elif v.action == GuardrailAction.FLAG:
                logger.warning(
                    "[Guardrail:EGRESS:FLAG] classifier=%s reason=%s",
                    v.classifier, v.reason[:200],
                )

        return verdicts

    @staticmethod
    def egress_blocked(verdicts: List[GuardrailVerdict]) -> bool:
        return any(v.blocked for v in verdicts)

    @staticmethod
    def egress_flags(verdicts: List[GuardrailVerdict]) -> List[GuardrailVerdict]:
        return [v for v in verdicts if v.action in (GuardrailAction.BLOCK, GuardrailAction.FLAG)]

    # -- Governance integration -------------------------------------------

    @staticmethod
    def verdicts_to_compliance_delta(verdicts: List[GuardrailVerdict]) -> float:
        """Map guardrail verdicts to a compliance-score delta (negative penalty).

        Blocks: -25 per verdict (major violation).
        Flags:   -8 per verdict (minor concern).
        Redacts: -5 per verdict (PII present).
        """
        delta = 0.0
        for v in verdicts:
            if v.blocked:
                delta -= 25.0
            elif v.action == GuardrailAction.FLAG:
                delta -= 8.0
            elif v.action == GuardrailAction.REDACT:
                delta -= 5.0
        return delta

    @staticmethod
    def verdicts_to_hr_delta(verdicts: List[GuardrailVerdict]) -> float:
        """Map guardrail verdicts to a hallucination-risk delta (0–0.3 additive).

        Injection / jailbreak attempts signal intent to circumvent governance
        which directly increases the epistemic risk of the response.
        """
        delta = 0.0
        for v in verdicts:
            if v.blocked and v.classifier in ("injection", "output_safety"):
                delta += 0.15
            elif v.action == GuardrailAction.FLAG:
                delta += 0.05
        return min(0.3, delta)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_layer: Optional[GuardrailLayer] = None


def get_guardrail_layer() -> GuardrailLayer:
    """Return the shared GuardrailLayer instance (lazily initialised)."""
    global _layer
    if _layer is None:
        _layer = GuardrailLayer()
    return _layer
