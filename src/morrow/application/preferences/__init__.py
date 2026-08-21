"""Application services for generic Preference lifecycle commands."""

from morrow.adapters.state.preference_projection import preferences_from_entries
from morrow.application.preferences.bridge import (
    candidate_operations,
    legacy_statement,
)
from morrow.application.preferences.context import (
    PreferenceContextBuilder,
    PreferenceReviewContextBuilder,
    snapshot_from_documents,
)
from morrow.application.preferences.inbox import (
    PreferenceDecisionPreview,
    PreferenceInbox,
    PreferenceInboxDecisionResult,
    PreferenceInboxError,
    PreferenceProposalView,
)
from morrow.application.preferences.jobs import (
    PreferenceReviewEnqueueResult,
    PreferenceReviewJobEnqueuer,
    PreferenceReviewJobService,
)
from morrow.application.preferences.proposals import (
    PreferenceProposalPipeline,
    PreferenceProposalPipelineError,
    PreferenceProposalPipelineResult,
    PreferenceProposalRejection,
)
from morrow.application.preferences.queries import PreferenceQueries
from morrow.application.preferences.recovery import (
    PreferenceRecoveryReport,
    PreferenceWriteRecovery,
)
from morrow.application.preferences.reviewer import (
    DeterministicPreferenceReviewer,
    PreferenceReviewRunner,
    PreferenceReviewRunResult,
)
from morrow.application.preferences.tool import (
    ManagePreferenceOperation,
    ManagePreferencesArguments,
    PreferenceManagementService,
    make_preference_management_tool,
)
from morrow.application.preferences.worker import ReviewWorker, ReviewWorkerResult
from morrow.application.preferences.writer import (
    PreferenceWritePreparation,
    PreferenceWriter,
    PreferenceWriterConflict,
    PreferenceWriterError,
    PreferenceWriteResult,
    PreferenceWriterNeedsResolution,
)

__all__ = [
    "PreferenceWritePreparation",
    "PreferenceWriteResult",
    "PreferenceWriter",
    "PreferenceWriterConflict",
    "PreferenceWriterError",
    "PreferenceWriterNeedsResolution",
    "PreferenceRecoveryReport",
    "PreferenceWriteRecovery",
    "PreferenceQueries",
    "candidate_operations",
    "legacy_statement",
    "preferences_from_entries",
    "ManagePreferencesArguments",
    "ManagePreferenceOperation",
    "PreferenceManagementService",
    "make_preference_management_tool",
    "PreferenceContextBuilder",
    "PreferenceReviewContextBuilder",
    "snapshot_from_documents",
    "PreferenceProposalPipeline",
    "PreferenceProposalPipelineError",
    "PreferenceProposalPipelineResult",
    "PreferenceProposalRejection",
    "PreferenceInbox",
    "PreferenceInboxError",
    "PreferenceInboxDecisionResult",
    "PreferenceDecisionPreview",
    "PreferenceProposalView",
    "PreferenceReviewEnqueueResult",
    "PreferenceReviewJobEnqueuer",
    "PreferenceReviewJobService",
    "DeterministicPreferenceReviewer",
    "PreferenceReviewRunResult",
    "PreferenceReviewRunner",
    "ReviewWorker",
    "ReviewWorkerResult",
]
