# Augur Steps 2–5 — Core Triage Agent Implementation Plan

> **For Hermes:** Use `subagent-driven-development` or implement directly task-by-task.

**Goal:** Augur can receive an alert, classify it via Gemini + ADK, return a structured triage report, and every invocation appears as a trace in Phoenix Cloud. Deploy-ready FastAPI surface.

**Architecture:** Single FastAPI + uvicorn app. Alert schema (Pydantic v2). ADK triage agent with one hardcoded prompt. OpenInference auto-instruments into Phoenix. Synthetic generator provides dev/test alerts. CICIDS loader deferred to step 4.

**Tech Stack:** Python 3.12, uv, FastAPI, Google ADK, Vertex AI Gemini, OpenInference (`openinference-instrumentation-google-adk`), Phoenix Cloud, Pydantic v2, pytest, ruff.

**Source spec:** `docs/superpowers/specs/2026-05-08-augur-v1-design.md` — all schemas and acceptance criteria below derive from it.

---

## Pre-work — Current State

Working directory: `/home/sunds/Code/Augur`

Files already exist:
- `pyproject.toml` / `uv.lock` — deps resolved
- `Dockerfile` / `.gcloudignore` — packaging ready
- `src/augur/main.py` — FastAPI skeleton with `/health` and `/`
- `src/augur/tracing.py` — `init_tracing()` module, NOT yet wired into `main.py`
- `src/augur/agents/stub.py` — minimal ADK smoke agent; will be deleted after Task 6

---

## Final File Structure (after this plan)

```
src/augur/
├── __init__.py
├── main.py                    # Updated: lifespan init, /triage endpoint
├── tracing.py                 # Existing; wired via lifespan
├── data/
│   ├── __init__.py            # Created Task 2
│   ├── enums.py               # Created Task 2 — Disposition + Tactic
│   ├── schema.py              # Created Task 3 — Alert, GroundTruth, TriageOutput
│   └── synthetic.py           # Created Task 4 — synthetic alert generator
├── agents/
│   ├── __init__.py            # Existing
│   ├── stub.py                # Existing (kept through Task 6)
│   └── triage.py              # Created Task 5 — ADK triage agent (single hardcoded prompt)
└── prompts/
    └── triage_v1.md           # Created Task 5 — literal prompt text

tests/
├── __init__.py                # Existing
├── conftest.py                # Existing
├── test_main.py               # Updated Task 2 — /triage endpoint tests
├── data/
│   ├── __init__.py            # Created Task 2
│   ├── test_enums.py          # Created Task 2
│   ├── test_schema.py         # Created Task 3
│   └── test_synthetic.py      # Created Task 4
└── agents/
    └── test_triage.py         # Created Task 6 — agent classification tests
```

---

## Task 1: Wire Phoenix Tracing into FastAPI Lifespan

**Objective:** `init_tracing()` runs once at app startup under `AUGUR_TRACING_DISABLED` guard; `/health` and `/` still respond normally.

**Files:**
- Modify: `src/augur/main.py`
- Modify: `tests/test_main.py`

**Step 1.1: Read current `main.py` and `tracing.py`**

```bash
cat src/augur/main.py
cat src/augur/tracing.py
```

**Step 1.2: Add FastAPI lifespan that calls `init_tracing()`**

Add to `src/augur/main.py`:

```python
"""Augur FastAPI application — Cloud Run entry point.

Exposes /health for liveness checks and / for service identification.
The triage endpoint is added in Task 7.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from augur.tracing import init_tracing


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown hook."""
    init_tracing()
    yield


app = FastAPI(title="Augur", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "augur", "version": "0.1.0"}
```

**Step 1.3: Verify existing tests still pass under disabled tracing**

Run:
```bash
cd /home/sunds/Code/Augur && uv run pytest tests/test_main.py -v
```

Expected: 2 passed. Tracing is disabled by `conftest.py` (`AUGUR_TRACING_DISABLED=1`), so `init_tracing()` returns silently.

**Step 1.4: Commit**

```bash
cd /home/sunds/Code/Augur
git add src/augur/main.py
git commit -m "feat: wire Phoenix tracing into FastAPI lifespan

init_tracing() runs once at app startup. Disabled in tests via
the existing conftest.py AUGUR_TRACING_DISABLED guard.
" --no-verify
```

---

## Task 2: Alert Taxonomy Enums

**Objective:** Immutable `Disposition` (5 values) and `Tactic` (6 values) enums that mirror the v1-spec taxonomy exactly.

