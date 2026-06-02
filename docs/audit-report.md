# Augur Codebase Audit Report

**Date:** 2026-06-02
**Scope:** Full source + test audit — bugs, logic errors, TODOs, missing features, test coverage gaps

---

## Executive Summary

The codebase is substantially complete for steps 1–9 of the 12-step build order. The CICIDS data pipeline (`mitre_mapping`, `cicids_loader`, `splits`) **is implemented and tested**. Phoenix MCP integration for eval/improvement agents **is also implemented** with both legacy (inline) and MCP-backed paths wired into the `/batch` endpoint. The test suite has **57 passing tests** and **2 failing** (Firestore auth in CI — expected without GCP credentials).

However, the audit identified **18 code issues** (3 high, 5 medium, 10 low), **1 dead code file**, **1 dashboard placeholder**, and **significant test coverage gaps** — 6 core modules have zero unit tests, and no error-path tests exist anywhere.

---

## 1. Code Issues

### HIGH — Logic Errors

#### H1. Precision/Recall computed identically (both eval agents)

**Files:** `src/augur/eval.py:80-81`, `src/augur/eval_phoenix.py:195-196`

Both eval agents set `precision = recall = tp / total`, making F1 always equal to accuracy. This is semantically wrong — precision and recall should be computed differently using a confusion matrix. In the current grouping-by-tactic approach, every item in a tactic bucket has the same ground-truth tactic, so "precision" and "recall" collapse to accuracy. This isn't a bug per se (the F1 value is still a useful signal), but it will confuse judges who expect precision and recall to differ.

**Recommended fix:** Either (a) rename the fields to `accuracy` and remove the misleading `precision`/`recall` labels, or (b) compute true precision/recall using a multi-class confusion matrix (predicted tactic vs. actual tactic, and predicted disposition vs. actual disposition). Option (b) is more impressive for the demo.

```python
# Example: true precision for a tactic = correctly predicted as this tactic /
# all predictions that claimed this tactic (including false positives)
```

---

#### H2. Triage agent ignores per-tactic prompt — always uses `_PROMPT_TEXT` for LLM call

**File:** `src/augur/agents/triage.py:114`

`build_triage_agent()` (line 53–71) correctly loads the prompt from Firestore into `agent.instruction`. But `run_triage()` (line 110) passes `_PROMPT_TEXT` (the module-level local file) to Gemini instead of the agent's instruction:

```python
# Line 114-115 — always uses module-level _PROMPT_TEXT, ignoring Firestore
contents=Content(
    role="user",
    parts=[Part(text=f"{_PROMPT_TEXT}\n\nAlert JSON:\n{alert_json}")],
```

This means the self-improvement loop writes new prompts to Firestore, but the triage agent never actually uses them. The demo narrative ("updated prompt → better results on re-run") is broken.

**Recommended fix:** Pass the agent's instruction text to the Gemini call:

```python
prompt = agent.instruction or _PROMPT_TEXT
contents=Content(
    role="user",
    parts=[Part(text=f"{prompt}\n\nAlert JSON:\n{alert_json}")],
)
```

---

#### H3. Race condition in `PromptStore.write_version` — version bump is not atomic

**File:** `src/augur/prompt_store.py:84-101`

`get_current_version()` reads the current version, then `write_version()` writes `current + 1` in a batch. Two concurrent improvement agents could read the same version and both write version N+1, causing one prompt to overwrite the other.

**Recommended fix:** Use a Firestore transaction instead of a batch:

```python
@firestore.transactional
def _bump(transaction, doc_ref, ...):
    snap = doc_ref.get(transaction=transaction)
    current = snap.to_dict().get("current_version", 0)
    new_version = current + 1
    transaction.update(doc_ref, {"current_version": new_version, ...})
    transaction.set(version_ref, {...})
    return new_version
```

---

### MEDIUM — Robustness Issues

#### M1. `_persist_eval` creates a new Firestore client on every call

**File:** `src/augur/main.py:33`

