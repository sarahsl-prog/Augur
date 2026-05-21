"""CICIDS2017/2018 attack-label → Augur tactic/technique/disposition mapping.

Source of truth: augur-mitre-taxonomy skill. Out-of-scope CICIDS labels
(DDoS family, PortScan, Heartbleed, BENIGN) return the UNMAPPED_OUT_OF_SCOPE
sentinel and are dropped from the alert corpus.

CICIDS labels use a Unicode en-dash (–) in some entries (e.g. "Web
Attack – Brute Force"). The dataset's CSVs may contain that exact byte
sequence; preserve it in keys.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from augur.data.enums import Disposition, Tactic


@dataclass(frozen=True)
class CicidsMapping:
    tactic: Tactic
    technique_id: str
    technique_name: str
    disposition: Disposition


# Sentinel for labels we explicitly drop. ``is`` comparison required.
UNMAPPED_OUT_OF_SCOPE: Final[object] = object()


_MAPPING: Final[dict[str, CicidsMapping]] = {
    # Credential Access — T1110 Brute Force
    "FTP-Patator": CicidsMapping(
        Tactic.CREDENTIAL_ACCESS, "T1110.001", "Password Guessing",
        Disposition.TRUE_POSITIVE_CRITICAL,
    ),
    "SSH-Patator": CicidsMapping(
        Tactic.CREDENTIAL_ACCESS, "T1110.001", "Password Guessing",
        Disposition.TRUE_POSITIVE_CRITICAL,
    ),
    "Web Attack – Brute Force": CicidsMapping(
        Tactic.CREDENTIAL_ACCESS, "T1110.001", "Password Guessing",
        Disposition.TRUE_POSITIVE_CRITICAL,
    ),
    "Web Attack – XSS": CicidsMapping(
        Tactic.INITIAL_ACCESS, "T1190", "Exploit Public-Facing Application",
        Disposition.TRUE_POSITIVE_CRITICAL,
    ),
    "Web Attack – Sql Injection": CicidsMapping(
        Tactic.INITIAL_ACCESS, "T1190", "Exploit Public-Facing Application",
        Disposition.TRUE_POSITIVE_CRITICAL,
    ),
    # Lateral Movement — T1021 Remote Services (the demo target tactic)
    "Infiltration": CicidsMapping(
        Tactic.LATERAL_MOVEMENT, "T1021.002", "SMB/Windows Admin Shares",
        Disposition.TRUE_POSITIVE_CRITICAL,
    ),
    # Command & Control — T1071 Application Layer Protocol
    "Bot": CicidsMapping(
        Tactic.COMMAND_AND_CONTROL, "T1071.001", "Web Protocols",
        Disposition.TRUE_POSITIVE_CRITICAL,
    ),
}

# Out-of-scope labels we recognize and drop (returning the sentinel).
_OUT_OF_SCOPE: Final[frozenset[str]] = frozenset(
    {
        "BENIGN",
        "DDoS",
        "DoS Hulk",
        "DoS GoldenEye",
        "DoS slowloris",
        "DoS Slowhttptest",
        "PortScan",
        "Heartbleed",
    }
)


def map_cicids_label(label: str) -> CicidsMapping | object:
    """Return ``CicidsMapping`` for in-scope labels, ``UNMAPPED_OUT_OF_SCOPE`` for OOS,
    and raise ``KeyError`` for unknown labels (signals dataset drift)."""
    if label in _OUT_OF_SCOPE:
        return UNMAPPED_OUT_OF_SCOPE
    if label in _MAPPING:
        return _MAPPING[label]
    raise KeyError(f"Unknown CICIDS label: {label!r}")
