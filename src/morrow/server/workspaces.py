"""Bounded workspace service lifetime on the existing Core owner loop."""

from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path

from morrow.application.execution_gate import ExecutionCoordinator
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.capabilities import AccessScope, ProcessIsolation
from morrow.services.directories import DirectoryService
from morrow.services.workspace import WorkspaceError, WorkspaceWriterLock

MAX_WORKSPACE_RUNTIMES = 4


class WorkspaceRuntimeRegistry:
    def __init__(self, application, default, identity, permission_profile, lock):
        self.application = application
        self.default = default
        self.permission_profile = permission_profile
        self.contexts = OrderedDict({identity.workspace_id: default})
        self.locks = {identity.workspace_id: lock}
        self.roots = {identity.workspace_id: Path(identity.path).resolve()}
        self.directories = DirectoryService((Path.home(), Path(identity.path).resolve().parent))
        self._close_store = default.close
        self.coordinator = ExecutionCoordinator()
        from morrow.application.attachment_parsing import AttachmentParsePool

        self.attachment_pool = AttachmentParsePool()
        self.maintaining = False
        self.maintenance_task = None
        self.maintenance_jobs = OrderedDict()
        default.workspaces = self
        default.close = self.close
        self.bind(default, identity)

    def bind(self, context, identity):
        context.chat.attachments.pool = self.attachment_pool

        def confined():
            profile = context.chat.permission_profile
            # Each Run reacquires the gate. Any pending unconfined request makes
            # this workspace conservative; changing defaults cannot widen a held Run.
            bound = [
                entry["binding"].get("settings", {}).get("permission")
                for sid in context.chat.drivers
                for entry in context.chat.interactions.records.pending(context.workspace_id, sid)
            ]
            if any(p not in (None, "auto-sandboxed") for p in bound):
                return False
            if bound and all(p == "auto-sandboxed" for p in bound):
                from morrow.core.capabilities import PermissionPreset, PermissionProfile

                profile = PermissionProfile.from_preset(PermissionPreset.AUTO_SANDBOXED)
            return (
                profile.access_scope is AccessScope.WORKSPACE
                and profile.process_isolation is ProcessIsolation.NATIVE_SANDBOX
                and not context.journal.list_mcp_servers("global")
                and not context.journal.list_mcp_servers("workspace", scope_id=context.workspace_id)
            )

        # Workflow definitions may select capabilities beyond the foreground
        # Chat profile, so their top-level gate remains globally conservative.
        context.supervisor.execution_lock = self.coordinator.gate(identity.path)
        context.chat.execution_lock = self.coordinator.gate(identity.path, confined=confined)
        # Recovery uses a consumed Run's frozen permissions, absent from the
        # pending-input classifier above. Keep its admission conservative.
        context.chat.recovery_lock = context.supervisor.execution_lock
        context.chat.streams.host_subscriber_count = lambda: sum(
            c.chat.streams.subscribers for c in self.contexts.values()
        )
        context.chat.interactions.check_admission = self.check_admission
        context.api.maintenance_check = self.require_maintenance_idle
        if not getattr(context.products, "_execution_proxy", False):
            context.products.backup.maintenance_check = self.require_maintenance_idle

    def check_admission(self):
        if self.maintaining:
            raise ApplicationError(
                ApplicationErrorCode.BUSY, "Data root maintenance is in progress"
            )

    def require_maintenance_idle(self):
        self.check_admission()
        if (
            self.coordinator.active
            or self.coordinator.waiting
            or any(
                self.busy(wid, pending=True, subscriptions=False)
                for wid in self.application.workspace_service._entries().workspaces
            )
        ):
            raise ApplicationError(
                ApplicationErrorCode.BUSY,
                "Data root has active work or references; stop/withdraw work across all workspaces",
            )

    @contextmanager
    def maintenance(self):
        self.require_maintenance_idle()
        additional = []
        self.maintaining = True
        try:
            # Retain idle registered workspaces against independent CLI writers.
            for wid in self.application.workspace_service._entries().workspaces:
                if wid not in self.locks:
                    lock = WorkspaceWriterLock(self.application.data_root, wid, timeout=0)
                    try:
                        lock.__enter__()
                    except WorkspaceError:
                        raise ApplicationError(
                            ApplicationErrorCode.BUSY, "另一个 Core 或 CLI 正占用数据根内的工作区"
                        ) from None
                    additional.append(lock)
            yield
        finally:
            for lock in reversed(additional):
                lock.__exit__(None, None, None)
            self.maintaining = False

    @classmethod
    def build(cls, application, identity, permission_profile):
        from .composition import build_server_context

        lock = WorkspaceWriterLock(application.data_root, identity.workspace_id)
        lock.__enter__()
        try:
            context = build_server_context(
                application, identity, permission_profile=permission_profile
            )
            cls(application, context, identity, permission_profile, lock)
            return context
        except BaseException:
            lock.__exit__(None, None, None)
            raise

    def require_identity(self, workspace_id):
        service = self.application.workspace_service
        entry = service._entries().workspaces.get(workspace_id)
        if entry is None or entry.removed:
            raise ApplicationError(ApplicationErrorCode.CROSS_WORKSPACE, "Unknown workspace scope")
        identity = service.get(workspace_id)
        if not Path(identity.path).is_dir() or str(Path(identity.path).resolve()) != identity.path:
            raise ApplicationError(
                ApplicationErrorCode.UNAVAILABLE, "Workspace folder is missing; relink it"
            )
        return identity

    def get(self, workspace_id):
        identity = self.require_identity(workspace_id)
        if workspace_id in self.contexts:
            if Path(identity.path).resolve() != self.roots[workspace_id]:
                raise ApplicationError(
                    ApplicationErrorCode.CONFLICT, "Workspace root changed; reconnect Core"
                )
            self.contexts.move_to_end(workspace_id)
            return self.contexts[workspace_id]
        self.check_admission()
        while len(self.contexts) >= MAX_WORKSPACE_RUNTIMES:
            idle = next(
                (
                    wid
                    for wid in self.contexts
                    if wid != self.default.workspace_id and not self.busy(wid)
                ),
                None,
            )
            if idle is None:
                raise ApplicationError(
                    ApplicationErrorCode.BUSY, "Workspace cache is busy; close idle subscriptions"
                )
            self.unload(idle)
        lock = WorkspaceWriterLock(self.application.data_root, workspace_id)
        try:
            lock.__enter__()
        except WorkspaceError:
            raise ApplicationError(
                ApplicationErrorCode.BUSY,
                "Workspace has another writer; attach to its Core or close it",
            ) from None
        try:
            from .composition import _build_management_context

            context = _build_management_context(
                self.application,
                identity,
                self.permission_profile,
                handle=self.default.store_handle,
                journal=self.default.journal,
            )
            context.workspaces = self
            self.bind(context, identity)
            self.contexts[workspace_id] = context
            self.locks[workspace_id] = lock
            self.roots[workspace_id] = Path(identity.path).resolve()
            return context
        except BaseException:
            lock.__exit__(None, None, None)
            raise

    def busy(self, workspace_id, *, pending=False, subscriptions=True):
        context = self.contexts.get(workspace_id)
        if context is not None and (
            context.chat.drivers
            or context.chat.attachments.jobs
            or context.journal._backend.read_one(
                "SELECT 1 FROM chat_attachments WHERE workspace_id=? AND state IN ('uploading','processing') LIMIT 1",
                (workspace_id,),
            )
            or context.supervisor._drivers
            or (subscriptions and context.chat.streams.subscribers)
            or (subscriptions and context.hub._subscribers)
        ):
            return True
        if pending:
            journal = self.default.journal
            return any(
                journal.interactions.pending(workspace_id, session.session_id)
                for session in journal.list_sessions(workspace_id)
            )
        return False

    def require_idle(self, workspace_id):
        if self.busy(workspace_id, pending=True):
            raise ApplicationError(
                ApplicationErrorCode.BUSY,
                "Workspace is in use; stop or withdraw queued work and close subscriptions",
            )

    def unload(self, workspace_id):
        if workspace_id == self.default.workspace_id:
            raise ApplicationError(ApplicationErrorCode.BUSY, "Startup workspace remains loaded")
        if self.busy(workspace_id):
            raise ApplicationError(ApplicationErrorCode.BUSY, "Workspace runtime is in use")
        context = self.contexts.pop(workspace_id, None)
        if context is not None:
            context.chat.runtimes.clear()
            context.chat.streams.states.clear()
            context.close()
            self.locks.pop(workspace_id).__exit__(None, None, None)
            self.roots.pop(workspace_id)

    async def shutdown(self):
        if self.maintenance_task is not None:
            # Do not release maintenance while an uncancellable filesystem worker
            # is still copying or verifying the bundle.
            import asyncio

            await asyncio.shield(self.maintenance_task)
        for context in tuple(self.contexts.values()):
            await context.chat.shutdown()
            await context.supervisor.shutdown()
            context.approval_waiters.cancel_all()

    def close(self):
        for wid, context in tuple(self.contexts.items()):
            if context is not self.default:
                context.close()
            self.locks.pop(wid).__exit__(None, None, None)
        self.contexts.clear()
        self.roots.clear()
        self._close_store()
