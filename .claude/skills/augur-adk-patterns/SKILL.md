---
name: augur-adk-patterns
description: Use when building, configuring, or deploying any Google ADK (Agent Development Kit) component of Augur, OR any GCP-side wiring — Vertex AI / Gemini auth, Cloud Run deploy, Firestore prompt store, IAM, service accounts, gcloud commands. Triggers on these words — "ADK", "Agent Development Kit", "google-adk", "Vertex", "Vertex AI", "Gemini", "gemini-1.5", "gemini-2", "Cloud Run", "Dockerfile", "service-account", "gcloud", "Firestore", "prompt store", "IAM", "ADC", "application default credentials". Owns the Firestore schema for the versioned prompt store, ADK auth pattern, and the hard rule that ONLY Vertex Gemini is allowed (no OpenAI/Anthropic/local LLMs). The Arize hackathon track REQUIRES code-owned ADK runtime — Visual Agent Builder is forbidden.
---

# Augur ADK & GCP Patterns

This skill owns the GCP-side decisions: ADK agent shape, Vertex/Gemini auth, Cloud Run deploy, and the Firestore prompt-store schema.

## Hard Rules (from `augur` orchestrator — repeated for clarity)

- **Runtime:** Google ADK only. No Visual Agent Builder. No LangChain/LangGraph wrappers in the runtime path — they break OpenInference auto-instrumentation for Phoenix.
- **LLM:** Vertex AI Gemini. Imports of `openai`, `anthropic`, `langchain_openai`, `transformers` (for inference) violate the track requirements. Block before implementing.
- **Hosting:** Cloud Run. Not Cloud Functions, not GKE — the README scopes deployment narrowly so cert prep aligns.

## ADK Agent Shape

ADK is genuinely newer than most LLM training data. **Do not fabricate API specifics from memory** — verify against the canonical docs before generating code:

- ADK quickstart: https://google.github.io/adk-docs/
- OpenInference ADK instrumentor: https://github.com/Arize-ai/openinference/tree/main/python/instrumentation/openinference-instrumentation-google-adk

When you don't know an exact ADK API call (constructor signature, tool registration, etc.), say so and either:
1. Use `WebFetch` against the docs above and quote the exact form, or
2. Ask the user to confirm the import path / class name from a working example they have.

What we *have* committed to, project-wide:

- **One ADK agent per Phoenix-traced unit of work.** Augur has three: `triage_agent`, `eval_agent`, `improvement_agent`. Each is its own ADK agent with its own prompt and tool set. Do not collapse them into one.
- **Tools are ADK tools, not raw Python functions.** This is what gets traced cleanly by OpenInference. Wrapping logic as a tool also makes it MCP-callable later if needed.

## Vertex AI Gemini Setup

### Auth

For local dev:
```bash
gcloud auth application-default login
gcloud config set project <PROJECT_ID>
```

For Cloud Run: attach a service account with `roles/aiplatform.user` and `roles/datastore.user` (Firestore). Do **not** mount a JSON key — Cloud Run injects ADC automatically when a service account is bound to the service.

Service account JSON keys are gitignored (`*-service-account.json`, `gcp-key*.json`). If you must download one for local testing, store it outside the repo (e.g., `~/.config/augur/`).

### Model selection

