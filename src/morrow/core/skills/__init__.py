"""Core Skill domain contracts: identity, manifest, trust, bindings and catalog views.

This package has no filesystem, YAML, SQLite or CLI dependency. Package IO and
validation live in ``adapters/skills``; discovery and conflict resolution live
in ``application/skills``.
"""

from .bindings import (
    ExtensionDocument,
    ExtensionMcpSection,
    GlobalExtensionDocument,
    SkillBinding,
    SkillLifecycleResult,
    SkillSelectionMode,
    SkillValidationReport,
    WorkspaceExtensionDocument,
)
from .context import SkillContextEntry, SkillContextProjection
from .drafts import (
    DRAFT_ID_PREFIX,
    VALIDATION_ID_PREFIX,
    SkillDraft,
    SkillDraftDiff,
    SkillDraftStatus,
    SkillDraftValidationReport,
    SkillFindingSeverity,
    SkillValidationFinding,
)
from .resources import SkillResourceRequest, SkillResourceResult
from .selection import SkillOmission, SkillSelection, SkillSelectionPlan
from .usage import (
    USAGE_ID_PREFIX,
    SkillComparisonStatus,
    SkillUsage,
    SkillUsageComparison,
    SkillUsageMetrics,
    SkillUsageStatus,
)

__all__ = [
    "ExtensionDocument",
    "ExtensionMcpSection",
    "GlobalExtensionDocument",
    "SkillBinding",
    "SkillLifecycleResult",
    "SkillSelectionMode",
    "SkillValidationReport",
    "WorkspaceExtensionDocument",
    "SkillContextEntry",
    "SkillContextProjection",
    "DRAFT_ID_PREFIX",
    "VALIDATION_ID_PREFIX",
    "SkillDraft",
    "SkillDraftDiff",
    "SkillDraftStatus",
    "SkillDraftValidationReport",
    "SkillFindingSeverity",
    "SkillOmission",
    "SkillResourceRequest",
    "SkillResourceResult",
    "SkillSelection",
    "SkillSelectionPlan",
    "SkillValidationFinding",
    "SkillComparisonStatus",
    "SkillUsage",
    "SkillUsageComparison",
    "SkillUsageMetrics",
    "SkillUsageStatus",
    "USAGE_ID_PREFIX",
]
