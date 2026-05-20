"""Alert taxonomy enums — v1 spec § Alert Taxonomy.

Disposition: exactly 5 values.
Tactic: exactly 6 values (MITRE ATT&CK scoped subset).
"""

from enum import StrEnum


class Disposition(StrEnum):
    """How the triage agent classifies an alert."""

    TRUE_POSITIVE_CRITICAL = "True Positive - Critical"
    TRUE_POSITIVE_POLICY = "True Positive - Policy Violation"
    FALSE_POSITIVE = "False Positive"
    BENIGN_POSITIVE = "Benign Positive"
    NEEDS_INVESTIGATION = "Needs Investigation"


class Tactic(StrEnum):
    """MITRE ATT&CK tactics in scope for v1."""

    INITIAL_ACCESS = "Initial Access"
    CREDENTIAL_ACCESS = "Credential Access"
    LATERAL_MOVEMENT = "Lateral Movement"
    EXFILTRATION = "Exfiltration"
    COMMAND_AND_CONTROL = "Command & Control"
    DEFENSE_EVASION = "Defense Evasion"
