"""Shared GUI/CLI command facade. Domain services remain the only state writers."""

from __future__ import annotations

from morrow.application.context_management import ContextManagementQueries, management_wire
from morrow.application.skill_management import SkillManagementQueries
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.domain import sha256_digest
from morrow.core.learning_commands import (
    AcceptLearningCandidateCommand,
    DeleteProjectKnowledgeCommand,
    DisableProjectKnowledgeCommand,
    EditAndAcceptLearningCandidateCommand,
    EnableProjectKnowledgeCommand,
    MarkProjectKnowledgeDisputedCommand,
    RejectLearningCandidateCommand,
)

from . import management_requests as requests

COMMAND_MODELS = {
    "preferences": requests.PreferenceWriteRequest,
    "profile": requests.ProfileWriteRequest,
    "preference-decision": requests.PreferenceDecisionRequest,
    "learning-decision": requests.LearningDecisionRequest,
    "knowledge": requests.KnowledgeLifecycleRequest,
    "skill-binding": requests.SkillBindingRequest,
    "skill-draft": requests.SkillDraftRequest,
    "skill-draft-create": requests.SkillDraftCreateRequest,
    "workflow-feedback": requests.WorkflowFeedbackRequest,
    "workflow-policy-decision": requests.WorkflowPolicyDecisionRequest,
    "workflow-evaluation": requests.WorkflowEvaluationRequest,
}


