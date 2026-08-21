"""Application services for generic Preference lifecycle commands."""

from morrow.adapters.state.preference_projection import preferences_from_entries
from morrow.application.preferences.bridge import (
    candidate_operations,
    legacy_statement,
)
from morrow.application.preferences.queries import PreferenceQueries
from morrow.application.preferences.recovery import (
    PreferenceRecoveryReport,
    PreferenceWriteRecovery,
)
from morrow.application.preferences.tool import (
    ManagePreferenceOperation,
    ManagePreferencesArguments,
    PreferenceManagementService,
    make_preference_management_tool,
)
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
]
