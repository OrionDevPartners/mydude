---
name: DSPy hermetic optimizer testing
description: How to test MIPROv2/GEPA prompt optimization offline with DummyLM, without a provider or network.
---

# Hermetic testing of DSPy optimizers (MIPROv2 / GEPA)

To exercise the prompt-optimization path (`src/promptopt/service.py`) in tests with
no provider and no network, inject a `dspy.utils.DummyLM` instead of the real LM
bridge (the service accepts an `lm=` arg for exactly this).

**The non-obvious part — one LM serves many signatures.** MIPROv2 doesn't only call
the program under test; its grounded proposer makes *meta* LM calls with their own
signatures (output fields like `proposed_instruction`, `observations`,
`program_description`, …). A DummyLM that returns only the program's output field
makes MIPROv2 raise `AdapterParseError: Expected to find output fields ...
[proposed_instruction]`.

**Fix:** pass DummyLM a single *superset* dict containing the program's output field
**and** the optimizer's meta fields, wrapped in an infinite iterator:
`DummyLM(itertools.repeat({"<program_field>": GOOD_TEXT, "proposed_instruction": ...,
"observations": ..., ...}))`. ChatAdapter extracts only the fields a given call
expects and ignores the extra blocks, so one dict satisfies every call type.

**Why `itertools.repeat` (not a list):** in list mode DummyLM does `iter(answers)`
and, when exhausted, returns a default `{"answer": "No more responses"}` (no crash,
but garbage scores). A non-list iterable is left as-is, so `repeat` yields the same
good response forever — robust against MIPROv2's trial fan-out and GEPA reflection.

**Other gotchas:**
- MIPROv2 requires the optional `optuna` package or it raises at compile time.
- Importing `dspy` prints spinner/ANSI escapes that make the bash tool return
  exit -1. Always redirect dspy-touching python to a file and `cat` it.
- Running the heavy test reliably (learned the hard way): start the process AND
  poll for completion in the SAME bash call — background jobs started in one bash
  call are killed when the next bash call starts. Log to a path INSIDE the
  workspace; `/tmp`, `/home/runner`, and `.local/state` get wiped between calls.
  The standalone runner buffers and prints PASS/FAIL only at the end, so an
  in-progress poll shows an empty file — that's normal, not a hang. It finishes in
  ~25-30s, well within the 120s cap when run+polled in one call.
- `tests/test_promptopt_governance.py` runs standalone (`python tests/...py`,
  exits non-zero on failure); pytest is NOT installed in this repl.
- GOOD_TEXT must contain all required section headers so `format_adherence` scores
  the candidate well; otherwise candidates score low and assertions on best-score
  get noisy.
