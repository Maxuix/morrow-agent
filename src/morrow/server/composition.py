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
from .workflow_stream import WorkflowStreamBridge


def build_server_context(
    application,
    identity,
    *,
    permission_profile: PermissionProfile | None = None,
) -> ServerContext:
    """Compose the full server stack; call only on the Core Host thread."""

    config = application.global_store.load().value
    if config is None or config.active_model is None:
        return _build_management_context(application, identity, permission_profile)
    try:
        provider, model = application.provider_service.build_active()
    except Exception:
        # A missing key, unavailable adapter or failed client construction must
        # leave the local settings surface available for repair. No operational
        # store or Session has been opened by this Provider-only preparation.
        return _build_management_context(application, identity, permission_profile)

    hub = EventHub()
    supervisor = RunSupervisor()
    waiters = ApprovalWaiters()
    approval_port = ServerApprovalPort(waiters)
    products = build_session_application(
        application,
        identity,
        permission_profile=permission_profile,
        approval_port=approval_port,
        provider=provider,
        model=model,
        persist_session=False,
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
    context = ServerContext(
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
            products.api,
            products.preference_service,
            products.commands.config_service,
            skills,
        ),
        close=products.persistence.store_session.close,
        store_handle=products.persistence.store_session,
    )

    _attach_chat(
        context, identity, permission_profile, products.persistence.store_session, approval_port
    )
    # Workflow leaves stream their stage progress through the same reply
    # projection; the Scheduler never depends on the transport itself. The
    # bridge also serves the reasoning-delta seam (P3.2) on the same object.
    bridge = WorkflowStreamBridge(context.chat, journal, identity.workspace_id)
    products.workflow_runtime.scheduler.event_observer = bridge
    products.workflow_runtime.scheduler.activity_observer = bridge
    journal.workflows.planning.activity_listener = bridge.planning_event
    return context


def make_context_builder(
    application,
    identity,
    *,
    permission_profile: PermissionProfile | None = None,
) -> Callable[[], ServerContext]:
    def build() -> ServerContext:
        from .workspaces import WorkspaceRuntimeRegistry

        return WorkspaceRuntimeRegistry.build(application, identity, permission_profile)

    return build


class _ExecutionProxy:
    """Resolve execution services only when an execution operation requests them."""

    _execution_proxy = True

    def __init__(self, load, attribute=None, **ready):
        self._load = load
        self._attribute = attribute
        self.__dict__.update(ready)

    def __getattr__(self, name):
        products = self._load()
        value = getattr(products, self._attribute) if self._attribute else products
        return getattr(value, name)


def _build_management_context(
    application, identity, permission_profile, *, handle=None, journal=None
):
    from pathlib import Path

    from morrow.adapters.state.preference_yaml import PreferenceYamlStore
    from morrow.application.preferences.queries import PreferenceQueries
    from morrow.application.preferences.tool import PreferenceManagementService
    from morrow.application.preferences.writer import PreferenceWriter
    from morrow.application.workflows.queries import WorkflowQueryService
    from morrow.bootstrap import (
        _open_operational_store,
        build_operational_api,
        build_operational_services,
    )
    from morrow.core.application import ApplicationError, ApplicationErrorCode
    from morrow.services.profile_configuration import ConfigPatchService

    owns_handle = handle is None
    handle = handle or _open_operational_store(application)
    try:
        operational = build_operational_services(
            application,
            identity.workspace_id,
            handle=handle,
            write=True,
            workspace_root=Path(identity.path),
            journal=journal,
        )
        journal = operational.journal
        yaml = PreferenceYamlStore(application.data_root.root)
        writer = PreferenceWriter(
            yaml, journal, identity.workspace_id, id_source=application.id_source, clock=journal.now
        )
        profile = ConfigPatchService(
            application.project_store, application.global_store, identity.workspace_id
        )
        profile.preference_writer = writer
        preferences = PreferenceManagementService(
            writer, PreferenceQueries(yaml, identity.workspace_id)
        )
        api = build_operational_api(
            application,
            identity.workspace_id,
            operational,
            config_service=profile,
            preference_writer=writer,
        )
        skills = build_skill_services(
            application, workspace_id=identity.workspace_id, journal=journal
        )
        hub = EventHub()
        waiters = ApprovalWaiters()
        approval_port = ServerApprovalPort(waiters)
        emitter = WorkflowEventEmitter(
            journal,
            workspace_id=identity.workspace_id,
            id_source=application.id_source,
            clock=journal.now,
            hub=hub,
        )
        approval_port.emitter = emitter
        cache = []

        def load():
            if not cache:
                config = application.global_store.load().value
                if config is None or config.active_model is None:
                    raise ApplicationError(
                        ApplicationErrorCode.UNAVAILABLE,
                        "Configure a Provider and active model before execution",
                    )
                products = build_session_application(
                    application,
                    identity,
                    permission_profile=permission_profile,
                    approval_port=approval_port,
                    store_session=handle,
                    journal=journal,
                    persist_session=False,
                )
                products.workflow_runtime.transitions.event_sink = emitter.emit
                workflow_bridge = WorkflowStreamBridge(context.chat, journal, identity.workspace_id)
                products.workflow_runtime.scheduler.event_observer = workflow_bridge
                products.workflow_runtime.scheduler.activity_observer = workflow_bridge
                journal.workflows.planning.activity_listener = workflow_bridge.planning_event
                cache.append(products)
                if context.workspaces is not None:
                    products.api.maintenance_check = context.workspaces.require_maintenance_idle
                    products.backup.maintenance_check = context.workspaces.require_maintenance_idle
                from .commands import ServerCommands

                if products.workflow_runtime.replan is not None:
                    products.workflow_runtime.replan.on_applied = ServerCommands(
                        context
                    )._replan_applied
                    products.workflow_runtime.replan.planning = context.chat.planning
                    products.workflow_runtime.replan.changes = context.chat.changes
                executor = products.orchestrator.runtime.loop.tool_executor
                context.tool_catalog = tuple(
                    {
                        "name": definition.function.name,
                        "description": definition.function.description,
                    }
                    for definition in (executor.definitions if executor is not None else ())
                )
            return cache[0]

        queries = WorkflowQueryService(journal, workspace_id=identity.workspace_id)
        context = ServerContext(
            workspace_id=identity.workspace_id,
            application=application,
            journal=journal,
            api=api,
            management=_ExecutionProxy(load, "workflow_management"),
            runtime=_ExecutionProxy(load, "workflow_runtime", queries=queries),
            products=_ExecutionProxy(load),
            emitter=emitter,
            hub=hub,
            supervisor=RunSupervisor(),
            approval_waiters=waiters,
            skill_queries=skills.queries,
            context_management=ManagementService(api, preferences, profile, skills),
            close=handle.close if owns_handle else lambda: None,
            store_handle=handle,
        )
        _attach_chat(context, identity, permission_profile, handle, approval_port)
        return context
    except BaseException:
        if owns_handle:
            handle.close()
        raise


