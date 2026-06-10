"""Triage Detail page — full classification output for a selected alert."""

from __future__ import annotations

import streamlit as st

from augur.dashboard.shared import (
    _demo_alerts,
    _firestore_available,
    get_triage_results,
    header_banner,
    inject_theme,
    phoenix_trace_url,
    set_selected_alert_id,
)

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

inject_theme()
use_demo = not _firestore_available()
header_banner(demo_mode=use_demo)

# Back to dashboard
if st.button("← Back to Dashboard"):
    st.switch_page("pages/dashboard.py")

st.divider()

# ---------------------------------------------------------------------------
# Find the selected alert
# ---------------------------------------------------------------------------

selected_alert_id = st.session_state.get("selected_alert_id", None)
all_alerts = _demo_alerts()

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
        import pandas as pd
        all_alerts = pd.DataFrame(rows)

if selected_alert_id is None:
    st.warning("No alert selected. Go back to the Dashboard and select an event.")
    if st.button("Return to Dashboard"):
        st.switch_page("pages/dashboard.py")
    st.stop()

filtered = all_alerts[all_alerts["alert_id"] == selected_alert_id]
if filtered.empty:
    st.error(f"Alert ID {selected_alert_id} not found in the feed.")
    if st.button("Return to Dashboard"):
        st.switch_page("pages/dashboard.py")
    st.stop()

alert = filtered.iloc[0]

# ---------------------------------------------------------------------------
# Detail panels
# ---------------------------------------------------------------------------

st.markdown(
    '<div class="panel"><div class="panel-header">TRIAGE DETAIL</div>',
    unsafe_allow_html=True,
)

# Top metrics
m1, m2, m3, m4 = st.columns(4)
with m1:
    st.metric("Disposition", alert.get("disposition", "—"))
with m2:
    st.metric("Tactic", alert.get("tactic", "—"))
with m3:
    st.metric("Confidence", alert.get("confidence", "—"))
with m4:
    st.metric("Severity", alert.get("severity", "—"))

st.write("")

# Identity & signals
st.subheader("Alert Identity")
identity_cols = st.columns(2)
with identity_cols[0]:
    st.markdown(
        f"**Alert ID:** `{alert.get('alert_id_short', alert.get('alert_id', '—')[:8])}`"
    )
    st.markdown(f"**Source:** `{alert.get('source', '—')}`")
    st.markdown(f"**Timestamp:** {alert.get('timestamp', '—')}")
with identity_cols[1]:
    st.markdown(f"**Detection Rule:** `{alert.get('detection_rule', '—')}`")
    st.markdown(f"**Signals:** `{alert.get('signals', '—')}`")

# Reasoning
reasoning = alert.get("reasoning", "")
if reasoning:
    st.subheader("Agent Reasoning")
    st.markdown(f"\u003e {reasoning}")

# Recommended action
rec_action = alert.get("recommended_action", "")
if rec_action:
    st.subheader("Recommended Action")
    st.markdown(rec_action)

# Phoenix trace link
trace_id = alert.get("trace_id", "")
if trace_id:
    url = phoenix_trace_url(trace_id)
    st.markdown(
        f'<a class="phoenix-link" href="{url}" target="_blank">'
        f'🔗 View trace in Phoenix: {trace_id[:16]}...</a>',
        unsafe_allow_html=True,
    )

st.markdown("</div>", unsafe_allow_html=True)
