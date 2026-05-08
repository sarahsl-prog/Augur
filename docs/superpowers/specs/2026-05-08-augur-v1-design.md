# Augur — Project Specification (v1 Draft)

> **Status:** First draft for user review. Locks four open decisions; tracks three remaining.
> **Source:** README.md (project brief), CLAUDE.md (invariants index), and the `.claude/skills/augur*` harness (taxonomy, ADK patterns, Phoenix loop).
> **Date:** 2026-05-08
> **Submission deadline:** 2026-06-11, 14:00 PDT (~5 weeks)

---

## Context

The project brief (`README.md`) defines architecture, tech stack, taxonomy, and a 12-step build order, but leaves four explicit open decisions plus several ambiguities that block implementation planning. Without a locked spec, future build sessions risk: drifting on component interfaces, inventing schema variations, choosing tech that violates Arize-track requirements, or over-scoping the demo dashboard.

This spec resolves the four high-leverage open decisions (per user input 2026-05-08), formalizes each component's interface and acceptance criteria, lists deferred work explicitly, and surfaces the three open decisions that remain. It is the contract that the implementation plan will be built against.

**v1 Goal:** A working, demo-ready, deployed Augur instance that performs the closed self-improvement loop visibly on real CICIDS data, with measurable per-tactic precision/recall improvement after one prompt rewrite, satisfying all Arize-track submission criteria.

---

## Hard Constraints (Recap, Non-Negotiable)

These come from the `augur` orchestrator skill and are repeated here as the spec's external contract. Any v1 component that violates one is rejected.

1. Agent runtime: **Google ADK** only. No Visual Agent Builder.
2. LLM: **Gemini via Vertex AI**. No `openai`, `anthropic`, `transformers` (inference), or LangChain wrappers in the runtime path.
3. Tracing: **Phoenix Cloud + OpenInference** (`openinference-instrumentation-google-adk`). No custom OTel exporters.
4. Self-improvement loop: agent must query its **own traces** via `@arizeai/phoenix-mcp` (the judging hook).
5. Disposition enum: exactly 5 values. **False Positive ≠ Benign Positive.**
6. MITRE tactic scope: exactly 6 tactics (Initial Access, Credential Access, Lateral Movement, Exfiltration, Command & Control, Defense Evasion).
7. Three runtime agents stay separate: Triage / Eval / Improvement.
8. Prompts versioned **per tactic** in Firestore.

---

## Locked Decisions (User-Confirmed 2026-05-08)