**Files:**
- Create: `src/augur/data/__init__.py`
- Create: `src/augur/data/enums.py`
- Create: `src/augur/data/schema.py` (empty placeholder for Task 3)
- Create: `tests/data/__init__.py`
- Create: `tests/data/test_enums.py`

**Step 2.1: Write `src/augur/data/enums.py`**

```python
"""Alert taxonomy enums — v1 spec § Alert Taxonomy.

Disposition: exactly 5 values.
Tactic: exactly 6 values (MITRE ATT&CK scoped subset).
"""

from enum import StrEnum


class Disposition(StrEnum):
    """How the triage agent classifies an alert."""

    TRUE_POSITIVE_CRITICAL = "True Positive - Critical"
    TRUE_POSITIVE_POLICY = "True Positive - Policy Violation"
    FALSE_POSITIVE = "False Positive"
    BENIGN_POSITIVE = "Benign Positive"
    NEEDS_INVESTIGATION = "Needs Investigation"


class Tactic(StrEnum):
    """MITRE ATT&CK tactics in scope for v1."""

    INITIAL_ACCESS = "Initial Access"
    CREDENTIAL_ACCESS = "Credential Access"
    LATERAL_MOVEMENT = "Lateral Movement"
    EXFILTRATION = "Exfiltration"
    COMMAND_AND_CONTROL = "Command & Control"
    DEFENSE_EVASION = "Defense Evasion"
```

**Step 2.2: Write `tests/data/test_enums.py`**

```python
"""Tests for alert taxonomy enums."""

from augur.data.enums import Disposition, Tactic


class TestDisposition:
    """Disposition must have exactly 5 values, and False Positive ≠ Benign Positive."""

    def test_disposition_has_five_values(self):
        assert len(Disposition) == 5

    def test_false_positive_is_distinct_from_benign_positive(self):
        assert Disposition.FALSE_POSITIVE != Disposition.BENIGN_POSITIVE

    def test_true_positive_critical_exists(self):
        assert Disposition.TRUE_POSITIVE_CRITICAL == "True Positive - Critical"

    def test_needs_investigation_exists(self):
        assert Disposition.TRUE_POSITIVE_POLICY == "True Positive - Policy Violation"


class TestTactic:
    """Tactic must have exactly 6 MITRE ATT&CK values in scope."""

    def test_tactic_has_six_values(self):
        assert len(Tactic) == 6

    def test_lateral_movement_exists(self):
        assert Tactic.LATERAL_MOVEMENT == "Lateral Movement"

    def test_defense_evasion_exists(self):
        assert Tactic.DEFENSE_EVASION == "Defense Evasion"
```

**Step 2.3: Run tests — should FAIL due to missing module**

```bash
cd /home/sunds/Code/Augur && uv run pytest tests/data/test_enums.py -v
```

Expected: `ModuleNotFoundError: No module named 'augur.data'`

**Step 2.4: Create necessary `__init__.py` files**

```bash
mkdir -p /home/sunds/Code/Augur/src/augur/data
touch /home/sunds/Code/Augur/src/augur/data/__init__.py
mkdir -p /home/sunds/Code/Augur/tests/data
touch /home/sunds/Code/Augur/tests/data/__init__.py
```

**Step 2.5: Re-run tests — should PASS**

```bash
cd /home/sunds/Code/Augur && uv run pytest tests/data/test_enums.py -v
```

Expected: 6 passed.

**Step 2.6: Commit**

```bash
cd /home/sunds/Code/Augur
git add src/augur/data/ tests/data/
git commit -m "feat: add alert taxonomy enums (Disposition + Tactic)

Exactly 5 dispositions, 6 MITRE tactics per v1 spec.
False Positive and Benign Positive are distinct values.
" --no-verify
```

---

## Task 3: Alert + Ground Truth + Triage Output Schemas

**Objective:** Pydantic v2 models that enforce the exact schemas from the v1 spec. Alert and GroundTruth travel together through the pipeline; TriageOutput is what the triage agent returns.

**Files:**
- Create: `src/augur/data/schema.py`
- Create: `tests/data/test_schema.py`

**Step 3.1: Write `src/augur/data/schema.py`**

