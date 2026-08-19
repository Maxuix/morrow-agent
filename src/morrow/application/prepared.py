"""Build durable prepared intents from a validated Assistant ToolCycle."""

from __future__ import annotations

import inspect
import json
from typing import Any

from pydantic import ValidationError

from morrow.core.capabilities import PolicyVerdict, ProcessIsolation, ToolCallContext
from morrow.core.domain import canonical_json_bytes, sha256_digest
from morrow.core.execution import (
    ConfigMutationEvidence,
    DurableToolExecution,
    EffectClass,
    FileMutationEvidence,
    PreparedIntent,
    ToolExecutionState,
    require_tool_call_arguments_budget,
    tool_declaration,
)
from morrow.core.models import AssistantMessage, FunctionToolCall
from morrow.core.permissions import UNCONFINED_HOST_WARNING, IsolationLabel
from morrow.core.ports import IdSource
from morrow.runtime.policy import ToolApproval
from morrow.runtime.session import Session
from morrow.runtime.tools import (
    ToolErrorCode,
    ToolExecutionError,
    ToolExecutor,
    _sanitize_approval_preview,
)
from morrow.services.files import LocalFileError, MutationPlan


def file_evidence_from_plan(plan: MutationPlan) -> FileMutationEvidence:
    parent = plan.target.parent
    before = plan.before.revision.sha256 if plan.before is not None else None
    return FileMutationEvidence(
        relative_path=plan.relative_path,
        operation=plan.operation.value,
        existed_before=plan.before is not None,
        before_sha256=before,
        expected_after_sha256=sha256_digest(plan.desired_raw),
        expected_size=len(plan.desired_raw),
        expected_kind="file",
        parent_exists=parent.exists(),
        parent_is_directory=parent.is_dir(),
        policy_version="files-v1",
        conflict_input_digest=sha256_digest(before or "absent"),
        changed_lines=plan.changed_lines,
        changed_bytes=plan.changed_bytes,
        preview_truncated=plan.diff_truncated,
    )


def config_evidence_from_arguments(
    arguments: Any, session: Session
) -> ConfigMutationEvidence | None:
    scope = getattr(arguments, "scope", None)
    target = getattr(arguments, "target", None)
    operation = getattr(arguments, "operation", None)
    if scope is None or target is None or operation is None:
        return None
    if str(target) == "profile":
        kind = "workspace_profile"
        revision = session.profile_revision
    elif str(scope) == "global":
        kind = "global_config"
        revision = 0
    else:
        kind = "workspace_preferences"
        revision = session.preferences_revision
    requested = {
        "path": getattr(arguments, "path", None),
        "operation": str(operation),
        "target": str(target),
    }
    return ConfigMutationEvidence(
        document_kind=kind,
        source_revision=revision,
        operation=str(operation),
        expected_fields_digest=sha256_digest(canonical_json_bytes(requested)),
    )


def _declaration_effect(name: str, isolation: ProcessIsolation) -> EffectClass:
    try:
        return tool_declaration(name, process_isolation=isolation).effect_class
    except Exception:
        return EffectClass.UNCONFINED_EXTERNAL_EFFECT


def _arguments_digest(raw: str) -> str:
    require_tool_call_arguments_budget(raw)
    try:
        parsed = json.loads(raw)
        return sha256_digest(canonical_json_bytes(parsed))
    except (TypeError, ValueError):
        return sha256_digest(raw)


def prepare_cycle_executions(
    message: AssistantMessage,
    *,
    session: Session,
    tool_executor: ToolExecutor | None,
    run_context,
    id_source: IdSource,
    workspace_id: str,
    task_run_id: str,
    turn_id: str,
    agent_run_id: str,
    mutation=None,
    isolation: ProcessIsolation = ProcessIsolation.HOST,
    permission_snapshot_id: str | None = None,
    grant_id: str | None = None,
    isolation_label: IsolationLabel | None = None,
) -> tuple[DurableToolExecution, ...]:
    permission_digest = sha256_digest(
        canonical_json_bytes(session.permission_profile.model_dump(mode="json"))
    )
    executions: list[DurableToolExecution] = []
    total = len(message.tool_calls)
    for ordinal, call in enumerate(message.tool_calls, start=1):
        intent = _prepare_one(
            call,
            ordinal=ordinal,
            total=total,
            session=session,
            tool_executor=tool_executor,
            run_context=run_context,
            permission_digest=permission_digest,
            mutation=mutation,
            isolation=isolation,
            grant_id=grant_id,
        )
        elevated = (
            grant_id is not None
            and call.name == "run_command"
            and intent.effect_class is EffectClass.UNCONFINED_EXTERNAL_EFFECT
            and intent.requires_approval
        )
        state = (
            ToolExecutionState.AWAITING_APPROVAL
            if intent.requires_approval
            else ToolExecutionState.PREPARED
        )
        executions.append(
            DurableToolExecution(
                tool_execution_id=id_source.new_id("tex"),
                workspace_id=workspace_id,
                session_id=session.session_id,
                task_run_id=task_run_id,
                turn_id=turn_id,
                agent_run_id=agent_run_id,
                call_id=call.id,
                ordinal=ordinal,
                tool_name=call.name,
                intent=intent,
                state=state,
                permission_snapshot_id=permission_snapshot_id,
                grant_id=grant_id if elevated else None,
                isolation=isolation_label if elevated else None,
            )
        )
    return tuple(executions)


