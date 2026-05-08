---
name: augur
description: Use BEFORE any work on the Augur project (the security alert triage agent hackathon submission for the Arize track). Triggers on any of these keywords appearing in the user's request — "Augur", "triage", "alert", "MITRE", "ATT&CK", "tactic", "disposition", "Phoenix", "OpenInference", "MCP server", "ADK", "Vertex", "Gemini", "Cloud Run", "Firestore", "prompt store", "eval agent", "improvement agent", "self-improvement loop", "CICIDS", "false positive", "benign positive", "build step", "next step", "deploy", "demo video" — OR when CLAUDE.md/README.md mentions hackathon scope. The skill identifies the current build step (1-12), enforces hard hackathon-track invariants, and dispatches to domain skills (augur-mitre-taxonomy, augur-adk-patterns, augur-phoenix-loop). Re-trigger on follow-up requests like "do the next step", "verify this", "rewrite the prompt", "fix the eval".
---

# Augur Orchestrator

Augur is a self-improving security alert triage agent built for the **Google Cloud Rapid Agent Hackathon — Arize track** (deadline **2026-06-11, 14:00 PDT**). The judged differentiator is the **closed-loop self-improvement**: the agent reads its own Phoenix traces, identifies miscategorization clusters, and rewrites its own per-tactic prompts. Read `README.md` and `CLAUDE.md` for full context — they are authoritative.

## Hard Invariants — Block on Violation

If a request would cause any of these to break, **stop and explain which invariant blocks the request**. Do not implement around them. These are hackathon-track requirements; violating them disqualifies the submission.

1. **Agent runtime must be Google ADK** — code-owned. Visual Agent Builder is not eligible for the Arize track.
2. **LLM must be Gemini via Vertex AI.** Not Anthropic, not OpenAI, not local models. If a library imports `openai` or `anthropic`, that is a violation.
3. **Tracing must be Phoenix Cloud via OpenInference auto-instrumentation** (`openinference-instrumentation-google-adk`). Custom OTel exporters that don't go to Phoenix break the demo.
4. **MCP server is `@arizeai/phoenix-mcp` via npx.** The agent must query its *own* traces — that closed loop is the judging hook.
5. **Disposition enum has exactly 5 values** (TP-Critical, TP-PolicyViolation, FP, BP, NeedsInvestigation) and **False Positive ≠ Benign Positive**. Conflating them signals lack of domain expertise.
6. **MITRE tactic scope is exactly the 6 in README** (Initial Access, Credential Access, Lateral Movement, Exfiltration, Command & Control, Defense Evasion). Don't add or drop tactics.
7. **Three runtime agents stay separate** (Triage / Eval / Improvement). Collapsing into one breaks the demo narrative.
8. **Prompts are versioned per tactic in Firestore.** A single global system prompt defeats the improvement loop.

When blocking, name the invariant by number (e.g., "Invariant 5") so the user can argue specifically.

## Build Step Protocol

Augur has a 12-step build order (README "Suggested Build Order"). Before implementing, identify which step is currently active:

1. ADK skeleton + Cloud Run config
2. OpenInference + Phoenix traces flowing
3. Alert schema + synthetic generator
4. CICIDS preprocessing (start: Brute Force + Lateral Movement subsets)
5. Basic triage agent, single hardcoded prompt, end-to-end traces in Phoenix
6. Firestore Prompt Store, versioned per tactic
7. Eval agent wired to Phoenix MCP
8. Improvement agent with prompt rewrite logic
9. Three agents wired into improvement loop end-to-end
10. Demo dashboard / UI
11. Deploy to Cloud Run
12. Record demo video

**Sequencing rule (non-negotiable):** Step 6 cannot start until step 5 produces traces in Phoenix. If a request asks to jump ahead (e.g., "let's build the improvement agent" while step 5 is incomplete), redirect: "Steps 5→6 sequencing is load-bearing — let's finish [current step] first."

To detect the current step: read `git log --oneline`, scan `README.md` for checkbox status, or check the file system for landmarks (e.g., `agents/triage/` exists → step 5 in progress or done; `firestore.rules` exists → step 6 in progress).

If unclear, ask the user: "Which step are we on?"

## Dispatch — When to Load Which Domain Skill

Don't load all of these at once. Pull them in by topic:

| Topic in user request | Load skill |
|-----------------------|------------|
| Alerts, dispositions, MITRE, classification, ground truth, CICIDS | `augur-mitre-taxonomy` |
| ADK agent code, Vertex auth, Gemini, Cloud Run deploy, Firestore schema | `augur-adk-patterns` |
| Phoenix tracing, OpenInference, MCP server, eval/improvement loop | `augur-phoenix-loop` |

Multiple may apply (e.g., "build the eval agent" → both `augur-phoenix-loop` and `augur-mitre-taxonomy`). Load all that match.

## Demo North Star

The 3-minute submission video shows a specific arc:

1. Baseline run on 50 labeled alerts — agent struggles, **consistently miscalls Lateral Movement as False Positive**.
2. Eval loop fires at N=25 — flags Lateral Movement failure cluster.
3. Improvement agent rewrites the Lateral Movement prompt using failed traces as negative few-shot.
4. Re-run same batch — measurably better precision/recall.
5. Phoenix dashboard: trace diff between v1 and v2 prompt on identical alert.

When deciding scope tradeoffs ("should I add feature X or polish Y?"), ask: *does this strengthen or distract from this arc?* Lateral Movement specifically is the demo target — when implementing, bias prompt-engineering effort toward making LM the visible failure-then-fix story.

## When to Dispatch the Verifier

Use the `augur-build-verifier` sub-agent (Agent tool, `subagent_type: general-purpose`) when:

- The user explicitly asks to verify, audit, or sanity-check the project.
- Before significant commits to `main` or before the final submission push.
- After a large refactor that touched multiple modules.

Do not run it on every change — it's a sweep, not a per-edit lint.

## Follow-up / Re-execution

When the user asks "do the next step" or "continue" without specifics:
1. Detect current step (above).
2. State which step you believe is active and what's next.
3. Pull the relevant domain skill(s).
4. Implement. Surface invariant checks inline.

When the user asks for a redo or correction of prior work:
1. Re-read the relevant domain skill(s) — the rules may have evolved.
2. Diff against the prior implementation.
3. If the change would break an invariant, block per the hard-invariant protocol above.
