# Project Brief: Self-Improving Security Triage Agent
## Google Cloud Rapid Agent Hackathon — Arize Track
## Deadline: June 11, 2026
<img width="1024" height="1024" alt="auger_eagle_standing_on_a_glowing_circuit" src="auger.jpg" />

---
Augur — a security triage agent that reads its own traces to improve its foresight.

## Concept Summary

Build a security alert triage agent that:
1. Ingests security alerts and classifies them using MITRE ATT&CK taxonomy
2. Generates structured triage reports with disposition, severity, and recommended action
3. Is fully instrumented with OpenTelemetry tracing via Arize Phoenix
4. Uses the Phoenix MCP server to query its own traces at runtime
5. Runs an autonomous self-improvement loop: identifies where it miscategorizes
   alerts and rewrites its own prompts to fix those patterns
6. Demonstrates measurable improvement in precision/recall over iterations

The self-improvement loop is the core differentiator. Most submissions will bolt
on tracing as an afterthought. This one makes the traces *functional* — the
agent reads its own operational history and gets better from it.

---

## Hackathon Track: Arize

- Prize bucket: $5k / $3k / $2k (1st/2nd/3rd), competing only within Arize track
- Judging: Technical implementation, meaningful tracing + MCP use, quality of
  self-improvement loop, overall impact
- Hard requirement: Code-owned agent runtime (Gemini CLI, Google ADK, Cloud Run).
  Visual Agent Builder alone is NOT supported for tracing.

---

## Tech Stack

| Layer           | Technology                                | Notes                        |
|-----------------|-------------------------------------------|------------------------------|
| Agent Runtime   | Google ADK                                | Required for Arize; GCP-native |
| LLM             | Gemini (via Vertex AI)                    | Required by hackathon        |
| Hosting         | Cloud Run                                 | GCP hands-on for cert work   |
| Tracing         | Arize Phoenix Cloud (free tier)           | OpenInference instrumentation |
| MCP Server      | @arizeai/phoenix-mcp via npx              | Agent queries its own traces  |
| Instrumentor    | openinference-instrumentation-google-adk  | Auto-instruments ADK agents  |
| Prompt Store    | Firestore (preferred)                     | Versioned prompts per tactic  |
| Language        | Python                                    | Primary dev language          |

Key links:
- Phoenix Cloud:    https://app.phoenix.arize.com
- Phoenix GitHub:   https://github.com/Arize-ai/phoenix
- Phoenix MCP docs: https://arize.com/docs/phoenix/integrations/phoenix-mcp-server
- pip install openinference-instrumentation-google-adk

---

## Alert Taxonomy: Two-Layer System

### Layer 1: Disposition (agent output per alert)

| Disposition                    | Description                                         |
|--------------------------------|-----------------------------------------------------|
| True Positive - Critical       | Real threat, immediate IR required                  |
| True Positive - Policy Violation | Real but not emergency, needs remediation         |
| False Positive                 | Bad detection logic or data; pure noise to tune out |
| Benign Positive                | Legitimate activity that triggered the rule         |
| Needs Investigation            | Ambiguous; escalate to senior analyst               |

IMPORTANT: False Positive != Benign Positive. This distinction signals domain
expertise to judges. A sysadmin running a legit tool is Benign Positive.
Bad detection logic firing on normal traffic is False Positive.

### Layer 2: MITRE ATT&CK Tactic (scoped subset for hackathon)

| Tactic             | Example Technique          | Why Included                          |
|--------------------|---------------------------|---------------------------------------|
| Initial Access     | T1190 Exploit Public App   | Clear signal in network logs          |
| Credential Access  | T1110 Brute Force          | CICIDS has great coverage             |
| Lateral Movement   | T1021 Remote Services      | Common FP/TP confusion; ideal for demo |
| Exfiltration       | T1041 C2 Channel           | Measurable in flow data               |
| Command & Control  | T1071 App Layer Protocol   | Benign positive noise; interesting eval |
| Defense Evasion    | T1036 Masquerading         | Tricky; good for showing improvement  |

### Structured Agent Output Per Alert

{
  "alert_id": "uuid",
  "disposition": "True Positive - Critical",
  "attack_tactic": "Lateral Movement",
  "attack_technique": "T1021.002",
  "attack_technique_name": "SMB/Windows Admin Shares",
  "confidence": 0.87,
  "severity": "High",
  "recommended_action": "Isolate host, escalate to IR",
  "reasoning": "...",
  "trace_id": "phoenix_trace_id"
}

---

## Data Pipeline: Hybrid Approach

Sources:
- CICIDS2017/2018: Public labeled network intrusion dataset. Ground truth labels,
  realistic attack patterns. Covers brute force, DoS, infiltration, botnet,
  web attacks. Download: https://www.unb.ca/cic/datasets/ids-2017.html
  https://www.yorku.ca/research/bccc/ucs-technical/cybersecurity-datasets-cds/
  
- Synthetic alerts: Python scripts generating alert-shaped data mapped to CICIDS
  categories. Controlled volume, edge case injection, reproducible failure patterns
  for demo purposes.

