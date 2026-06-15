"""Governance-aligned optimization metric.

score = 0.5 * format_adherence   (fraction of the 6 required sections present)
      + 0.35 * (CS / 100)        (compliance score from the existing analyzer)
      + 0.15 * (1 - HR)          (1 minus hallucination risk)

The format check is hard (regex on section headers) and the compliance term
reuses the live governance analyzer, so the metric cannot be gamed purely by
emitting the section labels. The governance approval gate is the ultimate
Goodhart mitigation: a high-scoring candidate still cannot go live without a vote.
"""
from types import SimpleNamespace
from typing import Any, Dict, List

from src.promptopt.specs import JUDGE_OUTPUT_FIELD, REQUIRED_SECTIONS

_FORMAT_W = 0.5
_CS_W = 0.35
_HR_W = 0.15


def extract_output(pred: Any, output_field: str = JUDGE_OUTPUT_FIELD) -> str:
    if pred is None:
        return ""
    if isinstance(pred, str):
        return pred
    val = getattr(pred, output_field, None)
    if val is None and isinstance(pred, dict):
        val = pred.get(output_field)
    return val if isinstance(val, str) else ("" if val is None else str(val))


def format_adherence(text: str, sections: List[str] = REQUIRED_SECTIONS):
    up = (text or "").upper()
    present = [s for s in sections if s in up]
    missing = [s for s in sections if s not in up]
    return (len(present) / len(sections) if sections else 1.0), missing


def score_text(text: str, sections: List[str] = REQUIRED_SECTIONS) -> Dict[str, Any]:
    """Score a single output; returns the composite score + its components."""
    frac, missing = format_adherence(text, sections)
    violations: List[str] = []
    try:
        from src.swarm.compliance import analyze_agent_output
        from src.swarm.hallucination import (
            build_features_from_compliance, compute_hallucination_risk,
        )
        report = analyze_agent_output(text or "", intent_refs=[], mode="ANALYTIC")
        cs = int(report.score)
        violations = list(report.violations or [])
        # One synthetic OK reply so the fail-ratio feature is 0 for a single output.
        feats = build_features_from_compliance(report, [SimpleNamespace(ok=True)], 0.0)
        hr = float(compute_hallucination_risk(feats))
    except Exception as e:
        # FAIL LOUD (pillar 1 & 4): a broken governance analyzer must NEVER hand a
        # candidate the best-possible compliance/HR. Score the inference path as
        # worst-case so a candidate cannot rise on an un-scored output, and surface
        # the failure as a violation for the optimizer feedback + audit trail.
        cs = 0
        hr = 1.0
        violations = ["GOVERNANCE_ANALYZER_FAILED: %s" % (e.__class__.__name__)]
    score = _FORMAT_W * frac + _CS_W * (cs / 100.0) + _HR_W * (1.0 - hr)
    score = max(0.0, min(1.0, score))
    return {
        "score": round(score, 4),
        "format_fraction": round(frac, 4),
        "missing_sections": missing,
        "compliance_score": cs,
        "hallucination_risk": round(hr, 4),
        "violations": violations,
    }


def aggregate_scores(score_dicts: List[Dict[str, Any]],
                     sections: List[str] = REQUIRED_SECTIONS) -> Dict[str, Any]:
    """Average the per-output component breakdowns into one comparable summary.

    Produces the same components ``score_text`` emits (composite score, section
    coverage, compliance, hallucination risk) but averaged across an evaluation
    set, so a candidate's score can be explained — not just shown — side by side
    with the live baseline. ``missing_sections`` lists every required section that
    was absent in at least one output (most-frequent first) for operator triage.
    """
    secs = list(sections) if sections else list(REQUIRED_SECTIONS)
    n = len(score_dicts)
    if n == 0:
        return {
            "n": 0,
            "score": None,
            "format_fraction": None,
            "compliance_score": None,
            "hallucination_risk": None,
            "missing_sections": [],
        }

    def _avg(key: str) -> float:
        return sum(float(d.get(key) or 0.0) for d in score_dicts) / n

    miss_counts: Dict[str, int] = {}
    for d in score_dicts:
        for s in d.get("missing_sections", []) or []:
            miss_counts[s] = miss_counts.get(s, 0) + 1
    missing = sorted(miss_counts, key=lambda s: (-miss_counts[s], secs.index(s) if s in secs else 99))

    return {
        "n": n,
        "score": round(_avg("score"), 4),
        "format_fraction": round(_avg("format_fraction"), 4),
        "compliance_score": round(_avg("compliance_score"), 1),
        "hallucination_risk": round(_avg("hallucination_risk"), 4),
        "missing_sections": missing,
    }


