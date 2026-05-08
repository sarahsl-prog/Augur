---
name: augur-mitre-taxonomy
description: Use whenever working with Augur alerts, dispositions, MITRE ATT&CK classifications, ground-truth labels, structured triage output, eval logic, or CICIDS dataset preprocessing. Triggers on these words — "disposition", "True Positive", "False Positive", "Benign Positive", "TP", "FP", "BP", "Needs Investigation", "tactic", "technique", "Initial Access", "Credential Access", "Lateral Movement", "Exfiltration", "Command & Control", "Defense Evasion", "T1190", "T1110", "T1021", "T1041", "T1071", "T1036", "MITRE", "ATT&CK", "CICIDS", "ground truth", "alert schema", "alert id", "triage report". Owns the canonical 5-disposition enum, the 6-tactic scoped subset, the FP-vs-BP semantic distinction, and the structured output JSON schema. Reference whenever creating prompts, eval scripts, or label assignments.
---

# Augur MITRE & Disposition Taxonomy

This skill owns the **canonical taxonomy** for Augur. The README defines the same content; if anything below conflicts with the README, the README wins and this skill must be updated.

## The 5 Dispositions

The triage agent emits exactly one of these per alert. Do not invent additional values. Do not collapse FP and BP — see "FP vs BP" below.

| Disposition | Meaning | When to assign |
|-------------|---------|----------------|
| `True Positive - Critical` | Real threat, immediate IR required | Confirmed malicious activity, active compromise indicators |
| `True Positive - Policy Violation` | Real, but not emergency. Needs remediation, not IR. | Confirmed unauthorized activity that isn't an active attack — e.g., user violating data policy |
| `False Positive` | Bad detection logic or bad data. Pure noise. | Detection rule fired on activity that does not match what the rule was designed to catch |
| `Benign Positive` | Legitimate activity that *correctly* triggered the rule. | Detection rule worked as designed, but the activity is authorized — e.g., sysadmin running an admin tool |
| `Needs Investigation` | Ambiguous, escalate to senior analyst | Insufficient signal to disposition confidently in either direction |

## FP vs BP — The Distinction That Signals Domain Expertise

This is the most-judged distinction in the project. Get it wrong in prompts, ground-truth labels, or eval logic and the demo loses credibility.

> **False Positive**: the detection rule itself is bad. It fired on traffic that doesn't actually match the threat behavior the rule was built for. Tune the rule.
>
> **Benign Positive**: the detection rule is good. It fired on activity that *does* match the threat pattern — but the activity was authorized. The user/system was supposed to do that. Tune the *exception list*, not the rule.

**Canonical example** (use this in prompts):

> A SOC alert fires on "PowerShell launching a remote SMB session to a domain controller."
> - If a sysadmin ran a legitimate admin script that did this → **Benign Positive**. The detection logic is correct; the activity is allowed.
> - If the alert fired on a user's harmless `dir \\share` command that doesn't actually open a remote SMB session (rule has buggy logic) → **False Positive**. The rule is broken.

When writing per-tactic prompts, include this distinction explicitly with at least one example each per tactic. The improvement agent will rewrite around this distinction; protect it.

## The 6 Scoped MITRE ATT&CK Tactics

Augur scopes itself to these 6 tactics. Do not extend without README approval — the demo's precision/recall numbers depend on a tight scope.

| Tactic | Example Technique ID | Example Technique Name | Why Augur included it |
|--------|---------------------|------------------------|------------------------|
| Initial Access | T1190 | Exploit Public-Facing Application | Clear signal in network logs; unambiguous in CICIDS |
| Credential Access | T1110 | Brute Force | CICIDS has rich coverage |
| Lateral Movement | T1021 | Remote Services (e.g., T1021.002 SMB/Windows Admin Shares) | **Demo's target failure-then-fix tactic.** High FP/BP confusion in real SOCs |
| Exfiltration | T1041 | Exfiltration Over C2 Channel | Measurable in flow data |
| Command & Control | T1071 | Application Layer Protocol | Benign-positive heavy — interesting eval material |
| Defense Evasion | T1036 | Masquerading | Tricky to disposition; good for showing improvement loop value |

**Lateral Movement is the demo target.** When tuning prompts, prioritize Lateral Movement quality — the demo arc requires the agent to *visibly fail* on LM at first run, then *visibly improve* after the rewrite.

## Structured Triage Output Schema

Every triage agent invocation must produce this shape. Validate at the boundary — don't trust the LLM to keep the keys stable.

```json
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
```

Field rules:
- `disposition`: one of the 5 values above (exact string match).
- `attack_tactic`: one of the 6 tactics above. Use "Not Applicable" only if disposition is `False Positive` — i.e., there is no real attack, so no tactic.
- `attack_technique` / `attack_technique_name`: must come from the public MITRE ATT&CK enterprise matrix. Don't invent IDs.
- `confidence`: float ∈ [0, 1].
- `severity`: one of `Low`, `Medium`, `High`, `Critical`.
- `trace_id`: the Phoenix trace ID for this invocation. Required — without it, the eval agent can't correlate ground truth back to the trace for the improvement loop.

## CICIDS → Augur Schema Mapping

CICIDS2017/2018 is the credibility data source. The synthetic generator must produce the same schema for ground-truth labels to be comparable.

Starting subset (build steps 4-5): **Brute Force + Lateral Movement**.

CICIDS attack labels map to Augur tactics like this:

| CICIDS attack label | Augur tactic | Notes |
|--------------------|---------------|-------|
| `FTP-Patator`, `SSH-Patator` | Credential Access (T1110) | Brute force |
| `Web Attack – Brute Force` | Credential Access (T1110) | |
| `Infiltration` | Lateral Movement (T1021 family) | Verify per-flow |
| `Botnet` | Command & Control (T1071) | |
| `DDoS`, `DoS *` | **Out of scope** | Not in the 6 tactics — drop or relabel |
| `PortScan` | **Out of scope** for this hackathon | Reconnaissance is excluded |
| Benign | Ground-truth disposition is `Benign Positive` if the rule fires; usually filtered out | |

Document the mapping decisions in the preprocessing script. Future-Claude will need this when adding more CICIDS coverage.

## Ground Truth Format

Each preprocessed alert must travel with its ground truth label so the eval agent can score traces:

```json
{
  "alert": { /* alert payload the triage agent sees */ },
  "ground_truth": {
    "disposition": "True Positive - Critical",
    "attack_tactic": "Credential Access",
    "attack_technique": "T1110.001"
  },
  "source": "cicids2017" // or "synthetic"
}
```

The triage agent must NOT see `ground_truth` at inference time. Strip it before passing to the agent; pass only the `alert` field.

## Per-Tactic Prompt Versioning

Prompts are stored per tactic in Firestore (see `augur-adk-patterns` for the schema). The taxonomy here drives what prompts must exist:

- One prompt per tactic from the 6 above = **6 versioned prompts minimum**.
- Plus one "router/dispatcher" prompt that picks which tactic to use, OR a unified prompt — design choice deferred to step 6.
- The improvement agent rewrites *one tactic's prompt at a time* (whichever the eval flagged), not the global system prompt.