Pipeline:
1. Preprocess CICIDS: extract features, normalize to alert schema
2. Synthetic generator produces alerts in same schema, same label distribution
3. Labels stored with alerts as ground truth for eval
4. Eval agent compares triage output vs ground truth: per-tactic precision/recall
5. Results stored in Prompt Store with trace IDs for Phoenix correlation

Why hybrid: CICIDS = credibility. Synthetic = control over demo failure patterns.

---

## Architecture: The Core Self-Improvement Loop

[Alert Stream]
      |
      v
[Triage Agent]
  - Loads current prompt from Prompt Store (per ATT&CK tactic)
  - Classifies alert: disposition + tactic/technique
  - Generates structured triage report
      |
      v
[Phoenix Tracing] <-- OpenInference auto-instrumentation
  - Every LLM call, tool use, decision traced
  - Trace linked to alert_id and ground truth label
      |
      v
[Eval Agent] (triggers every N alerts — recommend N=25)
  - Pulls traces from Phoenix via MCP server
  - Compares dispositions vs ground truth labels
  - Computes precision/recall per ATT&CK tactic
  - Identifies worst-performing tactic clusters
      |
      v
[Improvement Agent]
  - Queries Phoenix MCP for failed trace examples
  - Pulls current prompt for failing tactic from Prompt Store
  - Uses failed traces as negative examples in context
  - Rewrites prompt for that tactic
  - Stores new prompt version in Prompt Store
      |
      v
[Triage Agent loads updated prompt on next alert]
      |
    (loop)

What actually changes — implementation priority order:
1. Prompt Store rewrite (build first): versioned system prompts per ATT&CK tactic
   in Firestore. Improvement Agent rewrites failing tactic prompts using its own
   failed traces as context.
2. Few-shot example store (build second): agent adds failure cases as negative
   few-shot examples into retrieval. Powerful, not hard to add once #1 works.

---

## Demo Strategy: "Day in the Life" Narrative (~3 min video)

Framing: SOC analyst turns on the agent Monday morning.

Arc:
1. Agent starts with baseline prompts, run against 50 labeled alerts
2. Show it struggling: consistently miscalling Lateral Movement as False Positive.
   Phoenix traces show the reasoning chain.
3. Eval loop fires (25-alert threshold): identifies Lateral Movement failure cluster
4. Improvement Agent rewrites Lateral Movement prompt using failed trace examples
5. Run same alert batch: show improved precision/recall numbers
6. Phoenix dashboard: trace diff between v1 and v2 prompt, same alert, diff outcome

Key metrics to show on screen:
- Per-tactic precision/recall before/after improvement loop
- False positive rate by severity:
    Critical < 25% | High < 50% | Medium < 75% (industry benchmarks)
- Prompt version history in Prompt Store
- Phoenix trace comparison: v1 vs v2 prompt on identical alert

---

## GCP Cert Alignment

Hands-on experience with:
- Vertex AI / Gemini API
- Google ADK (Agent Development Kit)
- Cloud Run deployment
- Firestore (managed NoSQL)
- IAM and service accounts
- Cloud Build (optional CI/CD for prompt updates)

---

## Open Decisions (Resolve Early)

1. Prompt Store backend: Firestore (recommended — simpler, GCP-native, NoSQL)
   vs. Cloud SQL Postgres (more familiar but more setup)

2. Eval trigger: alert-count-based at N=25 (recommended for demo clarity)
   vs. time-based vs. manual

3. Phoenix hosting: Cloud free tier (recommended for hackathon — zero setup)
   vs. self-hosted on Cloud Run

4. Ground truth: pre-labeled CICIDS subset (recommended — reproducible)
   vs. live human feedback endpoint (impressive but complex)


---


## Suggested Build Order

Work in this order. Do not skip ahead.

 1. Set up Google ADK project skeleton with Cloud Run config
 2. Install OpenInference ADK instrumentation, verify traces in Phoenix Cloud
 3. Build alert schema + synthetic data generator
 4. Download + preprocess CICIDS2017 subset (start: Brute Force + Lateral Movement)
 5. Build basic triage agent, single hardcoded prompt, no self-improvement.
    Get end-to-end working and traces flowing into Phoenix.
 6. Add Prompt Store in Firestore with versioned prompts per tactic
 7. Build eval agent wired to Phoenix MCP
 8. Build improvement agent with prompt rewrite logic
 9. Wire all three agents together, run improvement loop end-to-end
10. Build demo dashboard/UI for the video
11. Deploy to Cloud Run
12. Record demo video

CRITICAL: Do not start step 6 until step 5 works. The self-improvement loop
is worthless if the base agent is broken. Get traces flowing first.

---

## Submission Checklist (June 11, 2026 @ 2pm PDT)

[ ] Hosted project URL (Cloud Run)
[ ] Public GitHub repo with open source license visible in About section
[ ] ~3 minute demo video
[ ] Arize track selected on Devpost
[ ] Completed Devpost submission form

