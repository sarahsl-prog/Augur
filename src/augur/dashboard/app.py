"""Augur Streamlit Dashboard — 4-panel layout with cyan/black/purple theme.

Run locally:
    uv run streamlit run src/augur/dashboard/app.py

Panels:
    1. Alert Feed (left)        — scrollable table of alerts with source badges
    2. Triage Detail (right-top) — selected alert classification + Phoenix link
    3. Per-Tactic Performance (right-mid) — grouped bar chart with flagged tactic
    4. Improvement Timeline (bottom) — chronological eval/improvement log
"""

import os
from datetime import datetime, timezone
from typing import Any

import altair as alt
import pandas as pd
import streamlit as st

from augur.data.enums import Disposition, Tactic

DEFAULT_PROJECT = os.getenv("GCP_PROJECT", "augur-495810")
PHOENIX_BASE = "https://app.phoenix.arize.com"

# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------
_PURPLE = "#7b2d8e"
_DARK_PURPLE = "#3a0a4e"
_CYAN = "#00e5ff"
_BLACK = "#0a0a0a"
_PINK = "#ff6b8a"
_GREEN = "#00ff9f"
_WHITE = "#f0f0f0"

st.set_page_config(
    page_title="Augur Dashboard",
    page_icon="\U0001f52e",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    f"""
    <style>
    /* Global dark background */
    .stApp {{
        background-color: {_BLACK};
        color: {_WHITE};
    }}
    /* Sidebar */
    section[data-testid="stSidebar"] {{
        background-color: #111111;
    }}
    section[data-testid="stSidebar"] * {{
        color: {_WHITE} !important;
    }}
    /* Headers */
    h1, h2, h3, .stMarkdown h1, .stMarkdown h2, .stMarkdown h3 {{
        color: {_WHITE} !important;
    }}
    /* Metric labels */
    [data-testid="stMetricLabel"] {{
        color: {_WHITE} !important;
    }}
    [data-testid="stMetricValue"] {{
        color: {_WHITE} !important;
    }}
    /* Purple accent bar at top */
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
    /* Panel containers */
    .panel {{
        background-color: #111111;
        border: 1px solid #333;
        border-radius: 8px;
        padding: 1rem;
        margin-bottom: 0.5rem;
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
    /* Alert row styling */
    .alert-row {{
        padding: 0.4rem 0.6rem;
        border-bottom: 1px solid #222;
        font-size: 0.85rem;
        color: {_WHITE};
        cursor: pointer;
    }}
    .alert-row:hover {{
        background-color: {_DARK_PURPLE};
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
    .flagged {{
        color: {_PINK} !important;
        font-weight: bold;
    }}
    /* Phoenix link */
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
    /* Timeline entry */
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
    /* Table text */
    .stDataFrame, .stTable {{
        color: {_WHITE};
    }}
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Firestore helpers (graceful fallback when unavailable)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Demo / fallback data (when Firestore isn't available)
# ---------------------------------------------------------------------------

def _demo_alerts() -> pd.DataFrame:
    from augur.data.synthetic import generate_alert_batch
    alerts, gts = generate_alert_batch(n=20)
    rows = []
    for a, gt in zip(alerts, gts):
        rows.append({
            "alert_id": str(a.alert_id)[:8],
            "source": a.source,
            "detection_rule": a.detection_rule_fired,
            "tactic": gt.attack_tactic.value if gt.attack_tactic else "—",
            "disposition": gt.disposition.value,
            "timestamp": a.timestamp or "—",
            "dst_port": a.raw_signals.dst_port,
            "src_ip": a.raw_signals.src_ip,
            "dst_ip": a.raw_signals.dst_ip,
            "confidence": round(0.5 + (hash(str(a.alert_id)) % 50) / 100, 2),
            "severity": ["Low", "Medium", "High", "Critical"][hash(str(a.alert_id)) % 4],
            "trace_id": f"demo-trace-{str(a.alert_id)[:8]}",
            "reasoning": f"Demo classification for {a.detection_rule_fired}",
        })
    return pd.DataFrame(rows)


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
        },
    ]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def metrics_df(eval_doc: dict[str, Any] | None) -> pd.DataFrame:
    if eval_doc is None:
        return pd.DataFrame()
    per = eval_doc.get("per_tactic", {})
    rows = []
    for tactic, m in per.items():
        rows.append({
            "Tactic": tactic,
            "Samples": m.get("n_total", 0),
            "Precision": round(m.get("precision", 0.0), 2),
            "Recall": round(m.get("recall", 0.0), 2),
            "F1": round(m.get("f1", 0.0), 2),
            "Failures": len(m.get("failure_trace_ids", [])),
        })
    return pd.DataFrame(rows)


def phoenix_trace_url(trace_id: str) -> str:
    return f"{PHOENIX_BASE}/projects/augur/traces/{trace_id}"


# ---------------------------------------------------------------------------
# Load data (Firestore or demo fallback)
# ---------------------------------------------------------------------------

use_demo = not _firestore_available()
latest_eval = get_latest_eval() if not use_demo else _demo_eval()
eval_history = get_eval_history(limit=20) if not use_demo else _demo_eval_history()
alert_df = _demo_alerts()  # Always use synthetic for alert feed (Firestore triage_results may not exist)

live_triage = get_triage_results(limit=50) if not use_demo else []
if live_triage:
    rows = []
    for t in live_triage:
        rows.append({
            "alert_id": str(t.get("alert_id", ""))[:8],
            "source": t.get("source", "unknown"),
            "detection_rule": t.get("detection_rule_fired", "—"),
            "tactic": t.get("attack_tactic", "—"),
            "disposition": t.get("disposition", "—"),
            "timestamp": t.get("timestamp", "—"),
            "dst_port": t.get("dst_port", 0),
            "src_ip": t.get("src_ip", "—"),
            "dst_ip": t.get("dst_ip", "—"),
            "confidence": t.get("confidence", 0.5),
            "severity": t.get("severity", "Medium"),
            "trace_id": t.get("trace_id", ""),
            "reasoning": t.get("reasoning", ""),
        })
    if rows:
        alert_df = pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

st.markdown(
    f"""
    <div class="purple-bar">
        <h1>\U0001f52e Augur Dashboard</h1>
        <span>Self-improving security alert triage &nbsp;|&nbsp;
        {"Demo Mode" if use_demo else "Live — Firestore connected"}
        &nbsp;|&nbsp; {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}</span>
    </div>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

st.sidebar.markdown(f'<h2 style="color:{_WHITE}">Navigation</h2>', unsafe_allow_html=True)
page = st.sidebar.radio(
    "Page",
    ["\U0001f4cb Alert Triage", "\U0001f4c8 Performance", "\U0001f504 Improvement Log", "\U0001f4dd Prompt History"],
    label_visibility="collapsed",
)
st.sidebar.divider()
st.sidebar.caption("Augur · Arize Track · Phoenix + ADK")
if use_demo:
    st.sidebar.warning("Firestore unavailable — showing demo data")


# ===================================================================
# PAGE: Alert Triage (main 2-column layout)
# ===================================================================
if page == "\U0001f4cb Alert Triage":

    col_feed, col_detail = st.columns([1, 2])

    with col_feed:
        st.markdown('<div class="panel"><div class="panel-header">ALERT FEED</div>', unsafe_allow_html=True)

        display_df = alert_df[["alert_id", "source", "detection_rule", "disposition"]].copy()
        display_df.columns = ["ID", "Source", "Rule", "Disposition"]

        selected_idx = st.dataframe(
            display_df,
            use_container_width=True,
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            height=500,
        )

        st.markdown('</div>', unsafe_allow_html=True)

    # Determine which alert is selected
    sel_rows = selected_idx.get("selection", {}).get("rows", []) if isinstance(selected_idx, dict) else []
    sel_alert = alert_df.iloc[sel_rows[0]] if sel_rows else alert_df.iloc[0]

    with col_detail:
        # Triage Detail panel
        st.markdown('<div class="panel"><div class="panel-header">TRIAGE DETAIL</div>', unsafe_allow_html=True)

        c1, c2, c3 = st.columns(3)
        with c1:
            st.metric("Disposition", sel_alert["disposition"])
        with c2:
            st.metric("Tactic", sel_alert["tactic"])
        with c3:
            st.metric("Confidence", sel_alert["confidence"])

        c4, c5, c6 = st.columns(3)
        with c4:
            st.metric("Severity", sel_alert["severity"])
        with c5:
            st.metric("Source", sel_alert["source"].upper())
        with c6:
            st.metric("Port", sel_alert["dst_port"])

        st.markdown(f"**Source IP:** `{sel_alert['src_ip']}` → **Dest IP:** `{sel_alert['dst_ip']}`")
        st.markdown(f"**Detection Rule:** `{sel_alert['detection_rule']}`")

        if sel_alert.get("reasoning"):
            with st.expander("Agent Reasoning"):
                st.write(sel_alert["reasoning"])

        trace_id = sel_alert.get("trace_id", "")
        if trace_id:
            url = phoenix_trace_url(trace_id)
            st.markdown(
                f'<a class="phoenix-link" href="{url}" target="_blank">'
                f'\U0001f517 View trace in Phoenix: {trace_id[:16]}...</a>',
                unsafe_allow_html=True,
            )

        st.markdown('</div>', unsafe_allow_html=True)

        # Per-Tactic Performance panel (below triage detail)
        st.markdown('<div class="panel"><div class="panel-header">PER-TACTIC PERFORMANCE</div>', unsafe_allow_html=True)

        df_perf = metrics_df(latest_eval)
        if not df_perf.empty:
            melted = df_perf.melt(
                id_vars=["Tactic"],
                value_vars=["Precision", "Recall", "F1"],
                var_name="Metric",
                value_name="Score",
            )

            color_scale = alt.Scale(
                domain=["Precision", "Recall", "F1"],
                range=[_PURPLE, _CYAN, _WHITE],
            )

            chart = (
                alt.Chart(melted)
                .mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
                .encode(
                    x=alt.X("Tactic:N", sort="-y", axis=alt.Axis(labelColor=_WHITE, titleColor=_WHITE)),
                    y=alt.Y("Score:Q", scale=alt.Scale(domain=[0, 1]), axis=alt.Axis(labelColor=_WHITE, titleColor=_WHITE)),
                    color=alt.Color("Metric:N", scale=color_scale, legend=alt.Legend(labelColor=_WHITE, titleColor=_WHITE)),
                    xOffset="Metric:N",
                    tooltip=["Tactic", "Metric", "Score"],
                )
                .properties(height=280)
                .configure_view(strokeWidth=0)
                .configure_axis(gridColor="#333")
            )
            st.altair_chart(chart, use_container_width=True)

            flagged = latest_eval.get("flagged_tactic") if latest_eval else None
            if flagged:
                st.markdown(f'⚠️ <span class="flagged">Flagged tactic: {flagged} (lowest F1)</span>', unsafe_allow_html=True)

            st.dataframe(
                df_perf,
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("No eval data available yet.")

        st.markdown('</div>', unsafe_allow_html=True)


# ===================================================================
# PAGE: Performance
# ===================================================================
elif page == "\U0001f4c8 Performance":
    st.markdown('<div class="panel"><div class="panel-header">PERFORMANCE OVERVIEW</div>', unsafe_allow_html=True)

    df_perf = metrics_df(latest_eval)
    if df_perf.empty:
        st.info("No eval results yet. Run `/batch` to generate data.")
        st.markdown('</div>', unsafe_allow_html=True)
        st.stop()

    melted = df_perf.melt(
        id_vars=["Tactic"],
        value_vars=["Precision", "Recall", "F1"],
        var_name="Metric",
        value_name="Score",
    )
    color_scale = alt.Scale(
        domain=["Precision", "Recall", "F1"],
        range=[_PURPLE, _CYAN, _WHITE],
    )
    chart = (
        alt.Chart(melted)
        .mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
        .encode(
            x=alt.X("Tactic:N", sort="-y", axis=alt.Axis(labelColor=_WHITE, titleColor=_WHITE)),
            y=alt.Y("Score:Q", scale=alt.Scale(domain=[0, 1]), axis=alt.Axis(labelColor=_WHITE, titleColor=_WHITE)),
            color=alt.Color("Metric:N", scale=color_scale, legend=alt.Legend(labelColor=_WHITE, titleColor=_WHITE)),
            xOffset="Metric:N",
            tooltip=["Tactic", "Metric", "Score"],
        )
        .properties(height=350)
        .configure_view(strokeWidth=0)
        .configure_axis(gridColor="#333")
    )
    st.altair_chart(chart, use_container_width=True)
    st.dataframe(df_perf, use_container_width=True, hide_index=True)

    # Delta vs previous eval
    if len(eval_history) >= 2:
        st.markdown("---")
        st.subheader("F1 Delta vs Previous Eval")
        prior = eval_history[1] if len(eval_history) > 1 else None
        if prior:
            df_prior = metrics_df(prior)
            if not df_prior.empty:
                merged = pd.merge(
                    df_perf[["Tactic", "F1"]], df_prior[["Tactic", "F1"]],
                    on="Tactic", suffixes=("_latest", "_prior"), how="outer",
                ).fillna(0)
                merged["Delta"] = (merged["F1_latest"] - merged["F1_prior"]).round(3)

                delta_chart = (
                    alt.Chart(merged)
                    .mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
                    .encode(
                        x=alt.X("Tactic:N", axis=alt.Axis(labelColor=_WHITE, titleColor=_WHITE)),
                        y=alt.Y("Delta:Q", title="F1 Change", axis=alt.Axis(labelColor=_WHITE, titleColor=_WHITE)),
                        color=alt.condition(
                            alt.datum.Delta >= 0,
                            alt.value(_GREEN),
                            alt.value(_PINK),
                        ),
                        tooltip=["Tactic", "F1_prior", "F1_latest", "Delta"],
                    )
                    .properties(height=280)
                    .configure_view(strokeWidth=0)
                    .configure_axis(gridColor="#333")
                )
                st.altair_chart(delta_chart, use_container_width=True)

    st.markdown('</div>', unsafe_allow_html=True)


# ===================================================================
# PAGE: Improvement Log
# ===================================================================
elif page == "\U0001f504 Improvement Log":
    st.markdown('<div class="panel"><div class="panel-header">IMPROVEMENT TIMELINE</div>', unsafe_allow_html=True)

    history = eval_history
    if not history:
        st.info("No eval runs yet. Run `/batch` to generate data.")
        st.markdown('</div>', unsafe_allow_html=True)
        st.stop()

    for entry in history:
        eval_id = entry.get("eval_run_id", entry.get("id", "—"))
        ts = entry.get("timestamp", "—")
        batch = entry.get("batch_size", "?")
        flagged = entry.get("flagged_tactic")
        improved = entry.get("improved_tactic")

        # Determine F1 for flagged tactic
        f1_str = ""
        if flagged and flagged in entry.get("per_tactic", {}):
            f1_val = entry["per_tactic"][flagged].get("f1", 0)
            f1_str = f" (F1: {f1_val:.2f})"

        if improved:
            v_before = entry.get("prompt_version_before", "?")
            v_after = entry.get("prompt_version_after", "?")
            css_class = "timeline-entry timeline-improved"
            icon = "✅"
            detail = f"Flagged **{flagged}**{f1_str} → Prompt v{v_before} → v{v_after}"
        elif flagged:
            css_class = "timeline-entry timeline-flagged"
            icon = "⚠️"
            detail = f"Flagged **{flagged}**{f1_str} — awaiting improvement"
        else:
            css_class = "timeline-entry"
            icon = "✅"
            detail = "All tactics above threshold"

        st.markdown(
            f'<div class="{css_class}">'
            f'{icon} <strong>{eval_id[:12]}...</strong> &nbsp;|&nbsp; {ts} &nbsp;|&nbsp; Batch: {batch}<br>'
            f'{detail}</div>',
            unsafe_allow_html=True,
        )

        # Trace IDs for this eval
        trace_ids = entry.get("trace_ids", [])
        if trace_ids:
            with st.expander(f"Trace IDs ({len(trace_ids)})"):
                for tid in trace_ids[:10]:
                    url = phoenix_trace_url(tid)
                    st.markdown(f"[\U0001f517 {tid[:20]}...]({url})")

        # Failure inspector for flagged tactic
        if flagged and flagged in entry.get("per_tactic", {}):
            fail_ids = entry["per_tactic"][flagged].get("failure_trace_ids", [])
            if fail_ids:
                with st.expander(f"Failed traces for {flagged} ({len(fail_ids)})"):
                    for fid in fail_ids[:10]:
                        url = phoenix_trace_url(fid)
                        st.markdown(f"[❌ {fid[:20]}...]({url})")

    st.markdown('</div>', unsafe_allow_html=True)


# ===================================================================
# PAGE: Prompt History
# ===================================================================
elif page == "\U0001f4dd Prompt History":
    st.markdown('<div class="panel"><div class="panel-header">PROMPT VERSION HISTORY</div>', unsafe_allow_html=True)

    tabs = st.tabs([t.value for t in Tactic])
    for tab, tactic in zip(tabs, Tactic):
        with tab:
            versions = get_prompt_versions(tactic.value)
            if not versions:
                st.info(f"No prompt versions for {tactic.value} yet.")
                continue

            rows = []
            for v in versions:
                rows.append({
                    "Version": v.get("version", "?"),
                    "Created By": v.get("created_by", "?"),
                    "Created At": str(v.get("created_at", "—")),
                    "Parent": v.get("parent_version", "—"),
                    "Eval Trigger": v.get("triggering_eval_id", "—"),
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

            with st.expander("Current Prompt Preview"):
                if versions:
                    latest_v = sorted(versions, key=lambda x: x.get("version", 0), reverse=True)[0]
                    st.code(latest_v.get("system_prompt", "—")[:2000], language="markdown")

    st.markdown('</div>', unsafe_allow_html=True)
