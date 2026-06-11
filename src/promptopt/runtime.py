"""Runtime execution of a governed prompt program (the live, approved version).

Used by the swarm at inference time. Loads the live instructions+demos for a
program, runs them through DSPy on a provider-backed LM, scores the output with
the governance analyzers, and records a trace the optimizers later consume.

Fail-loud: if no provider is available or DSPy parsing fails, the trace is
recorded as 'failed' and the exception is re-raised so the caller can fall back
to its own degraded path (it must never silently emit an unverified answer).
"""
from __future__ import annotations

from typing import Any, Dict, List

from src.promptopt.metric import extract_output, score_text
from src.promptopt.specs import JUDGE_PROGRAM, get_spec
from src.promptopt import store


def _build_demos(demos: List[Dict[str, Any]], input_fields: List[str]):
    import dspy
    out = []
    for d in demos or []:
        if not isinstance(d, dict):
            continue
        ex = dspy.Example(**d)
        present = [f for f in input_fields if f in d]
        out.append(ex.with_inputs(*present) if present else ex)
    return out


async def run_program(program_name: str, inputs: Dict[str, Any], max_tokens: int = 1500) -> str:
    """Run the live version of a program with the given input-field kwargs."""
    import dspy
    from src.promptopt.lm_bridge import ProviderBackedLM
    from src.promptopt.signatures import build_signature

    spec = get_spec(program_name)
    version_id, instructions, demos = store.get_live_instructions(program_name)

    sig = build_signature(program_name, instructions)
    predictor = dspy.Predict(sig)
    predictor.demos = _build_demos(demos, spec.input_fields)

    lm = ProviderBackedLM(runtime=True, max_tokens=max_tokens)
    predictor.set_lm(lm)

    call_kwargs = {f: (inputs.get(f) or "") for f in spec.input_fields}
    try:
        pred = await predictor.acall(**call_kwargs)
        text = extract_output(pred, spec.output_field)
        if not text.strip():
            raise RuntimeError("governed program '%s' produced an empty output" % program_name)
        info = score_text(text, spec.required_sections or None)
        store.record_trace(
            program_name, version_id, call_kwargs, text,
            score_info=info, status="ok",
            feedback={"missing_sections": info["missing_sections"], "violations": info["violations"]},
        )
        return text
    except Exception as e:
        store.record_trace(
            program_name, version_id, call_kwargs, "",
            score_info=None, status="failed",
            feedback={"error": str(e)[:500]},
        )
        raise


async def run_judge(user_request: str, provider_outputs: str,
                    risk_directive: str = "", max_tokens: int = 1500) -> str:
    """Governed merger/judge synthesis (replaces the hardcoded judge prompt)."""
    return await run_program(
        JUDGE_PROGRAM,
        {
            "user_request": user_request or "",
            "provider_outputs": provider_outputs or "",
            "risk_directive": risk_directive or "",
        },
        max_tokens=max_tokens,
    )


async def run_role(program_name: str, goal: str, task: str, context: str = "",
                   mode: str = "ANALYTIC", max_tokens: int = 1500) -> str:
    """Governed single-call execution of a cognitive-role agent.

    The role's discipline is the program's LIVE, approved instructions (replaces
    the hardcoded role prompt). Same trace + scoring path as the judge, so role
    runs feed the same optimization and governance promotion/rollback flow.
    """
    return await run_program(
        program_name,
        {
            "goal": goal or "",
            "task": task or "",
            "context": context or "",
            "mode": mode or "ANALYTIC",
        },
        max_tokens=max_tokens,
    )
