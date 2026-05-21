"""Tests for the CICIDS attack-label → Augur tactic/technique/disposition mapping."""

import pytest

from augur.data.enums import Disposition, Tactic
from augur.data.mitre_mapping import (
    UNMAPPED_OUT_OF_SCOPE,
    CicidsMapping,
    map_cicids_label,
)


@pytest.mark.parametrize(
    "cicids_label,expected_tactic,expected_disposition",
    [
        ("FTP-Patator", Tactic.CREDENTIAL_ACCESS, Disposition.TRUE_POSITIVE_CRITICAL),
        ("SSH-Patator", Tactic.CREDENTIAL_ACCESS, Disposition.TRUE_POSITIVE_CRITICAL),
        ("Web Attack – Brute Force", Tactic.CREDENTIAL_ACCESS,
         Disposition.TRUE_POSITIVE_CRITICAL),
        ("Infiltration", Tactic.LATERAL_MOVEMENT, Disposition.TRUE_POSITIVE_CRITICAL),
        ("Bot", Tactic.COMMAND_AND_CONTROL, Disposition.TRUE_POSITIVE_CRITICAL),
    ],
)
def test_in_scope_labels_map_correctly(cicids_label, expected_tactic, expected_disposition):
    result = map_cicids_label(cicids_label)
    assert result is not UNMAPPED_OUT_OF_SCOPE
    assert isinstance(result, CicidsMapping)
    assert result.tactic == expected_tactic
    assert result.disposition == expected_disposition
    assert result.technique_id not in (None, "")


@pytest.mark.parametrize(
    "out_of_scope_label",
    [
        "DDoS",
        "DoS Hulk",
        "DoS GoldenEye",
        "DoS slowloris",
        "DoS Slowhttptest",
        "PortScan",
        "Heartbleed",
    ],
)
def test_out_of_scope_labels_return_sentinel(out_of_scope_label):
    assert map_cicids_label(out_of_scope_label) is UNMAPPED_OUT_OF_SCOPE


def test_benign_label_returns_sentinel():
    """Benign rows shouldn't appear as alerts in the eval set; treat as out-of-scope."""
    assert map_cicids_label("BENIGN") is UNMAPPED_OUT_OF_SCOPE


def test_unknown_label_raises():
    with pytest.raises(KeyError):
        map_cicids_label("Mystery-Attack-2099")
