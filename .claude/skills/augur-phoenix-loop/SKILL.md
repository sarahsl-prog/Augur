---
name: augur-phoenix-loop
description: Use when wiring or modifying any part of Augur's observability and self-improvement loop — Phoenix tracing, OpenInference instrumentation, the Phoenix MCP server, the eval agent, the improvement agent, prompt rewriting, or trace correlation with ground truth. Triggers on these words — "Phoenix", "phoenix.arize.com", "OpenInference", "openinference-instrumentation", "tracing", "trace", "span", "OTel", "OpenTelemetry", "MCP", "@arizeai/phoenix-mcp", "phoenix-mcp", "eval agent", "improvement agent", "self-improvement", "prompt rewrite", "precision", "recall", "F1", "few-shot", "negative example", "failure cluster". This is the JUDGED differentiator of the project — protect the closed-loop architecture (agent reads its own traces and rewrites its own prompts).
---

# Augur Phoenix Self-Improvement Loop

This skill owns the observability spine and the closed self-improvement loop. The hackathon's Arize-track judging hook is **the agent reading its own traces via MCP and using them as input to rewrite its own prompts**. Most submissions will bolt tracing on as a passive afterthought; Augur's makes traces *functional*. Protect that.

## Three-Agent Architecture — Do Not Collapse

