"""Typed workbench access to existing learning and memory application services."""

from typing import Literal

from pydantic import Field

from morrow.application.context_management import management_wire
from morrow.application.management_requests import CommandRequest
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.configuration_promotion import PromotionOperationState
from morrow.core.models import ProtocolModel


class KnowledgeQuery(ProtocolModel):
    identity: str | None = Field(default=None, max_length=128)
    scope: Literal["workspace", "global"] = "workspace"
    status: str | None = Field(default=None, max_length=64)
    category: str | None = Field(default=None, max_length=64)
    candidate_type: str | None = Field(default=None, max_length=64)
    job_id: str | None = Field(default=None, max_length=128)
    outcome_id: str | None = Field(default=None, max_length=128)
    session_id: str | None = Field(default=None, max_length=128)
    revision: int | None = Field(default=None, ge=1)
    statement: str | None = Field(default=None, max_length=512)
    include_deleted: bool = False
    cursor: str | None = Field(default=None, max_length=128)
    limit: int = Field(default=50, ge=1, le=100)


class LearningAdminRequest(CommandRequest):
    action: Literal["mode", "request", "review", "retry", "promotion", "undo"]
    target: str | None = Field(default=None, max_length=128)
    mode: Literal["off", "review_only", "explicit_auto"] = "review_only"
    expected_row_version: int | None = Field(default=None, ge=0)
    expected_latest_version: int | None = Field(default=None, ge=0)
    recovery_action: Literal["retry", "finalize", "cancel", "abort"] = "retry"


class InboxAdminRequest(CommandRequest):
    action: Literal["review", "retry", "run_pending", "accept_many"]
    target: str | None = Field(default=None, max_length=128)
    limit: int = Field(default=100, ge=1, le=500)
    proposal_ids: tuple[str, ...] = Field(default=(), max_length=64)
    expected_row_versions: dict[str, int] = Field(default_factory=dict, max_length=64)
    expected_document_revision: int | None = Field(default=None, ge=0)


def required(value):
    if value is None:
        raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "The selected record is missing")
    return value


def query_knowledge(api, kind, q: KnowledgeQuery):
    page = {"cursor": q.cursor, "limit": q.limit}
    if kind == "learning-status":
        value = api.learning_status()
    elif kind == "reviews":
        value = (
            required(api.get_learning_review_view(q.identity))
            if q.identity
            else api.list_learning_review_views(
                status=q.status, task_outcome_id=q.outcome_id, **page
            )
        )
    elif kind == "candidates":
        value = (
            required(api.get_learning_candidate_view(q.identity))
            if q.identity
            else api.list_learning_candidate_views(
                status=q.status, candidate_type=q.candidate_type, **page
            )
        )
    elif kind in {"promotions", "activations"}:
        activations = kind == "activations"
        exclude = {"inverse_command_json" if activations else "prepared_change_json"}
        if q.identity:
            item = required(
                (api.get_learning_activation if activations else api.get_learning_promotion)(
                    q.identity
                )
            )
            value = item.model_dump(mode="json", exclude=exclude)
        else:
            result = api.learning.configuration_page(
                activations=activations,
                state=PromotionOperationState(q.status) if q.status else None,
                **page,
            )
            value = {
                "items": [v.model_dump(mode="json", exclude=exclude) for v in result.items],
                "next_cursor": result.next_cursor,
            }
    elif kind == "undo-preview":
        prepared = api.preview_learning_undo(required(q.identity))
        value = {"preview": prepared.preview_lines, "expected_revision": prepared.expected_revision}
    elif kind == "preference-status":
        value = api.preference_context_status(session_id=q.session_id)
    elif kind == "inbox-status":
        value = api.preference_review_status()
    elif kind == "inbox-jobs":
        value = (
            required(api.get_preference_review_job_view(q.identity))
            if q.identity
            else api.list_preference_review_jobs(status=q.status, **page)
        )
    elif kind == "proposals":
        value = (
            required(api.get_preference_proposal_view(q.identity))
            if q.identity
            else api.list_preference_proposal_views(status=q.status, job_id=q.job_id, **page)
        )
    elif kind == "proposal-preview":
        value = api.preview_preference_proposal(required(q.identity), edit=q.statement)
    elif kind == "knowledge":
        value = (
            required(api.get_project_knowledge(q.identity, revision=q.revision))
            if q.identity
            else api.list_project_knowledge(
                status=q.status, category=q.category, include_deleted=q.include_deleted, **page
            )
        )
    elif kind == "selections":
        value = (
            required(api.get_memory_selection(q.identity))
            if q.identity
            else api.list_memory_selections(**page)
        )
    else:
        raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Management query is unavailable")
    return management_wire(value)


def learning_admin(api, request: LearningAdminRequest):
    r = request
    if r.action == "mode":
        if r.expected_row_version is None:
            raise ApplicationError(ApplicationErrorCode.INVALID, "Policy revision is required")
        return api.set_learning_mode(
            r.mode, command_id=r.command_id, expected_row_version=r.expected_row_version
        ).value
    target = required(r.target)
    if r.action == "request":
        return api.request_learning_review(
            target, expected_latest_version=r.expected_latest_version, command_id=r.command_id
        ).value
    if r.action == "promotion":
        return api.recover_learning_promotion(target, action=r.recovery_action)
    if r.action == "undo":
        return api.undo_learning_activation(target, command_id=r.command_id).value
    raise ApplicationError(ApplicationErrorCode.INVALID, "Review must use supervised execution")


def inbox_admin(api, request: InboxAdminRequest):
    if request.action == "retry":
        return api.retry_preference_review_job(required(request.target))
    if request.action == "accept_many":
        if not request.proposal_ids or set(request.proposal_ids) != set(
            request.expected_row_versions
        ):
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "Preview all selected proposals first"
            )
        return api.accept_preference_proposals(
            request.proposal_ids,
            command_id=request.command_id,
            expected_row_versions=request.expected_row_versions,
            expected_document_revision=request.expected_document_revision,
        )
    raise ApplicationError(ApplicationErrorCode.INVALID, "Review must use supervised execution")