class ManagementService:
    def __init__(self, api, preferences, profile, skills, *, workflow_feedback=None) -> None:
        self.workflow_feedback = workflow_feedback
        self.api = api
        self.preferences = preferences
        self.profile = profile
        self.skills = skills
        self.context = api.command_context
        self.workspace_id = api.workspace_id
        self.queries = ContextManagementQueries(api, profile.project_store)
        self.skill_queries = SkillManagementQueries(skills, api.workspace_id)

    def query(self, kind, *, scope="workspace", task_run_id=None, agent_run_id=None, page=0):
        if not isinstance(page, int) or isinstance(page, bool) or not 0 <= page <= 100000:
            raise ApplicationError(ApplicationErrorCode.INVALID, "management page is invalid")
        if scope not in {"workspace", "global"}:
            raise ApplicationError(ApplicationErrorCode.INVALID, "management scope is invalid")
        if kind == "context":
            result = self.queries.resolved(task_run_id=task_run_id, agent_run_id=agent_run_id)
            if self.workflow_feedback:
                from morrow.core.workflows.feedback import WorkflowPolicyCandidate

                result["pending_learning_count"] += sum(
                    c.status in {"proposed", "applying"}
                    for c in self.workflow_feedback.records.list(
                        WorkflowPolicyCandidate, self.workspace_id
                    )
                )
            return result
        if kind == "preferences":
            return self.queries.preferences(scope)
        if kind == "profile":
            return self.queries.profile()
        if kind == "learning":
            result = self.queries.learning(page)
            result["orchestration"] = (
                self.workflow_feedback.review_view(page)
                if self.workflow_feedback
                else {"items": [], "next_cursor": None}
            )
            return result
        if kind == "workflow-evaluation" and self.workflow_feedback:
            from morrow.application.workflows.evaluation import WorkflowEvaluationService

            return WorkflowEvaluationService(self.workflow_feedback).dashboard(page)
        if kind == "workflow-policy-candidates" and self.workflow_feedback:
            return self.workflow_feedback.review_view(page)
        if kind == "knowledge":
            return self.queries.knowledge(page)
        if kind == "skills":
            return self.skill_queries.catalog(scope)
        if kind == "skill-drafts":
            return self.skill_queries.drafts(page)
        raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "management query is missing")

    def execute(self, kind, request, *, target=None):
        """Receipts for adapters without a native receipt; reuse every native saga."""
        if kind not in COMMAND_MODELS:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "management command is missing")
        try:
            return self._execute(kind, request, target=target)
        except ApplicationError:
            raise
        except Exception as exc:
            # Domain errors can contain user values: expose only a stable status.
            code = str(getattr(exc, "code", "invalid")).casefold()
            if "stale" in code or "stale" in str(exc).casefold():
                selected = ApplicationErrorCode.STALE
            elif "conflict" in code or "conflict" in type(exc).__name__.casefold():
                selected = ApplicationErrorCode.CONFLICT
            elif "unavailable" in code:
                selected = ApplicationErrorCode.UNAVAILABLE
            elif "resolution" in code:
                selected = ApplicationErrorCode.NEEDS_RECOVERY
            else:
                selected = ApplicationErrorCode.INVALID
            raise ApplicationError(selected, "management command could not be applied") from exc

    def _execute(self, kind, request, *, target):
        if kind == "preferences":
            return self.preferences.apply(request.arguments, command_id=request.command_id)
        if kind in {"learning-decision", "knowledge"}:
            return self._native_command(kind, request, target)
        payload = {"target": target, **request.model_dump(mode="json", exclude={"command_id"})}
        command, digest, replay = self.context._prepare(
            "management_" + kind, payload, request.command_id
        )
        if replay is not None:
            return {"status": "replayed", "result_id": replay.result_id}

        def work(txn):
            value = self._adapter_command(kind, request, target)
            result_id = getattr(value, "draft_id", None) or target or kind
            self.context._receipt(
                txn,
                command_id=command,
                operation="management_" + kind,
                digest=digest,
                session_id=None,
                result_kind=kind,
                result_id=result_id,
                event_cursor=None,
            )
            return management_wire(
                {"status": "applied", "result_id": result_id, "value": self._result_value(value)}
            )

        # Profile and Draft acceptance own file/YAML publication, so never nest
        # their saga in an outer SQLite transaction. OCC prevents a blind retry
        # after an interrupted publication; the normal domain recovery remains authoritative.
        if kind in {"profile", "skill-binding", "workflow-policy-decision"} or (
            kind in {"skill-draft", "preference-decision"} and request.action == "accept"
        ):
            value = self._adapter_command(kind, request, target)
            result_id = getattr(value, "draft_id", None) or target or kind
            self.api.journal.transact(
                lambda txn: self.context._receipt(
                    txn,
                    command_id=command,
                    operation="management_" + kind,
                    digest=digest,
                    session_id=None,
                    result_kind=kind,
                    result_id=result_id,
                    event_cursor=None,
                )
            )
            return management_wire(
                {"status": "applied", "result_id": result_id, "value": self._result_value(value)}
            )
        return self.api.journal.transact(work)

    @staticmethod
    def _result_value(value):
        # A Draft carries an internal package reference that is not a UI fact.
        from morrow.core.skills.drafts import SkillDraft

        if isinstance(value, SkillDraft):
            return value.model_dump(mode="json", exclude={"package_ref"})
        return value

    def _adapter_command(self, kind, request, target):
        if kind == "workflow-feedback" and self.workflow_feedback:
            return self.workflow_feedback.submit(request)
        if kind == "workflow-policy-decision" and self.workflow_feedback:
            return self.workflow_feedback.decide(target, request)
        if kind == "workflow-evaluation" and self.workflow_feedback:
            from morrow.application.workflows.evaluation import WorkflowEvaluationService

            return WorkflowEvaluationService(self.workflow_feedback).record(request)
        if kind == "skill-binding":
            owner_request = request.model_copy(
                update={"command_id": "cmd_" + sha256_digest(request.command_id)[:48]}
            )
            return self._native_command(kind, owner_request, target)
        if kind == "profile":
            prepared = self.profile.prepare(request.command)
            if prepared.expected_revision != request.expected_revision:
                raise ApplicationError(ApplicationErrorCode.STALE, "Profile revision is stale")
            return self.profile.apply_prepared(prepared, operation_id=request.command_id)
        if kind == "preference-decision":
            inbox = self.api.preference_inbox
            if request.action == "accept":
                return inbox.accept(
                    target,
                    command_id=request.command_id,
                    expected_row_version=request.expected_row_version,
                    expected_document_revision=request.expected_document_revision,
                    expected_target_revision=request.expected_target_revision,
                    edit=request.edit,
                )
            return inbox.reject(
                target,
                command_id=request.command_id,
                expected_row_version=request.expected_row_version,
                suppress=request.action == "suppress",
            )
        if kind == "skill-draft-create":
            return self.skills.drafts.create_from_candidate(request.candidate_id)
        if kind == "skill-draft":
            drafts = self.skills.drafts
            draft = drafts.get(target)
            if draft.row_version != request.expected_row_version:
                raise ApplicationError(ApplicationErrorCode.STALE, "Skill Draft row is stale")
            if request.action == "edit":
                return drafts.edit(target, skill_md=request.skill_md, command_id=request.command_id)
            if request.action == "validate":
                return drafts.revalidate(target)
            if request.action == "reject":
                return drafts.reject(target, reason="rejected_by_user")
            # The install saga has its own receipt namespace, shared by GUI and CLI.
            return drafts.accept(
                target, command_id="cmd_" + sha256_digest(request.command_id + ":install")[:48]
            )
        raise ApplicationError(ApplicationErrorCode.INVALID, "management action is invalid")

    def _native_command(self, kind, request, target):
        values = {"workspace_id": self.workspace_id, "command_id": request.command_id}
        if kind == "learning-decision":
            values.update(candidate_id=target, expected_row_version=request.expected_row_version)
            if request.action != "accept":
                result = self.api.learning.reject_candidate(
                    RejectLearningCandidateCommand(
                        **values, never_suggest=request.action == "suppress"
                    )
                )
            else:
                values.update(scope=request.scope, conflict_resolution=request.conflict_resolution)
                result = (
                    self.api.edit_and_accept_learning_candidate(
                        EditAndAcceptLearningCandidateCommand(
                            **values, final_payload=request.final_payload
                        )
                    )
                    if request.final_payload is not None
                    else self.api.accept_learning_candidate(
                        AcceptLearningCandidateCommand(**values)
                    )
                )
            return management_wire(result.value)
        if kind == "knowledge":
            classes = {
                "enable": EnableProjectKnowledgeCommand,
                "disable": DisableProjectKnowledgeCommand,
                "dispute": MarkProjectKnowledgeDisputedCommand,
                "delete": DeleteProjectKnowledgeCommand,
            }
            methods = {
                "enable": self.api.memory.enable_knowledge,
                "disable": self.api.memory.disable_knowledge,
                "dispute": self.api.memory.mark_disputed,
                "delete": self.api.memory.delete_knowledge,
            }
            return management_wire(
                methods[request.action](
                    classes[request.action](
                        **values,
                        knowledge_id=target,
                        expected_row_version=request.expected_row_version,
                    )
                ).value
            )
        existing = self.api.journal.get_application_command_receipt(
            self.workspace_id, request.command_id
        )
        if existing is None and request.expected_digest != self.skill_queries.binding_digest(
            request.scope
        ):
            raise ApplicationError(
                ApplicationErrorCode.STALE, "Skill bindings changed; refresh first"
            )
        values = {
            "scope_id": self.skill_queries.scope_id(request.scope),
            "command_id": request.command_id,
        }
        lifecycle = self.skills.lifecycle
        if request.action in {"pin", "update"}:
            if request.version_id is None:
                raise ApplicationError(
                    ApplicationErrorCode.INVALID, "select an exact Skill version"
                )
            result = lifecycle.pin(target, request.version_id, **values)
        else:
            result = {
                "enable": lifecycle.enable,
                "disable": lifecycle.disable,
                "rollback": lifecycle.rollback,
            }[request.action](target, **values)
        return management_wire(result)
