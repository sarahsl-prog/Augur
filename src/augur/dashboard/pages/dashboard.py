"""Augur Dashboard — main page with 4-panel Layout B.

Left  (~60%): Incoming Event Feed
Right (~40%): Triage Activity → Eval Activity → Improvement History
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from augur.data.enums import Disposition, Tactic
from augur.dashboard.shared import (
    _demo_alerts,
    _demo_eval,
    _demo_eval_history,
    _demo_improvements,
    _firestore_available,
    get_eval_history,
    get_latest_eval,
    get_triage_results,
    header_banner,
    inject_auto_refresh,
    inject_theme,
    phoenix_trace_url,
    set_selected_alert_id,
)

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

inject_theme()
inject_auto_refresh(seconds=15)

use_demo = not _firestore_available()
header_banner(demo_mode=use_demo)

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------

latest_eval = get_latest_eval() if not use_demo else _demo_eval()
eval_history = get_eval_history(limit=20) if not use_demo else _demo_eval_history()

alert_df = _demo_alerts()
live_triage = get_triage_results(limit=100) if not use_demo else []
if live_triage:
    rows = []
    for t in live_triage:
        rows.append({
            "alert_id": str(t.get("alert_id", "")),
            "alert_id_short": str(t.get("alert_id", ""))[:8],
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
            "signals": (
                f"{t.get('src_ip', '—')} → "
                f"{t.get('dst_ip', '—')}:{t.get('dst_port', 0)}"
            ),
        })
    if rows:
        alert_df = pd.DataFrame(rows)

# Prefer descending by timestamp; fall back to current order.
if "timestamp" in alert_df.columns:
    try:
        alert_df["_ts_sort"] = pd.to_datetime(alert_df["timestamp"], errors="coerce")
        alert_df = alert_df.sort_values("_ts_sort", ascending=False).reset_index(drop=True)
        alert_df = alert_df.drop(columns=["_ts_sort"])
    except Exception:
        pass

alert_df = alert_df.head(100).reset_index(drop=True)

# ---------------------------------------------------------------------------
# Improvements (derive from eval history in live mode)
# ---------------------------------------------------------------------------

if use_demo:
    improvements = _demo_improvements()
else:
    improvements = []
    for entry in eval_history:
        if entry.get("improved_tactic"):
            improvements.append({
                "tactic": entry["improved_tactic"],
                "timestamp": entry.get("timestamp", "—"),
                "before_version": entry.get("prompt_version_before", "?"),
                "after_version": entry.get("prompt_version_after", "?"),
                "eval_trigger_id": entry.get("eval_run_id", entry.get("id", "—")),
                "change_summary": "Prompt rewrite triggered by eval",
            })

# ---------------------------------------------------------------------------
# Layout B
# ---------------------------------------------------------------------------

col_feed, col_right = st.columns([3, 2])

# ===========================================================================
# LEFT: Incoming Event Feed
# ===========================================================================
with col_feed:
    st.markdown(
        '<div class="panel">'
        '<div class="panel-header">INCOMING EVENT FEED</div>',
        unsafe_allow_html=True,
    )

    display_df = alert_df[["alert_id_short", "source", "signals", "detection_rule"]].copy()
    display_df.columns = ["ID", "Source", "Signals", "Rule"]

    selected_idx = st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        height=520,
    )

    sel_alert_id: str | None = None
    sel_rows = (
        selected_idx.get("selection", {}).get("rows", [])
        if isinstance(selected_idx, dict)
        else []
    )
    if sel_rows:
        sel_alert_id = str(alert_df.iloc[sel_rows[0]]["alert_id"])

    if sel_alert_id:
        if st.button("🔍 View Triage Details", use_container_width=True):
            set_selected_alert_id(sel_alert_id)
            st.switch_page("pages/triage_detail.py")
    else:
        st.caption("Select an alert row to view triage details")

    st.markdown("</div>", unsafe_allow_html=True)

# ===========================================================================
# RIGHT: stacked panels
# ===========================================================================
with col_right:

    # -----------------------------------------------------------------------
    # Panel 1 — Triage Activity
    # -----------------------------------------------------------------------
    st.markdown(
        '<div class="panel">'
        '<div class="panel-header">TRIAGE ACTIVITY</div>',
        unsafe_allow_html=True,
    )

    latest_5 = alert_df.head(5)
    for _, row in latest_5.iterrows():
        st.markdown(
            f'<div class="triage-card">'
            f'<span style="color:#00e5ff">{row["disposition"]}</span> &nbsp;|&nbsp; '
            f'Tactic: <b>{row["tactic"]}</b> &nbsp;|&nbsp; '
            f'Confidence: {row["confidence"]} &nbsp;|&nbsp; '
            f'Severity: {row["severity"]}'
            f'</div>',
            unsafe_allow_html=True,
        )

    if st.button("🛡️  View Augur MITRE Taxonomy", use_container_width=True):
        st.switch_page("pages/mitre_taxonomy.py")

    st.markdown("</div>", unsafe_allow_html=True)

    # -----------------------------------------------------------------------
    # Panel 2 — Eval Activity
    # -----------------------------------------------------------------------
    st.markdown(
        '<div class="panel">'
        '<div class="panel-header">EVAL ACTIVITY</div>',
        unsafe_allow_html=True,
    )

    if not eval_history:
        st.info("No eval runs yet.")
    else:
        for entry in eval_history[:10]:
            eval_id = entry.get("eval_run_id", entry.get("id", "—"))
            ts = entry.get("timestamp", "—")
            batch = entry.get("batch_size", "?")
            flagged = entry.get("flagged_tactic")

            f1_str = ""
            if flagged and flagged in entry.get("per_tactic", {}):
                f1_val = entry["per_tactic"][flagged].get("f1", 0)
                f1_str = f" (F1: {f1_val:.2f})"

            improved = entry.get("improved_tactic")
            if improved:
                icon = "✅"
                detail = (
                    f"Improved <b>{improved}</b> — "
                    f"prompt v{entry.get('prompt_version_before', '?')} → "
                    f"v{entry.get('prompt_version_after', '?')}"
                )
                css_class = "timeline-entry timeline-improved"
            elif flagged:
                icon = "⚠️"
                detail = f"Flagged <b>{flagged}</b>{f1_str}"
                css_class = "timeline-entry timeline-flagged"
            else:
                icon = "✅"
                detail = "All tactics above threshold"
                css_class = "timeline-entry"

            st.markdown(
                f'<div class="{css_class}">'
                f'{icon} <strong>{eval_id[:12]}…</strong> &nbsp;|&nbsp; '
                f'{ts} &nbsp;|&nbsp; Batch: {batch}<br>'
                f'{detail}'
                f'</div>',
                unsafe_allow_html=True,
            )

            per = entry.get("per_tactic", {})
            if per:
                with st.expander("Per-tactic breakdown"):
                    cols = st.columns([2, 1, 1, 1, 1])
                    cols[0].write("Tactic")
                    cols[1].write("Precision")
                    cols[2].write("Recall")
                    cols[3].write("F1")
                    cols[4].write("Fails")
                    for tactic, m in per.items():
                        _fails = len(m.get("failure_trace_ids", []))
                        cols = st.columns([2, 1, 1, 1, 1])
                        cols[0].write(tactic)
                        cols[1].write(f"{m.get('precision', 0):.2f}")
                        cols[2].write(f"{m.get('recall', 0):.2f}")
                        cols[3].write(f"{m.get('f1', 0):.2f}")
                        cols[4].write(str(_fails))

    st.markdown(
        '<a class="phoenix-link" '
        'href="https://app.phoenix.arize.com" target="_blank">'
        '🔗 View Phoenix Dashboard</a>',
        unsafe_allow_html=True,
    )

    st.markdown("</div>", unsafe_allow_html=True)

    # -----------------------------------------------------------------------
    # Panel 3 — Improvement History
    # -----------------------------------------------------------------------
    st.markdown(
        '<div class="panel">'
        '<div class="panel-header">IMPROVEMENT HISTORY</div>',
        unsafe_allow_html=True,
    )

    if not improvements:
        st.info("No improvements recorded yet.")
    else:
        for imp in improvements[:3]:
            st.markdown(
                f'<div class="improvement-entry">'
                f'<b>{imp["tactic"]}</b> &nbsp;|&nbsp; '
                f'v{imp["before_version"]} → v{imp["after_version"]} &nbsp;|&nbsp; '
                f'{imp["timestamp"]}<br>'
                f'{imp["change_summary"]}'
                f'</div>',
                unsafe_allow_html=True,
            )

    st.markdown("</div>", unsafe_allow_html=True)
