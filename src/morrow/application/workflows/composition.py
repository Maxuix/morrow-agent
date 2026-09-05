"""Opt-in Workflow runtime composition; ordinary Direct remains the default path.

This builder is the explicit application/test entry for the isolated Workflow
slice. Nothing here is wired into the default chat composition.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from morrow.application.workflows.finalizer import WorkflowOutcomeFinalizer
from morrow.application.workflows.patching import PatchApplicationService
from morrow.application.workflows.queries import WorkflowQueryService
from morrow.application.workflows.replan import ReplanCoordinator
from morrow.application.workflows.scheduler import WorkflowScheduler
from morrow.application.workflows.start import WorkflowStartService
from morrow.application.workflows.transitions import WorkflowTransitionService
from morrow.core.models import utc_now


@dataclass(frozen=True)
class WorkflowRuntime:
    start: WorkflowStartService
    scheduler: WorkflowScheduler
    transitions: WorkflowTransitionService
    finalizer: WorkflowOutcomeFinalizer
    queries: WorkflowQueryService
    patches: PatchApplicationService
    replan: ReplanCoordinator


def build_workflow_runtime(
    journal,
    store_session,
    *,
    workspace_id: str,
    artifacts,
    agent_publication,
    preparation,
    id_source,
    runtime_instance_id: str,
    clock: Callable[[], datetime] = utc_now,
    skill_selection=None,
    preference_loader=None,
    initialize_context=None,
    retry_sleep=None,
    faults=None,
    mutation=None,
    change_capture=None,
) -> WorkflowRuntime:
    transitions = WorkflowTransitionService(journal, workspace_id=workspace_id, clock=clock)
    finalizer = WorkflowOutcomeFinalizer(
        journal,
        workspace_id=workspace_id,
        transitions=transitions,
        id_source=id_source,
        clock=clock,
        artifacts=artifacts,
    )
    start = WorkflowStartService(
        journal,
        workspace_id=workspace_id,
        artifacts=artifacts,
        id_source=id_source,
        clock=clock,
    )
    scheduler = WorkflowScheduler(
        journal,
        workspace_id=workspace_id,
        id_source=id_source,
        store_session=store_session,
        runtime_instance_id=runtime_instance_id,
        artifacts=artifacts,
        transitions=transitions,
        finalizer=finalizer,
        agent_publication=agent_publication,
        preparation=preparation,
        clock=clock,
        skill_selection=skill_selection,
        preference_loader=preference_loader,
        initialize_context=initialize_context,
        retry_sleep=retry_sleep,
        faults=faults,
        mutation=mutation,
        change_capture=change_capture,
    )
    patches = PatchApplicationService(
        journal,
        workspace_id=workspace_id,
        catalog=agent_publication.catalog,
        id_source=id_source,
        finalizer=finalizer,
        clock=clock,
    )
    replan = ReplanCoordinator(patches, transitions)
    scheduler.replan = replan
    return WorkflowRuntime(
        start=start,
        scheduler=scheduler,
        transitions=transitions,
        finalizer=finalizer,
        queries=WorkflowQueryService(journal, workspace_id=workspace_id),
        patches=patches,
        replan=replan,
    )
