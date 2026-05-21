"""CICIDS2017/2018 CSV → Alert + GroundTruth loader.

CICIDS column quirks:
- Some CSVs have leading-space columns (' Source IP'). We strip headers
  defensively.
- Protocol is numeric (6=TCP, 17=UDP, 1=ICMP) — we map back to strings.
- The Label column drives the MITRE mapping (see mitre_mapping.py).
- Out-of-scope rows (BENIGN, DDoS family, PortScan, Heartbleed) are
  silently dropped.
- Unknown labels raise KeyError (surfaces dataset drift).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal

import pandas as pd

from augur.data.mitre_mapping import UNMAPPED_OUT_OF_SCOPE, map_cicids_label
from augur.data.schema import (
    Alert,
    AlertContext,
    GroundTruth,
    RawSignals,
)

_PROTO_MAP = {6: "TCP", 17: "UDP", 1: "ICMP"}


def _norm_columns(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [c.strip() for c in df.columns]
    return df


def _row_to_pair(
    row: pd.Series,
    source: Literal["cicids2017", "cicids2018"],
) -> tuple[Alert, GroundTruth] | None:
    label = str(row["Label"]).strip()
    mapping = map_cicids_label(label)
    if mapping is UNMAPPED_OUT_OF_SCOPE:
        return None

    proto_num = int(row["Protocol"])
    protocol = _PROTO_MAP.get(proto_num)
    if protocol is None:
        # Unknown protocol — drop the row defensively
        return None

    alert_id = str(uuid.uuid4())
    ts_raw = str(row["Timestamp"]).strip()
    # CICIDS2017 timestamps come in mixed formats; pandas handles both
    ts = pd.to_datetime(ts_raw, errors="coerce")
    if pd.isna(ts):
        return None
    ts_dt = ts.to_pydatetime()

    signals = RawSignals(
        src_ip=str(row["Source IP"]),
        dst_ip=str(row["Destination IP"]),
        dst_port=int(row["Destination Port"]),
        protocol=protocol,
        flow_duration_ms=int(row["Flow Duration"]),  # CICIDS gives microseconds; we keep raw
        packet_count=int(row["Total Fwd Packets"]) + int(row["Total Backward Packets"]),
        byte_count=int(row["Total Length of Fwd Packets"])
                  + int(row["Total Length of Bwd Packets"]),
        flags=[],  # CICIDS flag columns are sparse; left empty for v1
    )
    # Optional extra fields from CICIDS — attach as dict to extra payload
    extra_fields = {}
    for extra in [
        "Fwd Packet Length Max", "Fwd Packet Length Min",
        "Fwd Packet Length Mean", "Fwd Packet Length Std",
        "Bwd Packet Length Max", "Bwd Packet Length Min",
        "Flow Bytes/s", "Flow Packets/s",
    ]:
        if extra in row:
            try:
                extra_fields[extra.replace(" ", "_").lower()] = float(row[extra])
            except (ValueError, TypeError):
                pass

    context = AlertContext(
        host_role="unknown",  # CICIDS doesn't ship host-role metadata
        user_account=None,
        is_business_hours=8 <= ts_dt.hour < 18,
    )
    alert = Alert(
        alert_id=alert_id,
        timestamp=ts_dt.isoformat(),
        source=source,
        raw_signals=signals,
        detection_rule_fired=label,  # the CICIDS label is the detection rule for v1
        context=context,
    )
    gt = GroundTruth(
        alert_id=alert_id,
        disposition=mapping.disposition,
        attack_tactic=mapping.tactic,
        attack_technique=mapping.technique_id,
        source=source,
    )
    return alert, gt


def load_cicids_csv(
    path: Path | str,
    source: Literal["cicids2017", "cicids2018"] = "cicids2017",
) -> list[tuple[Alert, GroundTruth]]:
    """Load a single CICIDS CSV file, return paired (Alert, GroundTruth) tuples.

    Out-of-scope rows are dropped silently. Unknown labels raise KeyError.
    """
    df = pd.read_csv(path, low_memory=False)
    df = _norm_columns(df)
    out: list[tuple[Alert, GroundTruth]] = []
    for _idx, row in df.iterrows():
        pair = _row_to_pair(row, source)
        if pair is not None:
            out.append(pair)
    return out
