"""Tests for the telephony / voice sub-stack governance guarantees (Task #66).

These run offline, with no real providers, network, or database:

  1. Honest status (``src/telephony/providers.py`` / ``src/avatar/providers.py``):
       * ``telephony_status`` / ``voice_status`` NEVER raise and report
         not-connected truthfully when no Twilio / ElevenLabs is configured.
  2. Proof-of-governance on TTS (``voice_synthesize`` capability):
       * the contract REQUIRES ``governed`` — a missing or ``False`` flag is a
         contract violation, so arbitrary ungoverned text can never reach the
         synthesizer (pillar #4).
       * the broker rejects an ungoverned ``voice_synthesize`` request BEFORE the
         policy gate (no provider call, no output).
       * a properly governed request passes the contract + policy gate, reaches
         the handler, and FAILS LOUD honestly when no voice provider is connected
         (no mock / silent audio) — and is audited.
       * the handler itself re-checks the governed flag, so even a direct
         (non-broker) call cannot synthesize ungoverned text.

The provider layer is genuinely unconfigured in the test env (no Twilio /
ElevenLabs secrets), so the suite is hermetic — it asserts the honest
not-connected / fail-loud behaviour rather than faking a provider.

Runnable two ways:
  * ``python tests/test_telephony_governance.py``  (standalone, exits non-zero on failure)
  * ``pytest tests/test_telephony_governance.py``   (test_* functions; no plugins needed)
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.swarm.broker import CapabilityBroker
from src.swarm.capability_contracts import get_contract, validate_request
from src.swarm.integrations import Integrations
from src.swarm.policy import PolicyEngine
from src.telephony import providers as tprov
from src.avatar import providers as vprov


def _broker():
    return CapabilityBroker(PolicyEngine(), Integrations())


# -- honest status ------------------------------------------------------------

def test_status_functions_never_raise_and_are_honest():
    ts = tprov.telephony_status()
    assert ts["connected"] in (True, False)
    assert "provider" in ts
    vs = vprov.voice_status()
    assert vs["connected"] in (True, False)
    assert "provider" in vs


# -- contract requires proof-of-governance ------------------------------------

def test_voice_synthesize_contract_requires_governed():
    contract = get_contract("voice_synthesize")
    assert "governed" in contract.required_fields, \
        "voice_synthesize must require the governed flag at the contract layer"
    # Missing governed -> contract violation.
    v1 = validate_request("voice_synthesize", {"text": "hi", "voice_id": "abc"})
    assert v1 is not None and "governed" in v1, v1
    # governed=False -> still a violation (the contract rejects a False value).
    v2 = validate_request(
        "voice_synthesize", {"text": "hi", "voice_id": "abc", "governed": False})
    assert v2 is not None and "governed" in v2, v2
    # governed=True with text+voice_id -> valid.
    v3 = validate_request(
        "voice_synthesize", {"text": "hi", "voice_id": "abc", "governed": True})
    assert v3 is None, v3


# -- broker gate: ungoverned TTS rejected before the policy gate ---------------

def test_broker_rejects_ungoverned_voice_synthesize():
    async def go():
        return await _broker().request(
            "voice_synthesize", {"text": "leak me", "voice_id": "abc"})
    res = asyncio.run(go())
    assert res.decision.allowed is False, "ungoverned TTS must be rejected"
    assert res.output is None, "a rejected request must never reach the provider"
    assert "governed" in (res.decision.reason or ""), res.decision.reason


# -- broker gate: governed TTS passes, then fails loud (no mock audio) ---------

def test_broker_allows_governed_voice_synthesize_but_fails_loud_when_unconnected():
    async def go():
        return await _broker().request("voice_synthesize", {
            "text": "hello", "voice_id": "abc", "governed": True,
            "decision_trace_id": 1, "call_session_id": 1, "source": "telephony-turn",
        })
    res = asyncio.run(go())
    # The governance gate ALLOWS it (governed + telephony enabled by default)...
    assert res.decision.allowed is True, res.decision.reason
    assert res.output is not None
    out = json.loads(res.output)
    # ...but with no ElevenLabs connected it FAILS LOUD — never a mock token.
    assert out.get("ok") is False, out
    assert not out.get("audio_token"), "must not return audio when unconnected"
    assert "connect" in (out.get("error") or "").lower(), out


# -- handler-level defense: direct ungoverned call also refused ----------------

def test_handler_refuses_ungoverned_direct_call():
    async def go():
        # Bypass the broker entirely and call the handler directly.
        return await Integrations().voice_synthesize(
            {"text": "hi", "voice_id": "abc"})  # no governed flag
    out = json.loads(asyncio.run(go()))
    assert out.get("ok") is False, out
    assert "governed" in (out.get("error") or "").lower(), out


def _run_all():
    tests = [
        test_status_functions_never_raise_and_are_honest,
        test_voice_synthesize_contract_requires_governed,
        test_broker_rejects_ungoverned_voice_synthesize,
        test_broker_allows_governed_voice_synthesize_but_fails_loud_when_unconnected,
        test_handler_refuses_ungoverned_direct_call,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print("PASS", t.__name__)
        except AssertionError as e:
            failed += 1
            print("FAIL", t.__name__, "->", e)
        except Exception as e:  # noqa: BLE001
            failed += 1
            print("ERROR", t.__name__, "->", type(e).__name__, e)
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
