"""Tests for the CICIDS CSV loader."""

from pathlib import Path

from augur.data.cicids_loader import load_cicids_csv
from augur.data.enums import Disposition, Tactic

FIXTURE = Path(__file__).parent / "fixtures" / "cicids_sample.csv"


def test_loader_returns_paired_alerts_and_ground_truth():
    pairs = load_cicids_csv(FIXTURE, source="cicids2017")
    assert len(pairs) > 0
    for alert, gt in pairs:
        assert alert.alert_id == gt.alert_id
        assert alert.source == "cicids2017"
        assert gt.source == "cicids2017"


def test_loader_drops_out_of_scope_rows():
    """BENIGN and DDoS rows in the fixture should be dropped, not raised."""
    pairs = load_cicids_csv(FIXTURE, source="cicids2017")
    # 6 rows in fixture: SSH-Patator, Bot, Infiltration, BENIGN, DDoS, FTP-Patator
    # 4 in-scope; 2 dropped
    assert len(pairs) == 4


def test_loader_assigns_correct_tactic_for_ssh_patator():
    pairs = load_cicids_csv(FIXTURE, source="cicids2017")
    ssh = next(
        (gt for _, gt in pairs if gt.attack_technique == "T1110.001"),
        None,
    )
    assert ssh is not None
    assert ssh.attack_tactic == Tactic.CREDENTIAL_ACCESS


def test_loader_assigns_lateral_movement_for_infiltration():
    pairs = load_cicids_csv(FIXTURE, source="cicids2017")
    lm = next(
        (gt for _, gt in pairs if gt.attack_tactic == Tactic.LATERAL_MOVEMENT),
        None,
    )
    assert lm is not None
    assert lm.attack_technique == "T1021.002"


def test_loader_alerts_validate():
    pairs = load_cicids_csv(FIXTURE, source="cicids2017")
    for alert, _gt in pairs:
        assert alert.raw_signals.protocol in {"TCP", "UDP", "ICMP"}
        assert alert.context.host_role in {
            "workstation", "domain_controller", "server", "unknown"
        }
