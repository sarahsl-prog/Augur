"""Tests for the Firestore-backed prompt store."""

from unittest.mock import MagicMock, patch, call

import pytest

from augur.data.enums import Tactic
from augur.prompt_store import PromptStore


def _mock_db():
    """Return a mock Firestore client with chainable collection/document refs."""
    db = MagicMock()
    return db


def _mock_doc(exists: bool = True, data: dict | None = None):
    doc = MagicMock()
    doc.exists = exists
    doc.to_dict.return_value = data or {}
    return doc


class TestGetCurrentVersion:
    def test_returns_zero_when_doc_missing(self):
        db = _mock_db()
        db.collection.return_value.document.return_value.get.return_value = _mock_doc(
            exists=False
        )
        store = PromptStore(db=db)
        assert store.get_current_version(Tactic.INITIAL_ACCESS) == 0

    def test_returns_version_from_existing_doc(self):
        db = _mock_db()
        db.collection.return_value.document.return_value.get.return_value = _mock_doc(
            exists=True, data={"current_version": 3}
        )
        store = PromptStore(db=db)
        assert store.get_current_version(Tactic.INITIAL_ACCESS) == 3

    def test_returns_zero_when_field_missing(self):
        db = _mock_db()
        db.collection.return_value.document.return_value.get.return_value = _mock_doc(
            exists=True, data={}
        )
        store = PromptStore(db=db)
        assert store.get_current_version(Tactic.INITIAL_ACCESS) == 0


class TestGetPrompt:
    def test_returns_empty_when_version_zero(self):
        db = _mock_db()
        db.collection.return_value.document.return_value.get.return_value = _mock_doc(
            exists=False
        )
        store = PromptStore(db=db)
        assert store.get_prompt(Tactic.INITIAL_ACCESS) == ""

    def test_returns_prompt_text_for_existing_version(self):
        db = _mock_db()
        tactic_doc = _mock_doc(exists=True, data={"current_version": 2})
        version_doc = _mock_doc(
            exists=True, data={"system_prompt": "You are a triage agent."}
        )
        doc_ref = MagicMock()
        doc_ref.get.return_value = tactic_doc
        doc_ref.collection.return_value.document.return_value.get.return_value = (
            version_doc
        )
        db.collection.return_value.document.return_value = doc_ref
        store = PromptStore(db=db)
        assert store.get_prompt(Tactic.INITIAL_ACCESS) == "You are a triage agent."


class TestSeedInitialPrompts:
    def test_skips_already_seeded_tactics(self):
        db = _mock_db()
        doc_ref = MagicMock()
        doc_ref.get.return_value = _mock_doc(exists=True, data={"current_version": 1})
        db.collection.return_value.document.return_value = doc_ref
        store = PromptStore(db=db)
        store.seed_initial_prompts("base prompt")
        doc_ref.set.assert_not_called()

    def test_seeds_when_version_zero(self):
        db = _mock_db()
        doc_ref = MagicMock()
        doc_ref.get.return_value = _mock_doc(exists=True, data={"current_version": 0})
        version_ref = MagicMock()
        doc_ref.collection.return_value.document.return_value = version_ref
        db.collection.return_value.document.return_value = doc_ref
        store = PromptStore(db=db)
        store.seed_initial_prompts("base prompt")
        assert doc_ref.set.call_count == len(Tactic)
        assert version_ref.set.call_count == len(Tactic)


class TestWriteVersion:
    def test_uses_transaction_for_atomicity(self):
        db = _mock_db()
        doc_ref = MagicMock()
        snap = _mock_doc(exists=True, data={"current_version": 2})
        doc_ref.get.return_value = snap
        version_ref = MagicMock()
        doc_ref.collection.return_value.document.return_value = version_ref
        db.collection.return_value.document.return_value = doc_ref

        transaction = MagicMock()
        db.transaction.return_value = transaction

        # The @firestore.transactional decorator calls the function with
        # the transaction object. We need to simulate that behavior.
        # Since we're mocking Firestore, we verify the transaction is created.
        store = PromptStore(db=db)
        db.transaction.assert_not_called()

        # We can't easily test the full transactional flow without a real
        # Firestore emulator, but we verify the method doesn't use batch.
        # The key assertion is that db.batch() is NOT called.
        try:
            store.write_version(
                tactic=Tactic.LATERAL_MOVEMENT,
                system_prompt="revised prompt",
                created_by="improvement_agent",
                parent_version=2,
                triggering_eval_id="eval-1",
            )
        except Exception:
            pass
        db.batch.assert_not_called()
        db.transaction.assert_called_once()