| Agent | Reads from | Writes to | Trigger |
|-------|------------|-----------|---------|
| **Triage Agent** | Alert payload + Firestore (current prompt for the alert's tactic) | Phoenix trace (auto via OpenInference) + structured output to caller | Per alert |
| **Eval Agent** | Phoenix traces (via MCP) + ground-truth labels | Eval result doc (per-tactic precision/recall + failure cluster IDs) | Every N=25 alerts (recommended for demo clarity) |
| **Improvement Agent** | Phoenix failed traces (via MCP) + current prompt (Firestore) + eval result | New prompt version (Firestore) | When eval flags a tactic with degraded metrics |

**Rule:** these are three separate ADK agents in three separate files (or three modules; the boundary is what matters). Collapsing for brevity destroys the demo narrative.

## Step 2 — OpenInference Auto-Instrumentation

Install:
```bash
uv add openinference-instrumentation-google-adk arize-phoenix-otel
```

Wire-up (one-time at process start, before any ADK agent runs):

```python
from phoenix.otel import register
from openinference.instrumentation.google_adk import GoogleADKInstrumentor

tracer_provider = register(
    project_name="augur",
    endpoint="https://app.phoenix.arize.com/v1/traces",
    headers={"api_key": os.environ["PHOENIX_API_KEY"]},
    auto_instrument=True,
)
GoogleADKInstrumentor().instrument(tracer_provider=tracer_provider)
```

Verify: run any ADK agent once. Open https://app.phoenix.arize.com — your `augur` project should show traces with LLM call spans and tool spans nested correctly. **If traces don't appear, do not move past build step 2.** Step 5+ depends on this.

### What gets traced

OpenInference auto-instrumentation captures:
- LLM input/output text and metadata (model, params, latency)
- Tool calls (name, args, result)
- Agent decision boundaries (one root span per triage invocation)

Each Phoenix trace ID is the value Augur stores in the structured triage output's `trace_id` field (see `augur-mitre-taxonomy`). That's how the eval agent correlates traces to ground truth.

### Custom span attributes for ground-truth correlation

Add `alert_id` and `ground_truth_disposition` as span attributes on the *eval-side* (not on the triage agent — the triage agent must not see ground truth). Example:

```python
from opentelemetry import trace
span = trace.get_current_span()
span.set_attribute("augur.alert_id", alert.id)
span.set_attribute("augur.ground_truth_disposition", ground_truth.disposition)
```

This makes eval queries trivial: filter by `augur.alert_id` and compare predicted vs. ground truth.

## Step 7 — Phoenix MCP Server

The MCP server is what makes the loop "self-improving" — the agent queries its own traces.

### Run the server

`@arizeai/phoenix-mcp` runs via npx — no install:

```bash
npx -y @arizeai/phoenix-mcp \
  --baseUrl https://app.phoenix.arize.com \
  --apiKey "$PHOENIX_API_KEY"
```

### Configure the agent to use it

Add it to the project's MCP config so the eval and improvement agents can call it. Exact config path depends on the runtime (ADK MCP support / direct subprocess) — verify against current docs before generating code:

- Phoenix MCP overview: https://arize.com/docs/phoenix/integrations/phoenix-mcp-server

For the hackathon, the simplest pattern is: spawn the MCP server as a subprocess from the orchestrator, expose its tools to the eval and improvement ADK agents as ADK tools, and let auto-instrumentation handle the tracing.

### Common queries the eval agent runs

- `list_recent_traces(project="augur", limit=25)` — the alert batch to score
- `get_trace_by_id(trace_id)` — full span tree for a single triage decision
- `query_traces(filter="augur.ground_truth_disposition != predicted_disposition")` — failure cluster

### Common queries the improvement agent runs

- For the failing tactic, pull 5-10 representative failed traces (full reasoning + final disposition) to use as negative examples in the rewrite prompt.

## Step 7 — Eval Agent Logic

Eval agent input: a list of recent traces (~25) + their ground-truth labels.

Output:
```json
{
  "eval_run_id": "uuid",
  "timestamp": "ISO-8601",
  "per_tactic": {
    "Lateral Movement": { "precision": 0.42, "recall": 0.71, "f1": 0.53, "n": 8 },
    "Credential Access": { "precision": 0.91, "recall": 0.88, "f1": 0.89, "n": 11 },
    ...
  },
  "failure_clusters": [
    {
      "tactic": "Lateral Movement",
      "pattern": "Misclassifying SMB admin shares (T1021.002) as False Positive",
      "trace_ids": ["trace_abc", "trace_def", ...]
    }
  ],
  "flagged_tactic_for_improvement": "Lateral Movement"
}
```

The `flagged_tactic_for_improvement` field is what triggers the improvement agent. Pick the worst-F1 tactic that has at least 5 alerts in the batch (avoid flagging a tactic with n=1 — too noisy).

### Industry benchmarks for context

Use these as *display* metrics in the demo (not blocking thresholds):

- False positive rate by severity: Critical < 25%, High < 50%, Medium < 75%

Showing these on the dashboard signals domain awareness to judges.

## Step 8 — Improvement Agent Logic

Improvement agent input:
- Current prompt for the flagged tactic (from Firestore)
- 5-10 failed-trace examples from Phoenix (full reasoning + alert + ground truth)
- Eval result summary

Output: new prompt for that tactic, written into Firestore as a new version (see `augur-adk-patterns` for the schema).

### Rewrite strategy (priority order)

1. **(Build first)** Add explicit rules covering the failure pattern. E.g., if the failure cluster is "FP-classified SMB admin shares," add a rule: "If the alert is SMB admin shares (T1021.002) AND the source host has DC-Admin role tag, classify as Benign Positive — not False Positive." See README "What actually changes" priority order.

2. **(Build second)** Add failed cases as negative few-shot examples in the prompt. Store them in the `few_shot_negative` field of the new Firestore version document. Don't build this until #1 works — README priority is explicit.

The improvement agent itself is a Gemini call with a meta-prompt: "Given this current prompt for tactic X, these 5 failure traces showing what the agent got wrong, and the ground truth — produce a revised prompt that would have classified these correctly without regressing on the rest of the tactic's behavior."

### Don't regress

The improvement agent should optionally be re-evaluated on the *prior* alert batch with the new prompt before promotion. If F1 drops on the prior set, abandon the rewrite. (Stretch goal — first demo may skip this guard.)

## The Closed Loop, End to End

```
[Triage Agent runs on alert]
    ↓ writes Phoenix trace, returns disposition
[Caller stores trace_id ↔ ground_truth pair]
    ↓ after 25 alerts
[Eval Agent: pulls 25 traces via MCP, scores vs ground truth, flags worst tactic]
    ↓ writes eval result
[Improvement Agent: reads flagged tactic prompt + failed traces via MCP, rewrites]
    ↓ writes new Firestore version, bumps current_version
[Next alert with that tactic uses the new prompt — loop continues]
```

The demo video shows this closed loop visibly: same alert input, v1 prompt → wrong answer (in Phoenix); v2 prompt → right answer (in Phoenix); side by side. Wire the demo dashboard to make that diff trivially screenshot-able.

## What NOT to Build

- **No custom OTel exporter that bypasses Phoenix.** It looks identical in code review and it nukes the entire judging hook.
- **No "memory" abstraction layer** between traces and the improvement agent. The MCP server *is* the memory layer. Adding a wrapper hides what the judges want to see.
- **No improvement agent that rewrites prompts without trace evidence.** A meta-LLM rewriting prompts from generic best-practices is not what the track is judging. The trace-grounded rewrite is the differentiator.
