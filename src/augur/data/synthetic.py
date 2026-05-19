"""Synthetic alert generator for dev and testing.

Produces alerts in the canonical Alert + GroundTruth schema, with
controlled tactic/disposition distribution. NOT used for demo input (spec D4).
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

from augur.data.enums import Disposition, Tactic
from augur.data.schema import Alert, AlertContext, GroundTruth, RawSignals


# Technique mapping per tactic for believable demo data
_TECHNIQUES: dict[Tactic, list[str]] = {
    Tactic.INITIAL_ACCESS: ["T1190", "T1133"],
    Tactic.CREDENTIAL_ACCESS: ["T1110.001", "T1003"],
    Tactic.LATERAL_MOVEMENT: ["T1021.002", "T1021.001"],
    Tactic.EXFILTRATION: ["T1041", "T1048"],
    Tactic.COMMAND_AND_CONTROL: ["T1071.001", "T1571"],
    Tactic.DEFENSE_EVASION: ["T1036", "T1078"],
}


def _make_alert(
    tactic: Tactic, disposition: Disposition, idx: int
) -> tuple[Alert, GroundTruth]:
    """Build one (alert, ground_truth) pair for a given tactic + disposition."""
    technique = _TECHNIQUES[tactic][idx % len(_TECHNIQUES[tactic])]
    alert_id = uuid4()

    # Port hints for plausibility
    port_hint = {
        Tactic.INITIAL_ACCESS: 80,
        Tactic.CREDENTIAL_ACCESS: 22,
        Tactic.LATERAL_MOVEMENT: 445,
        Tactic.EXFILTRATION: 443,
        Tactic.COMMAND_AND_CONTROL: 8080,
        Tactic.DEFENSE_EVASION: 53,
    }[tactic]

    alert = Alert(
        alert_id=alert_id,
        source="synthetic",
        raw_signals=RawSignals(
            src_ip=f"10.0.{idx % 256}.{idx % 16}",
            dst_ip=f"10.0.{(idx + 1) % 256}.{idx % 16}",
            dst_port=port_hint + idx,
            protocol="TCP",
            flow_duration_ms=1000 + idx * 10,
            packet_count=50 + idx,
            byte_count=5000 + idx * 100,
        ),
        detection_rule_fired=f"detect_{tactic.value.lower().replace(' ', '_')}_{idx}",
        context=AlertContext(
            host_role="workstation" if idx % 2 == 0 else "server",
            is_business_hours=idx % 3 == 0,
        ),
    )

    gt = GroundTruth(
        alert_id=alert_id,
        disposition=disposition,
        attack_tactic=tactic if disposition != Disposition.FALSE_POSITIVE else None,
        attack_technique=technique if disposition != Disposition.FALSE_POSITIVE else None,
        source="synthetic",
    )
    return alert, gt


def generate_alert_batch(
    n: int = 25,
    *,
    tactic_distribution: dict[Tactic, int] | None = None,
) -> tuple[list[Alert], list[GroundTruth]]:
    """Generate N (alert, ground_truth) pairs with a default tactic spread.

    Default spread: round-robin across all 6 tactics.
    Disposition is deterministic per tactic for now (mostly TP-Critical, some FP/BP).
    """
    tactics = list(Tactic)
    alerts: list[Alert] = []
    truths: list[GroundTruth] = []

    for i in range(n):
        tactic = tactics[i % len(tactics)]
        # Distribute dispositions: mostly TP-Critical, occasional FP/BP/NeedsInvest
        mod = i % 5
        disposition = {
            0: Disposition.TRUE_POSITIVE_CRITICAL,
            1: Disposition.TRUE_POSITIVE_CRITICAL,
            2: Disposition.TRUE_POSITIVE_POLICY,
            3: Disposition.FALSE_POSITIVE,
            4: Disposition.BENIGN_POSITIVE,
        }[mod]
        alert, gt = _make_alert(tactic, disposition, i)
        alerts.append(alert)
        truths.append(gt)

    return alerts, truths


def iter_alerts(
    n: int = 25,
) -> Iterator[tuple[Alert, GroundTruth]]:
    """Lazy generator of alert + ground_truth pairs."""
    tactics = list(Tactic)
    for i in range(n):
        tactic = tactics[i % len(tactics)]
        mod = i % 5
        disposition = [
            Disposition.TRUE_POSITIVE_CRITICAL,
            Disposition.TRUE_POSITIVE_CRITICAL,
            Disposition.TRUE_POSITIVE_POLICY,
            Disposition.FALSE_POSITIVE,
            Disposition.BENIGN_POSITIVE,
        ][mod]
        yield _make_alert(tactic, disposition, i)
