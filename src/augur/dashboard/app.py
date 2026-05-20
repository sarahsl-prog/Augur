"""Augur Streamlit Dashboard -- single-file entry point.

Run locally:
    cd /home/sunds/Code/Augur
    uv run streamlit run src/augur/dashboard/app.py
"""

import os
from datetime import datetime, timezone
from typing import Any

import altair as alt
import pandas as pd
import streamlit as st
from google.cloud import firestore

from augur.data.enums import Tactic

DEFAULT_PROJECT = os.getenv("GCP_PROJECT", "augur-495810")

st.set_page_config(page_title="Augur Dashboard", page_icon="U+1F52E", layout="wide")


def _db():
    return firestore.Client(project=DEFAULT_PROJECT)


@st.cache_data(ttl=30)
def get_latest_eval() -> dict[str, Any] | None:
    docs = (
        _db()
        .collection("eval_results")
        .order_by("timestamp", direction=firestore.Query.DESCENDING)
        .limit(1)
        .stream()
    )
    for d in docs:
        return {"id": d.id, **d.to_dict()}
    return None


@st.cache_data(ttl=30)
def get_eval_history(limit: int = 50) -> list[dict[str, Any]]:
    docs = (
        _db()
        .collection("eval_results")
        .order_by("timestamp", direction=firestore.Query.DESCENDING)
        .limit(limit)
        .stream()
    )
    return [{"id": d.id, **d.to_dict()} for d in docs]


@st.cache_data(ttl=30)
def get_prompt_versions(tactic: Tactic) -> pd.DataFrame:
    versions = (
        _db()
        .collection("prompts")
        .document(tactic.value)
        .collection("versions")
        .order_by("created_at", direction=firestore.Query.DESCENDING)
        .stream()
    )
    rows = []
    for v in versions:
        d = v.to_dict()
        rows.append({
            "version": int(v.id),
            "created_by": d.get("created_by", "?"),
            "created_at": str(d.get("created_at", "—")),
            "parent_version": d.get("parent_version"),
            "triggering_eval_id": d.get("triggering_eval_id"),
            "system_prompt": d.get("system_prompt", "")[:250] + "...",
        })
    return pd.DataFrame(rows)


def get_current_prompt(tactic: Tactic) -> str:
    doc = _db().collection("prompts").document(tactic.value).get()
    if not doc.exists:
        return "—"
    current = doc.to_dict().get("current_version", 0)
    if current == 0:
        return "—"
    vdoc = (
        _db()
        .collection("prompts")
        .document(tactic.value)
        .collection("versions")
        .document(str(current))
        .get()
    )
    if vdoc.exists:
        return vdoc.to_dict().get("system_prompt", "—")
    return "—"


def metrics_df(eval_doc: dict | None) -> pd.DataFrame:
    if eval_doc is None:
        return pd.DataFrame()
    per = eval_doc.get("per_tactic", {})
    rows = []
    for tactic, m in per.items():
        rows.append({
            "tactic": tactic,
            "n_total": m.get("n_total", 0),
            "precision": round(m.get("precision", 0.0), 2),
            "recall": round(m.get("recall", 0.0), 2),
            "f1": round(m.get("f1", 0.0), 2),
            "accuracy": round(m.get("accuracy", 0.0), 2),
            "n_failures": len(m.get("failure_trace_ids", [])),
        })
    return pd.DataFrame(rows)


# -- Sidebar ---------------------------------------------------
st.sidebar.title("Augur Dashboard")
st.sidebar.caption("Self-improving security alert triage")
st.sidebar.divider()

page = st.sidebar.radio(
    "Navigation",
    ["Overview", "Prompt History", "Failure Inspector", "Eval Log"],
)
st.sidebar.divider()
st.sidebar.caption("Built with Streamlit &middot; Firestore + Phoenix")