```python
def _persist_eval(eval_result, project="augur-495810"):
    db = firestore.Client(project=project)  # new client every time
```

This creates a fresh gRPC channel per eval persistence. Use the singleton from `prompt_store._get_db()` or cache the client.

---

#### M2. Phoenix MCP client `__aexit__` doesn't close the session

**File:** `src/augur/phoenix_mcp_client.py:140-148`

```python
async def __aexit__(self, ...):
    if self._stdio_transport is not None:
        await _exit_ctx(self._stdio_transport, exc_type, exc_val, exc_tb)
    self._session = None  # session is abandoned, not closed
```

The `ClientSession` is never explicitly closed. Depending on the MCP SDK version this could leak subprocess handles.

**Recommended fix:** Close the session before exiting the transport:

```python
if self._session is not None:
    # Some MCP versions expose close(); others don't
    if hasattr(self._session, "close"):
        await self._session.close()
```

---

#### M3. `_enter_ctx` / `_exit_ctx` helper is fragile

**File:** `src/augur/phoenix_mcp_client.py:33-51`

The helpers try to duck-type whether `stdio_client` returns a context manager or a raw tuple. If the MCP SDK changes behavior, this silently breaks. The `_exit_ctx` receives `val` but should receive the original context manager, not the yielded value.

**Recommended fix:** Pin the MCP SDK version in `pyproject.toml` and use the documented API directly.

---

#### M4. CICIDS loader treats Flow Duration as milliseconds but CICIDS stores microseconds

**File:** `src/augur/data/cicids_loader.py:66`

```python
flow_duration_ms=int(row["Flow Duration"]),  # CICIDS gives microseconds; we keep raw
```

The comment acknowledges the mismatch but stores raw microseconds in a field named `_ms`. This will give incorrect duration values in traces and dashboards.

**Recommended fix:** Either divide by 1000 (`int(row["Flow Duration"]) // 1000`) or rename the field to `flow_duration_us`.

---

#### M5. Test MCP tests fail without GCP credentials — should mock Firestore

**File:** `tests/test_main_mcp.py`

The 2 failing tests (`test_batch_with_phoenix_mcp`, `test_batch_phoenix_mcp_no_improve`) mock `run_triage`, `run_eval_phoenix`, and `run_improvement_phoenix` but don't mock `_persist_eval`, which creates a real Firestore client. This causes `DefaultCredentialsError` in CI.

**Recommended fix:** Add `@patch("augur.main._persist_eval")` to both tests.

---

### LOW — Cleanup / Minor Issues

#### L1. Dead file: `src/augur/agents/stub.py`

The file's own docstring says "Delete this file after Task 15 lands." It's unused — no imports reference it.

#### L2. Dashboard FP Rate section uses hardcoded placeholder data

**File:** `src/augur/dashboard/app.py:194-216`

```python
st.subheader("FP Rate by Severity (placeholder)")
fp_data = pd.DataFrame({
    "severity": ["Critical", "High", "Medium"],
    "threshold": [0.25, 0.50, 0.75],
    "current": [0.18, 0.42, 0.65],
})
```

This shows fake numbers. Either wire it to real eval data or remove it before the demo.

#### L3. `improvement.py` (legacy) meta-prompt doesn't include ground truth

**File:** `src/augur/improvement.py:44-47`

The legacy improvement agent only includes `agent_reasoning` in the trace summary, not the ground truth disposition/tactic. The MCP version (`improvement_phoenix.py`) correctly includes both predicted and actual values. This inconsistency means the legacy path gives the LLM less context to improve.

#### L4. Phoenix API key exposed in process arguments and request body

**Files:** `src/augur/phoenix_mcp_client.py:118-121`, `src/augur/main.py:107`

The MCP client passes `--apiKey` as a command-line argument, exposing it in `ps aux` and `/proc/*/cmdline`. Additionally, the `/batch` endpoint accepts `phoenix_api_key` as a plain request body field, meaning it appears in request logs.

**Recommended fix:** Pass the API key via environment variable to the subprocess. Remove `phoenix_api_key` from the request body and only read from env vars.