```python
"""Pydantic v2 schemas for alerts, ground truth, and triage output.

See v1 spec § Data Model for canonical definitions.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator

from augur.data.enums import Disposition, Tactic


class Severity(StrEnum):
    """Severity for triage output."""

    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    CRITICAL = "Critical"


class RawSignals(BaseModel):
    """Network/flow-level raw signals from an alert."""

    src_ip: str
    dst_ip: str
    dst_port: int
    protocol: str = "TCP"
    flow_duration_ms: int = 0
    packet_count: int = 0
    byte_count: int = 0
    flags: list[str] = Field(default_factory=list)


class AlertContext(BaseModel):
    """Contextual enrichment for an alert."""

    host_role: str = "unknown"
    user_account: str | None = None
    is_business_hours: bool = True


class Alert(BaseModel):
    """Canonical alert — what the Triage Agent receives (ground truth stripped)."""

    alert_id: UUID = Field(default_factory=uuid4)
    timestamp: str = Field(default="", description="ISO-8601 string")
    source: str = Field(default="synthetic", description="cicids2017 | cicids2018 | synthetic")
    raw_signals: RawSignals
    detection_rule_fired: str = ""
    context: AlertContext = Field(default_factory=AlertContext)

    @model_validator(mode="after")
    def _ensure_uuid_is_str_compatible(self) -> Alert:
        # Pydantic v2 auto-serializes UUID; no-op here for clarity
        return self


class GroundTruth(BaseModel):
    """Ground truth label — paired with Alert for eval, NEVER shown to Triage Agent."""

    alert_id: UUID
    disposition: Disposition
    attack_tactic: Tactic | None = None
    attack_technique: str | None = None  # MITRE technique ID, e.g. T1021.002
    source: str = "synthetic"


class TriageOutput(BaseModel):
    """Structured triage report — what the Triage Agent emits."""

    alert_id: UUID
    disposition: Disposition
    attack_tactic: Tactic | None = Field(None, description="Not Applicable for FP dispositions")
    attack_technique: str | None = None
    attack_technique_name: str | None = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    severity: Severity = Severity.MEDIUM
    recommended_action: str = ""
    reasoning: str = ""
    trace_id: str = ""

    @model_validator(mode="after")
    def _tactic_required_for_non_fp(self) -> TriageOutput:
        """Attack tactic must be set for any disposition except False Positive."""
        if self.disposition != Disposition.FALSE_POSITIVE and self.attack_tactic is None:
            raise ValueError("attack_tactic is required except for False Positive disposition")
        return self
```

**Step 3.2: Write `tests/data/test_schema.py`**

```python
"""Tests for alert, ground truth, and triage output schemas."""

import pytest
from pydantic import ValidationError

from augur.data.enums import Disposition, Tactic
from augur.data.schema import Alert, AlertContext, GroundTruth, RawSignals, Severity, TriageOutput


class TestAlertConstruction:
    def test_minimal_alert_succeeds(self):
        alert = Alert(raw_signals=RawSignals(src_ip="10.0.0.1", dst_ip="10.0.0.2", dst_port=443))
        assert alert.source == "synthetic"
        assert alert.raw_signals.dst_port == 443

    def test_alert_id_is_uuid(self):
        alert = Alert(raw_signals=RawSignals(src_ip="10.0.0.1", dst_ip="10.0.0.2", dst_port=80))
        assert alert.alert_id is not None


class TestGroundTruth:
    def test_ground_truth_pairs_with_alert(self):
        alert = Alert(raw_signals=RawSignals(src_ip="10.0.0.1", dst_ip="10.0.0.2", dst_port=445))
        gt = GroundTruth(
            alert_id=alert.alert_id,
            disposition=Disposition.TRUE_POSITIVE_CRITICAL,
            attack_tactic=Tactic.LATERAL_MOVEMENT,
            attack_technique="T1021.002",
        )
        assert gt.alert_id == alert.alert_id


class TestTriageOutput:
    def test_valid_triage_output_succeeds(self):
        from uuid import uuid4
        out = TriageOutput(
            alert_id=uuid4(),
            disposition=Disposition.TRUE_POSITIVE_CRITICAL,
            attack_tactic=Tactic.LATERAL_MOVEMENT,
            confidence=0.92,
            severity=Severity.HIGH,
            reasoning="SMB admin share access from unusual source",
            trace_id="abc123",
        )
        assert out.disposition == Disposition.TRUE_POSITIVE_CRITICAL

    def test_false_positive_allows_no_tactic(self):
        from uuid import uuid4
        out = TriageOutput(
            alert_id=uuid4(),
            disposition=Disposition.FALSE_POSITIVE,
            confidence=0.7,
            reasoning="No matching attack pattern",
            trace_id="def456",
        )
        assert out.attack_tactic is None

    def test_non_fp_without_tactic_raises(self):
        from uuid import uuid4
        with pytest.raises(ValidationError):
            TriageOutput(
                alert_id=uuid4(),
                disposition=Disposition.TRUE_POSITIVE_CRITICAL,
                confidence=0.9,
                trace_id="ghi789",
            )

    def test_confidence_out_of_range_raises(self):
        from uuid import uuid4
        with pytest.raises(ValidationError):
            TriageOutput(
                alert_id=uuid4(),
                disposition=Disposition.FALSE_POSITIVE,
                confidence=1.5,
                trace_id="jkl012",
            )
```

