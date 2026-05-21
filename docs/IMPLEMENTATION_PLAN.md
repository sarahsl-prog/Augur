# Augur Detailed Implementation Plan

> Branch: `implement-mcp-tests`
> Created: 2026-05-21
> Goal: Fix broken tests, wire Phoenix MCP into eval/improvement loop, and get everything running on a clean branch before merging to main.

---

## Task 1: Fix Pytest Import Issue

**Goal:** `pytest` runs without `ModuleNotFoundError: augur`

**Background:** Tests currently fail collection because `augur` is a `src/` package (not top-level). The test runner can't find the package. Typical fixes:

1. Install the package in editable mode: `uv pip install -e .`
2. Or add `src/` to `PYTHONPATH`
3. Or configure pytest to use `pythonpath = ["src"]` in `pyproject.toml`

**Steps:**
- [ ] Diagnose why `uv run pytest` can't import `augur`
- [ ] Verify Python path / installation state
- [ ] Fix via editable install or pyproject.toml config
- [ ] Verify all 6 existing tests pass (or at least collect and run)
- [ ] Commit

**Verification:** `uv run pytest --collect-only` succeeds and lists `test_enums.py`, `test_schema.py`, `test_synthetic.py`, `test_triage.py`, `test_main.py`

---

## Task 2: Wire Phoenix MCP into Eval Agent (eval.py)

**Goal:** The eval agent pulls *actual traces* from Phoenix Cloud via the `@arizeai/phoenix-mcp` server, uses trace content to compute per-tactic precision/recall, and identifies true failure clusters.

**What works now:** `eval.py` compares prediction arrays inline. It has no MCP, no Phoenix trace queries, no LLM-as-a-Judge. It is essentially a scoring function.

**What judges expect:** The eval agent demonstrates "meaningful tracing + MCP use." It should:
1. Connect to the Phoenix MCP server (`@arizeai/phoenix-mcp` via npx)
2. Query traces for a given batch/eval window
3. Use trace data (actual agent reasoning chains) to compute precision/recall
4. Optionally use LLM-as-a-Judge for ambiguous cases

**Implementation approach:**

Since `npx @arizeai/phoenix-mcp` spawns an MCP server as a subprocess, we have two options:

**Option A: Use the Python MCP SDK** (recommended for integration into Python eval)
- Add `mcp` to pyproject.toml dependencies
- Spawn `@arizeai/phoenix-mcp` as a subprocess via Python `mcp.Client`
- The MCP server exposes tools like `get_traces`, `search_traces`, etc.
- Query traces by `eval_run_id` or time window
- Parse trace content to extract agent reasoning

**Option B: Pre-aggregate traces + Phoenix SDK**
- Instead of spawning MCP, use Phoenix's Python SDK (`arize-phoenix-otel` already installed)
- Query traces via Phoenix REST API directly
- Less MCP-specific but still "meaningful tracing"

**Recommendation:** Option A because the Arize track explicitly scores MCP integration. The MCP server is already baked into the Dockerfile (Node.js).

Steps:
1. [ ] Add `mcp>=1.0` and `phoenix-evaluations` (if exists) or `arize-phoenix` to dependencies
2. [ ] Implement `augur/tracing.py` additions: Phoenix MCP client wrapper
3. [ ] Add `augur/eval_mcp.py` (or refactor `eval.py`): eval agent that queries Phoenix traces via MCP
4. [ ] Retain backward compatibility: keep `eval.py` simple version but add optional MCP mode
5. [ ] Add test: mock MCP client, verify trace query params
6. [ ] Update `main.py` `/batch` endpoint to pass eval_run_id so traces are queryable
7. [ ] Ensure trace tags include eval_run_id for correlation

**Verification:** A smoke test runs: spawn batch, get eval_run_id, query Phoenix via MCP for traces matching that run, compute scores, verify non-zero traces returned.

---

## Task 3: Wire Phoenix MCP into Improvement Agent (improvement.py)

**Goal:** The improvement agent queries Phoenix MCP for failed traces of a specific tactic, extracts agent reasoning chains, and uses that as context for prompt rewrites.

**What works now:** The improvement agent gets failed traces as dicts passed from the `/batch` endpoint. It never looks at actual Phoenix traces.

**What changes:** The agent should:
1. Receive `eval_run_id` and `tactic`
2. Query Phoenix MCP: `get_traces(project_name="augur", eval_run_id=...)`
3. Extract trace content — specifically agent reasoning and model decisions
4. Feed failed trace examples into the meta-prompt for rewriting
5. Store the new version in Firestore

**Implementation approach:**
1. [ ] Refactor `improvement.py` to accept Phoenix MCP client (or trace data fetched via MCP)
2. [ ] Define a trace-to-example parser (extract alert, reasoning, predicted vs actual from Phoenix trace)
3. [ ] Update `run_improvement()` to query Phoenix if `failed_traces` list is empty (MCP mode)
4. [ ] Ensure the meta-prompt can consume Phoenix trace format
5. [ ] Add test with mocked MCP trace data
6. [ ] Verify integration end-to-end: `/batch` → eval → improvement → new prompt version in Firestore