#### L5. `eval_phoenix.py` input parsing may overwrite tactic with alert input data

**File:** `src/augur/eval_phoenix.py:88-96`

When parsing trace spans, the code first looks at span attributes for `disposition`/`tactic`, then also tries to parse the `input` attribute's JSON. If the input happens to contain `disposition` or `attack_tactic` (from the alert), it overwrites the agent's *prediction* with the alert's *ground truth* input field:

```python
if "disposition" in inp:
    result["disposition"] = inp["disposition"]  # could be GT, not prediction
```

**Recommended fix:** Only extract `alert_id` from the input, not disposition/tactic.

#### L6. Redundant dict lookup in `_parse_traces`

**File:** `src/augur/phoenix_mcp_client.py:251`

```python
spans = item.get("spans", item.get("spans", []))
```

The same key `"spans"` is looked up twice — the inner default is never reached. Likely a copy-paste error where the second key should be a different field name (e.g., `"children"`).

#### L7. `NEEDS_INVESTIGATION` disposition never generated by synthetic alerts

**File:** `src/augur/data/synthetic.py:91-97`

The disposition distribution cycles through TP-Critical, TP-Critical, TP-Policy, FP, and BP — but never `NEEDS_INVESTIGATION`. This means the fifth disposition is never exercised in synthetic testing or eval.

#### L8. Misleading test name

**File:** `tests/data/test_enums.py:19`

`test_needs_investigation_exists` actually asserts `TRUE_POSITIVE_POLICY == "True Positive - Policy Violation"` — does not test `NEEDS_INVESTIGATION` at all.

#### L9. `run_triage` ignores the `agent` parameter entirely

**File:** `src/augur/agents/triage.py:91-152`

`run_triage(agent, alert)` accepts an `Agent` parameter but never references it. It creates its own `Client` and calls `generate_content` directly. The `build_triage_agent()` result is dead code. This is related to H2 — the agent's Firestore-loaded instruction is built but never used.

#### L10. Bare `except` in lifespan silences startup errors

**File:** `src/augur/main.py:72-73`

```python
except Exception:
    pass  # Don't fail startup if Firestore isn't reachable locally
```

This suppresses all errors during prompt seeding, including misconfigured credentials or corrupt prompt files, with no logging.

---

## 2. Feature Completeness Assessment

### CICIDS Data Pipeline — IMPLEMENTED

| Module | Status | Notes |
|--------|--------|-------|
| `mitre_mapping.py` | Complete | 7 in-scope labels, 8 OOS labels, KeyError for unknowns |
| `cicids_loader.py` | Complete | CSV parsing, column normalization, protocol mapping, OOS filtering |
| `splits.py` | Complete | Stratified by tactic, deterministic seeding |

All three modules have tests (5 tests for loader, 4 for mapping, 5 for splits). The CICIDS pipeline is fully functional.

### Phoenix MCP Integration — IMPLEMENTED

| Module | Status | Notes |
|--------|--------|-------|
| `phoenix_mcp_client.py` | Complete | Stdio MCP wrapper, trace/span queries, PhoenixTrace dataclass |
| `eval_phoenix.py` | Complete | Queries traces via MCP, matches to GT, computes metrics |
| `improvement_phoenix.py` | Complete | Fetches trace content via MCP, builds meta-prompt, rewrites |
| `/batch` endpoint MCP toggle | Complete | `use_phoenix_mcp: true` activates MCP path |

Both eval and improvement agents query real Phoenix traces via MCP. The wiring is complete.

### Remaining Build Steps (from README)

| # | Task | Status |
|---|------|--------|
| 10 | Deploy to Cloud Run | Not done — `cloudbuild.yaml` + `Dockerfile` exist, but no deployed service |
| 11 | Record demo video | Not done |
| 12 | Submission | Not done |

---

## 3. Test Coverage Analysis

### Current State: 57 passing, 2 failing (59 total)