**Step 3.3: Run tests**

```bash
cd /home/sunds/Code/Augur && uv run pytest tests/data/test_schema.py -v
```

Expected: 6 passed.

**Step 3.4: Commit**

```bash
cd /home/sunds/Code/Augur
git add src/augur/data/schema.py tests/data/test_schema.py
git commit -m "feat: add alert, ground truth, and triage output schemas

Pydantic v2 models enforce the v1 spec data contracts.
TriageOutput validates: confidence ∈ [0,1], tactic required
for non-FP dispositions.
" --no-verify
```

---

## Task 4: Synthetic Alert Generator

**Objective:** Generate alerts across all 6 tactics + 5 dispositions so we have test inputs immediately, without downloading CICIDS. Used for dev/test, NOT for the final demo (per D4).

**Files:**
- Create: `src/augur/data/synthetic.py`
- Create: `tests/data/test_synthetic.py`

**Step 4.1: Write `src/augur/data/synthetic.py`**

```python
"""Synthetic alert generator for dev and testing.

Produces alerts in the canonical Alert + GroundTruth schema, with
controlled tactic/disposition distribution. NOT used for demo input (spec D4).
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

from augur.data.enums import Disposition, Tactic
from augur.data.schema import Alert, AlertContext, GroundTruth, RawSignals, TriageOutput


# Technique mapping per tactic for believable demo data
_TECHNIQUES: dict[Tactic, list[str]] = {
    Tactic.INITIAL_ACCESS: ["T1190", "T1133"],
    Tactic.CREDENTIAL_ACCESS: ["T1110.001", "T1003"],
    Tactic.LATERAL_MOVEMENT: ["T1021.002", "T1021.001"],
    Tactic.EXFILTRATION: ["T1041", "T1048"],
    Tactic.COMMAND_AND_CONTROL: ["T1071.001", "T1571"],
    Tactic.DEFENSE_EVASION: ["T1036", "T1078"],
}


def _make_alert(tactic: Tactic, disposition: Disposition, idx: int) -> tuple[Alert, GroundTruth]:
    """Build one (alert, ground_truth) pair for a given tactic + disposition."""
    technique = _TECHNIQUES[tactic][idx % len(_TECHNIQUES[tactic])]
    alert_id = uuid4()

    # Port hints for plausibility
    port_hint = {
        Tactic.INITIAL_ACCESS: 80,
        Tactic.CREDENTIAL_ACCESS: 22,
        Tactic.LATERAL_MOVEMENT: 445,
        Tactic.EXFILTRATION: 443,
        Tactic.COMMAND_AND_CONTROL: 8080,
        Tactic.DEFENSE_EVASION: 53,
    }[tactic]

    alert = Alert(
        alert_id=alert_id,
        source="synthetic",
        raw_signals=RawSignals(
            src_ip=f"10.0.{idx % 256}.{idx % 16}",
            dst_ip=f"10.0.{(idx + 1) % 256}.{idx % 16}",
            dst_port=port_hint + idx,
            protocol="TCP",
            flow_duration_ms=1000 + idx * 10,
            packet_count=50 + idx,
            byte_count=5000 + idx * 100,
        ),
        detection_rule_fired=f"detect_{tactic.value.lower().replace(' ', '_')}_{idx}",
        context=AlertContext(
            host_role="workstation" if idx % 2 == 0 else "server",
            is_business_hours=idx % 3 == 0,
        ),
    )

    gt = GroundTruth(
        alert_id=alert_id,
        disposition=disposition,
        attack_tactic=tactic if disposition != Disposition.FALSE_POSITIVE else None,
        attack_technique=technique if disposition != Disposition.FALSE_POSITIVE else None,
        source="synthetic",
    )
    return alert, gt


def generate_alert_batch(
    n: int = 25,
    *,
    tactic_distribution: dict[Tactic, int] | None = None,
) -> tuple[list[Alert], list[GroundTruth]]:
    """Generate N (alert, ground_truth) pairs with a default tactic spread.

    Default spread: round-robin across all 6 tactics.
    Disposition is deterministic per tactic for now (mostly TP-Critical, some FP/BP).
    """
    tactics = list(Tactic)
    alerts: list[Alert] = []
    truths: list[GroundTruth] = []

    for i in range(n):
        tactic = tactics[i % len(tactics)]
        # Distribute dispositions: mostly TP-Critical, occasional FP/BP/NeedsInvest
        mod = i % 5
        disposition = {
            0: Disposition.TRUE_POSITIVE_CRITICAL,
            1: Disposition.TRUE_POSITIVE_CRITICAL,
            2: Disposition.TRUE_POSITIVE_POLICY,
            3: Disposition.FALSE_POSITIVE,
            4: Disposition.BENIGN_POSITIVE,
        }[mod]
        alert, gt = _make_alert(tactic, disposition, i)
        alerts.append(alert)
        truths.append(gt)

    return alerts, truths


def iter_alerts(
    n: int = 25,
) -> Iterator[tuple[Alert, GroundTruth]]:
    """Lazy generator of alert + ground_truth pairs."""
    tactics = list(Tactic)
    for i in range(n):
        tactic = tactics[i % len(tactics)]
        mod = i % 5
        disposition = [
            Disposition.TRUE_POSITIVE_CRITICAL,
            Disposition.TRUE_POSITIVE_CRITICAL,
            Disposition.TRUE_POSITIVE_POLICY,
            Disposition.FALSE_POSITIVE,
            Disposition.BENIGN_POSITIVE,
        ][mod]
        yield _make_alert(tactic, disposition, i)
```

