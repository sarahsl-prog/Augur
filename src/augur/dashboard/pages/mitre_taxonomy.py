"""Augur MITRE Taxonomy — reference page for the scoped tactics + techniques."""

from __future__ import annotations

from collections import defaultdict

import streamlit as st

from augur.dashboard.shared import header_banner, inject_theme
from augur.data.mitre_mapping import _MAPPING


# ---------------------------------------------------------------------------
# Build grouped data from the mapping
# ---------------------------------------------------------------------------

def _build_taxonomy():
    groups = defaultdict(list)
    for label, mapping in _MAPPING.items():
        groups[mapping.tactic.value].append({
            "cicids_label": label,
            "technique_id": mapping.technique_id,
            "technique_name": mapping.technique_name,
            "disposition": mapping.disposition.value,
        })
    # Maintain a fixed ordering matching Tactic enum
    from augur.data.enums import Tactic
    ordered = []
    for tactic in Tactic:
        entries = groups.get(tactic.value, [])
        ordered.append({
            "tactic": tactic.value,
            "entries": entries,
            "count": len(entries),
        })
    return ordered


# ---------------------------------------------------------------------------
# Page render
# ---------------------------------------------------------------------------

inject_theme()
header_banner(demo_mode=True)  # No Firestore needed here

if st.button("← Back to Dashboard"):
    st.switch_page("pages/dashboard.py")

st.divider()

st.markdown(
    '#\U0001f6e1\ufe0f Augur MITRE ATT\u0026CK Taxonomy\n'
    '_The six tactics and mapped techniques currently in scope for the Triage Agent._',
    unsafe_allow_html=True,
)

st.info(
    "Tip: These are the ATT\u0026CK tactics the agent knows about. "
    "When you see an eval flag a tactic, it means the agent is struggling to "
    "classify alerts in that category correctly."
)

for group in _build_taxonomy():
    tactic = group["tactic"]
    entries = group["entries"]

    st.markdown(
        f'### {tactic}\n'
        f'_Mapped CICIDS labels: {group["count"]}_',
        unsafe_allow_html=True,
    )

    if not entries:
        st.caption("No CICIDS labels currently mapped to this tactic.")
        continue

    # Table of mapped labels / techniques / disposition
    table_rows = []
    for e in entries:
        table_rows.append({
            "CICIDS Label": e["cicids_label"],
            "Technique": f"{e['technique_id']} — {e['technique_name']}",
            "Default Disposition": e["disposition"],
        })

    import pandas as pd
    st.dataframe(pd.DataFrame(table_rows), use_container_width=True, hide_index=True)

st.markdown("---")
st.caption(
    "Not mapped: BENIGN, DDoS family, DoS variants, PortScan, Heartbleed. "
    "These are explicitly out-of-scope for the v1 demo."
)
