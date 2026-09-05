"""Server composition: build the Core service bundle on the Core thread.

The builder returned here must run inside the Core Host's runtime thread so
the thread-bound SQLite session, the journal and every composed service share
one owner. GUI and CLI invoke the same application services — this module only
wires them, adding the event sink, the approval port and the run supervisor.
"""

from __future__ import annotations

from collections.abc import Callable

from morrow.application.management import ManagementService
from morrow.bootstrap import build_session_application, build_skill_services
from morrow.core.capabilities import PermissionProfile

from .approvals import ServerApprovalPort
from .context import ServerContext
from .events import EventHub, WorkflowEventEmitter
from .host import ApprovalWaiters, RunSupervisor


def build_server_context(
    application,
    identity,
    *,
    permission_profile: PermissionProfile | None = None,
) -> ServerContext:
    """Compose the full server stack; call only on the Core Host thread."""

    hub = EventHub()
    supervisor = RunSupervisor()
    waiters = ApprovalWaiters()
    approval_port = ServerApprovalPort(waiters)
    products = build_session_application(
        application,
        identity,
        permission_profile=permission_profile,
        approval_port=approval_port,
    )
    journal = products.api.journal
    emitter = WorkflowEventEmitter(
        journal,
        workspace_id=identity.workspace_id,
        id_source=application.id_source,
        clock=journal.now,
        hub=hub,
    )
    # The transitions service is shared by the scheduler, the finalizer and the
    # management facade, so this one seam covers every run/node state change.
    products.workflow_runtime.transitions.event_sink = emitter.emit
    approval_port.emitter = emitter

    tool_executor = products.orchestrator.runtime.loop.tool_executor
    tool_catalog = tuple(
        {
            "name": definition.function.name,
            "description": definition.function.description,
        }
        for definition in (tool_executor.definitions if tool_executor is not None else ())
    )
    skills = build_skill_services(application, workspace_id=identity.workspace_id, journal=journal)
    return ServerContext(
        workspace_id=identity.workspace_id,
        application=application,
        journal=journal,
        api=products.api,
        management=products.workflow_management,
        runtime=products.workflow_runtime,
        emitter=emitter,
        hub=hub,
        supervisor=supervisor,
        approval_waiters=waiters,
        skill_queries=skills.queries,
        tool_catalog=tool_catalog,
        products=products,
        context_management=ManagementService(
            products.api, products.preference_service, products.commands.config_service, skills
        ),
        close=products.persistence.store_session.close,
    )


def make_context_builder(
    application,
    identity,
    *,
    permission_profile: PermissionProfile | None = None,
) -> Callable[[], ServerContext]:
    def build() -> ServerContext:
        return build_server_context(application, identity, permission_profile=permission_profile)

    return build
