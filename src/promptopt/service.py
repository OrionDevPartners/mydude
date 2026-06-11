"""Optimization service: run MIPROv2 (baseline) then GEPA (reflective) to evolve a
program's prompt, persisting candidate versions with provenance + scores.

DSPy's compile() is synchronous and minutes-long, so launch_run() validates +
records a PromptOptimizationRun then hands off to a daemon thread. A row-level
lock (with_for_update) on the program enforces a single concurrent run. On
startup, recover_orphans() marks any 'running' rows left by a crash as failed.

Fail-loud: no provider, or any optimizer error, ends the run as 'failed' with the
error recorded — never a silent fallback to an unverified prompt. The optimizer
core accepts an injected LM so tests can drive it hermetically with DummyLM.
"""
from __future__ import annotations

import json
import threading
import traceback
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from src.database import SessionLocal
from src.models import PromptOptimizationRun, PromptProgram
from src.providers.secrets import get_env
from src.promptopt import store
from src.promptopt.metric import gepa_metric, make_gepa_metric, make_metric, metric
from src.promptopt.runtime import _build_demos
from src.promptopt.specs import get_spec

DEFAULT_MIN_TRACES = 10


class RunInProgress(RuntimeError):
    """A run is already active for this program (maps to HTTP 409)."""


class NotEnoughTraces(ValueError):
    """Too few usable traces to optimize (maps to HTTP 400)."""


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def min_traces() -> int:
    try:
        v = get_env("PROMPTOPT_MIN_TRACES")
        return int(v) if v else DEFAULT_MIN_TRACES
    except Exception:
        return DEFAULT_MIN_TRACES


# ---------------------------------------------------------------------------
# Optimizer core (LM injectable for hermetic tests)
# ---------------------------------------------------------------------------

def _make_examples(traces, input_fields: List[str]):
    import dspy
    examples = []
    for t in traces:
        inp = json.loads(t.inputs_json or "{}")
        ex = dspy.Example(**{f: inp.get(f, "") for f in input_fields})
        examples.append(ex.with_inputs(*input_fields))
    return examples


def _split(examples: list) -> Tuple[list, list]:
    n = len(examples)
    if n < 4:
        return examples, examples
    k = max(1, int(n * 0.3))
    return examples[:-k], examples[-k:]


def _build_student(program_name: str, instructions: str, demos, lm):
    import dspy
    from src.promptopt.signatures import build_signature
    spec = get_spec(program_name)
    student = dspy.Predict(build_signature(program_name, instructions))
    student.demos = _build_demos(demos, spec.input_fields)
    student.set_lm(lm)
    return student


def _evaluate(program, dataset, input_fields: List[str], metric_fn=metric) -> Optional[float]:
    if not dataset:
        return None
    total, n = 0.0, 0
    for ex in dataset:
        n += 1
        try:
            pred = program(**{f: getattr(ex, f, "") for f in input_fields})
            total += metric_fn(ex, pred)
        except Exception:
            pass  # a failed prediction scores 0
    return round(total / max(1, n), 4)


def _evaluate_detailed(program, dataset, input_fields: List[str],
                       output_field: str, sections) -> Tuple[Optional[float], Optional[Dict[str, Any]]]:
    """Score a program over a dataset AND return the averaged metric breakdown.

    Runs the same predictions as ``_evaluate`` but keeps each output's component
    scores (section coverage / compliance / hallucination risk), so the operator
    can see WHY a candidate scored what it did, not only the composite number. A
    failed prediction is scored worst-case (empty output) — never silently
    skipped — so the breakdown can't be gamed by dropping hard examples.
    """
    from src.promptopt.metric import aggregate_scores, extract_output, score_text
    if not dataset:
        return None, None
    rows: List[Dict[str, Any]] = []
    for ex in dataset:
        try:
            pred = program(**{f: getattr(ex, f, "") for f in input_fields})
            text = extract_output(pred, output_field)
        except Exception:
            text = ""
        rows.append(score_text(text, sections))
    agg = aggregate_scores(rows, sections)
    return agg.get("score"), agg


def _serialize_demos(demos) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for d in demos or []:
        try:
            out.append(d.toDict())
        except Exception:
            try:
                out.append({k: d.get(k) for k in d.keys()})
            except Exception:
                continue
    return out


