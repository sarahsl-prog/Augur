"""Firestore-backed per-tactic prompt store.

Each tactic gets a document collection of versioned prompts.
The triage agent reads the current version at runtime.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

from google.cloud import firestore

from augur.data.enums import Tactic

logger = logging.getLogger(__name__)

DEFAULT_PROJECT = os.getenv("GCP_PROJECT", "augur-495810")
_db: Optional[firestore.Client] = None


def _get_db() -> firestore.Client:
    """Lazy singleton for the Firestore client."""
    global _db
    if _db is None:
        _db = firestore.Client(project=DEFAULT_PROJECT)
    return _db


@dataclass
class PromptVersion:
    """A single prompt version record."""

    system_prompt: str
    created_at: str  # iso-8601
    created_by: str  # "human" | "improvement_agent"
    parent_version: int | None
    triggering_eval_id: str | None


class PromptStore:
    """Store and retrieve versioned prompts per tactic."""

    COLLECTION = "prompts"

    def __init__(self, db: firestore.Client | None = None) -> None:
        self.db = db or _get_db()

    def _doc_ref(self, tactic: Tactic | str) -> firestore.DocumentReference:
        """Firestore doc reference for a tactic."""
        key = tactic.value if isinstance(tactic, Tactic) else tactic
        return self.db.collection(self.COLLECTION).document(key)
    
    def get_current_version(self, tactic: Tactic | str) -> int:
        """Return the current version number for a tactic (default 0)."""
        doc = self._doc_ref(tactic).get()
        if doc.exists:
            return doc.to_dict().get("current_version", 0)
        return 0

    def get_prompt(self, tactic: Tactic | str) -> str:
        """Return the current system prompt text for a tactic."""
        current = self.get_current_version(tactic)
        if current == 0:
            logger.warning("No prompt found for %s — returning empty string", tactic)
            return ""
        version_doc = self._doc_ref(tactic).collection("versions").document(str(current)).get()
        if version_doc.exists:
            return version_doc.to_dict().get("system_prompt", "")
        return ""

    def write_version(
        self,
        tactic: Tactic | str,
        system_prompt: str,
        created_by: str = "improvement_agent",
        parent_version: int | None = None,
        triggering_eval_id: str | None = None,
    ) -> int:
        """Write a new prompt version and bump current_version atomically."""
        doc_ref = self._doc_ref(tactic)
        current = self.get_current_version(tactic)
        new_version = current + 1

        batch = self.db.batch()
        batch.update(doc_ref, {
            "current_version": new_version,
            "updated_at": firestore.SERVER_TIMESTAMP,
            "tactic": tactic.value if isinstance(tactic, Tactic) else tactic,
        })
        version_ref = doc_ref.collection("versions").document(str(new_version))
        batch.set(version_ref, {
            "system_prompt": system_prompt,
            "created_at": firestore.SERVER_TIMESTAMP,
            "created_by": created_by,
            "parent_version": parent_version,
            "triggering_eval_id": triggering_eval_id,
        })
        batch.commit()
        logger.info("Wrote prompt v%d for tactic %s", new_version, tactic)
        return new_version

    def seed_initial_prompts(self, base_prompt: str) -> None:
        """Create v1 prompts for every tactic from a base prompt.

        Idempotent: skips tactics that already have a current_version > 0.
        """
        for tactic in Tactic:
            if self.get_current_version(tactic) > 0:
                logger.info("Tactic %s already seeded — skipping", tactic.value)
                continue
            doc_ref = self._doc_ref(tactic)
            doc_ref.set({
                "current_version": 1,
                "created_at": firestore.SERVER_TIMESTAMP,
                "tactic": tactic.value,
            })
            doc_ref.collection("versions").document("1").set({
                "system_prompt": base_prompt,
                "created_at": firestore.SERVER_TIMESTAMP,
                "created_by": "human",
                "parent_version": None,
                "triggering_eval_id": None,
            })
            logger.info("Seeded v1 prompt for tactic %s", tactic.value)
