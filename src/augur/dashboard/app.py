"""Augur Dashboard entry point — multi-page Streamlit app."""

from __future__ import annotations

import os

import streamlit as st

from augur.dashboard.shared import page_config

# Page config MUST be first Streamlit call.
page_config()

_HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

dashboard = st.Page(
    os.path.join(_HERE, "pages", "dashboard.py"),
    title="Dashboard",
    icon="🏠",
    default=True,
)
triage_detail = st.Page(
    os.path.join(_HERE, "pages", "triage_detail.py"),
    title="Triage Detail",
    icon="🔍",
)
mitre_taxonomy = st.Page(
    os.path.join(_HERE, "pages", "mitre_taxonomy.py"),
    title="MITRE Taxonomy",
    icon="🛡️",
)

# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------

pg = st.navigation([dashboard, triage_detail, mitre_taxonomy])
pg.run()