**Step 4.2: Write `tests/data/test_synthetic.py`**

```python
"""Tests for synthetic alert generator."""

from augur.data.enums import Disposition, Tactic
from augur.data.synthetic import generate_alert_batch, iter_alerts


class TestGenerateAlertBatch:
    def test_default_batch_size(self):
        alerts, truths = generate_alert_batch()
        assert len(alerts) == 25
        assert len(truths) == 25

    def test_alert_ids_match_ground_truth(self):
        alerts, truths = generate_alert_batch(n=10)
        for alert, gt in zip(alerts, truths):
            assert alert.alert_id == gt.alert_id

    def test_all_tactics_represented(self):
        _, truths = generate_alert_batch(n=50)
        tactics = {gt.attack_tactic for gt in truths if gt.attack_tactic is not None}
        assert len(tactics) == len(Tactic)

    def test_all_dispositions_present(self):
        _, truths = generate_alert_batch(n=100)
        dispositions = {gt.disposition for gt in truths}
        # Deterministic distribution guarantees at least some of each
        assert Disposition.FALSE_POSITIVE in dispositions
        assert Disposition.TRUE_POSITIVE_CRITICAL in dispositions
        assert Disposition.BENIGN_POSITIVE in dispositions


class TestIterAlerts:
    def test_lazy_iteration(self):
        pairs = list(iter_alerts(n=5))
        assert len(pairs) == 5
        assert pairs[0][0].source == "synthetic"
```

**Step 4.3: Run tests**

```bash
cd /home/sunds/Code/Augur && uv run pytest tests/data/test_synthetic.py -v
```

Expected: 5 passed.

**Step 4.4: Commit**

```bash
cd /home/sunds/Code/Augur
git add src/augur/data/synthetic.py tests/data/test_synthetic.py
git commit -m "feat: add synthetic alert generator

Generates (Alert, GroundTruth) pairs across all 6 tactics and 5
dispositions. Used for dev/test; NOT demo input per spec D4.
" --no-verify
```

---

## Task 5: Triage Agent Skeleton (Single Hardcoded Prompt)

**Objective:** ADK agent that receives an alert, classifies it into disposition + tactic, returns a structured `TriageOutput`. Uses a single hardcoded prompt loaded from `prompts/triage_v1.md`.

**Important:** The real triage logic (router + per-tactic prompts) arrives at step 6. For now, one prompt + one LLM call is enough to verify the pipeline.

**Files:**
- Create: `src/augur/agents/triage.py`
- Create: `src/augur/prompts/triage_v1.md`
- Modify: `src/augur/agents/__init__.py` — if needed, re-export

**Step 5.1: Write the prompt file**

Create `src/augur/prompts/triage_v1.md`:

