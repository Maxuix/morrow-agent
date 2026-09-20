"""Chat Workflow admission reuses TaskService, Start and the Workflow supervisor."""

from morrow.application.workflows.start import StartWorkflowCommand
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.domain import canonical_json_bytes, sha256_digest
from morrow.core.workflows.contracts import TaskContract


def workflow_command(entry):
    return "cmd_chat_workflow_" + sha256_digest(entry["interaction_id"])[:40]


class ChatWorkflowService:
    def __init__(self, context):
        self.context = context

    def binding(self, session_id, request):
        c = self.context
        selection = request.workflow
        revision = c.journal.workflows.get_revision(c.workspace_id, selection.workflow_revision_id)
        if revision is None or revision.workflow_definition_id != selection.workflow_definition_id:
            raise ApplicationError(ApplicationErrorCode.INVALID, "请选择已发布的 Workflow 精确版本")
        if c.journal.workflows.get_task_plan_provenance(
            c.workspace_id, selection.workflow_revision_id
        ) or revision.workflow_definition_id.startswith("task_"):
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "task plans require an explicit start"
            )
        c.runtime.start._check_gates(c.journal, revision)
        if (
            request.attachments
            or request.allow_unconfined_host
            or request.settings.model_dump(exclude_none=True)
        ):
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "Workflow 使用其节点模型与权限配置；请将附件用于普通对话，或移除后提交任务文本",
            )
        if selection.promoted_plan is not None:
            planning = selection.promoted_plan
            draft = c.products.workflow_drafts.get(planning.draft_id)
            metadata = draft.draft.planner if draft else None
            policy = (
                c.products.orchestration_policies.resolve(metadata.features.task_type)
                if metadata
                else None
            )
            if not (
                draft
                and metadata
                and policy
                and draft.draft.frozen_workflow_revision_id == selection.workflow_revision_id
                and metadata.source_hash == draft.draft.source_hash
                and metadata.request_digest
                == sha256_digest(canonical_json_bytes(planning.model_dump(mode="json")))
                and planning.task.objective == request.text
                and (policy.scope, policy.policy_id, policy.revision)
                == (metadata.policy_scope, metadata.policy_id, metadata.policy_revision)
                and c.products.orchestration_policies.auto_run(policy)
            ):
                raise ApplicationError(
                    ApplicationErrorCode.CONFLICT, "自动运行依据已变化；请审阅计划后手动发送"
                )
        TaskContract(objective=request.text)
        if selection.root_task_run_id:
            task = c.api.get_task(selection.root_task_run_id)
            if task is None or task.session_id != session_id:
                raise ApplicationError(
                    ApplicationErrorCode.NOT_FOUND, "Workflow 根任务不属于此对话"
                )
        return {
            "binding_version": 1,
            "workflow": selection.model_dump(mode="json"),
            "conversation_position": c.chat.require_session(session_id).conversation_position,
        }

    def run(self, entry):
        c = self.context
        receipt = c.journal.get_application_command_receipt(c.workspace_id, workflow_command(entry))
        return c.journal.workflows.get_run(c.workspace_id, receipt.result_id) if receipt else None

    async def drive(self, entry):
        c = self.context
        from morrow.core.interactions import InteractionRequest

        request = InteractionRequest.model_validate(entry["request"])
        run = self.run(entry)
        if run is None:
            # Revalidate current gates without changing the frozen revision choice.
            self.binding(entry["session_id"], request)
            selection = request.workflow
            sid = entry["session_id"]
            session = c.chat.require_session(sid)
            task_id = selection.root_task_run_id or session.current_task_run_id
            task = c.api.get_task(task_id) if task_id else None
            if task is None:
                task = c.api.task_new(
                    sid, command_id="cmd_chat_task_" + sha256_digest(entry["interaction_id"])[:40]
                ).value
            expected = selection.expected_root_row_version or task.row_version
            direct = c.management.requires_client_message(
                selection.workflow_definition_id, revision_id=selection.workflow_revision_id
            )
            started = c.management.start_foreground(
                StartWorkflowCommand(
                    workflow_definition_id=selection.workflow_definition_id,
                    workflow_revision_id=selection.workflow_revision_id,
                    session_id=sid,
                    root_task_run_id=task.task_run_id,
                    expected_root_row_version=expected,
                    contract=TaskContract(objective=request.text),
                    command_id=workflow_command(entry),
                    client_message_id=request.client_message_id if direct else None,
                )
            )
            run = started.run
            if not direct:
                c.journal.interactions.update(entry, status="consumed")
            # Reuse the server's existing fact publisher through a composition callback.
            self.on_started(run)
        # Drop the idle cached Session before the Workflow creates its Session-owned log.
        c.chat.runtimes.pop(entry["session_id"], None)
        c.supervisor.ensure_driver(
            run.workflow_run_id,
            lambda: c.runtime.scheduler.run(run.workflow_run_id, cancelled_is_user=False),
        )
        self.on_change(entry["session_id"])
        try:
            await c.supervisor.wait_driver(run.workflow_run_id)
        finally:
            c.chat.runtimes.pop(entry["session_id"], None)
        return c.journal.workflows.get_run(c.workspace_id, run.workflow_run_id)

    def page(self, session_id, *, before=None):
        c = self.context
        c.chat.require_session(session_id)
        rows = c.journal._backend.read_all(
            "SELECT client_message_id,position FROM chat_interactions WHERE workspace_id=? AND session_id=? AND json_extract(request_json,'$.intent')='explicit_workflow' AND position<? ORDER BY position DESC LIMIT 51",
            (c.workspace_id, session_id, before or 9223372036854775807),
        )
        items = []
        for key, position in rows[:50]:
            entry = c.journal.interactions.get(c.workspace_id, session_id, key)
            run = self.run(entry)
            outcomes = (
                c.journal.list_task_outcomes(c.workspace_id, run.root_task_run_id) if run else ()
            )
            latest = outcomes[-1] if outcomes else None
            items.append(
                {
                    "client_message_id": key,
                    "position": position,
                    "text": entry["request"]["text"],
                    "status": entry["status"],
                    "workflow": entry["binding"]["workflow"],
                    "workflow_run_id": run.workflow_run_id if run else None,
                    "task_run_id": run.root_task_run_id if run else None,
                    "run_status": run.status.value if run else None,
                    "outcome": {
                        "outcome_id": latest.outcome_id,
                        "summary": latest.summary,
                        "task_status": latest.task_status.value,
                    }
                    if latest
                    else None,
                }
            )
        return {"items": items, "next_cursor": str(rows[49][1]) if len(rows) > 50 else None}
