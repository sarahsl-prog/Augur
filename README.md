# Augur

Self-improving security alert triage agent.

Google Cloud Rapid Agent Hackathon — Arize Track | Deadline: June 11, 2026

<img width="1024" height="1024" alt="auger_eagle_standing_on_a_glowing_circuit" src="auger.jpg" />

---

## What It Does

Augur ingests security alerts and classifies them using MITRE ATT&CK taxonomy, then
uses its own traces to get better:

1. **Triage Agent** — classifies each alert (disposition + tactic + technique)
2. **Phoenix Tracing** — every call is auto-traced into Arize Phoenix Cloud
3. **Eval Agent** — queries Phoenix via the MCP server to score predictions against
   ground truth per-tactic
4. **Improvement Agent** — fetches failed traces from Phoenix via MCP, builds a
   meta-prompt, and rewrites the per-tactic system prompt
5. The triage agent loads the updated prompt for the next batch

**The differentiator:** The traces aren't decorative. The eval and improvement agents
*read operational history from Phoenix* instead of comparing local dicts. That is the
MCP integration the Arize track judges.

---

## Tech Stack

| Layer | Technology | Notes |
|---|---|---|
| Agent runtime | Python 3.12, FastAPI, uvicorn | Not using ADK Runner; direct Vertex API calls with manual OTel spans |
| LLM | Gemini via Vertex AI | project `augur-495810`, region `us-central1` |
| Hosting | Cloud Run | GCP-native, binds to `$PORT` |
| Tracing | Arize Phoenix Cloud | `https://app.phoenix.arize.com` |
| MCP Server | `@arizeai/phoenix-mcp` via npx | Used by eval + improvement agents |
| Instrumentor | `openinference-instrumentation-google-adk` | Auto-instruments ADK Runner (we add manual spans for direct API calls) |
| Prompt Store | Google Firestore | Versioned prompts per tactic |
| Data | CICIDS2017/2018 + synthetic | CICIDS parsed by `mitre_mapping.py`; synthetic generator for dev/test |

---

## Quick Start (Local)

### Prerequisites

- Python 3.12
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- Google Cloud SDK (for deploy)
- Vertex AI + Firestore access (service account key or ADC)
- Phoenix Cloud API key (free tier)
- Node.js (for the MCP server; already in Docker)

### Environment Variables

Create `.env` or export manually:

```bash
export PHOENIX_API_KEY="your-phoenix-api-key"          # Required for tracing
export AUGUR_TRACING_DISABLED="0"                       # Set "1" to skip OTel in tests
export GOOGLE_CLOUD_PROJECT="augur-495810"
export GOOGLE_CLOUD_LOCATION="us-central1"
```

### Install & Test

```bash
git clone https://github.com/sarahsl-prog/Augur.git
cd Augur

# uv handles virtual env + editable install automatically
uv sync

# Run the test suite (33 tests, ~5s)
uv run pytest -q

# Start the API locally
uv run uvicorn augur.main:app --host 0.0.0.0 --port 8080
```

### Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/health` | `GET` | Liveness check |
| `/` | `GET` | Service info |
| `/triage` | `POST` | Classify a single `Alert` → `TriageOutput` |
| `/batch` | `POST` | Generate alerts, triage, eval, optionally improve |

### `/batch` MCP Toggle

The closed-loop `/batch` endpoint runs the legacy inline eval by default. To use
Phoenix MCP-backed eval and improvement (the Arize-track path), set
`use_phoenix_mcp: true` in the request:

```bash
curl -X POST http://localhost:8080/batch \
  -H "Content-Type: application/json" \
  -d '{
    "n": 25,
    "eval_every": 25,
    "improve": true,
    "use_phoenix_mcp": true
  }'
```

Response example:

```json
{
  "triaged": 25,
  "eval_run_id": "eval-abc-123",
  "flagged_tactic": "Lateral Movement",
  "improved": true,
  "mcp_enabled": true
}
```

---

## Key Modules

| Module | Purpose |
|---|---|
| `src/augur/agents/triage.py` | Builds + runs triage agent; injects manual OTel span with `trace_id` into `TriageOutput` |
| `src/augur/phoenix_mcp_client.py` | Async context manager wrapping `@arizeai/phoenix-mcp` stdio server |
| `src/augur/eval_phoenix.py` | MCP-based eval: pulls traces from Phoenix, computes per-tactic precision/recall |
| `src/augur/improvement_phoenix.py` | MCP-based improvement: pulls failed trace content from Phoenix, rewrites prompt |
| `src/augur/data/mitre_mapping.py` | CICIDS attack label → Augur tactic/technique/disposition mapping |
| `src/augur/data/cicids_loader.py` | `load_cicids_csv(path)` → `list[tuple[Alert, GroundTruth]]` |
| `src/augur/data/splits.py` | Tactic-stratified train/dev/test split |
| `src/augur/tracing.py` | `init_tracing()` + `trace_span()` context manager for manual spans |

---

## Build Order

Current branch: `implement-mcp-tests` (ahead of `main`)