| Test File | Tests | Verdict |
|-----------|-------|---------|
| `test_enums.py` | 6 | Good — validates cardinality and distinctness |
| `test_schema.py` | 4 | Adequate — covers construction, FP-no-tactic, non-FP-requires-tactic, confidence range |
| `test_synthetic.py` | 5 | Good — batch size, ID pairing, tactic coverage, disposition spread, lazy iteration |
| `test_mitre_mapping.py` | 4 parametrized groups | Good — in-scope, OOS, BENIGN, unknown label |
| `test_cicids_loader.py` | 5 | Good — paired output, OOS filtering, tactic assignment, validation |
| `test_splits.py` | 5 | Good — proportions, test fraction, disjoint IDs, determinism, stratification |
| `test_triage.py` | 4 | Adequate — JSON parsing (plain, markdown fence, generic fence), agent construction |
| `test_main.py` | 4 | Adequate — health, root, triage endpoint, triage with tactic |
| `test_main_mcp.py` | 2 (both fail) | Blocked — Firestore auth not mocked |
| `test_phoenix_mcp_client.py` | 5 | Good — API key validation, list_tools, get_traces parsing, agent_reasoning, model_input |

### Missing Edge Case Tests

#### Critical gaps (should add before demo):

1. **`test_main_mcp.py` — fix the 2 failing tests** by mocking `_persist_eval`. Without these, the MCP-backed `/batch` path has zero passing integration tests.

2. **`test_main.py` — no test for the legacy `/batch` endpoint**. The batch endpoint (legacy path) is untested. Add a test that mocks `run_triage` + `run_eval` + `run_improvement` and verifies the response shape.

3. **`eval.py` / `eval_phoenix.py` — no unit tests**. Neither eval agent has direct unit tests. They're only tested indirectly via the `/batch` endpoint tests (which fail). Add tests for:
   - Empty predictions list → should return EvalResult with empty per_tactic
   - All predictions correct → F1 = 1.0, no flagged tactic
   - All predictions wrong → F1 = 0.0, flagged tactic set
   - Mixed results with < 5 samples per tactic → should not flag (threshold guard)
   - Mismatched alert_ids (prediction has ID not in ground truth) → gracefully skipped

4. **`improvement.py` / `improvement_phoenix.py` — no unit tests**. Both improvement agents are untested. Add mock-Gemini tests for:
   - Gemini returns valid JSON → new prompt written to Firestore
   - Gemini returns empty response → RuntimeError raised
   - No current prompt in store → RuntimeError raised

5. **`prompt_store.py` — no unit tests**. The Firestore prompt store has zero tests. Add tests using a mocked Firestore client for:
   - `get_current_version` when doc doesn't exist → returns 0
   - `get_prompt` when version exists → returns prompt text
   - `write_version` → increments version number
   - `seed_initial_prompts` idempotency → doesn't overwrite existing

6. **`tracing.py` — no unit tests**. Add tests for:
   - `trace_span` with tracing disabled → yields `_NoOpSpan`
   - `_NoOpSpan.trace_id` is empty string
   - `trace_span` sets attributes on real spans (mock the tracer)

#### Nice-to-have edge case tests:

7. **CICIDS loader — malformed CSV** (missing columns, NaN timestamps, non-numeric protocol)
8. **Splits — empty input** (`split_pairs([])` should return three empty lists)
9. **Splits — single item** (should go to one split, others empty)
10. **Synthetic — n=0** (`generate_alert_batch(n=0)` should return empty lists)
11. **Schema — `TriageOutput` with `Needs Investigation` disposition** (requires tactic per validator)
12. **MCP client — `get_traces` with empty response** (should return empty list)
13. **MCP client — `_call_tool` with non-JSON response** (should return `{"raw": ...}`)
14. **Triage agent — `_parse_agent_response` with empty string** (should raise `ValueError`)
15. **Triage agent — `_parse_agent_response` with prose + embedded JSON** (regex fallback)

---

## 4. Recommended Fix Priority

### Must-fix before demo (HIGH):