_SAMPLE_FIELDS = (
    "score", "format_fraction", "compliance_score", "hallucination_risk",
    "missing_sections",
)
_SAMPLE_OUTPUT_CAP = 1000


def worst_examples(rows: List[Dict[str, Any]], limit: int = 3,
                   output_cap: int = _SAMPLE_OUTPUT_CAP) -> List[Dict[str, Any]]:
    """Return the lowest-scoring per-output rows (worst first), capped to ``limit``.

    Lets an operator drill past the averaged breakdown into the individual sample
    outputs that dragged a candidate's score down — and *which sections each was
    missing* — so failure modes are visible before promotion. Each returned row is
    the ``score_text`` component split plus a capped copy of the output text (the
    caller must have set ``output`` on each row). Only the allow-listed component
    fields + a few violations are kept so persisted provenance stays bounded.
    """
    ordered = sorted(rows, key=lambda r: float(r.get("score") or 0.0))
    out: List[Dict[str, Any]] = []
    for r in ordered[: max(0, limit)]:
        s: Dict[str, Any] = {k: r.get(k) for k in _SAMPLE_FIELDS}
        s["missing_sections"] = list(r.get("missing_sections") or [])
        s["violations"] = list((r.get("violations") or [])[:3])
        s["output"] = (r.get("output") or "")[:output_cap]
        out.append(s)
    return out


def metric(gold: Any, pred: Any, trace: Any = None, *args, **kwargs) -> float:
    """Scalar metric for MIPROv2 / dspy.Evaluate (judge defaults)."""
    return score_text(extract_output(pred))["score"]


def make_metric(output_field: str = JUDGE_OUTPUT_FIELD,
                sections: List[str] = REQUIRED_SECTIONS):
    """Build a scalar metric bound to a program's output field + required sections.

    Lets every governed program (judge or cognitive role) be optimized with the
    same scoring law against its OWN signature output field and section contract.
    """
    secs = list(sections) if sections else list(REQUIRED_SECTIONS)

    def _metric(gold: Any, pred: Any, trace: Any = None, *args, **kwargs) -> float:
        return score_text(extract_output(pred, output_field), secs)["score"]

    return _metric


def make_feedback(s: Dict[str, Any], sections: List[str] = REQUIRED_SECTIONS) -> str:
    secs = list(sections) if sections else list(REQUIRED_SECTIONS)
    fb: List[str] = []
    if s["missing_sections"]:
        fb.append(
            "Missing required sections: " + ", ".join(s["missing_sections"])
            + ". Include EVERY header exactly: " + ", ".join(secs) + "."
        )
    if s["violations"]:
        fb.append(
            "Compliance violations: " + "; ".join(s["violations"][:5])
            + ". Add epistemic labels (VERIFIED/DERIVED/HYPOTHESIS/UNKNOWN) and "
            "evidence pointers for load-bearing claims."
        )
    if not fb:
        fb.append(
            "All sections present and compliant. Tighten wording, keep epistemic "
            "labels, and remove token bloat."
        )
    return " ".join(fb)


def gepa_metric(gold: Any, pred: Any, trace: Any = None,
                pred_name: Any = None, pred_trace: Any = None):
    """Feedback-style metric for GEPA: returns Prediction(score, feedback) (judge defaults)."""
    import dspy
    s = score_text(extract_output(pred))
    return dspy.Prediction(score=s["score"], feedback=make_feedback(s))


def make_gepa_metric(output_field: str = JUDGE_OUTPUT_FIELD,
                     sections: List[str] = REQUIRED_SECTIONS):
    """Build a GEPA feedback metric bound to a program's output field + sections."""
    secs = list(sections) if sections else list(REQUIRED_SECTIONS)

    def _gepa(gold: Any, pred: Any, trace: Any = None,
              pred_name: Any = None, pred_trace: Any = None):
        import dspy
        s = score_text(extract_output(pred, output_field), secs)
        return dspy.Prediction(score=s["score"], feedback=make_feedback(s, secs))

    return _gepa