def _attach_chat(context, identity, permission_profile, handle, approval_port):
    from morrow.application.chat_runtime import SessionRuntimeManager
    from morrow.server.replies import SessionReasoningObserver

    def factory(session_id, model=None):
        provider = None
        if model is not None:
            config = context.application.global_store.load().value.providers[model.provider_id]
            credential = context.application.provider_service.credential_resolver(
                model.provider_id, config.credential_ref
            )
            provider = context.application.registry.create(config, credential)
        products = build_session_application(
            context.application,
            identity,
            permission_profile=permission_profile,
            approval_port=approval_port,
            resume_session_id=session_id,
            provider=provider,
            model=model,
            store_session=handle,
            journal=context.journal,
            activity_observer=SessionReasoningObserver(
                context.chat.streams, identity.workspace_id, session_id
            ),
            pause_control=context.chat.turn_pause_control,
        )
        if context.workspaces is not None:
            products.api.maintenance_check = context.workspaces.require_maintenance_idle
            products.backup.maintenance_check = context.workspaces.require_maintenance_idle
        return products

    context.chat = SessionRuntimeManager(
        workspace_id=identity.workspace_id,
        api=context.api,
        factory=factory,
        execution_lock=context.supervisor.execution_lock,
        history_bytes=lambda sid: context.journal.chat_timeline.history_bytes(
            identity.workspace_id, sid
        ),
    )

    # Durable pause authority shared by the chat endpoints, the drive wrapper
    # and every chat AgentLoop (P03/P04 coordinator wiring, lane A seams).
    from morrow.application.execution_pause import ChatTurnPauseControl, ExecutionPauseService

    context.chat.pause_service = ExecutionPauseService(
        context.journal,
        workspace_id=identity.workspace_id,
        id_source=context.application.id_source,
        clock=context.journal.now,
    )
    context.chat.turn_pause_control = ChatTurnPauseControl(context.journal, identity.workspace_id)

    from morrow.application.interactions import InteractionService

    context.chat.permission_profile = permission_profile or PermissionProfile()
    from morrow.application.chat_settings import ChatSettingsService
    from morrow.core.capabilities import PermissionPreset

    context.chat.settings = ChatSettingsService(
        context.application,
        context.journal,
        identity.workspace_id,
        default_permission=next(
            (
                p.value
                for p in PermissionPreset
                if PermissionProfile.from_preset(p) == context.chat.permission_profile
            ),
            "manual",
        ),
    )

    from morrow.application.attachments import AttachmentService

    context.chat.attachments = AttachmentService(
        context.journal,
        context.api.artifacts,
        identity.workspace_id,
        context.chat.require_session,
        context.application.id_source,
    )

    from morrow.application.workflows.plan_admission import TaskPlanAdmissionService
    from morrow.application.workflows.task_planning import TaskPlanningService

    context.chat.planning = TaskPlanningService(context)
    context.chat.planning.recover()
    context.chat.admission = TaskPlanAdmissionService(context)

    from morrow.application.control_receipts import ControlReceiptService
    from morrow.application.workflow_controls import WorkflowControlService
    from morrow.application.workflows.plan_change import PlanChangeService

    context.chat.controls = WorkflowControlService(context)
    context.chat.changes = PlanChangeService(context)
    context.chat.control_receipts = ControlReceiptService(
        context.chat, context.journal, identity.workspace_id, context.application.id_source
    )
    runtime = context.__dict__.get("runtime")
    if runtime is not None and not getattr(runtime, "_execution_proxy", False):
        if getattr(runtime, "replan", None) is not None:
            runtime.replan.planning = context.chat.planning
            runtime.replan.changes = context.chat.changes

    context.chat.interactions = InteractionService(
        context.chat,
        context.application,
        context.journal,
        identity.workspace_id,
    )

    from morrow.application.chat_timeline import TimelineService
    from morrow.application.result_presentation import TaskResultProjector
    from morrow.application.task_artifacts import TaskArtifactsService

    def result_projector():
        """Chat-result projection over the existing TaskArtifacts authority.

        Built per index instance so the Workflow query projection (wired as a
        later composition step) is read once it exists, never captured as
        ``None``.
        """

        return TaskResultProjector(
            context.journal,
            TaskArtifactsService(
                context.journal,
                artifacts=context.api.artifacts,
                workflow_queries=getattr(context.runtime, "queries", None),
                workspace_id=identity.workspace_id,
            ),
            artifacts=context.api.artifacts,
            workspace_id=identity.workspace_id,
        )

    context.chat.timeline = TimelineService(
        context.chat,
        context.journal,
        identity.workspace_id,
        result_projector_factory=result_projector,
    )

    from morrow.server.replies import ReplyStreams

    context.chat.streams = ReplyStreams(context.chat, context.journal, identity.workspace_id)
    context.chat.interactions.on_admit = context.chat.streams.state
    context.chat.interactions.on_event = context.chat.streams.event
    context.chat.interactions.on_change = context.chat.streams.changed
    # Post-commit timeline fan-out (P07, lane C wiring): committed entries wake
    # the session stream so clients replay the window by item_id; a crash
    # before this runs leaves the durable outbox row for replay_outbox().
    context.chat.timeline.index.broadcast = lambda entry: context.chat.streams.changed(
        entry.root_session_id
    )

    from morrow.application.chat_workflows import ChatWorkflowService
    from morrow.server.commands import ServerCommands

    context.chat.workflows = ChatWorkflowService(context)
    context.chat.workflows.on_started = lambda run: ServerCommands(context)._emit_run_created(
        run, relation="chat"
    )
    context.chat.workflows.on_change = context.chat.streams.changed

    from morrow.application.execution import ExecutionProjection

    context.chat.execution = ExecutionProjection(
        manager=context.chat,
        journal=context.journal,
        workspace_id=identity.workspace_id,
        supervisor=context.supervisor,
        planning=context.chat.planning,
        chat_pause_enabled=True,
    )
    from morrow.application.recovery_status import RecoveryStatusProjection

    context.chat.recovery_status = RecoveryStatusProjection(
        manager=context.chat,
        journal=context.journal,
        workspace_id=identity.workspace_id,
    )
    _attach_execution_sync(context)


