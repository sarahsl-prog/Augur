# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Context

Augur is a **Google Cloud Rapid Agent Hackathon** submission for the **Arize track** (deadline: **2026-06-11, 2pm PDT**). It is a self-improving security alert triage agent that reads its own Phoenix traces to rewrite its own prompts and improve precision/recall over time.

The repo is currently greenfield: only `README.md` (the project brief) exists. There is no source code, build system, or test suite yet. **Read `README.md` in full before making architectural decisions** — it contains the authoritative tech stack, taxonomy, and build order.

## Build Order Is Load-Bearing

The README prescribes a 12-step build order. Two sequencing rules must not be violated:

1. **Do not start step 6 (Prompt Store) until step 5 works** — the self-improvement loop is worthless if the base triage agent isn't producing traces in Phoenix.
2. **Prompt Store rewrite is priority #1, few-shot example store is #2.** Don't invert this.

When asked to "add the next thing," check current progress against the 12-step list in `README.md` and continue from there rather than jumping ahead.

## Hard Architectural Constraints

These are non-negotiable hackathon-track requirements:

- **Agent runtime must be code-owned** — Google ADK on Cloud Run. Visual Agent Builder is *not* supported for tracing on the Arize track.
- **LLM must be Gemini via Vertex AI** (hackathon requirement).
- **Tracing must use OpenInference auto-instrumentation** (`openinference-instrumentation-google-adk`) into Phoenix Cloud. The agent must also query its *own* traces via the Phoenix MCP server (`@arizeai/phoenix-mcp` via npx) — that closed loop is the judged differentiator.
- **Prompt Store**: default to **Firestore** unless a strong reason emerges otherwise (decision recorded in README "Open Decisions").

## Three-Agent Architecture

The system is three cooperating agents, not one. Keep them separate:

| Agent | Role | Trigger |
|-------|------|---------|
| **Triage Agent** | Loads current prompt per ATT&CK tactic from Prompt Store, classifies alerts, emits structured triage report | Per alert |
| **Eval Agent** | Pulls traces via Phoenix MCP, computes per-tactic precision/recall vs. ground truth, identifies failure clusters | Every N=25 alerts (recommended) |
| **Improvement Agent** | Pulls failed traces as negative examples, rewrites the failing tactic's prompt, stores new version | When eval flags a failing tactic |

The improvement loop's value depends on prompts being **versioned per ATT&CK tactic** in Firestore — not one global prompt. Future code changes that collapse this to a single prompt break the demo narrative.

## Taxonomy Invariants

Two distinctions in the alert taxonomy signal domain expertise to judges and must not be conflated in code or prompts:

- **False Positive ≠ Benign Positive.** False Positive = bad detection logic firing on normal traffic. Benign Positive = legitimate activity that *correctly* matched the rule (e.g., sysadmin running a real admin tool).
- The agent emits **disposition** (5 values) *and* **MITRE ATT&CK tactic/technique** as separate fields. Don't merge them.

The structured output schema is defined in `README.md` lines 85–96. Treat it as the contract.

## Data Pipeline

Hybrid by design:

- **CICIDS2017/2018**: real labeled data, gives the eval credibility. Start with Brute Force + Lateral Movement subsets.
- **Synthetic alerts**: Python generator producing alerts in the same schema, used to *control* failure patterns for demo reproducibility.

Both must share one alert schema; ground truth labels travel with the alert through the pipeline so the Eval Agent can score Phoenix traces against them.

## Build / Test / Lint Commands

None yet — no source code, package manifest, or tooling exists. Once the ADK skeleton lands (build order step 1), update this section with the actual commands.

## Demo North Star

The ~3-minute demo video shows: baseline prompts struggle on Lateral Movement → eval flags the cluster → improvement agent rewrites that tactic's prompt → same batch reruns with measurably better precision/recall → Phoenix trace diff between v1 and v2 prompt on an identical alert. When making trade-off calls, prefer work that strengthens this narrative over peripheral polish.