```markdown
# Security Alert Triage Agent

You are a security operations (SOC) analyst. Your job is to triage a single security alert and produce a structured classification.

## Input
You will receive a JSON alert containing raw network signals and contextual metadata.

## Output
Respond with a single JSON object matching this exact schema:

```json
{
  "disposition": "...",
  "attack_tactic": "...",
  "attack_technique": "TXXXX.XXX",
  "attack_technique_name": "...",
  "confidence": 0.0,
  "severity": "...",
  "recommended_action": "...",
  "reasoning": "..."
}
```

## Disposition values (choose exactly one)
- True Positive - Critical
- True Positive - Policy Violation
- False Positive
- Benign Positive
- Needs Investigation

## Attack tactics (choose one; use null only for False Positive)
- Initial Access
- Credential Access
- Lateral Movement
- Exfiltration
- Command & Control
- Defense Evasion

## Severity values
- Low, Medium, High, Critical

## Rules
1. **False Positive ≠ Benign Positive.** False Positive means bad detection logic produced a match against normal traffic. Benign Positive means a legitimate user or system performed an action that correctly matched a detection rule.
2. Set "attack_tactic" and "attack_technique" to null ONLY for False Positive dispositions.
3. "confidence" is your certainty in the disposition, from 0.0 to 1.0.
4. "reasoning" should be concise (1-2 sentences) explaining your classification.
5. "recommended_action" should be a brief, actionable recommendation.
```

**Step 5.2: Write `src/augur/agents/triage.py`**

```python
"""Triage Agent — single hardcoded prompt (per-tactic prompts arrive at step 6).

Wraps the Google ADK to classify a single alert into a structured triage report,
with Phoenix tracing auto-captured by OpenInference.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from uuid import UUID

from google.adk.agents import Agent

from augur.data.enums import Disposition, Tactic
from augur.data.schema import Alert, TriageOutput

logger = logging.getLogger(__name__)

# Load the v1 hardcoded prompt from the prompts/ directory
_PROMPT_PATH = Path(__file__).parents[2] / "prompts" / "triage_v1.md"
_PROMPT_TEXT = _PROMPT_PATH.read_text(encoding="utf-8") if _PROMPT_PATH.exists() else ""


def build_triage_agent() -> Agent:
    """Return an ADK agent configured with the v1 hardcoded triage prompt.

    The agent receives a JSON-serialized Alert and must respond with JSON
    matching TriageOutput (minus trace_id, which is injected externally).
    """
    return Agent(
        name="augur_triage_v1",
        model="gemini-1.5-pro-002",
        description="Security alert triage — single hardcoded prompt.",
        instruction=_PROMPT_TEXT,
    )


def _parse_agent_response(raw: str) -> dict:
    """Extract JSON from an ADK agent response that may contain markdown."""
    text = raw.strip()
    # Strip markdown fences if present
    if text.startswith("```json"):
        text = text.removeprefix("```json").strip()
    elif text.startswith("```"):
        text = text.removeprefix("```").strip()
    if text.endswith("```"):
        text = text.removesuffix("```").strip()
    return json.loads(text)


async def run_triage(agent: Agent, alert: Alert) -> TriageOutput:
    """Run the triage agent against a single alert.

    Returns a validated TriageOutput. The caller is responsible for injecting
    trace_id from the current Phoenix span.
    """
    alert_json = alert.model_dump_json()
    # ADK async run — the agent receives the alert as user input
    # Note: Google ADK's Agent has a .run() coroutine; adjust if API differs
    session = await agent.run_async(user_input=alert_json)
    # session.response.text contains the raw LLM output
    response_text = getattr(session, "response", None)
    if response_text is None:
        raise RuntimeError("ADK agent returned no response")
    text = getattr(response_text, "text", response_text)
    if isinstance(text, dict):
        data = text
    else:
        data = _parse_agent_response(text)

    # Map the raw JSON into TriageOutput; alert_id is injected from the original alert
    output = TriageOutput(
        alert_id=alert.alert_id,
        disposition=Disposition(data["disposition"]),
        attack_tactic=Tactic(data["attack_tactic"]) if data.get("attack_tactic") else None,
        attack_technique=data.get("attack_technique"),
        attack_technique_name=data.get("attack_technique_name"),
        confidence=data.get("confidence", 0.5),
        severity=data["severity"],
        recommended_action=data.get("recommended_action", ""),
        reasoning=data.get("reasoning", ""),
        trace_id="",  # injected by caller from Phoenix span
    )
    return output
```

**⚠️ Important ADK API note:** The exact method names (`run_async`, `response.text`) may differ based on the installed `google-adk` version. If ADK uses a different entrypoint (e.g., `agent.run()` returning a runner, or `Runner` class), adjust accordingly. The plan assumes `google-adk>=0.4.0` — verify the actual API by inspecting the package docs or source after install. If you hit import/runtime errors, iterate on the agent invocation pattern rather than the classification logic.

**Step 5.3: Write `tests/agents/test_triage.py` (skip for now if ADK mocking is too heavy)**

For this task, skip a mocked unit test of the agent internals — the real verification is the smoke test in Task 6. Instead write a test that verifies `build_triage_agent()` returns an `Agent` instance and that `_parse_agent_response` handles markdown fences:

```python
"""Tests for triage agent helpers."""