**Verification:** Run `/batch` with `improve=true`, verify `flagged_tactic` gets improved, check Firestore for new prompt version.

---

## Task 4: Add CICIDS Data Pipeline (Missing Step 4)

**Goal:** Replace synthetic-only data with real CICIDS2017/2018 preprocessed alerts + ground truth.

**What's missing per build order:**
- `mitre_mapping.py` — CICIDS attack label → Augur tactic/technique/disposition
- `cicids_loader.py` — CSV → Alert + GroundTruth pairs
- `splits.py` — train/dev/test split logic

**Caveat:** CICIDS2017 is ~100GB. We should:
1. Download a small subset (Brute Force + Lateral Movement only ~2GB)
2. Preprocess offline to JSONL
3. Gitignore the raw CSVs, check in a small sample fixture
4. Include preprocessing script that user runs manually

**Steps:**
1. [ ] Create `src/augur/data/mitre_mapping.py` — mapping table from CICIDS labels
2. [ ] Create `src/augur/data/cicids_loader.py` — pandas read_csv → Alert/GT
3. [ ] Create `src/augur/data/splits.py` — stratified train/dev/test split
4. [ ] Add `scripts/download_cicids_subset.py` — download + preprocess subset
5. [ ] Add small fixture CSV (100 rows) for tests
6. [ ] Add tests for loader, mapping, splits
7. [ ] Update `/batch` to optionally load from preprocessed JSONL instead of synthetic

**Verification:** Standalone script runs and produces valid Alert+GT JSONL. Tests pass.

---

## Task 5: Record Demo Video (~3 minutes)

**Goal:** Produce the hackathon submission demo video.

**Story arc per README:**
1. "Monday morning" — agent starts with baseline prompts
2. Run 50 labeled alerts (synthetic or CICIDS subset)
3. Show struggling on Lateral Movement → Phoenix traces show reasoning
4. Eval fires: identifies Lateral Movement failure cluster
5. Improvement agent rewrites prompt using failed traces
6. Re-run same batch: improved precision/recall numbers
7. Phoenix dashboard: trace diff v1 vs v2, same alert

**Technical prep needed:**
- Need working MCP integration (Task 2 + 3) for meaningful trace diffs
- Need CICIDS data or believable synthetic labels mapped to tactics
- Dashboard must show before/after clearly

**Steps:**
1. [ ] Verify Cloud Run deployment is live and accessible
2. [ ] Prepare a batch of alerts with known failure patterns
3. [ ] Run first pass (baseline) and capture results
4. [ ] Trigger improvement loop and capture results
5. [ ] Run second pass with updated prompt and capture results
6. [ ] Capture Phoenix dashboard screenshots of trace diff
7. [ ] Screen record the narrative with OBS / ShareX
8. [ ] Edit to ~3 minutes (hard Devpost limit)
9. [ ] Export and upload

---

## Files to Touch

| File | Task | Notes |
|------|------|-------|
| `pyproject.toml` | 1, 2, 3 | Add `mcp`, maybe `arize-phoenix`, fix pytest config |
| `tests/conftest.py` | 1 | Ensure import path is correct |
| `src/augur/eval.py` | 2 | Refactor or add MCP-aware eval |
| `src/augur/eval_mcp.py` | 2 | New: MCP-based eval agent |
| `src/augur/improvement.py` | 3 | Add trace fetching from Phoenix |
| `src/augur/tracing.py` | 2, 3 | Add Phoenix MCP client wrapper |
| `src/augur/main.py` | 2, 3 | Update /batch endpoint for MCP mode |
| `src/augur/data/mitre_mapping.py` | 4 | New |
| `src/augur/data/cicids_loader.py` | 4 | New |
| `src/augur/data/splits.py` | 4 | New |
| `tests/test_eval_mcp.py` | 2 | New |
| `tests/test_improvement.py` | 3 | New or update existing |
| `tests/data/test_cicids_loader.py` | 4 | New |
| `scripts/download_cicids_subset.py` | 4 | New |
| `tests/conftest.py` | 2, 3 | Mock MCP client fixture |

---

## Branch Strategy

- Work on `implement-mcp-tests` branch
- Each task gets its own commit(s)
- After Task 1+2+3 pass tests, open PR for review
- Task 4 (CICIDS) and Task 5 (video) can happen in parallel or after MCP is merged

---

## Risks & Blockers

1. **Phoenix MCP server availability** — requires `npx @arizeai/phoenix-mcp` to be running. In CI/tests we mock it.
2. **GCP credentials** — eval and improvement require GCP auth for Firestore + Vertex. Locally you have ADC; CI would need service account.
3. **CICIDS download size** — 100GB dataset, must work with subset only.
4. **Demo data believability** — synthetic alerts need enough realism for the video narrative.

