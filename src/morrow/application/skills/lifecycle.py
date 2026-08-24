"""Governed Skill package and Binding lifecycle application boundary."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime

from morrow.adapters.skills.managed_store import ManagedSkillPackageStore
from morrow.application.skills.binding_lifecycle import SkillBindingLifecycleMixin
from morrow.application.skills.bindings import SkillBindingService
from morrow.application.skills.errors import (
    SkillLifecycleError,
    SkillLifecycleNeedsResolution,
)
from morrow.application.skills.package_lifecycle import SkillPackageLifecycleMixin
from morrow.application.skills.persistence import SkillLifecyclePersistenceMixin
from morrow.application.skills.recovery import (
    SkillLifecycleRecoveryMixin,
    SkillOperationStore,
)
from morrow.application.skills.resolution import SkillLifecycleResolutionMixin
from morrow.core.skills.bindings import SkillLifecycleResult
from morrow.core.skills.catalog import SkillVersion
from morrow.runtime.ids import RandomIdSource


class SkillLifecycleService(
    SkillPackageLifecycleMixin,
    SkillBindingLifecycleMixin,
    SkillLifecycleRecoveryMixin,
    SkillLifecycleResolutionMixin,
    SkillLifecyclePersistenceMixin,
):
    """Application boundary for Skill package and Binding changes.

    The catalog is read-only input. Extension YAML remains the Binding authority;
    the operational journal receives only bounded evidence after filesystem/YAML
    work has been verified.
    """

    def __init__(
        self,
        yaml_store,
        package_store: ManagedSkillPackageStore,
        catalog,
        *,
        journal=None,
        workspace_id: str | None = None,
        id_source=None,
        clock: Callable[[], datetime] | None = None,
        reference_checker: Callable[[str], tuple[str, ...]] | None = None,
        dependency_checker: Callable[[SkillVersion], tuple[str, ...]] | None = None,
        available_tools: Iterable[str] | None = None,
        available_mcp_servers: Iterable[str] | None = None,
    ) -> None:
        self.yaml_store = yaml_store
        self.package_store = package_store
        self.catalog = catalog
        self.journal = journal
        self.workspace_id = workspace_id
        self.id_source = id_source or RandomIdSource()
        self.clock = clock or (lambda: datetime.now(UTC))
        self.bindings = SkillBindingService(yaml_store, workspace_id)
        self.operations = SkillOperationStore(package_store.data_root)
        self.reference_checker = reference_checker
        self.dependency_checker = dependency_checker
        self.available_tools = frozenset(available_tools) if available_tools is not None else None
        self.available_mcp_servers = (
            frozenset(available_mcp_servers) if available_mcp_servers is not None else None
        )
        self._memory_receipts: dict[str, tuple[str, SkillLifecycleResult]] = {}


__all__ = [
    "SkillLifecycleError",
    "SkillLifecycleNeedsResolution",
    "SkillLifecycleService",
]
