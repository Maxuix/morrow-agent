"""SQLite persistence for bounded AgentRun request and terminal observations."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime

from morrow.adapters.state.transaction import SqliteJournalBackend
from morrow.core.compaction import TokenAccountingBasis
from morrow.core.domain import DurableAgentRun
from morrow.core.models import (
    AgentStopCode,
    FinishReason,
    ModelCost,
    ModelErrorCode,
    ModelFinishReason,
    ModelUsage,
    UsageAvailability,
)
from morrow.core.observability import (
    MODEL_REQUEST_ID_PREFIX,
    AgentRunObservation,
    AgentRunRetryProgress,
    AgentRunTerminalMetrics,
    ModelRequestObservation,
    ModelRequestPurpose,
    ModelRequestState,
    ToolTerminalCounts,
)
from morrow.core.ports import IdSource
from morrow.core.prompt import PromptProfileEvidence
from morrow.core.store import StorageError, StorageErrorCode


def _unix(value: datetime) -> int:
    return int(value.timestamp())


def _from_unix(value: object) -> datetime:
    return datetime.fromtimestamp(int(value), UTC)


def _optional_unix(value: datetime | None) -> int | None:
    return _unix(value) if value is not None else None


def _optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    if value not in (0, 1, False, True):
        raise StorageError(
            StorageErrorCode.NEEDS_REPAIR,
            "AgentRun boolean observation is not safe to read",
        )
    return bool(value)


def _optional_json(value) -> str | None:
    if value is None:
        return None
    return json.dumps(
        value.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _optional_model(raw: object, schema):
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise StorageError(
            StorageErrorCode.NEEDS_REPAIR,
            "AgentRun request evidence is not safe to read",
        )
    try:
        value = schema.model_validate_json(raw)
    except (TypeError, ValueError):
        raise StorageError(
            StorageErrorCode.NEEDS_REPAIR,
            "AgentRun request evidence is not safe to read",
        ) from None
    if _optional_json(value) != raw:
        raise StorageError(
            StorageErrorCode.NEEDS_REPAIR,
            "AgentRun request evidence is not canonical",
        )
    return value


_REQUEST_COLUMNS = (
    "model_request_id, workspace_id, agent_run_id, attempt_ordinal, state, admitted_at_unix, "
    "settled_at_unix, estimated_request_chars, request_char_budget, cleared_cycle_count, "
    "dropped_turn_count, dropped_cycle_count, dropped_record_count, tool_rounds, tool_calls, "
    "finish_reason, error_code, usage_availability, input_tokens, output_tokens, total_tokens, "
    "cost_availability, cost_amount_minor, cost_currency, cost_source, purpose, "
    "prompt_evidence_json, resolved_outcome_contract_json, policy_schema_version, "
    "estimated_context_tokens, context_window_tokens, reserve_tokens, keep_recent_tokens, "
    "accounting_basis, compaction_required"
)
_METRICS_COLUMNS = (
    "agent_run_id, workspace_id, finish_reason, stop_code, model_attempts, retry_count, "
    "tool_rounds, tool_calls, max_estimated_request_chars, request_char_budget, "
    "cleared_cycle_count, dropped_turn_count, dropped_cycle_count, dropped_record_count, "
    "usage_availability, input_tokens, output_tokens, total_tokens, cost_availability, "
    "cost_amount_minor, cost_currency, cost_source, tool_terminal_counts_json, "
    "validation_outcome, completion_outcome, completion_basis, completion_reason_code, "
    "finalized_at_unix, policy_schema_version, max_context_tokens, last_context_tokens, "
    "context_window_tokens, reserve_tokens, keep_recent_tokens, accounting_basis, "
    "compaction_count, overflow_recovery_count"
)
_REQUEST_VALUE_COUNT = 35
_METRICS_VALUE_COUNT = 37
_RETRY_COLUMNS = (
    "agent_run_id, workspace_id, consecutive_model_retries, total_retry_count, "
    "summary_retry_count, updated_at_unix"
)


class SqliteObservabilityJournal:
    """One narrow repository for request admission, settlement and run finalization."""

    def __init__(
        self,
        backend: SqliteJournalBackend,
        *,
        id_source: IdSource,
        get_agent_run: Callable[[str, str], DurableAgentRun | None],
        list_tool_executions: Callable[[str, str], tuple] | None = None,
    ) -> None:
        self.backend = backend
        self.id_source = id_source
        self.get_agent_run = get_agent_run
        self.list_tool_executions = list_tool_executions

    def admit_model_request(
        self,
        workspace_id: str,
        *,
        agent_run_id: str,
        attempt_ordinal: int,
        estimated_request_chars: int,
        request_char_budget: int,
        cleared_cycle_count: int = 0,
        dropped_turn_count: int = 0,
        dropped_cycle_count: int = 0,
        dropped_record_count: int = 0,
        tool_rounds: int = 0,
        tool_calls: int = 0,
        purpose: ModelRequestPurpose | str = ModelRequestPurpose.AGENT,
        prompt_evidence: PromptProfileEvidence | None = None,
        policy_schema_version: int | None = None,
        estimated_context_tokens: int | None = None,
        context_window_tokens: int | None = None,
        reserve_tokens: int | None = None,
        keep_recent_tokens: int | None = None,
        accounting_basis: TokenAccountingBasis | str | None = None,
        compaction_required: bool | None = None,
        model_request_id: str | None = None,
        admitted_at: datetime | None = None,
    ) -> ModelRequestObservation:
        run = self._require_run(workspace_id, agent_run_id)
        stamp = admitted_at or self.backend.now()
        candidate = ModelRequestObservation(
            model_request_id=model_request_id or self.id_source.new_id(MODEL_REQUEST_ID_PREFIX),
            workspace_id=workspace_id,
            agent_run_id=agent_run_id,
            attempt_ordinal=attempt_ordinal,
            admitted_at=stamp,
            estimated_request_chars=estimated_request_chars,
            request_char_budget=request_char_budget,
            cleared_cycle_count=cleared_cycle_count,
            dropped_turn_count=dropped_turn_count,
            dropped_cycle_count=dropped_cycle_count,
            dropped_record_count=dropped_record_count,
            tool_rounds=tool_rounds,
            tool_calls=tool_calls,
            purpose=purpose,
            prompt_evidence=prompt_evidence,
            policy_schema_version=policy_schema_version,
            estimated_context_tokens=estimated_context_tokens,
            context_window_tokens=context_window_tokens,
            reserve_tokens=reserve_tokens,
            keep_recent_tokens=keep_recent_tokens,
            accounting_basis=accounting_basis,
            compaction_required=compaction_required,
        )
        del run

        def work() -> ModelRequestObservation:
            existing = self._request_by_ordinal(workspace_id, agent_run_id, attempt_ordinal)
            if existing is not None:
                if self._same_admission(existing, candidate):
                    return existing
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE,
                    "AgentRun model request admission conflicts with an existing ordinal",
                )
            by_id = self.get_model_request(workspace_id, candidate.model_request_id)
            if by_id is not None:
                if self._same_admission(by_id, candidate):
                    return by_id
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE,
                    "AgentRun model request identifier is already in use",
                )
            self.backend.executor().execute(
                f"INSERT INTO agent_run_model_requests({_REQUEST_COLUMNS}) VALUES ({', '.join('?' for _ in range(_REQUEST_VALUE_COUNT))})",
                self._request_values(candidate),
            )
            stored = self.get_model_request(workspace_id, candidate.model_request_id)
            if stored is None:
                raise StorageError(
                    StorageErrorCode.NEEDS_REPAIR,
                    "AgentRun model request could not be read after admission",
                )
            return stored

        return self.backend.transact(work)

    def settle_model_request(
        self,
        workspace_id: str,
        model_request_id: str,
        *,
        state: ModelRequestState | str,
        finish_reason: ModelFinishReason | str | None = None,
        error_code: ModelErrorCode | str | None = None,
        usage: ModelUsage | None = None,
        cost: ModelCost | None = None,
        settled_at: datetime | None = None,
    ) -> ModelRequestObservation:
        selected_state = ModelRequestState(state)
        if selected_state is ModelRequestState.ADMITTED:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE,
                "AgentRun model request cannot settle to admitted",
            )
        existing = self.get_model_request(workspace_id, model_request_id)
        if existing is None:
            raise StorageError(StorageErrorCode.NOT_FOUND, "AgentRun model request is missing")
        try:
            candidate = ModelRequestObservation.model_validate(
                {
                    **existing.model_dump(mode="python"),
                    "state": selected_state,
                    "settled_at": settled_at or self.backend.now(),
                    "finish_reason": (
                        ModelFinishReason(finish_reason) if finish_reason is not None else None
                    ),
                    "error_code": ModelErrorCode(error_code) if error_code is not None else None,
                    "usage": usage or ModelUsage.unavailable(),
                    "cost": cost or ModelCost.unavailable(),
                },
                strict=True,
            )
        except (TypeError, ValueError):
            raise StorageError(
                StorageErrorCode.UNAVAILABLE,
                "AgentRun model request settlement is invalid",
            ) from None

        def work() -> ModelRequestObservation:
            current = self.get_model_request(workspace_id, model_request_id)
            if current is None:
                raise StorageError(StorageErrorCode.NOT_FOUND, "AgentRun model request is missing")
            if current.state is not ModelRequestState.ADMITTED:
                if current.model_copy(update={"settled_at": candidate.settled_at}) == candidate:
                    return current
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE,
                    "AgentRun model request was already settled differently",
                )
            self.backend.executor().execute(
                "UPDATE agent_run_model_requests SET state = ?, settled_at_unix = ?, "
                "finish_reason = ?, error_code = ?, usage_availability = ?, input_tokens = ?, "
                "output_tokens = ?, total_tokens = ?, cost_availability = ?, "
                "cost_amount_minor = ?, cost_currency = ?, cost_source = ? "
                "WHERE model_request_id = ? AND workspace_id = ? AND state = 'admitted'",
                (
                    candidate.state.value,
                    _unix(candidate.settled_at),
                    candidate.finish_reason.value if candidate.finish_reason else None,
                    candidate.error_code.value if candidate.error_code else None,
                    candidate.usage.availability.value,
                    candidate.usage.input_tokens,
                    candidate.usage.output_tokens,
                    candidate.usage.total_tokens,
                    candidate.cost.availability.value,
                    candidate.cost.amount_minor,
                    candidate.cost.currency,
                    candidate.cost.source,
                    model_request_id,
                    workspace_id,
                ),
            )
            stored = self.get_model_request(workspace_id, model_request_id)
            if stored is None:
                raise StorageError(
                    StorageErrorCode.NEEDS_REPAIR,
                    "AgentRun model request could not be read after settlement",
                )
            return stored

        return self.backend.transact(work)

    def finalize_agent_run(
        self,
        workspace_id: str,
        *,
        agent_run_id: str,
        finish_reason: FinishReason | str,
        stop_code: AgentStopCode | str | None = None,
        stop_detail: str | None = None,
        model_attempts: int = 0,
        retry_count: int = 0,
        tool_rounds: int = 0,
        tool_calls: int = 0,
        max_estimated_request_chars: int | None = None,
        request_char_budget: int | None = None,
        cleared_cycle_count: int | None = None,
        dropped_turn_count: int | None = None,
        dropped_cycle_count: int | None = None,
        dropped_record_count: int | None = None,
        usage: ModelUsage | None = None,
        cost: ModelCost | None = None,
        policy_schema_version: int | None = None,
        max_context_tokens: int | None = None,
        last_context_tokens: int | None = None,
        context_window_tokens: int | None = None,
        reserve_tokens: int | None = None,
        keep_recent_tokens: int | None = None,
        accounting_basis: TokenAccountingBasis | str | None = None,
        compaction_count: int = 0,
        overflow_recovery_count: int = 0,
        validation_outcome: str = "not_run",
        finalized_at: datetime | None = None,
    ) -> AgentRunTerminalMetrics:
        run = self._require_run(workspace_id, agent_run_id)
        task_run_id = self._task_run_id(run)
        try:
            selected_finish_reason = FinishReason(finish_reason)
            selected_stop_code = AgentStopCode(stop_code) if stop_code is not None else None
        except (TypeError, ValueError):
            raise StorageError(
                StorageErrorCode.UNAVAILABLE,
                "AgentRun terminal metrics finish facts are invalid",
            ) from None
        if (
            selected_finish_reason
            in (FinishReason.STOP, FinishReason.STEERED, FinishReason.CANCELLED)
            and selected_stop_code is not None
        ) or (selected_finish_reason is FinishReason.ERROR and selected_stop_code is None):
            raise StorageError(
                StorageErrorCode.UNAVAILABLE,
                "AgentRun terminal metrics finish facts are inconsistent",
            )
        requests = self.list_model_requests(workspace_id, agent_run_id)
        if any(item.state is ModelRequestState.ADMITTED for item in requests):
            raise StorageError(
                StorageErrorCode.UNAVAILABLE,
                "unsettled AgentRun model requests prevent terminal finalization",
            )
        aggregate_usage = usage or _aggregate_usage(requests)
        aggregate_cost = cost or _aggregate_cost(requests)
        executions = self._tool_executions(workspace_id, agent_run_id)
        if any(item.state.value != "closed" for item in executions):
            raise StorageError(
                StorageErrorCode.UNAVAILABLE,
                "non-terminal ToolExecutions prevent terminal finalization",
            )
        counts = self._tool_counts(executions)
        persisted_tool_calls = len(executions)
        persisted_tool_rounds = len(
            {getattr(item, "assistant_record_id", None) or item.turn_id for item in executions}
        )
        candidate = AgentRunTerminalMetrics(
            agent_run_id=agent_run_id,
            workspace_id=workspace_id,
            session_id=run.session_id,
            task_run_id=task_run_id,
            turn_id=run.turn_id,
            finish_reason=selected_finish_reason,
            stop_code=selected_stop_code,
            stop_detail=stop_detail,
            model_attempts=model_attempts,
            retry_count=retry_count,
            tool_rounds=max(tool_rounds, persisted_tool_rounds),
            tool_calls=max(tool_calls, persisted_tool_calls),
            max_estimated_request_chars=(
                max_estimated_request_chars
                if max_estimated_request_chars is not None
                else max((item.estimated_request_chars for item in requests), default=0)
            ),
            request_char_budget=request_char_budget
            or max((item.request_char_budget for item in requests), default=1),
            cleared_cycle_count=(
                cleared_cycle_count
                if cleared_cycle_count is not None
                else sum(item.cleared_cycle_count for item in requests)
            ),
            dropped_turn_count=(
                dropped_turn_count
                if dropped_turn_count is not None
                else sum(item.dropped_turn_count for item in requests)
            ),
            dropped_cycle_count=(
                dropped_cycle_count
                if dropped_cycle_count is not None
                else sum(item.dropped_cycle_count for item in requests)
            ),
            dropped_record_count=(
                dropped_record_count
                if dropped_record_count is not None
                else sum(item.dropped_record_count for item in requests)
            ),
            usage=aggregate_usage,
            cost=aggregate_cost,
            tool_terminal_counts=counts,
            policy_schema_version=policy_schema_version,
            max_context_tokens=max_context_tokens,
            last_context_tokens=last_context_tokens,
            context_window_tokens=context_window_tokens,
            reserve_tokens=reserve_tokens,
            keep_recent_tokens=keep_recent_tokens,
            accounting_basis=accounting_basis,
            compaction_count=compaction_count,
            overflow_recovery_count=overflow_recovery_count,
            validation_outcome=validation_outcome,
            finalized_at=finalized_at or self.backend.now(),
        )

        def work() -> AgentRunTerminalMetrics:
            existing = self._metrics_for_run(workspace_id, agent_run_id)
            if existing is not None:
                if (
                    existing.model_copy(update={"finalized_at": candidate.finalized_at})
                    == candidate
                ):
                    return existing
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE,
                    "AgentRun terminal metrics were already finalized differently",
                )
            self.backend.executor().execute(
                f"INSERT INTO agent_run_terminal_metrics({_METRICS_COLUMNS}) VALUES ({', '.join('?' for _ in range(_METRICS_VALUE_COUNT))})",
                self._metrics_values(candidate),
            )
            stored = self._metrics_for_run(workspace_id, agent_run_id)
            if stored is None:
                raise StorageError(
                    StorageErrorCode.NEEDS_REPAIR,
                    "AgentRun terminal metrics could not be read after finalization",
                )
            return stored

        return self.backend.transact(work)

    def get_model_request(
        self, workspace_id: str, model_request_id: str
    ) -> ModelRequestObservation | None:
        row = self.backend.read_one(
            f"SELECT {_REQUEST_COLUMNS} FROM agent_run_model_requests "
            "WHERE workspace_id = ? AND model_request_id = ?",
            (workspace_id, model_request_id),
        )
        return _request_from_row(row) if row is not None else None

    def list_model_requests(
        self, workspace_id: str, agent_run_id: str
    ) -> tuple[ModelRequestObservation, ...]:
        rows = self.backend.read_all(
            f"SELECT {_REQUEST_COLUMNS} FROM agent_run_model_requests "
            "WHERE workspace_id = ? AND agent_run_id = ? ORDER BY attempt_ordinal ASC",
            (workspace_id, agent_run_id),
        )
        return tuple(_request_from_row(row) for row in rows)

    def get_terminal_metrics(
        self, workspace_id: str, agent_run_id: str
    ) -> AgentRunTerminalMetrics | None:
        return self._metrics_for_run(workspace_id, agent_run_id)

    def get_agent_run_observation(
        self, workspace_id: str, agent_run_id: str
    ) -> AgentRunObservation | None:
        run = self.get_agent_run(workspace_id, agent_run_id)
        if run is None:
            return None
        task_run_id = self._task_run_id(run)
        metrics = self.get_terminal_metrics(workspace_id, agent_run_id)
        return AgentRunObservation(
            agent_run_id=run.agent_run_id,
            workspace_id=workspace_id,
            session_id=run.session_id,
            task_run_id=task_run_id,
            turn_id=run.turn_id,
            resume_of_agent_run_id=run.resume_of_agent_run_id,
            created_at=run.created_at,
            terminal_metrics=metrics,
            retry_progress=self.get_retry_progress(workspace_id, agent_run_id),
            requests=self.list_model_requests(workspace_id, agent_run_id),
        )

    def get_retry_progress(
        self, workspace_id: str, agent_run_id: str
    ) -> AgentRunRetryProgress | None:
        row = self.backend.read_one(
            f"SELECT {_RETRY_COLUMNS} FROM agent_run_retry_progress "
            "WHERE workspace_id = ? AND agent_run_id = ?",
            (workspace_id, agent_run_id),
        )
        return _retry_progress_from_row(row) if row is not None else None

    def record_retry_progress(
        self,
        workspace_id: str,
        *,
        agent_run_id: str,
        consecutive_model_retries: int,
        total_retry_count: int,
        summary_retry_count: int,
        updated_at: datetime | None = None,
    ) -> AgentRunRetryProgress:
        self._require_run(workspace_id, agent_run_id)
        candidate = AgentRunRetryProgress(
            agent_run_id=agent_run_id,
            workspace_id=workspace_id,
            consecutive_model_retries=consecutive_model_retries,
            total_retry_count=total_retry_count,
            summary_retry_count=summary_retry_count,
            updated_at=updated_at or self.backend.now(),
        )

        def work() -> AgentRunRetryProgress:
            current = self.get_retry_progress(workspace_id, agent_run_id)
            if current is not None:
                if (
                    candidate.total_retry_count < current.total_retry_count
                    or candidate.summary_retry_count < current.summary_retry_count
                ):
                    raise StorageError(
                        StorageErrorCode.UNAVAILABLE,
                        "AgentRun retry progress cannot move backwards",
                    )
                if (
                    candidate.consecutive_model_retries == current.consecutive_model_retries
                    and candidate.total_retry_count == current.total_retry_count
                    and candidate.summary_retry_count == current.summary_retry_count
                ):
                    return current
                self.backend.executor().execute(
                    "UPDATE agent_run_retry_progress SET consecutive_model_retries = ?, "
                    "total_retry_count = ?, summary_retry_count = ?, updated_at_unix = ? "
                    "WHERE workspace_id = ? AND agent_run_id = ?",
                    (
                        candidate.consecutive_model_retries,
                        candidate.total_retry_count,
                        candidate.summary_retry_count,
                        _unix(candidate.updated_at),
                        workspace_id,
                        agent_run_id,
                    ),
                )
            else:
                self.backend.executor().execute(
                    f"INSERT INTO agent_run_retry_progress({_RETRY_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        candidate.agent_run_id,
                        candidate.workspace_id,
                        candidate.consecutive_model_retries,
                        candidate.total_retry_count,
                        candidate.summary_retry_count,
                        _unix(candidate.updated_at),
                    ),
                )
            stored = self.get_retry_progress(workspace_id, agent_run_id)
            if stored is None:
                raise StorageError(
                    StorageErrorCode.NEEDS_REPAIR,
                    "AgentRun retry progress could not be read after update",
                )
            return stored

        return self.backend.transact(work)

    def _require_run(self, workspace_id: str, agent_run_id: str) -> DurableAgentRun:
        run = self.get_agent_run(workspace_id, agent_run_id)
        if run is None:
            raise StorageError(StorageErrorCode.NOT_FOUND, "AgentRun is missing")
        return run

    def _task_run_id(self, run: DurableAgentRun) -> str:
        row = self.backend.read_one(
            "SELECT task_run_id FROM turns WHERE turn_id = ? AND session_id = ?",
            (run.turn_id, run.session_id),
        )
        if row is None:
            raise StorageError(
                StorageErrorCode.NEEDS_REPAIR,
                "AgentRun observation subject is not safe to read",
            )
        return str(row[0])

    def _request_by_ordinal(
        self, workspace_id: str, agent_run_id: str, attempt_ordinal: int
    ) -> ModelRequestObservation | None:
        row = self.backend.read_one(
            f"SELECT {_REQUEST_COLUMNS} FROM agent_run_model_requests "
            "WHERE workspace_id = ? AND agent_run_id = ? AND attempt_ordinal = ?",
            (workspace_id, agent_run_id, attempt_ordinal),
        )
        return _request_from_row(row) if row is not None else None

    @staticmethod
    def _same_admission(
        existing: ModelRequestObservation, candidate: ModelRequestObservation
    ) -> bool:
        return (
            existing.model_copy(
                update={
                    "model_request_id": candidate.model_request_id,
                    "admitted_at": candidate.admitted_at,
                }
            )
            == candidate
        )

    @staticmethod
    def _request_values(request: ModelRequestObservation) -> tuple[object, ...]:
        return (
            request.model_request_id,
            request.workspace_id,
            request.agent_run_id,
            request.attempt_ordinal,
            request.state.value,
            _unix(request.admitted_at),
            _optional_unix(request.settled_at),
            request.estimated_request_chars,
            request.request_char_budget,
            request.cleared_cycle_count,
            request.dropped_turn_count,
            request.dropped_cycle_count,
            request.dropped_record_count,
            request.tool_rounds,
            request.tool_calls,
            request.finish_reason.value if request.finish_reason else None,
            request.error_code.value if request.error_code else None,
            request.usage.availability.value,
            request.usage.input_tokens,
            request.usage.output_tokens,
            request.usage.total_tokens,
            request.cost.availability.value,
            request.cost.amount_minor,
            request.cost.currency,
            request.cost.source,
            request.purpose.value,
            _optional_json(request.prompt_evidence),
            None,
            request.policy_schema_version,
            request.estimated_context_tokens,
            request.context_window_tokens,
            request.reserve_tokens,
            request.keep_recent_tokens,
            request.accounting_basis.value if request.accounting_basis else None,
            int(request.compaction_required) if request.compaction_required is not None else None,
        )

    def _metrics_for_run(
        self, workspace_id: str, agent_run_id: str
    ) -> AgentRunTerminalMetrics | None:
        row = self.backend.read_one(
            f"SELECT {_METRICS_COLUMNS} FROM agent_run_terminal_metrics "
            "WHERE workspace_id = ? AND agent_run_id = ?",
            (workspace_id, agent_run_id),
        )
        if row is None:
            return None
        run = self.get_agent_run(workspace_id, agent_run_id)
        if run is None:
            raise StorageError(
                StorageErrorCode.NEEDS_REPAIR,
                "AgentRun terminal observation subject is not safe to read",
            )
        return _metrics_from_row(row, run=run, task_run_id=self._task_run_id(run))

    def _tool_executions(self, workspace_id: str, agent_run_id: str) -> tuple:
        if self.list_tool_executions is None:
            return ()
        return tuple(self.list_tool_executions(workspace_id, agent_run_id))

    @staticmethod
    def _tool_counts(executions: tuple) -> ToolTerminalCounts:
        values = {name: 0 for name in ToolTerminalCounts.model_fields}
        for execution in executions:
            name = execution.disposition.value
            if name in values:
                values[name] += 1
        return ToolTerminalCounts(**values)

    @staticmethod
    def _metrics_values(metrics: AgentRunTerminalMetrics) -> tuple[object, ...]:
        usage = metrics.usage
        cost = metrics.cost
        return (
            metrics.agent_run_id,
            metrics.workspace_id,
            metrics.finish_reason.value,
            metrics.stop_code.value if metrics.stop_code else None,
            metrics.model_attempts,
            metrics.retry_count,
            metrics.tool_rounds,
            metrics.tool_calls,
            metrics.max_estimated_request_chars,
            metrics.request_char_budget,
            metrics.cleared_cycle_count,
            metrics.dropped_turn_count,
            metrics.dropped_cycle_count,
            metrics.dropped_record_count,
            usage.availability.value,
            usage.input_tokens,
            usage.output_tokens,
            usage.total_tokens,
            cost.availability.value,
            cost.amount_minor,
            cost.currency,
            cost.source,
            json.dumps(
                metrics.tool_terminal_counts.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            metrics.validation_outcome,
            "not_run",
            "not_completed",
            # Reuse the existing nullable reason-code slot to avoid a migration for optional
            # diagnostics. AgentLoop's observation finalization remains best-effort and fail-open.
            metrics.stop_detail,
            _unix(metrics.finalized_at),
            metrics.policy_schema_version,
            metrics.max_context_tokens,
            metrics.last_context_tokens,
            metrics.context_window_tokens,
            metrics.reserve_tokens,
            metrics.keep_recent_tokens,
            metrics.accounting_basis.value if metrics.accounting_basis else None,
            metrics.compaction_count,
            metrics.overflow_recovery_count,
        )


def _usage_from_columns(
    availability: object, input_tokens, output_tokens, total_tokens
) -> ModelUsage:
    try:
        return ModelUsage(
            availability=UsageAvailability(str(availability)),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
        )
    except (TypeError, ValueError) as exc:
        raise StorageError(
            StorageErrorCode.NEEDS_REPAIR, "AgentRun usage observation is not safe to read"
        ) from exc


def _cost_from_columns(availability: object, amount_minor, currency, source) -> ModelCost:
    try:
        return ModelCost(
            availability=UsageAvailability(str(availability)),
            amount_minor=amount_minor,
            currency=currency,
            source=source,
        )
    except (TypeError, ValueError) as exc:
        raise StorageError(
            StorageErrorCode.NEEDS_REPAIR, "AgentRun cost observation is not safe to read"
        ) from exc


def _retry_progress_from_row(row: tuple[object, ...]) -> AgentRunRetryProgress:
    try:
        return AgentRunRetryProgress(
            agent_run_id=str(row[0]),
            workspace_id=str(row[1]),
            consecutive_model_retries=int(row[2]),
            total_retry_count=int(row[3]),
            summary_retry_count=int(row[4]),
            updated_at=_from_unix(row[5]),
        )
    except (IndexError, TypeError, ValueError) as exc:
        raise StorageError(
            StorageErrorCode.NEEDS_REPAIR, "AgentRun retry progress is not safe to read"
        ) from exc


def _request_from_row(row: tuple[object, ...]) -> ModelRequestObservation:
    try:
        return ModelRequestObservation(
            model_request_id=str(row[0]),
            workspace_id=str(row[1]),
            agent_run_id=str(row[2]),
            attempt_ordinal=int(row[3]),
            state=ModelRequestState(str(row[4])),
            admitted_at=_from_unix(row[5]),
            settled_at=_from_unix(row[6]) if row[6] is not None else None,
            estimated_request_chars=int(row[7]),
            request_char_budget=int(row[8]),
            cleared_cycle_count=int(row[9]),
            dropped_turn_count=int(row[10]),
            dropped_cycle_count=int(row[11]),
            dropped_record_count=int(row[12]),
            tool_rounds=int(row[13]),
            tool_calls=int(row[14]),
            finish_reason=ModelFinishReason(str(row[15])) if row[15] is not None else None,
            error_code=ModelErrorCode(str(row[16])) if row[16] is not None else None,
            usage=_usage_from_columns(row[17], row[18], row[19], row[20]),
            cost=_cost_from_columns(row[21], row[22], row[23], row[24]),
            purpose=ModelRequestPurpose(str(row[25])),
            prompt_evidence=_optional_model(row[26], PromptProfileEvidence),
            policy_schema_version=int(row[28]) if row[28] is not None else None,
            estimated_context_tokens=int(row[29]) if row[29] is not None else None,
            context_window_tokens=int(row[30]) if row[30] is not None else None,
            reserve_tokens=int(row[31]) if row[31] is not None else None,
            keep_recent_tokens=int(row[32]) if row[32] is not None else None,
            accounting_basis=(TokenAccountingBasis(str(row[33])) if row[33] is not None else None),
            compaction_required=_optional_bool(row[34]),
        )
    except StorageError:
        raise
    except (IndexError, TypeError, ValueError) as exc:
        raise StorageError(
            StorageErrorCode.NEEDS_REPAIR, "AgentRun model request observation is not safe to read"
        ) from exc


def _metrics_from_row(
    row: tuple[object, ...], *, run: DurableAgentRun, task_run_id: str
) -> AgentRunTerminalMetrics:
    try:
        raw_counts = json.loads(str(row[22]))
        if not isinstance(raw_counts, dict):
            raise ValueError("tool terminal counts must be a mapping")
        counts = ToolTerminalCounts.model_validate(raw_counts, strict=True)
        canonical_counts = json.dumps(
            counts.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if str(row[22]) != canonical_counts:
            raise ValueError("tool terminal counts are not canonical")
        return AgentRunTerminalMetrics(
            agent_run_id=str(row[0]),
            workspace_id=str(row[1]),
            session_id=run.session_id,
            task_run_id=task_run_id,
            turn_id=run.turn_id,
            finish_reason=FinishReason(str(row[2])),
            stop_code=AgentStopCode(str(row[3])) if row[3] is not None else None,
            stop_detail=str(row[26]) if row[26] is not None else None,
            model_attempts=int(row[4]),
            retry_count=int(row[5]),
            tool_rounds=int(row[6]),
            tool_calls=int(row[7]),
            max_estimated_request_chars=int(row[8]),
            request_char_budget=int(row[9]),
            cleared_cycle_count=int(row[10]),
            dropped_turn_count=int(row[11]),
            dropped_cycle_count=int(row[12]),
            dropped_record_count=int(row[13]),
            usage=_usage_from_columns(row[14], row[15], row[16], row[17]),
            cost=_cost_from_columns(row[18], row[19], row[20], row[21]),
            tool_terminal_counts=counts,
            validation_outcome=str(row[23]),
            finalized_at=_from_unix(row[27]),
            policy_schema_version=int(row[28]) if row[28] is not None else None,
            max_context_tokens=int(row[29]) if row[29] is not None else None,
            last_context_tokens=int(row[30]) if row[30] is not None else None,
            context_window_tokens=int(row[31]) if row[31] is not None else None,
            reserve_tokens=int(row[32]) if row[32] is not None else None,
            keep_recent_tokens=int(row[33]) if row[33] is not None else None,
            accounting_basis=(TokenAccountingBasis(str(row[34])) if row[34] is not None else None),
            compaction_count=int(row[35]),
            overflow_recovery_count=int(row[36]),
        )
    except StorageError:
        raise
    except (IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise StorageError(
            StorageErrorCode.NEEDS_REPAIR, "AgentRun terminal observation is not safe to read"
        ) from exc


def _aggregate_usage(requests: tuple[ModelRequestObservation, ...]) -> ModelUsage:
    if not requests or any(
        item.usage.availability is UsageAvailability.UNAVAILABLE for item in requests
    ):
        return ModelUsage.unavailable()
    input_values = [item.usage.input_tokens for item in requests]
    output_values = [item.usage.output_tokens for item in requests]
    total_values = [item.usage.total_tokens for item in requests]
    values: dict[str, int] = {}
    if all(value is not None for value in input_values):
        values["input_tokens"] = sum(value for value in input_values if value is not None)
    if all(value is not None for value in output_values):
        values["output_tokens"] = sum(value for value in output_values if value is not None)
    if all(value is not None for value in total_values):
        values["total_tokens"] = sum(value for value in total_values if value is not None)
    if "input_tokens" in values and "output_tokens" in values:
        values["total_tokens"] = values["input_tokens"] + values["output_tokens"]
    if not values:
        return ModelUsage.unavailable()
    return ModelUsage(availability=UsageAvailability.AVAILABLE, **values)


def _aggregate_cost(requests: tuple[ModelRequestObservation, ...]) -> ModelCost:
    if not requests or any(
        item.cost.availability is UsageAvailability.UNAVAILABLE for item in requests
    ):
        return ModelCost.unavailable()
    currencies = {item.cost.currency for item in requests}
    sources = {item.cost.source for item in requests}
    if len(currencies) != 1 or len(sources) != 1:
        return ModelCost.unavailable()
    amount = sum(item.cost.amount_minor or 0 for item in requests)
    return ModelCost(
        availability=UsageAvailability.AVAILABLE,
        amount_minor=amount,
        currency=next(iter(currencies)),
        source=next(iter(sources)),
    )


__all__ = ["SqliteObservabilityJournal"]