| # | Status | Task |
|---|---|---|
| 1 | ✅ | Project skeleton + Cloud Run config + Firestore prompt store |
| 2 | ✅ | OpenInference ADK instrumentation → Phoenix Cloud |
| 3 | ✅ | Alert schema + synthetic data generator |
| 4 | ✅ | CICIDS preprocessing: `mitre_mapping.py`, `cicids_loader.py`, `splits.py` |
| 5 | ✅ | Basic triage agent working end-to-end + traces in Phoenix |
| 6 | ✅ | Prompt Store in Firestore with versioned prompts per tactic |
| 7 | ✅ | Eval agent wired to Phoenix MCP |
| 8 | ✅ | Improvement agent with prompt rewrite logic + MCP |
| 9 | ✅ | Wire MCP eval + improvement into `/batch` endpoint |
| 10 | ⬜ | Deploy to Cloud Run (`implement-mcp-tests` branch) |
| 11 | ⬜ | Record demo video |

---

## Deploy to Cloud Run

### 1. Set up Artifact Registry repository (once)

```bash
gcloud artifacts repositories create augur \
  --repository-format=docker \
  --location=us-central1 \
  --project=augur-495810
```

### 2. Build + push with Cloud Build

```bash
cd Augur
gcloud builds submit --config cloudbuild.yaml --project=augur-495810
```

### 3. Deploy runtime service

```bash
gcloud run deploy augur-runtime \
  --image=us-central1-docker.pkg.dev/augur-495810/augur/runtime:latest \
  --region=us-central1 \
  --project=augur-495810 \
  --platform=managed \
  --allow-unauthenticated \
  --set-env-vars="PHOENIX_API_KEY=${PHOENIX_API_KEY}" \
  --max-instances=10 \
  --memory=1Gi \
  --cpu=1 \
  --timeout=300s \
  --port=8080
```

### 4. Deploy dashboard (optional)

```bash
gcloud run deploy augur-dashboard \
  --image=us-central1-docker.pkg.dev/augur-495810/augur/dashboard:latest \
  --region=us-central1 \
  --project=augur-495810 \
  --platform=managed \
  --allow-unauthenticated \
  --max-instances=1 \
  --memory=512Mi
```

---

## Alert Taxonomy

### Disposition Layer (what the agent decides)

| Disposition | Description |
|---|---|
| True Positive - Critical | Real threat, immediate response required |
| True Positive - Policy Violation | Real but not emergent; needs remediation |
| False Positive | Bad detection logic or data; noise to tune out |
| Benign Positive | Legitimate activity that triggered the rule |
| Needs Investigation | Ambiguous; escalate to senior analyst |

### MITRE ATT&CK Tactics (v1 scope)

| Tactic | Example Technique | Coverage |
|---|---|---|
| Initial Access | T1190 Exploit Public-Facing Application | CICIDS Web Attack labels |
| Credential Access | T1110 Brute Force | CICIDS FTP-Patator, SSH-Patator |
| Lateral Movement | T1021 Remote Services | CICIDS Infiltration |
| Command & Control | T1071 App Layer Protocol | CICIDS Bot |
| Exfiltration | T1041 C2 Channel | Synthetic (CICIDS2017 lacks explicit label) |
| Defense Evasion | T1036 Masquerading | Synthetic (CICIDS2017 lacks explicit label) |

---

## Data Pipeline

CICIDS2017 is ~100 GB; we work with downloaded subsets. Use the fixture under
`tests/data/fixtures/cicids_sample.csv` for unit tests.

The pipeline:

1. Download CICIDS subset CSVs → `data/raw/`
2. `load_cicids_csv(path)` parses + maps labels via `mitre_mapping.py`
3. `split_pairs(pairs)` → stratified train/dev/test
4. Test set is held out; never used for prompt tuning per spec D4

---

## Architecture: Self-Improvement Loop

```
[Alert Stream]
      |
      v
[Triage Agent] ← per-tactic prompt version from Firestore
      |
      v
[Manual OTel span]
  - augur.disposition, augur.attack_tactic, etc.
  - trace_id injected into TriageOutput
  |  (exported to Phoenix Cloud)
      v
[Batch Eval — every N alerts]
  Legacy path: inline dict comparison
  MCP path:   query Phoenix traces via MCP, match by trace_id/alert_id
      |
      v
[Flagged tactic?]
  └─yes→ [Improvement Agent]
         Legacy path: local failed_traces dicts
         MCP path:   fetch trace content from Phoenix via MCP,
                     extract reasoning, rewrite prompt
           |
           v
        [Firestore: new prompt version written]
           |
           v
  [Triage Agent loads updated prompt on next alert]
```

---

## Submission Checklist (June 11, 2026 @ 2:00 PM PDT)

- [ ] Hosted project URL (Cloud Run)
- [ ] Public GitHub repo with open source license
- [ ] ~3 minute demo video
- [ ] Arize track selected on Devpost
- [ ] Completed Devpost submission form

---

## License

This project is open source under the MIT License (see `LICENSE`).

## Links

- Phoenix Cloud: https://app.phoenix.arize.com
- Phoenix GitHub: https://github.com/Arize-ai/phoenix
- Phoenix MCP docs: https://arize.com/docs/phoenix/integrations/phoenix-mcp-server