def _mipro():
    try:
        from dspy.teleprompt import MIPROv2
    except Exception:
        from dspy import MIPROv2
    return MIPROv2


def _gepa():
    try:
        from dspy import GEPA
    except Exception:
        from dspy.teleprompt import GEPA
    return GEPA


def run_optimizers(program_name: str, instructions: str, demos,
                   trainset: list, valset: list, lm,
                   budgets: Dict[str, Any]) -> Tuple[Optional[float], Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """Run MIPROv2 then GEPA. Returns (base_score, base_breakdown, [candidate dicts]).

    Every score is evaluated WITH its component breakdown against the SAME
    eval set, so the candidate vs. live comparison the operator sees is apples to
    apples (same examples, same metric, same run)."""
    spec = get_spec(program_name)
    candidates: List[Dict[str, Any]] = []

    # Spec-aware metrics: each program is scored against its OWN signature output
    # field + section contract (the judge and every cognitive role share the law,
    # not the field names).
    sections = spec.required_sections or None
    gepa_feedback_metric = make_gepa_metric(spec.output_field, sections)
    eval_set = valset or trainset

    student = _build_student(program_name, instructions, demos, lm)
    base, base_breakdown = _evaluate_detailed(
        student, eval_set, spec.input_fields, spec.output_field, sections
    )

    # --- MIPROv2 baseline ---------------------------------------------------
    MIPROv2 = _mipro()
    mipro = MIPROv2(
        metric=make_metric(spec.output_field, sections), prompt_model=lm, task_model=lm,
        auto=budgets.get("mipro_auto", "light"),
        num_threads=budgets.get("num_threads", 1),
    )
    compiled = mipro.compile(
        student, trainset=trainset, valset=valset, requires_permission_to_run=False
    )
    mp = compiled.predictors()[0]
    mp_score, mp_breakdown = _evaluate_detailed(
        compiled, eval_set, spec.input_fields, spec.output_field, sections
    )
    candidates.append({
        "optimizer": "MIPROv2",
        "instructions": mp.signature.instructions,
        "demos": _serialize_demos(getattr(mp, "demos", [])),
        "score": mp_score,
        "breakdown": mp_breakdown,
    })

    # --- GEPA reflective (seeded from the MIPRO result) ---------------------
    GEPA = _gepa()
    gepa_kwargs: Dict[str, Any] = {
        "metric": gepa_feedback_metric,
        "reflection_lm": lm,
        "num_threads": budgets.get("num_threads", 1),
        "reflection_minibatch_size": budgets.get("gepa_minibatch", 2),
    }
    if "gepa_max_metric_calls" in budgets:
        gepa_kwargs["max_metric_calls"] = budgets["gepa_max_metric_calls"]
    else:
        gepa_kwargs["auto"] = budgets.get("gepa_auto", "light")
    gepa = GEPA(**gepa_kwargs)
    gcompiled = gepa.compile(compiled, trainset=trainset, valset=valset)
    gp = gcompiled.predictors()[0]
    gp_score, gp_breakdown = _evaluate_detailed(
        gcompiled, eval_set, spec.input_fields, spec.output_field, sections
    )
    candidates.append({
        "optimizer": "GEPA",
        "instructions": gp.signature.instructions,
        "demos": _serialize_demos(getattr(gp, "demos", [])),
        "score": gp_score,
        "breakdown": gp_breakdown,
    })

    return base, base_breakdown, candidates


# ---------------------------------------------------------------------------
# Run lifecycle
# ---------------------------------------------------------------------------

def _finish(run_id: int, **fields) -> None:
    db = SessionLocal()
    try:
        run = db.query(PromptOptimizationRun).filter_by(id=run_id).first()
        if run is None:
            return
        for k, v in fields.items():
            setattr(run, k, v)
        run.completed_at = datetime.utcnow()
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def execute_run(run_id: int, program_name: str, optimizer: str = "mipro+gepa",
                budgets: Optional[Dict[str, Any]] = None, lm=None) -> None:
    """Body of an optimization run. Updates the run row to completed/failed."""
    budgets = budgets or {}
    db = SessionLocal()
    try:
        prog = store.get_program(db, program_name)
        if prog is None:
            _finish(run_id, status="failed", error="program '%s' not found" % program_name)
            return
        program_id = prog.id
        traces = store.load_usable_traces(db, program_id, limit=budgets.get("trace_limit", 200))
        spec = get_spec(program_name)
        examples = _make_examples(traces, spec.input_fields)
    finally:
        db.close()

    _, live_instructions, live_demos = store.get_live_instructions(program_name)
    trainset, valset = _split(examples)

    if lm is None:
        from src.promptopt.lm_bridge import ProviderBackedLM, available_provider
        if available_provider() is None:
            _finish(run_id, status="failed",
                    error="No LLM provider available for optimization. Add a working "
                          "API key or connect a provider, then retry.")
            return
        lm = ProviderBackedLM(runtime=False, max_tokens=budgets.get("max_tokens", 1500))

    try:
        base, base_breakdown, candidates = run_optimizers(
            program_name, live_instructions, live_demos, trainset, valset, lm, budgets
        )
        persisted: List[Dict[str, Any]] = []
        best = base
        for c in candidates:
            delta = None
            if c["score"] is not None and base is not None:
                delta = round(c["score"] - base, 4)
            provenance = {
                "optimizer": c["optimizer"],
                "base_score": base,
                "candidate_score": c["score"],
                "delta": delta,
                # Per-component breakdown for the operator's promote/rollback call:
                # the candidate's metric components and the SAME-RUN live baseline,
                # so "why is this better" is auditable, not just a single number.
                "breakdown": c.get("breakdown"),
                "base_breakdown": base_breakdown,
                "trainset_size": len(trainset),
                "valset_size": len(valset),
                "run_id": run_id,
                "date": _now_iso(),
            }
            vid = store.create_candidate_version(
                program_id, c["instructions"], c["demos"], provenance, c["score"], run_id
            )
            persisted.append({"version_id": vid, "optimizer": c["optimizer"], "score": c["score"]})
            if c["score"] is not None and (best is None or c["score"] > best):
                best = c["score"]
        _finish(
            run_id, status="completed", base_score=base, best_score=best,
            trainset_size=len(trainset),
            candidates_json=json.dumps(persisted),
            log="Produced %d candidate version(s)." % len(persisted),
        )
    except Exception as e:
        _finish(
            run_id, status="failed",
            error=(str(e) + "\n" + traceback.format_exc())[:4000],
        )


def launch_run(program_name: str, optimizer: str = "mipro+gepa",
               started_by: str = "operator",
               budgets: Optional[Dict[str, Any]] = None) -> int:
    """Validate + record a run, then start it on a daemon thread. Returns run id."""
    db = SessionLocal()
    try:
        prog = (
            db.query(PromptProgram)
            .filter_by(name=program_name)
            .with_for_update()
            .first()
        )
        if prog is None:
            raise ValueError("unknown program '%s'" % program_name)
        active = (
            db.query(PromptOptimizationRun)
            .filter_by(program_id=prog.id, status="running")
            .first()
        )
        if active is not None:
            raise RunInProgress(
                "an optimization run is already in progress for '%s'" % program_name
            )
        usable = store.count_usable_traces(db, prog.id)
        need = min_traces()
        if usable < need:
            raise NotEnoughTraces(
                "need at least %d usable traces to optimize '%s', have %d"
                % (need, program_name, usable)
            )
        run = PromptOptimizationRun(
            program_id=prog.id, optimizer=optimizer, status="running",
            trainset_size=usable, started_by=started_by,
        )
        db.add(run)
        db.commit()
        run_id = run.id
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    t = threading.Thread(
        target=execute_run, args=(run_id, program_name, optimizer, budgets),
        name="promptopt-run-%d" % run_id, daemon=True,
    )
    t.start()
    return run_id


def recover_orphans() -> int:
    """Mark runs left 'running' by a crash/restart as failed. Returns count."""
    db = SessionLocal()
    try:
        rows = db.query(PromptOptimizationRun).filter_by(status="running").all()
        for r in rows:
            r.status = "failed"
            r.error = "interrupted by application restart"
            r.completed_at = datetime.utcnow()
        db.commit()
        return len(rows)
    except Exception:
        db.rollback()
        return 0
    finally:
        db.close()