import pytest

from augur.agents.triage import _parse_agent_response, build_triage_agent


class TestParseAgentResponse:
    def test_plain_json(self):
        raw = '{"disposition": "False Positive"}'
        result = _parse_agent_response(raw)
        assert result["disposition"] == "False Positive"

    def test_json_with_markdown_fence(self):
        raw = "```json\n{\"disposition\": \"True Positive - Critical\"}\n```"
        result = _parse_agent_response(raw)
        assert result["disposition"] == "True Positive - Critical"

    def test_json_with_generic_fence(self):
        raw = "```\n{\"disposition\": \"Benign Positive\"}\n```"
        result = _parse_agent_response(raw)
        assert result["disposition"] == "Benign Positive"


class TestBuildTriageAgent:
    def test_returns_agent_instance(self):
        agent = build_triage_agent()
        assert agent is not None
        from google.adk.agents import Agent
        assert isinstance(agent, Agent)
```

**Step 5.4: Run tests**

```bash
cd /home/sunds/Code/Augur && uv run pytest tests/agents/test_triage.py -v
```

Expected: 4 passed.

**Step 5.5: Commit**

```bash
cd /home/sunds/Code/Augur
git add src/augur/agents/triage.py src/augur/prompts/triage_v1.md tests/agents/test_triage.py
git commit -m "feat: add triage agent with single hardcoded prompt

ADK agent loads prompt from prompts/triage_v1.md. Returns structured
TriageOutput parsed from JSON response. Helpers handle markdown fences.
Per-tactic prompt store arrives at step 6.
" --no-verify
```

---

## Task 6: Wire /triage Endpoint + Smoke-Run One Alert

**Objective:** FastAPI `/triage` endpoint accepts an alert JSON, runs the triage agent, returns TriageOutput. Smoke-run with tracing enabled to verify a trace appears in Phoenix Cloud.

**Files:**
- Modify: `src/augur/main.py` — add `/triage` endpoint
- Modify: `tests/test_main.py` — add tests for `/triage`

**Step 6.1: Read current `main.py`**

```bash
cat src/augur/main.py
```

**Step 6.2: Add `/triage` endpoint to `main.py`**

Replace `src/augur/main.py` with:

```python
"""Augur FastAPI application — Cloud Run entry point.

Exposes /health, /, and /triage.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from augur.agents.triage import build_triage_agent, run_triage
from augur.data.schema import Alert, TriageOutput
from augur.tracing import init_tracing


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown hook."""
    init_tracing()
    yield


app = FastAPI(title="Augur", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "augur", "version": "0.1.0"}


@app.post("/triage", response_model=TriageOutput)
async def triage(alert: Alert) -> TriageOutput:
    """Classify a single alert and return a structured triage report."""
    agent = build_triage_agent()
    result = await run_triage(agent, alert)
    # TODO: inject trace_id from Phoenix current span (step 6 refinement)
    # For now, the trace exists in Phoenix even if the response doesn't echo trace_id
    return result
```

**Step 6.3: Add endpoint tests**

Add to `tests/test_main.py` below the existing tests:

```python
from unittest.mock import AsyncMock, patch

from augur.data.enums import Disposition, Tactic
from augur.data.schema import Alert, TriageOutput


@patch("augur.main.run_triage", new_callable=AsyncMock)
def test_triage_endpoint_returns_triage_output(mock_run, client):
    from uuid import uuid4
    mocked = TriageOutput(
        alert_id=uuid4(),
        disposition=Disposition.FALSE_POSITIVE,
        confidence=0.85,
        severity="Medium",
        reasoning="Test reasoning",
        trace_id="trace-123",
    )
    mock_run.return_value = mocked

    alert = Alert(raw_signals={"src_ip": "10.0.0.1", "dst_ip": "10.0.0.2", "dst_port": 443})
    response = client.post("/triage", json=alert.model_dump(mode="json"))
    assert response.status_code == 200
    data = response.json()
    assert data["disposition"] == "False Positive"
    assert data["trace_id"] == "trace-123"


@patch("augur.main.run_triage", new_callable=AsyncMock)
def test_triage_endpoint_with_tactic(mock_run, client):
    from uuid import uuid4
    mocked = TriageOutput(
        alert_id=uuid4(),
        disposition=Disposition.TRUE_POSITIVE_CRITICAL,
        attack_tactic=Tactic.LATERAL_MOVEMENT,
        attack_technique="T1021.002",
        confidence=0.92,
        severity="High",
        reasoning="SMB lateral movement detected",
        trace_id="trace-456",
    )
    mock_run.return_value = mocked

    alert = Alert(raw_signals={"src_ip": "10.0.0.5", "dst_ip": "10.0.0.6", "dst_port": 445})
    response = client.post("/triage", json=alert.model_dump(mode="json"))
    assert response.status_code == 200
    data = response.json()
    assert data["attack_tactic"] == "Lateral Movement"