def _attach_execution_sync(context):
    """Mirror workflow lifecycle facts into the owning sessions' reply streams.

    The transitions event sink is the one seam every run/node state change
    crosses. Subscribing here keeps the Chat execution projection live across
    node switches, pause/drain settlement and terminal closes — including for
    runs the Scheduler drives outside the ordinary chat driver registry.
    """

    journal = context.journal
    workspace_id = context.workspace_id

    def sessions_for_run(workflow_run_id):
        run = journal.workflows.get_run(workspace_id, workflow_run_id)
        if run is None:
            return ()
        root = journal.get_task_run(workspace_id, run.root_task_run_id)
        sessions = {root.session_id} if root is not None else set()
        for node in journal.workflows.list_nodes(workspace_id, workflow_run_id):
            if node.conversation_session_id:
                sessions.add(node.conversation_session_id)
        return sessions

    def on_workflow_event(_event_type, aggregate_kind, aggregate_id, payload):
        run_id = (
            aggregate_id if aggregate_kind == "workflow_run" else payload.get("workflow_run_id")
        )
        if not isinstance(run_id, str):
            return
        for session_id in sessions_for_run(run_id):
            journal.after_commit(
                lambda session_id=session_id: context.chat.streams.changed(session_id)
            )

    context.emitter.subscribers.append(on_workflow_event)