def _prepare_one(
    call: FunctionToolCall,
    *,
    ordinal: int,
    total: int,
    session: Session,
    tool_executor: ToolExecutor | None,
    run_context,
    permission_digest: str,
    mutation,
    isolation: ProcessIsolation,
    grant_id: str | None,
) -> PreparedIntent:
    registered = tool_executor.tool_set.tools.get(call.name) if tool_executor is not None else None
    schema_digest = (
        sha256_digest(canonical_json_bytes(registered.definition.model_dump(mode="json")))
        if registered is not None
        else sha256_digest(call.name)
    )
    requires_approval = bool(
        registered is not None and registered.execution_policy.approval is ToolApproval.REQUIRED
    )
    file_evidence: tuple[FileMutationEvidence, ...] = ()
    config_evidence = None
    preview: tuple[str, ...] = ()
    policy_verdict: PolicyVerdict | None = None
    if registered is not None:
        try:
            arguments = registered.arguments_model.model_validate_json(call.arguments, strict=True)
            context = ToolCallContext(
                run=run_context,
                call_id=call.id,
                tool_name=call.name,
                ordinal=ordinal,
                total=total,
                result_limit=max(1, getattr(tool_executor.run_policy, "effective_result_limit", 1)),
            )
            policy_preview: tuple[str, ...] = ()
            if registered.intent_resolver is not None:
                resolved = registered.intent_resolver(arguments, context)
                if inspect.isawaitable(resolved):
                    raise ToolExecutionError(
                        ToolErrorCode.PREFLIGHT_FAILED,
                        "prepared intent cannot await during persist",
                    )
                if tool_executor.capability_policy is not None:
                    decision = tool_executor.capability_policy.evaluate(
                        resolved,
                        allow_unconfined_host=grant_id is not None and call.name == "run_command",
                    )
                    policy_verdict = decision.verdict
                    requires_approval = decision.verdict is PolicyVerdict.REQUIRE_APPROVAL
                    policy_preview = tuple(decision.preview_summary)
                    if (
                        decision.verdict is PolicyVerdict.REQUIRE_APPROVAL
                        and grant_id is not None
                        and call.name == "run_command"
                        and resolved.kind.value == "process"
                        and resolved.requires_host
                    ):
                        policy_preview = (UNCONFINED_HOST_WARNING, *policy_preview)
            if registered.context_approval_preview is not None:
                local_preview = registered.context_approval_preview(arguments, context)
            elif registered.approval_preview is not None:
                local_preview = registered.approval_preview(arguments)
            else:
                local_preview = ()
            preview = _sanitize_approval_preview(
                (*policy_preview, *tuple(local_preview)),
                budget=registered.approval_preview_budget,
            )
            if mutation is not None:
                plan = mutation.cached_plan(run_context.run_id, call.id)
                if plan is not None:
                    file_evidence = (file_evidence_from_plan(plan),)
            config_evidence = config_evidence_from_arguments(arguments, session)
        except (ValidationError, ToolExecutionError, LocalFileError, ValueError, TypeError):
            preview = ()
    return PreparedIntent(
        tool_name=call.name,
        call_id=call.id,
        ordinal=ordinal,
        arguments_digest=_arguments_digest(call.arguments),
        schema_digest=schema_digest,
        permission_context_digest=permission_digest,
        effect_class=_declaration_effect(call.name, isolation),
        requires_approval=requires_approval,
        policy_verdict=policy_verdict,
        file_evidence=file_evidence,
        config_evidence=config_evidence,
        preview=preview,
    )