| # | Decision | Locked Choice | Implication |
|---|---|---|---|
| D1 | CICIDS attack scope | **All 6 tactics** with real labeled data | Preprocessing covers Brute Force, LM, IA, Cred Access, Exfil, C2, Defense Evasion. Higher upfront data work; strongest credibility story. |
| D2 | Eval trigger (README open #2) | **Alert-count N=25** | Eval agent fires after each 25-alert batch. Deterministic for demo recording. |
| D3 | Demo dashboard | **Streamlit read-only (~1 day)** | Python-native; pulls from Phoenix + Firestore. No live state machinery. |
| D4 | Demo input source | **CICIDS held-out subset only** | Demo runs on real labeled data. Synthetic generator stays in scope for dev/test, NOT demo input. |

### Default-decided (matching README recommendations + harness)

| # | Decision | Default | Source |
|---|---|---|---|
| D5 | Prompt store backend (README open #1) | Firestore | README recommendation; locked in `augur-adk-patterns` |
| D6 | Phoenix hosting (README open #3) | Phoenix Cloud free tier | README recommendation |
| D7 | Ground-truth source (README open #4) | Pre-labeled CICIDS subset | README recommendation; consistent with D4 |
| D8 | Three-agent deploy topology | Co-hosted in one Cloud Run service | `augur-adk-patterns` recommendation; minimizes cold-start during demo |
| D9 | Concurrency model | Synchronous request/response per alert | Simplest; alert-count trigger requires it |

---

## Architecture

```
                ┌─────────────────────────────────────────────┐
                │         Cloud Run service (single)          │
                │                                              │
   alert ─────► │  ┌────────────┐                              │
                │  │  Triage    │── Phoenix trace (auto)       │
                │  │  Agent     │── triage report (return)     │
                │  └─────┬──────┘                              │
                │        │ reads current prompt                │
                │        ▼                                     │
                │  ┌────────────┐                              │
                │  │ Firestore  │  prompts/{tactic}/versions/  │
                │  │   store    │                              │
                │  └────────────┘                              │
                │        ▲                                     │
                │  every │ 25 alerts                           │
                │  ┌────────────┐                              │
                │  │   Eval     │── reads traces via MCP       │
                │  │  Agent     │── writes eval_result doc     │
                │  └─────┬──────┘                              │
                │        │ flags tactic                        │
                │        ▼                                     │
                │  ┌────────────┐                              │
                │  │ Improvement│── reads failed traces via MCP│
                │  │   Agent    │── writes new prompt version  │
                │  └────────────┘                              │
                └─────────────────────────────────────────────┘
                           ▲                       ▲
              traces ──────┘                       │
              ┌─────────────────┐                  │
              │ Phoenix Cloud   │◄─── MCP server (npx subprocess)
              └─────────────────┘
                                                    
              ┌─────────────────┐
              │ Streamlit       │◄── reads Phoenix + Firestore
              │ Dashboard       │
              └─────────────────┘
```

**Single-process invariant:** all three ADK agents run in one Python process inside one Cloud Run container. Phoenix MCP runs as a subprocess (npx) inside the same container OR as a sidecar — to be locked at build step 7.

---

## Component Specifications

### C1. Data Pipeline

**Purpose:** Convert CICIDS2017/2018 raw flow records and synthetic generator output into canonical alerts with ground-truth labels.

**Inputs:**
- CICIDS2017/2018 CSV files (raw flow records with `Label` column)
- Synthetic generator config (tactic distribution, edge-case injection rules)

**Outputs:**
- Three JSONL files: `alerts_train.jsonl`, `alerts_dev.jsonl`, `alerts_test.jsonl`
- Each line conforms to the **Alert + Ground Truth Schema** (see Data Model below)
- Test set held out — never used for prompt tuning

**Subcomponents:**
- **CICIDS preprocessor** (`src/data/cicids_loader.py`): maps CICIDS attack labels → Augur tactic + technique + disposition. The mapping table is canonical and lives in `augur-mitre-taxonomy` skill.
- **Synthetic generator** (`src/data/synthetic.py`): produces alerts in the same schema. Used for dev/test only, NOT demo input. Allows controlled edge-case construction.
- **Schema validator** (`src/data/schema.py`): pydantic model that validates every alert produced.

**Acceptance:**
- All 6 MITRE tactics have ≥50 ground-truth-labeled alerts in the combined corpus.
- Test set is held out from the moment data lands; no prompt or model touches it before demo eval.
- Synthetic generator passes the same schema validator.
- Disposition distribution: at least 1 alert per disposition value per tactic where semantically possible (e.g., FP/BP rare for Initial Access; that's fine).

### C2. Triage Agent

**Purpose:** Classify a single alert into the structured triage report.

**Inputs:** One alert (Alert Schema, ground truth stripped).

**Outputs:** Structured triage report (Triage Output Schema) with `trace_id` populated from the active Phoenix span.

**Behavior:**
1. Read the alert payload.
2. Call **router** (Gemini) with a small system prompt: classify the alert into one of the 6 tactics (or "Not Applicable" if the agent's first read suggests False Positive). Returns just `{tactic: ..., preliminary_disposition_hint: ...}`.
3. Read the active prompt version for that tactic from Firestore.
4. Call **classifier** (Gemini) with the per-tactic system prompt + alert. Returns the full triage report.
5. Validate output against schema; fill in `trace_id` from the OpenTelemetry current span.
6. Return triage report. Add `augur.alert_id` and `augur.ground_truth_disposition` as span attributes (the latter only set by the eval-side pairing — see C3).

**Wrapped as ADK tools:**
- `route_tactic(alert)` → tactic name
- `classify_with_prompt(alert, tactic, prompt_version)` → triage report

**Acceptance:**
- Given any valid alert, returns a schema-valid triage report.
- Trace appears in Phoenix with both router and classifier as nested spans under one root span.
- `trace_id` in the output matches the Phoenix UI's trace ID for the same invocation.
- p95 latency under 5 seconds (Gemini 1.5 Pro × 2 calls + Firestore read).

**Open decision (tracked):** the **router + per-tactic** strategy above is the recommended default. Alternatives — a single unified prompt, or running all 6 tactic prompts in parallel and taking max-confidence — are tracked in the Open Decisions section. Spec assumes router + per-tactic unless the user picks differently.

### C3. Eval Agent

**Purpose:** Score the most recent batch of triage decisions, identify the worst-performing tactic for the improvement agent.

**Trigger:** Alert-count counter reaches 25. Counter is per-process; resets after eval runs.

**Inputs:**
- Phoenix project name (`augur`)
- The 25 trace IDs of the most recent triage runs
- Their corresponding ground-truth labels (paired via `augur.alert_id` span attribute)

**Outputs:** Eval Result document, written to Firestore at `eval_results/{eval_run_id}/`.

**Behavior:**
1. Pull the 25 most recent traces via Phoenix MCP (`list_recent_traces` or equivalent).
2. For each trace: extract predicted disposition + tactic + technique. Pair with ground truth via `augur.alert_id`.
3. Compute per-tactic precision, recall, F1 (macro across dispositions per tactic). Compute per-disposition rates (especially FP rate by severity for the demo dashboard).
4. Identify failure clusters: tactics with F1 below threshold (recommend 0.6) AND n ≥ 5 in the batch.
5. Pick `flagged_tactic_for_improvement` = lowest F1 tactic meeting the threshold + n criteria. If none qualify, set to `null` (no rewrite this round).
6. Write Eval Result document.

**Acceptance:**
- Given a batch with at least one mis-classified tactic, produces an Eval Result with that tactic flagged.
- Numbers reproduce on the same trace set + ground truth (deterministic).
- Document is queryable by the dashboard and the improvement agent.

### C4. Improvement Agent

**Purpose:** Rewrite the prompt for the flagged tactic using its own failed traces as context.

**Trigger:** Eval Agent emits an Eval Result with `flagged_tactic_for_improvement != null`.

**Inputs:**
- Eval Result document (provides flagged tactic + trace IDs of failures)
- Current prompt for the flagged tactic (read from Firestore)
- 5–10 failed traces (full reasoning + alert + ground truth) pulled via Phoenix MCP

**Outputs:** New prompt version written to Firestore (per the schema in `augur-adk-patterns`).

**Behavior:**
1. Read failed traces via MCP. Extract: alert, agent's reasoning chain, agent's predicted disposition, ground-truth disposition.
2. Read current prompt version for the flagged tactic from Firestore.
3. Construct a meta-prompt for Gemini: "Given this current prompt for tactic X, these N failure cases showing what the agent got wrong, and the ground truth — produce a revised prompt that would have classified these correctly."
4. Call Gemini. Receive revised prompt text.
5. Write new version to Firestore as `versions/{N+1}` with `created_by: improvement_agent`, `parent_version: N`, `triggering_eval_id: eval_run_id`.
6. Update the parent doc's `current_version` pointer to N+1 (atomic).
7. Log an "improvement event" record for the dashboard's prompt-history view.

**Acceptance:**
- New prompt version exists in Firestore after each successful run.
- `current_version` pointer updated atomically (no torn reads).
- Audit chain (`parent_version` + `triggering_eval_id`) lets a human reconstruct why each rewrite happened.

**v1 stretch (deferred to v1.5 if time-pressed):** regression guard — re-evaluate new prompt on the *prior* batch before promotion; abandon if F1 drops.

### C5. Demo Dashboard (Streamlit)

**Purpose:** Visual surface for the demo video. Read-only.

**Tech:** Streamlit, deployed as a separate Cloud Run service.

**Reads:**
- Firestore: prompt versions per tactic; eval result history
- Phoenix Cloud: trace details for side-by-side trace diff (via REST API or MCP)

**Pages:**
1. **Overview** — current per-tactic precision/recall (latest eval), with delta vs. prior eval. Industry-benchmark FP-rate-by-severity comparison (Critical < 25%, High < 50%, Medium < 75%).
2. **Prompt history** — for each of the 6 tactics: version timeline with `created_by`, `triggering_eval_id`, and the prompt diff between consecutive versions.
3. **Trace diff** — pick an alert ID; render the agent's reasoning under v1 prompt vs. v2 prompt side-by-side. This is THE money shot of the demo video.
4. **Eval log** — chronological list of eval runs; clickable to drill into per-tactic breakdown.

**Acceptance:**
- All four pages render without errors when the loop has run at least once.
- Page 3 (trace diff) handles the case where v2 hasn't been rewritten yet for that tactic (graceful "single version" view).
- Streamlit deploys to Cloud Run successfully via `gcloud run deploy`.

### C6. Hosting & Deployment

**Production target:** Two Cloud Run services in one GCP project:
- `augur-runtime` — the three ADK agents + Phoenix MCP subprocess. Public URL for the alert-ingest endpoint.
- `augur-dashboard` — Streamlit. Public URL for the demo video.

**Auth:**
- Local dev: `gcloud auth application-default login`
- Cloud Run: bound service account with `roles/aiplatform.user` (Vertex), `roles/datastore.user` (Firestore), `roles/run.invoker` (cross-service if needed). No JSON keys.

**Secrets:**
- `PHOENIX_API_KEY` — Cloud Run env var (use `--set-secrets` ideally to pull from Secret Manager).
- `GCP_PROJECT` — Cloud Run env var.

**Deploy commands** documented in `README.md` once step 11 lands.

### C7. Submission Deliverables

| Item | Owner | Status criteria |
|------|-------|-----------------|
| Hosted project URL | Cloud Run dashboard | Public URL accessible without auth |
| Public GitHub repo | https://github.com/sarahsl-prog/Augur | Open-source license file present (MIT recommended); README has setup + deploy instructions |
| ~3 minute demo video | User records | Hits all 5 metrics from "Demo North Star" section |
| Devpost submission | User submits | Arize track selected; form complete |

---

## Data Model

### Alert Schema

```python
{
  "alert_id": "uuid",
  "timestamp": "ISO-8601",
  "source": "cicids2017" | "cicids2018" | "synthetic",
  "raw_signals": {
    "src_ip": "...", "dst_ip": "...", "dst_port": 0,
    "protocol": "TCP" | "UDP" | "ICMP",
    "flow_duration_ms": 0,
    "packet_count": 0,
    "byte_count": 0,
    "flags": ["SYN", "ACK", ...],
    /* additional CICIDS-derived features */
  },
  "detection_rule_fired": "string — name of the synthetic detection rule that matched",
  "context": {
    "host_role": "workstation" | "domain_controller" | "server" | "unknown",
    "user_account": "string | null",
    "is_business_hours": true | false
  }
}
```

### Ground Truth Schema (separate file alongside alerts in dev/test sets)

```python
{
  "alert_id": "uuid",  // matches alert
  "ground_truth": {
    "disposition": "True Positive - Critical" | ... ,  // 5-disposition enum
    "attack_tactic": "Initial Access" | ... | "Not Applicable",  // 6 tactics or N/A for FP
    "attack_technique": "T1110.001" | null
  },
  "source": "cicids2017" | "synthetic"
}
```

The Triage Agent receives only the alert object — never the ground truth.

### Triage Output Schema

(from `augur-mitre-taxonomy`, repeated for completeness)

```python
{
  "alert_id": "uuid",
  "disposition": "...",  // one of 5
  "attack_tactic": "...",  // one of 6 or "Not Applicable"
  "attack_technique": "T1021.002",
  "attack_technique_name": "SMB/Windows Admin Shares",
  "confidence": 0.87,
  "severity": "Low" | "Medium" | "High" | "Critical",
  "recommended_action": "string",
  "reasoning": "string",
  "trace_id": "phoenix_trace_id"
}
```

### Prompt Store Schema

(from `augur-adk-patterns`, locked)

```
firestore/prompts/{triage-{tactic-kebab-case}}/
  current_version: int
  tactic: string
  created_at: timestamp
  versions/{version_int}/
    system_prompt: string
    few_shot_negative: list  // empty in v1; populated in v1.5
    created_at: timestamp
    created_by: "human" | "improvement_agent"
    parent_version: int | null
    triggering_eval_id: string | null
```

Six top-level documents, one per tactic.

### Eval Result Schema

```python
{
  "eval_run_id": "uuid",
  "timestamp": "ISO-8601",
  "batch_size": 25,
  "trace_ids": ["..."],
  "per_tactic": {
    "Lateral Movement": {
      "precision": 0.42, "recall": 0.71, "f1": 0.53, "n": 8,
      "per_disposition": { "True Positive - Critical": {...}, ... }
    },
    /* ... one entry per tactic with n > 0 in this batch ... */
  },
  "fp_rate_by_severity": {
    "Critical": 0.18, "High": 0.42, "Medium": 0.65
  },
  "failure_clusters": [
    { "tactic": "Lateral Movement", "n_failures": 5, "trace_ids": [...] }
  ],
  "flagged_tactic_for_improvement": "Lateral Movement" | null
}
```

---

## Build Sequence (Mapping README's 12 Steps)

| Step | Components | Outcome | Skill that drives this step |
|------|------------|---------|------------------------------|
| 1 | C6 | ADK skeleton; empty triage agent deployable to Cloud Run | augur-adk-patterns |
| 2 | C6 | OpenInference wired; traces flow on basic invocation | augur-phoenix-loop |
| 3 | C1 | Alert schema + synthetic generator | augur-mitre-taxonomy |
| 4 | C1 | CICIDS preprocessing for all 6 tactics (D1) | augur-mitre-taxonomy |
| 5 | C2 | Triage agent with one hardcoded prompt (router strategy not yet — single prompt only); E2E traces visible per alert | augur-adk-patterns + augur-mitre-taxonomy |
| 6 | C2 | Firestore prompt store; triage agent now reads from store; **router + per-tactic** strategy lands here | augur-adk-patterns |
| 7 | C3 | Eval agent + MCP wiring | augur-phoenix-loop |
| 8 | C4 | Improvement agent with prompt-rewrite logic | augur-phoenix-loop |
| 9 | C2+C3+C4 | All three agents wired into the closed loop end-to-end | augur orchestrator |
| 10 | C5 | Streamlit dashboard pages 1-4 | (no harness skill — direct work) |
| 11 | C6 | Cloud Run deploy of both services; secrets via Secret Manager | augur-adk-patterns |
| 12 | C7 | Demo video record + Devpost submit | (manual, user-owned) |

**Sequencing rule from `augur` orchestrator:** Step 6 cannot start until step 5 produces traces in Phoenix.

---

## Risk Register

| ID | Risk | Severity | Mitigation |
|----|------|----------|------------|
| R1 | Demo arc unreliable (D4 trade-off) | **HIGH** | During development (around step 9), run eval against multiple held-out CICIDS batches; pick a batch that reliably produces a Lateral Movement failure cluster. Tune the v1 hardcoded prompt to be deliberately weak on LM (still legitimate — the agent learning from its own data is the story). Keep synthetic generator as a *recorded* fallback batch in case CICIDS doesn't cooperate the day of recording. |
| R2 | Phoenix free-tier trace volume limits | Medium | Rate-limit dev runs. Use small batch sizes (10-25 alerts) during development. Save aggressive trace usage for late-stage demo rehearsals. |
| R3 | ADK API instability | Medium | Pin ADK and openinference-instrumentation-google-adk versions in `pyproject.toml` / `uv.lock`. Don't update mid-sprint. |
| R4 | Phoenix MCP server runtime issues | Medium | Multistage Dockerfile (Python + Node) OR run MCP as a sidecar Cloud Run service. Decide at step 7. Document the chosen approach in `augur-phoenix-loop` skill. |
| R5 | Improvement agent regression (new prompt worse than old) | Medium | v1.5 — add regression guard. v1 — accept the risk; demo recording is a controlled environment. |
| R6 | Time pressure (5 weeks, solo) | **HIGH** | Strict scope discipline (see Out of Scope). Defer few-shot store. One dashboard. No multi-region deploy. |
| R7 | Disposition / tactic drift between codebase and skills | Low (mitigated) | `augur-build-verifier` sub-agent sweeps invariants pre-submission. Run before final commit. |

---

## Out of Scope for v1

- **Few-shot example store** (README priority #2): defer to v1.5 if time permits. v1 ships with `few_shot_negative: []` in every prompt version.
- **Live human feedback endpoint** (README open #4): declined for v1 per D7.
- **Improvement agent regression guard** (R5 mitigation): defer to v1.5.
- **Multi-region Cloud Run deploy**: single region (us-central1).
- **Custom Phoenix self-hosting** (README open #3): use Cloud free tier per D6.
- **Reconnaissance / DDoS / DoS / PortScan tactics**: outside the 6-tactic MITRE scope.
- **Authentication on the demo URL**: `--allow-unauthenticated` is fine for hackathon submission.
- **Cost monitoring / budget alerting**: not before submission. Manual budget watching only.
- **Slack / email alerting on improvement events**: dashboard is the only notification surface.

---

## Open Decisions Still Pending

| ID | Decision | Default | Why deferred |
|---|---|---|---|
| O1 | **Triage agent's tactic-routing strategy** (C2 step 2-3) | **Router + per-tactic** (two LLM calls per alert): clean semantics, matches per-tactic versioning. | Alternatives (unified prompt; parallel run all 6) are valid but change the improvement-agent's input/output. Decide before step 6. |
| O2 | **Phoenix MCP runtime topology** | TBD between (a) subprocess in same Cloud Run container, multi-stage Dockerfile, or (b) MCP as separate Cloud Run sidecar service. | Decide at step 7 once we test the npx invocation pattern. |
| O3 | **Few-shot store storage if added in v1.5** | TBD between (a) embedded in Firestore version doc (`few_shot_negative` field), or (b) separate vector store (e.g., Vertex AI Matching Engine). | Not blocking v1. Decide if v1.5 enters scope. |

These should be locked before the implementation plan reaches the corresponding build step.

---

## Verification (How We Know v1 Works)

This is what an end-to-end smoke test against deployed v1 looks like — the demo flow is the verification:

1. **Pre-flight:** Trigger the verifier sub-agent (`augur-build-verifier`). All hard invariants must PASS or be N/A.
2. **Data ready:** `alerts_test.jsonl` exists with ≥50 alerts per tactic and matching ground truth.
3. **Baseline run:** POST 25 test alerts to the Cloud Run runtime URL. Each call returns a schema-valid triage report. All 25 traces appear in Phoenix Cloud under project `augur`.
4. **Eval fires:** After the 25th alert, the eval agent runs automatically. An Eval Result document appears in Firestore. `flagged_tactic_for_improvement` is set (preferably "Lateral Movement" — see R1 mitigation).
5. **Improvement fires:** Improvement Agent runs. A new version of the flagged tactic's prompt appears in Firestore. `current_version` pointer is updated.
6. **Re-run:** POST the same 25 alerts again. New triage outputs reflect the updated prompt. Phoenix shows new traces.
7. **Eval re-fires:** Second Eval Result shows F1 improvement on the flagged tactic.
8. **Dashboard:** All four Streamlit pages render. Page 3 (trace diff) shows v1 vs. v2 outputs side-by-side on at least one alert.
9. **Submission package:** Hosted URL accessible, GitHub repo public with license, demo video uploaded, Devpost form submitted with Arize track selected.

If steps 1-8 pass with margin, the demo recording is just capturing this flow on screen.

---

## What This Spec Is Not

- **Not an implementation plan.** No file paths, code snippets, commit boundaries, or task decomposition. That's the next deliverable, produced by the `superpowers:writing-plans` skill once this spec is approved.
- **Not a final design.** It's a v1 draft. Open decisions O1-O3 will lock before they bind. Spec evolves per the harness's evolution protocol — change log in CLAUDE.md.

---

## Self-Review

- [x] No "TBD" or placeholder requirements in locked sections (D1-D9 all decided; component contracts complete). Pending items are explicitly listed under "Open Decisions Still Pending" with defaults.
- [x] Internal consistency: prompt-store schema matches across C2/C4 and Data Model; eval result schema matches across C3/C5; triage output schema matches across C2 and Data Model.
- [x] Scope: single submission, one Cloud Run runtime + one dashboard service. Decomposable into per-component implementation plans, not per-subsystem sub-projects.
- [x] Ambiguity: tactic-routing strategy is the one place a reader could interpret the design two ways. Surfaced as O1 with a default, not left ambiguous.