# -- Page: Overview ----------------------------------------------
if page == "Overview":
    st.title("Overview")
    latest = get_latest_eval()
    history = get_eval_history(limit=10)

    if not latest:
        st.info("No eval results yet. Run `/batch` to generate data.")
        st.stop()

    df = metrics_df(latest)
    if df.empty:
        st.warning("Eval found but no per-tactic metrics.")
        st.stop()

    st.subheader("Per-Tactic Performance (latest run)")
    chart = (
        alt.Chart(df)
        .mark_bar()
        .encode(
            x=alt.X("tactic:N", sort="-y"),
            y=alt.Y("f1:Q", scale=alt.Scale(domain=[0, 1])),
            color=alt.condition(
                alt.datum.f1 < 0.6,
                alt.value("#ff6b6b"),
                alt.value("#4dabf7"),
            ),
            tooltip=["tactic", "precision", "recall", "f1", "n_total", "n_failures"],
        )
        .properties(height=350)
    )
    st.altair_chart(chart, use_container_width=True)

    st.dataframe(
        df[["tactic", "n_total", "precision", "recall", "f1", "n_failures"]],
        use_container_width=True,
        hide_index=True,
    )

    if len(history) >= 2:
        st.subheader("Delta vs Previous Eval")
        prior = history[1]
        df_prior = metrics_df(prior)
        if not df_prior.empty:
            merged = pd.merge(
                df[["tactic", "f1"]], df_prior[["tactic", "f1"]],
                on="tactic", suffixes=("_latest", "_prior"),
            )
            merged["delta"] = (merged["f1_latest"] - merged["f1_prior"]).round(2)
            delta_chart = (
                alt.Chart(merged)
                .mark_bar()
                .encode(
                    x=alt.X("tactic:N", sort="-y"),
                    y=alt.Y("delta:Q", title="F1 Change"),
                    color=alt.condition(
                        alt.datum.delta >= 0,
                        alt.value("#51cf66"),
                        alt.value("#ff6b6b"),
                    ),
                    tooltip=["tactic", "f1_prior", "f1_latest", "delta"],
                )
                .properties(height=300)
            )
            st.altair_chart(delta_chart, use_container_width=True)

    st.subheader("FP Rate by Severity (placeholder)")
    fp_data = pd.DataFrame({
        "severity": ["Critical", "High", "Medium"],
        "threshold": [0.25, 0.50, 0.75],
        "current": [0.18, 0.42, 0.65],
    })
    fp_chart = (
        alt.Chart(fp_data)
        .mark_bar()
        .encode(
            x=alt.X("severity:N", sort=["Critical", "High", "Medium"]),
            y=alt.Y("current:Q", title="FP Rate"),
            color=alt.condition(
                alt.datum.current <= alt.datum.threshold,
                alt.value("#51cf66"),
                alt.value("#ff6b6b"),
            ),
            tooltip=["severity", "current", "threshold"],
        )
        .properties(height=250)
    )
    st.altair_chart(fp_chart, use_container_width=True)
    st.caption("Thresholds: Critical <= 25%, High <= 50%, Medium <= 75%")


# -- Page: Prompt History --------------------------------------
elif page == "Prompt History":
    st.title("Prompt History")
    tabs = st.tabs([t.value for t in Tactic])
    for tab, tactic in zip(tabs, Tactic):
        with tab:
            df = get_prompt_versions(tactic)
            if df.empty:
                st.info(f"No prompt versions for {tactic.value} yet.")
                continue
            st.subheader("Version Timeline")
            st.dataframe(
                df[["version", "created_by", "created_at", "parent_version", "triggering_eval_id"]],
                use_container_width=True,
                hide_index=True,
            )
            with st.expander("Current Prompt Preview"):
                st.text(get_current_prompt(tactic))


# -- Page: Failure Inspector -----------------------------------
elif page == "Failure Inspector":
    st.title("Failure Inspector")
    latest = get_latest_eval()
    if not latest:
        st.info("No eval results yet. Run `/batch` to generate data.")
        st.stop()

    df = metrics_df(latest)
    failed = df[df["n_failures"] > 0]
    if failed.empty:
        st.info("No failures in the latest eval!")
        st.stop()

    tactic_str = st.selectbox("Tactic with failures", failed["tactic"].tolist(), index=0)
    metrics = latest["per_tactic"].get(tactic_str)
    if metrics:
        fail_ids = metrics.get("failure_trace_ids", [])
        st.caption(f"{len(fail_ids)} failure trace(s)")
        st.json(fail_ids[:10] if fail_ids else [])
    else:
        st.warning("No metrics for selected tactic.")


# -- Page: Eval Log --------------------------------------------
else:
    st.title("Eval Log")
    history = get_eval_history(limit=50)
    if not history:
        st.info("No eval runs yet. Run `/batch` to generate data.")
        st.stop()

    st.subheader("Recent Eval Runs")
    summaries = []
    for h in history:
        summaries.append({
            "eval_run_id": h.get("eval_run_id", h.get("id", "—")),
            "timestamp": h.get("timestamp", "—"),
            "batch_size": h.get("batch_size", "—"),
            "flagged_tactic": h.get("flagged_tactic") or "—",
            "improved": "yes" if h.get("flagged_tactic") else "—",
        })
    st.dataframe(pd.DataFrame(summaries), use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Drill Down")
    opts = [f"{s['eval_run_id']} ({s['timestamp']})" for s in summaries]
    sel = st.selectbox("Select eval run", opts, index=0)
    sel_id = sel.split(" (")[0]
    selected = next((h for h in history if h.get("eval_run_id") == sel_id), None)
    if selected:
        df = metrics_df(selected)
        if not df.empty:
            chart = (
                alt.Chart(df)
                .mark_bar()
                .encode(
                    x=alt.X("tactic:N", sort="-y"),
                    y=alt.Y("f1:Q", scale=alt.Scale(domain=[0, 1])),
                    color=alt.condition(
                        alt.datum.f1 < 0.6,
                        alt.value("#ff6b6b"),
                        alt.value("#4dabf7"),
                    ),
                    tooltip=["tactic", "precision", "recall", "f1", "n_total", "n_failures"],
                )
                .properties(height=350)
            )
            st.altair_chart(chart, use_container_width=True)
            st.dataframe(
                df[["tactic", "n_total", "precision", "recall", "f1", "accuracy", "n_failures"]],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.warning("No per-tactic metrics for this eval.")
    else:
        st.error("Eval run not found.")