| # | Issue | Effort | Impact |
|---|-------|--------|--------|
| H2 | Triage agent ignores Firestore prompt | 5 min | Demo-breaking: self-improvement loop doesn't actually work |
| M5 | Fix 2 failing MCP tests (mock `_persist_eval`) | 5 min | CI credibility |
| H1 | Precision/recall always identical | 30 min | Judge credibility — confusing metrics |

### Should-fix before submission (MEDIUM):

| # | Issue | Effort | Impact |
|---|-------|--------|--------|
| L4 | `eval_phoenix.py` input parsing overwrites prediction | 10 min | Silently wrong eval metrics |
| M4 | Flow Duration units mismatch | 5 min | Data accuracy |
| M1 | `_persist_eval` creates new Firestore client each call | 5 min | Performance |
| M2 | MCP session not closed | 5 min | Resource leak |
| Gap 1 | Add legacy `/batch` endpoint test | 15 min | Test coverage |
| Gap 2 | Add `eval.py` unit tests | 20 min | Test coverage |
| Gap 3 | Add `prompt_store.py` unit tests | 20 min | Test coverage |

### Nice-to-have (LOW):

| # | Issue | Effort | Impact |
|---|-------|--------|--------|
| L1 | Delete `stub.py` | 1 min | Cleanup |
| L2 | Remove or wire dashboard placeholder | 15 min | Demo polish |
| H3 | Transaction for version bump | 15 min | Correctness under concurrency |
| L3 | Legacy improvement meta-prompt parity | 10 min | Consistency |
| M3 | Pin MCP SDK version | 5 min | Stability |

---

## 5. Steps to Implement Missing Test Coverage

### Step 1: Fix the 2 failing MCP tests (5 min)

In `tests/test_main_mcp.py`, add `@patch("augur.main._persist_eval")` to both test functions so they don't attempt to create a real Firestore client.

### Step 2: Fix H2 — triage agent uses Firestore prompt (5 min)

In `src/augur/agents/triage.py:run_triage()`, replace `_PROMPT_TEXT` with `agent.instruction` in the Gemini call.

### Step 3: Fix L4 — eval_phoenix input parsing (10 min)

In `src/augur/eval_phoenix.py:88-96`, only extract `alert_id` from the input JSON, not `disposition` or `attack_tactic`.

### Step 4: Add eval agent unit tests (20 min)

Create `tests/test_eval.py` with tests for `run_eval()`:
- Empty predictions
- All correct (F1=1.0)
- All wrong (F1=0.0)
- Below threshold (< 5 samples)
- Mismatched alert IDs

### Step 5: Add eval_phoenix unit tests (20 min)

Create `tests/test_eval_phoenix.py` with mocked MCP client tests for `run_eval_phoenix()`.

### Step 6: Add prompt_store unit tests (20 min)

Create `tests/test_prompt_store.py` with mocked Firestore tests.

### Step 7: Add improvement agent unit tests (20 min)

Create `tests/test_improvement.py` with mocked Gemini + Firestore tests.

### Step 8: Add legacy /batch endpoint test (15 min)

In `tests/test_main.py`, add a test for `POST /batch` with mocked dependencies.

### Step 9: Add edge case tests for existing modules (30 min)

Add the "nice-to-have" tests listed in section 3 above.

---

## 6. Summary

| Category | Count |
|----------|-------|
| High-severity issues | 3 |
| Medium-severity issues | 5 |
| Low-severity issues | 10 |
| Passing tests | 57 |
| Failing tests | 2 (fixable with 1-line mock) |
| Untested modules | 6 (eval, eval_phoenix, improvement, improvement_phoenix, prompt_store, tracing) |
| Missing edge case tests | ~15 |
| Build steps remaining | 3 (deploy, demo video, submission) |

**Bottom line:** The CICIDS pipeline and Phoenix MCP integration are both implemented and wired in. The most critical fix is **H2** (triage agent ignoring Firestore prompts) — without it, the self-improvement demo narrative doesn't work. After that, fixing the 2 failing tests and adding eval/improvement unit tests would give solid coverage for submission.