Default to **Gemini 1.5 Pro** (or whatever Vertex's current top-tier is at submission time) for the triage and improvement agents — quality matters for the demo. **Gemini 1.5 Flash** is acceptable for the eval agent if cost becomes an issue, since eval is structured math over traces, not high-stakes reasoning.

Pin the model version in code (e.g., `gemini-1.5-pro-002`), not `gemini-1.5-pro` (a moving alias). Phoenix trace diffs across runs become noisy when the model silently changes.

## Cloud Run Deploy

Each ADK agent is deployable as its own Cloud Run service, OR they can co-host in one container. **Recommend co-hosting** for the hackathon — three services means three cold starts during the demo. A single container exposing different routes per agent keeps the demo snappy.

### Minimum viable Dockerfile shape

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN pip install uv && uv sync --frozen --no-dev
COPY src/ ./src/
CMD ["uv", "run", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

Cloud Run expects port 8080. Don't hardcode another port.

### Deploy command

```bash
gcloud run deploy augur \
  --source . \
  --region us-central1 \
  --service-account augur-runtime@${PROJECT_ID}.iam.gserviceaccount.com \
  --set-env-vars="PHOENIX_API_KEY=...,PHOENIX_PROJECT=augur,GCP_PROJECT=${PROJECT_ID}" \
  --allow-unauthenticated
```

`--allow-unauthenticated` is fine for the hackathon demo URL. For a real deployment, use IAP or token auth.

## Firestore Prompt Store Schema

This is the canonical schema. Don't deviate without updating this skill.

```
firestore/
└── prompts/                              (collection)
    ├── triage-initial-access/            (document — one per tactic)
    │   ├── current_version: 3            (int — pointer to active version)
    │   ├── tactic: "Initial Access"
    │   ├── created_at: <ts>
    │   └── versions/                     (subcollection)
    │       ├── 1/                        (document)
    │       │   ├── system_prompt: "..."
    │       │   ├── few_shot_negative: [...]    # failure cases from improvement loop
    │       │   ├── created_at: <ts>
    │       │   ├── created_by: "human" | "improvement_agent"
    │       │   └── parent_version: null
    │       ├── 2/
    │       │   ├── ...
    │       │   ├── created_by: "improvement_agent"
    │       │   ├── parent_version: 1
    │       │   └── triggering_eval_id: "<eval_run_id>"
    │       └── 3/
    └── triage-credential-access/
        └── ...
```

Document IDs follow `triage-{tactic-kebab-case}`. One doc per tactic = exactly 6 docs (matching the 6 tactics in `augur-mitre-taxonomy`).

Why this shape:
- `current_version` pointer makes prompt rollback a single-field write.
- `versions/` subcollection retains every version forever — judges can inspect the prompt evolution.
- `parent_version` + `triggering_eval_id` create an audit chain back to the failed traces that motivated the rewrite.
- `created_by` distinguishes human-authored from agent-rewritten — crucial for the demo's narrative.

## Reading & Writing Prompts

The triage agent reads:
```python
def load_active_prompt(tactic: str) -> str:
    doc = firestore.collection("prompts").document(f"triage-{slug(tactic)}").get()
    version = doc.get("current_version")
    v_doc = doc.reference.collection("versions").document(str(version)).get()
    return v_doc.get("system_prompt")
```

The improvement agent writes a new version atomically:
```python
def publish_new_version(tactic: str, new_prompt: str, eval_run_id: str, parent_version: int):
    doc_ref = firestore.collection("prompts").document(f"triage-{slug(tactic)}")
    new_version = parent_version + 1
    doc_ref.collection("versions").document(str(new_version)).set({
        "system_prompt": new_prompt,
        "created_at": SERVER_TIMESTAMP,
        "created_by": "improvement_agent",
        "parent_version": parent_version,
        "triggering_eval_id": eval_run_id,
    })
    doc_ref.update({"current_version": new_version})
```

Use a Firestore transaction if you need read-modify-write atomicity on `current_version`. For the hackathon demo, the eval loop runs serially so a transaction isn't strictly required — but document this decision.

## When Adding a New Cloud Resource

Before creating a new GCP service / IAM role / API enable, check:

1. Does it cost money idle? (Cloud Run scales to zero — fine. Cloud SQL doesn't — bad for hackathon budget.)
2. Does it require manual IAM steps a future Claude session won't remember? If yes, add the `gcloud` command to `README.md` under a "Setup" section.
3. Does it need a service-account key file? Default to **no key** — use ADC + workload identity wherever possible. Keys are an exfil risk and the gitignore is already defensive about them but the best fix is "don't generate one."
