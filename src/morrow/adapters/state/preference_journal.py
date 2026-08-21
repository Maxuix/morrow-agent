"""Public facade for the bounded SQLite Preference v13 repositories."""

from __future__ import annotations

from morrow.adapters.state.operational import OperationalStoreSession
from morrow.adapters.state.preference_journal_codec import PreferenceJournalFailure
from morrow.adapters.state.preference_proposal_journal import PreferenceProposalJournalMixin
from morrow.adapters.state.preference_review_journal import PreferenceReviewJournalMixin
from morrow.adapters.state.preference_write_batch_journal import PreferenceWriteBatchJournalMixin
from morrow.adapters.state.transaction import SqliteJournalBackend


class SqlitePreferenceJournal(
    PreferenceReviewJournalMixin,
    PreferenceProposalJournalMixin,
    PreferenceWriteBatchJournalMixin,
):
    """One bounded facade over the Review, Inbox, and Writer repositories."""

    def __init__(
        self,
        backend_or_session: SqliteJournalBackend | OperationalStoreSession,
    ) -> None:
        self.backend = (
            backend_or_session
            if isinstance(backend_or_session, SqliteJournalBackend)
            else SqliteJournalBackend(backend_or_session)
        )


__all__ = ["PreferenceJournalFailure", "SqlitePreferenceJournal"]
