"""Focused Typer surfaces for the Learning Inbox and Project Knowledge."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import typer

from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.learning import (
    LearningCandidateStatus,
    LearningCandidateType,
    LearningReviewStatus,
)
from morrow.core.learning_commands import (
    AcceptLearningCandidateCommand,
    DeleteProjectKnowledgeCommand,
    DisableProjectKnowledgeCommand,
    EditAndAcceptLearningCandidateCommand,
    EnableProjectKnowledgeCommand,
    MarkProjectKnowledgeDisputedCommand,
    RejectLearningCandidateCommand,
)
from morrow.core.learning_memory import (
    LearningConflictResolution,
    ProjectKnowledgeCategory,
    ProjectKnowledgeStatus,
)
from morrow.core.learning_payloads import (
    PreferenceCandidatePayload,
    ProfileCandidatePayload,
    ProjectKnowledgeCandidatePayload,
)

learning_app = typer.Typer(help="Learning Inbox、Review 与候选决策。")
memory_app = typer.Typer(help="Project Knowledge 生命周期与历史。")


def _cli_helpers():
    # Import lazily so this focused registration module does not own store wiring.
    from morrow.interfaces.cli import (
        _cli_error,
        _close_state,
        _emit_model,
        _emit_page,
        _state_services,
    )

    return _cli_error, _close_state, _emit_model, _emit_page, _state_services


def _command_id(api, command_id: str | None) -> str:
    return command_id or api.id_source.new_id("cmd")


def _candidate_or_error(api, candidate_id: str):
    candidate = api.get_learning_candidate_view(candidate_id)
    if candidate is None:
        raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Learning Candidate is missing")
    return candidate


def _confirm_or_exit(question: str) -> None:
    if typer.confirm(question, default=False):
        return
    typer.echo("已取消，未写入状态。")
    raise typer.Exit(code=2)


def _emit_promotion_result(value, *, as_json: bool) -> None:
    if not as_json and getattr(value, "activation_id", None) is not None:
        action = "撤销" if value.outcome == "reversed" else "激活"
        label = "Active Preference" if value.target == "preferences" else "Active Profile field"
        scope = value.scope.value if value.scope is not None else "unknown"
        typer.echo(
            f"{action}{label}：scope={scope}；path={value.path}；"
            f"revision={value.revision}；activation={value.activation_id}。"
        )
        return
    _cli_helpers()[2](value, as_json=as_json)


def _run_state_command(
    *,
    state_root: Path | None,
    workspace_id: str | None,
    directory: Path,
    write: bool,
    action: Callable[[object], None],
) -> None:
    cli_error, close_state, _emit_model, _emit_page, state_services = _cli_helpers()
    handle = None
    try:
        _application, handle, api, _doctor, _backup = state_services(
            state_root=state_root,
            workspace_id=workspace_id,
            directory=directory,
            write=write,
        )
        action(api)
    except typer.Exit:
        raise
    except Exception as exc:
        cli_error(exc)
        raise typer.Exit(code=2) from None
    finally:
        close_state(handle)


@learning_app.command("status")
def learning_status(
    as_json: bool = typer.Option(False, "--json"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    def action(api) -> None:
        _cli_helpers()[2](api.learning_status(), as_json=as_json)

    _run_state_command(
        state_root=state_root,
        workspace_id=workspace_id,
        directory=directory,
        write=True,
        action=action,
    )


@learning_app.command("inbox")
def learning_inbox(
    status: LearningCandidateStatus = typer.Option(LearningCandidateStatus.PROPOSED, "--status"),
    candidate_type: LearningCandidateType | None = typer.Option(None, "--type"),
    cursor: str | None = typer.Option(None, "--cursor"),
    limit: int = typer.Option(50, "--limit", min=1, max=100),
    as_json: bool = typer.Option(False, "--json"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    def action(api) -> None:
        page = api.list_learning_candidate_views(
            status=status,
            candidate_type=candidate_type,
            cursor=cursor,
            limit=limit,
        )
        _cli_helpers()[3](
            page,
            render=lambda item: (
                f"{item.candidate_id}\t{item.candidate_type.value}\t"
                f"{item.status.value}\tversion={item.row_version}"
            ),
            as_json=as_json,
        )

    _run_state_command(
        state_root=state_root,
        workspace_id=workspace_id,
        directory=directory,
        write=True,
        action=action,
    )


@learning_app.command("show")
def learning_show(
    candidate_id: str,
    as_json: bool = typer.Option(False, "--json"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    def action(api) -> None:
        _cli_helpers()[2](_candidate_or_error(api, candidate_id), as_json=as_json)

    _run_state_command(
        state_root=state_root,
        workspace_id=workspace_id,
        directory=directory,
        write=True,
        action=action,
    )


@learning_app.command("reviews")
def learning_reviews(
    status: LearningReviewStatus | None = typer.Option(None, "--status"),
    cursor: str | None = typer.Option(None, "--cursor"),
    limit: int = typer.Option(50, "--limit", min=1, max=100),
    as_json: bool = typer.Option(False, "--json"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    def action(api) -> None:
        page = api.list_learning_review_views(status=status, cursor=cursor, limit=limit)
        _cli_helpers()[3](
            page,
            render=lambda item: (
                f"{item.review_id}\t{item.status.value}\t"
                f"candidates={item.candidate_count}\tversion={item.review_version}"
            ),
            as_json=as_json,
        )

    _run_state_command(
        state_root=state_root,
        workspace_id=workspace_id,
        directory=directory,
        write=False,
        action=action,
    )


@learning_app.command("promotions")
def learning_promotions(
    operation_id: str | None = typer.Option(None, "--operation-id"),
    action: str | None = typer.Option(None, "--action"),
    as_json: bool = typer.Option(False, "--json"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    def action_handler(api) -> None:
        if (operation_id is None) != (action is None):
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "--operation-id 与 --action 必须同时提供",
            )
        if operation_id is not None and action is not None:
            value = api.recover_learning_promotion(operation_id, action=action)
            _emit_promotion_result(
                value.value if hasattr(value, "value") else value,
                as_json=as_json,
            )
            return
        items = api.list_learning_promotions()
        _cli_helpers()[2](items, as_json=as_json)
        if not as_json and not items:
            typer.echo("没有待处理的配置 promotion。")

    _run_state_command(
        state_root=state_root,
        workspace_id=workspace_id,
        directory=directory,
        write=operation_id is not None and action is not None,
        action=action_handler,
    )


@learning_app.command("undo")
def learning_undo(
    activation_id: str,
    command_id: str | None = typer.Option(None, "--command-id"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    def action(api) -> None:
        activation = api.get_learning_activation(activation_id)
        if activation is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "配置 activation 不存在")
        prepared = api.preview_learning_undo(activation_id)
        _cli_helpers()[2](prepared)
        _confirm_or_exit("确认撤销这项配置 activation？")
        value = api.undo_learning_activation(
            activation_id,
            command_id=_command_id(api, command_id),
        ).value
        _emit_promotion_result(value, as_json=False)

    _run_state_command(
        state_root=state_root,
        workspace_id=workspace_id,
        directory=directory,
        write=True,
        action=action,
    )


@learning_app.command("accept")
def learning_accept(
    candidate_id: str,
    scope: str | None = typer.Option(None, "--scope"),
    conflict_resolution: LearningConflictResolution = typer.Option(
        LearningConflictResolution.NONE, "--conflict-resolution"
    ),
    command_id: str | None = typer.Option(None, "--command-id"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    def action(api) -> None:
        candidate = _candidate_or_error(api, candidate_id)
        preview = api.preview_learning_candidate_decision(
            candidate_id,
            scope=scope,
            conflict_resolution=conflict_resolution,
        )
        _cli_helpers()[2](preview)
        _confirm_or_exit("确认接受这项 Learning Candidate？")
        command = AcceptLearningCandidateCommand(
            workspace_id=api.workspace_id,
            candidate_id=candidate.candidate_id,
            expected_row_version=candidate.row_version,
            command_id=_command_id(api, command_id),
            scope=scope,
            conflict_resolution=conflict_resolution,
        )
        value = api.accept_learning_candidate(command).value
        if value.outcome == "candidate_only":
            typer.echo(
                "候选已接受为候选/反馈；未创建或激活 Skill、Workflow 或 Orchestration 状态。"
            )
        _emit_promotion_result(value, as_json=False)

    _run_state_command(
        state_root=state_root,
        workspace_id=workspace_id,
        directory=directory,
        write=True,
        action=action,
    )


@learning_app.command("edit")
def learning_edit(
    candidate_id: str,
    path: str | None = typer.Option(None, "--path"),
    edit_value: str | None = typer.Option(None, "--value"),
    statement: str | None = typer.Option(None, "--statement"),
    semantic_key: str | None = typer.Option(None, "--semantic-key"),
    category: ProjectKnowledgeCategory | None = typer.Option(None, "--category"),
    scope: str | None = typer.Option(None, "--scope"),
    conflict_resolution: LearningConflictResolution = typer.Option(
        LearningConflictResolution.NONE, "--conflict-resolution"
    ),
    command_id: str | None = typer.Option(None, "--command-id"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    def action(api) -> None:
        candidate = _candidate_or_error(api, candidate_id)
        payload = candidate.candidate.proposed_payload
        if isinstance(payload, PreferenceCandidatePayload):
            if any(item is not None for item in (statement, semantic_key, category)):
                raise ApplicationError(
                    ApplicationErrorCode.INVALID, "Preference 不能使用 Project Knowledge 字段"
                )
            selected_path = path or payload.path
            selected_value = edit_value
            if selected_value is None:
                final_payload = payload.model_copy(update={"path": selected_path})
            elif selected_path == "instructions":
                final_payload = payload.model_copy(
                    update={"path": selected_path, "value": (selected_value,)}
                )
            else:
                final_payload = payload.model_copy(
                    update={"path": selected_path, "value": selected_value}
                )
        elif isinstance(payload, ProfileCandidatePayload):
            if any(item is not None for item in (statement, semantic_key, category)):
                raise ApplicationError(
                    ApplicationErrorCode.INVALID, "Profile 不能使用 Project Knowledge 字段"
                )
            selected_path = path or payload.path
            selected_value = edit_value
            if selected_value is None:
                final_payload = payload.model_copy(update={"path": selected_path})
            elif selected_path in {"goals", "tech_stack", "constraints", "conventions"}:
                final_payload = payload.model_copy(
                    update={"path": selected_path, "value": (selected_value,)}
                )
            else:
                final_payload = payload.model_copy(
                    update={"path": selected_path, "value": selected_value}
                )
        elif not isinstance(payload, ProjectKnowledgeCandidatePayload):
            if any(
                item is not None for item in (path, edit_value, statement, semantic_key, category)
            ):
                raise ApplicationError(
                    ApplicationErrorCode.INVALID,
                    "当前候选类型不支持这些编辑字段",
                )
            final_payload = payload
        else:
            if path is not None or edit_value is not None:
                raise ApplicationError(
                    ApplicationErrorCode.INVALID, "Project Knowledge 使用专用字段编辑"
                )
            if any(value is not None for value in (statement, semantic_key, category)):
                raise ApplicationError(
                    ApplicationErrorCode.INVALID,
                    "当前 CLI 仅支持 Project Knowledge 的字段编辑；其他类型可用原值确认候选接受",
                )
            final_payload = payload
            final_payload = payload.model_copy(
                update={
                    key: value
                    for key, value in {
                        "statement": statement,
                        "semantic_key": semantic_key,
                        "category": category,
                    }.items()
                    if value is not None
                }
            )
        preview = api.preview_learning_candidate_decision(
            candidate_id,
            edit=final_payload,
            scope=scope,
            conflict_resolution=conflict_resolution,
        )
        _cli_helpers()[2](preview)
        _confirm_or_exit("确认保存编辑后的 Learning Candidate？")
        command = EditAndAcceptLearningCandidateCommand(
            workspace_id=api.workspace_id,
            candidate_id=candidate.candidate_id,
            expected_row_version=candidate.row_version,
            command_id=_command_id(api, command_id),
            scope=scope,
            conflict_resolution=conflict_resolution,
            final_payload=final_payload,
        )
        value = api.edit_and_accept_learning_candidate(command).value
        if value.outcome == "candidate_only":
            typer.echo(
                "候选已接受为候选/反馈；未创建或激活 Skill、Workflow 或 Orchestration 状态。"
            )
        _emit_promotion_result(value, as_json=False)

    _run_state_command(
        state_root=state_root,
        workspace_id=workspace_id,
        directory=directory,
        write=True,
        action=action,
    )


@learning_app.command("reject")
def learning_reject(
    candidate_id: str,
    never_suggest: bool = typer.Option(False, "--never-suggest"),
    reason: str = typer.Option("rejected_by_user", "--reason"),
    command_id: str | None = typer.Option(None, "--command-id"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    def action(api) -> None:
        candidate = _candidate_or_error(api, candidate_id)
        _cli_helpers()[2](api.preview_learning_candidate_decision(candidate_id))
        _confirm_or_exit("确认拒绝这项 Learning Candidate？")
        command = RejectLearningCandidateCommand(
            workspace_id=api.workspace_id,
            candidate_id=candidate.candidate_id,
            expected_row_version=candidate.row_version,
            command_id=_command_id(api, command_id),
            never_suggest=never_suggest,
            reason=reason,
        )
        _cli_helpers()[2](api.learning.reject_candidate(command).value)

    _run_state_command(
        state_root=state_root,
        workspace_id=workspace_id,
        directory=directory,
        write=True,
        action=action,
    )


def _knowledge_or_error(api, knowledge_id: str, *, revision: int | None = None):
    value = api.get_project_knowledge(knowledge_id, revision=revision)
    if value is None:
        raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Project Knowledge is missing")
    return value


@memory_app.command("list")
def memory_list(
    status: ProjectKnowledgeStatus | None = typer.Option(None, "--status"),
    category: ProjectKnowledgeCategory | None = typer.Option(None, "--category"),
    include_deleted: bool = typer.Option(False, "--include-deleted"),
    cursor: str | None = typer.Option(None, "--cursor"),
    limit: int = typer.Option(50, "--limit", min=1, max=100),
    as_json: bool = typer.Option(False, "--json"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    def action(api) -> None:
        page = api.list_project_knowledge(
            status=status,
            category=category,
            include_deleted=include_deleted,
            cursor=cursor,
            limit=limit,
        )
        _cli_helpers()[3](
            page,
            render=lambda item: (
                f"{item.head.knowledge_id}\t{item.head.status.value}\t"
                f"{item.head.category.value}\t{item.head.semantic_key}"
            ),
            as_json=as_json,
        )

    _run_state_command(
        state_root=state_root,
        workspace_id=workspace_id,
        directory=directory,
        write=False,
        action=action,
    )


@memory_app.command("show")
def memory_show(
    knowledge_id: str,
    revision: int | None = typer.Option(None, "--revision", min=1),
    as_json: bool = typer.Option(False, "--json"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    def action(api) -> None:
        _cli_helpers()[2](
            _knowledge_or_error(api, knowledge_id, revision=revision), as_json=as_json
        )

    _run_state_command(
        state_root=state_root,
        workspace_id=workspace_id,
        directory=directory,
        write=False,
        action=action,
    )


def _memory_mutation(
    api,
    knowledge_id: str,
    command_id: str | None,
    command_type,
    operation: str,
) -> None:
    view = _knowledge_or_error(api, knowledge_id)
    _cli_helpers()[2](view)
    if operation == "delete":
        typer.echo("这是逻辑删除：历史修订和备份仍会保留，当前记录将不再参与 Memory 选择。")
    _confirm_or_exit(f"确认执行 Project Knowledge {operation}？")
    command = command_type(
        workspace_id=api.workspace_id,
        knowledge_id=view.head.knowledge_id,
        expected_row_version=view.head.row_version,
        command_id=_command_id(api, command_id),
    )
    if operation == "disable":
        result = api.disable_project_knowledge(command)
    elif operation == "enable":
        result = api.enable_project_knowledge(command)
    elif operation == "dispute":
        result = api.dispute_project_knowledge(command)
    else:
        result = api.delete_project_knowledge(command)
    _cli_helpers()[2](result.value)


@memory_app.command("disable")
def memory_disable(
    knowledge_id: str,
    command_id: str | None = typer.Option(None, "--command-id"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    _run_state_command(
        state_root=state_root,
        workspace_id=workspace_id,
        directory=directory,
        write=True,
        action=lambda api: _memory_mutation(
            api, knowledge_id, command_id, DisableProjectKnowledgeCommand, "disable"
        ),
    )


@memory_app.command("enable")
def memory_enable(
    knowledge_id: str,
    command_id: str | None = typer.Option(None, "--command-id"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    _run_state_command(
        state_root=state_root,
        workspace_id=workspace_id,
        directory=directory,
        write=True,
        action=lambda api: _memory_mutation(
            api, knowledge_id, command_id, EnableProjectKnowledgeCommand, "enable"
        ),
    )


@memory_app.command("dispute")
def memory_dispute(
    knowledge_id: str,
    command_id: str | None = typer.Option(None, "--command-id"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    _run_state_command(
        state_root=state_root,
        workspace_id=workspace_id,
        directory=directory,
        write=True,
        action=lambda api: _memory_mutation(
            api, knowledge_id, command_id, MarkProjectKnowledgeDisputedCommand, "dispute"
        ),
    )


@memory_app.command("delete")
def memory_delete(
    knowledge_id: str,
    command_id: str | None = typer.Option(None, "--command-id"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    _run_state_command(
        state_root=state_root,
        workspace_id=workspace_id,
        directory=directory,
        write=True,
        action=lambda api: _memory_mutation(
            api, knowledge_id, command_id, DeleteProjectKnowledgeCommand, "delete"
        ),
    )


__all__ = ["learning_app", "memory_app"]
