"""Shared theme, data, and helpers for the Augur multi-page Streamlit dashboard."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import streamlit as st

# ---------------------------------------------------------------------------
# Theme constants
# ---------------------------------------------------------------------------

_PURPLE = "#7b2d8e"
_DARK_PURPLE = "#3a0a4e"
_CYAN = "#00e5ff"
_BLACK = "#0a0a0a"
_PINK = "#ff6b8a"
_GREEN = "#00ff9f"
_WHITE = "#f0f0f0"
_DARK_GREY = "#111111"

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

def page_config() -> None:
    st.set_page_config(
        page_title="Augur Dashboard",
        page_icon="\U0001f52e",
        layout="wide",
        initial_sidebar_state="expanded",
    )


# ---------------------------------------------------------------------------
# Global CSS
# ---------------------------------------------------------------------------

def inject_theme() -> None:
    st.markdown(
        f"""
        <style>
        .stApp {{
            background-color: {_BLACK};
            color: {_WHITE};
        }}
        section[data-testid="stSidebar"] {{
            background-color: {_DARK_GREY};
        }}
        section[data-testid="stSidebar"] * {{
            color: {_WHITE} !important;
        }}
        h1, h2, h3, .stMarkdown h1, .stMarkdown h2, .stMarkdown h3 {{
            color: {_WHITE} !important;
        }}
        [data-testid="stMetricLabel"] {{
            color: {_WHITE} !important;
        }}
        [data-testid="stMetricValue"] {{
            color: {_WHITE} !important;
        }}
        .purple-bar {{
            background: linear-gradient(90deg, {_PURPLE}, {_DARK_PURPLE});
            padding: 0.8rem 1.2rem;
            border-radius: 8px;
            border: 1px solid {_CYAN};
            margin-bottom: 1rem;
        }}
        .purple-bar h1 {{
            color: {_WHITE} !important;
            margin: 0;
            font-size: 1.6rem;
        }}
        .purple-bar span {{
            color: {_WHITE};
            font-size: 0.9rem;
        }}
        .panel {{
            background-color: {_DARK_GREY};
            border: 1px solid #333;
            border-radius: 8px;
            padding: 1rem;
            margin-bottom: 0.8rem;
        }}
        .panel-header {{
            background: linear-gradient(90deg, {_PURPLE}, {_DARK_PURPLE});
            color: {_WHITE};
            padding: 0.5rem 0.8rem;
            border-radius: 6px 6px 0 0;
            margin: -1rem -1rem 0.8rem -1rem;
            font-weight: 600;
            font-size: 0.95rem;
            border-bottom: 2px solid {_CYAN};
        }}
        .badge-cicids {{
            background-color: {_PURPLE};
            color: {_WHITE};
            padding: 0.15rem 0.5rem;
            border-radius: 4px;
            font-size: 0.75rem;
        }}
        .badge-synthetic {{
            background-color: #1a3a4a;
            color: {_CYAN};
            padding: 0.15rem 0.5rem;
            border-radius: 4px;
            font-size: 0.75rem;
        }}
        .phoenix-link {{
            background-color: {_DARK_PURPLE};
            color: {_WHITE} !important;
            padding: 0.4rem 0.8rem;
            border-radius: 6px;
            text-decoration: none;
            border: 1px solid {_PURPLE};
            display: inline-block;
            margin-top: 0.3rem;
        }}
        .phoenix-link:hover {{
            border-color: {_CYAN};
        }}
        .triage-card {{
            background-color: #1a1a1a;
            border-left: 3px solid {_CYAN};
            padding: 0.5rem 0.7rem;
            margin-bottom: 0.4rem;
            border-radius: 0 4px 4px 0;
            font-size: 0.85rem;
        }}
        .timeline-entry {{
            padding: 0.5rem 0.8rem;
            border-left: 3px solid {_PURPLE};
            margin-bottom: 0.5rem;
            color: {_WHITE};
            font-size: 0.85rem;
        }}
        .timeline-improved {{
            border-left-color: {_GREEN};
        }}
        .timeline-flagged {{
            border-left-color: {_PINK};
        }}
        .improvement-entry {{
            padding: 0.4rem 0.7rem;
            border-left: 3px solid {_CYAN};
            margin-bottom: 0.4rem;
            background: #1a1a1a;
            border-radius: 0 4px 4px 0;
            font-size: 0.85rem;
            color: {_WHITE};
        }}
        .stDataFrame, .stTable {{
            color: {_WHITE};
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def header_banner(demo_mode: bool) -> None:
    st.markdown(
        f"""
        <div class="purple-bar">
            <h1>\U0001f52e Augur Dashboard</h1>
            <span>Self-improving security alert triage &nbsp;|&nbsp;
            {"Demo Mode" if demo_mode else "Live — Firestore connected"}
            &nbsp;|&nbsp; {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Auto-refresh
# ---------------------------------------------------------------------------

def inject_auto_refresh(seconds: int = 15) -> None:
    """Inject a JS snippet that reloads the page every N seconds."""
    st.markdown(
        f"""
        <script>
        function autoRefresh() {{
            window.setTimeout(function() {{
                window.location.reload();
            }}, {seconds * 1000});
        }}
        autoRefresh();
        </script>
        """,
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def get_selected_alert_id() -> str | None:
    return st.session_state.get("selected_alert_id", None)


def set_selected_alert_id(alert_id: str) -> None:
    st.session_state.selected_alert_id = alert_id


# ---------------------------------------------------------------------------
# Firestore helpers
# ---------------------------------------------------------------------------

DEFAULT_PROJECT = os.getenv("GCP_PROJECT", "augur-495810")
_PHOENIX_BASE = "https://app.phoenix.arize.com"


@st.cache_resource
def _get_cached_db():
    from google.cloud import firestore as fs
    return fs.Client(project=DEFAULT_PROJECT)


def _get_db():
    try:
        return _get_cached_db()
    except Exception:
        return None


def _firestore_available() -> bool:
    return _get_db() is not None


@st.cache_data(ttl=15)
def get_latest_eval() -> dict[str, Any] | None:
    db = _get_db()
    if db is None:
        return None
    try:
        from google.cloud import firestore as fs
        docs = (
            db.collection("eval_results")
            .order_by("timestamp", direction=fs.Query.DESCENDING)
            .limit(1)
            .stream()
        )
        for d in docs:
            return {"id": d.id, **d.to_dict()}
    except Exception:
        pass
    return None


@st.cache_data(ttl=15)
def get_eval_history(limit: int = 50) -> list[dict[str, Any]]:
    db = _get_db()
    if db is None:
        return []
    try:
        from google.cloud import firestore as fs
        docs = (
            db.collection("eval_results")
            .order_by("timestamp", direction=fs.Query.DESCENDING)
            .limit(limit)
            .stream()
        )
        return [{"id": d.id, **d.to_dict()} for d in docs]
    except Exception:
        return []


@st.cache_data(ttl=15)
def get_triage_results(limit: int = 100) -> list[dict[str, Any]]:
    db = _get_db()
    if db is None:
        return []
    try:
        from google.cloud import firestore as fs
        docs = (
            db.collection("triage_results")
            .order_by("timestamp", direction=fs.Query.DESCENDING)
            .limit(limit)
            .stream()
        )
        return [{"id": d.id, **d.to_dict()} for d in docs]
    except Exception:
        return []


@st.cache_data(ttl=15)
def get_prompt_versions(tactic_value: str) -> list[dict[str, Any]]:
    db = _get_db()
    if db is None:
        return []
    try:
        from google.cloud import firestore as fs
        versions = (
            db.collection("prompts")
            .document(tactic_value)
            .collection("versions")
            .order_by("created_at", direction=fs.Query.DESCENDING)
            .stream()
        )
        return [
            {"version": int(v.id), **v.to_dict()}
            for v in versions
        ]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Demo / fallback data
# ---------------------------------------------------------------------------

def _demo_alerts() -> pd.DataFrame:
    from augur.data.synthetic import generate_alert_batch
    alerts, gts = generate_alert_batch(n=20)
    rows = []
    for a, gt in zip(alerts, gts):
        tactic = gt.attack_tactic.value if gt.attack_tactic else "—"
        disp = gt.disposition.value
        rows.append({
            "alert_id": str(a.alert_id),
            "alert_id_short": str(a.alert_id)[:8],
            "source": a.source,
            "detection_rule": a.detection_rule_fired,
            "tactic": tactic,
            "disposition": disp,
            "timestamp": a.timestamp or "—",
            "dst_port": a.raw_signals.dst_port,
            "src_ip": a.raw_signals.src_ip,
            "dst_ip": a.raw_signals.dst_ip,
            "confidence": round(0.5 + (hash(str(a.alert_id)) % 50) / 100, 2),
            "severity": ["Low", "Medium", "High", "Critical"][hash(str(a.alert_id)) % 4],
            "trace_id": f"demo-trace-{str(a.alert_id)[:8]}",
            "reasoning": f"Demo classification for {a.detection_rule_fired}",
            "signals": f"{a.raw_signals.src_ip} → {a.raw_signals.dst_ip}:{a.raw_signals.dst_port}",
        })
    df = pd.DataFrame(rows)
    # Keep only the most recent 100 rows (demo mode: 20, live mode: real data)
    return df


def _demo_eval() -> dict[str, Any]:
    return {
        "eval_run_id": "eval-demo-001",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "batch_size": 25,
        "per_tactic": {
            "Initial Access": {"n_total": 5, "n_correct": 4, "precision": 0.80, "recall": 0.80, "f1": 0.80, "accuracy": 0.80, "failure_trace_ids": ["t-ia-1"]},
            "Credential Access": {"n_total": 6, "n_correct": 5, "precision": 0.83, "recall": 0.83, "f1": 0.83, "accuracy": 0.83, "failure_trace_ids": ["t-ca-1"]},
            "Lateral Movement": {"n_total": 5, "n_correct": 2, "precision": 0.50, "recall": 0.40, "f1": 0.44, "accuracy": 0.40, "failure_trace_ids": ["t-lm-1", "t-lm-2", "t-lm-3"]},
            "Exfiltration": {"n_total": 4, "n_correct": 3, "precision": 0.75, "recall": 0.75, "f1": 0.75, "accuracy": 0.75, "failure_trace_ids": ["t-ex-1"]},
            "Command & Control": {"n_total": 5, "n_correct": 5, "precision": 1.0, "recall": 1.0, "f1": 1.0, "accuracy": 1.0, "failure_trace_ids": []},
            "Defense Evasion": {"n_total": 4, "n_correct": 3, "precision": 0.75, "recall": 0.75, "f1": 0.75, "accuracy": 0.75, "failure_trace_ids": ["t-de-1"]},
        },
        "flagged_tactic": "Lateral Movement",
    }


def _demo_eval_history() -> list[dict[str, Any]]:
    return [
        {
            "eval_run_id": "eval-demo-003",
            "timestamp": "2026-06-02T14:30:00Z",
            "batch_size": 25,
            "flagged_tactic": None,
            "per_tactic": {
                "Lateral Movement": {"n_total": 5, "n_correct": 4, "precision": 0.80, "recall": 0.80, "f1": 0.80, "accuracy": 0.80, "failure_trace_ids": ["t-3-1"]},
            },
            "trace_ids": ["t-3-1"],
        },
        {
            "eval_run_id": "eval-demo-002",
            "timestamp": "2026-06-02T14:15:00Z",
            "batch_size": 25,
            "flagged_tactic": "Lateral Movement",
            "per_tactic": {
                "Lateral Movement": {"n_total": 5, "n_correct": 3, "precision": 0.65, "recall": 0.60, "f1": 0.62, "accuracy": 0.60, "failure_trace_ids": ["t-2-1", "t-2-2"]},
            },
            "improved_tactic": "Lateral Movement",
            "prompt_version_before": 1,
            "prompt_version_after": 2,
            "trace_ids": ["t-2-1", "t-2-2"],
        },
        {
            "eval_run_id": "eval-demo-001",
            "timestamp": "2026-06-02T14:00:00Z",
            "batch_size": 25,
            "flagged_tactic": "Lateral Movement",
            "per_tactic": {
                "Lateral Movement": {"n_total": 5, "n_correct": 2, "precision": 0.50, "recall": 0.40, "f1": 0.44, "accuracy": 0.40, "failure_trace_ids": ["t-1-1", "t-1-2", "t-1-3"]},
            },
            "improved_tactic": "Lateral Movement",
            "prompt_version_before": 0,
            "prompt_version_after": 1,
            "trace_ids": ["t-1-1", "t-1-2", "t-1-3"],
        },
    ]


def _demo_improvements() -> list[dict[str, Any]]:
    return [
        {
            "tactic": "Lateral Movement",
            "timestamp": "2026-06-02T14:15:00Z",
            "before_version": 1,
            "after_version": 2,
            "eval_trigger_id": "eval-demo-002",
            "change_summary": "Added examples of SMBv2 vs v1 lateral movement patterns",
        },
        {
            "tactic": "Lateral Movement",
            "timestamp": "2026-06-02T14:00:00Z",
            "before_version": 0,
            "after_version": 1,
            "eval_trigger_id": "eval-demo-001",
            "change_summary": "Initial tactic-specific prompt created from baseline",
        },
        {
            "tactic": "Credential Access",
            "timestamp": "2026-06-01T09:30:00Z",
            "before_version": 2,
            "after_version": 3,
            "eval_trigger_id": "eval-demo-000",
            "change_summary": "Differentiated FTP Patator vs SSH Patator timing signatures",
        },
    ]


# ---------------------------------------------------------------------------
# Utility formatting
# ---------------------------------------------------------------------------

def phoenix_trace_url(trace_id: str) -> str:
    return f"{_PHOENIX_BASE}/projects/augur/traces/{trace_id}"


def source_badge(source: str) -> str:
    if source.lower() in ("cicids2017", "cicids2018"):
        return '<span class="badge-cicids">CICIDS</span>'
    return '<span class="badge-synthetic">SYNTH</span>'