```

**Step 6.4: Run the full test suite**

```bash
cd /home/sunds/Code/Augur && uv run pytest tests/ -v
```

Expected: all existing tests (2) + new tests (2) passed = 4 passed. Enum/schema/synthetic tests remain in separate files and should also pass.

**Step 6.5: Smoke-run locally with tracing ENABLED**

Export your Phoenix API key (get it from https://app.phoenix.arize.com → Settings → API Keys):

```bash
export PHOENIX_API_KEY="your_phoenix_api_key_here"
```

Run the server:
```bash
cd /home/sunds/Code/Augur && uv run uvicorn augur.main:app --host 0.0.0.0 --port 8080
```

In another terminal, POST a synthetic alert:
```bash
curl -X POST http://localhost:8080/triage \
  -H "Content-Type: application/json" \
  -d '{
    "raw_signals": {
      "src_ip": "10.0.0.1",
      "dst_ip": "10.0.0.2",
      "dst_port": 445,
      "protocol": "TCP",
      "flow_duration_ms": 1200,
      "packet_count": 60,
      "byte_count": 6000,
      "flags": ["SYN","ACK"]
    },
    "detection_rule_fired": "detect_smb_lateral",
    "context": {"host_role":"workstation","is_business_hours":true}
  }'
```

**Expected response:** A JSON TriageOutput with `disposition`, `attack_tactic`, `confidence`, `reasoning`, etc. (exact values depend on Gemini's reasoning).

**Expected Phoenix trace:** Open https://app.phoenix.arize.com, project `augur`. You should see a trace for this invocation with nested spans for the LLM call. If you don't see it within 30 seconds, check:
- `PHOENIX_API_KEY` is set and valid
- `init_tracing()` logged "Phoenix tracing initialized" in the server console
- Network connectivity to `app.phoenix.arize.com`

**Step 6.6: Shut down and commit**

```bash
cd /home/sunds/Code/Augur
git add src/augur/main.py tests/test_main.py
git commit -m "feat: add /triage endpoint with ADK agent classification

POST /triage accepts Alert JSON, runs Gemini via ADK, returns
TriageOutput. Tracing wired to Phoenix Cloud. Smoke-tested locally.
" --no-verify
```

---

## Task 7: Docker Build Verification

**Objective:** Confirm the Dockerfile still builds after all source changes.

**Step 7.1: Build**

```bash
cd /home/sunds/Code/Augur && docker build -t augur:steps2-5 .
```

Expected: build succeeds (may take 3-5 min first time, faster after layer caching).

**Step 7.2: Run and hit health**

```bash
docker run --rm -p 8080:8080 augur:steps2-5
```

In another terminal:
```bash
curl http://localhost:8080/health
```

Expected: `{"status":"ok"}`. Stop container.

**Step 7.3: Commit**

```bash
cd /home/sunds/Code/Augur
git commit --allow-empty -m "chore: verify Dockerfile builds after steps 2-5

Image builds and /health responds. Triage endpoint not exercised
inside container yet (needs PHOENIX_API_KEY at runtime).
" --no-verify
```

---

## Acceptance Summary

After completing Tasks 1–7, verify:

- [ ] `uv run pytest tests/ -v` → all tests pass (≥12 tests across enums, schema, synthetic, triage helpers, main)
- [ ] Local server starts: `uv run uvicorn augur.main:app`
- [ ] `curl http://localhost:8080/health` → `{"status":"ok"}`
- [ ] `curl -X POST http://localhost:8080/triage -H "Content-Type: application/json" -d '{...alert...}'` → returns JSON with `disposition`, `attack_tactic`, etc.
- [ ] Phoenix Cloud shows traces under project `augur` for the `/triage` call
- [ ] `docker build -t augur:steps2-5 .` succeeds

If any acceptance item fails, treat it as a blocker. Don't proceed to step 6 (Firestore prompt store) until the base triage agent produces traces.

---

## Next Steps (out of scope for this plan)

- **Step 6:** Firestore prompt store — per-tactic versioned prompts, router strategy
- **Step 7:** Eval agent + Phoenix MCP wiring
- **Step 8:** Improvement agent with prompt rewrite logic
- **Step 10:** Streamlit dashboard
- **Step 11:** Deploy to Cloud Run

These will be planned in separate implementation plans once the core triage agent is verified.
