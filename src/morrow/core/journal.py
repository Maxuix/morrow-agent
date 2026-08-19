"""Narrow lifecycle and conversation-journal ports for the Operational Store."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from morrow.core.domain import (
    DurableAgentRun,
    DurableConversationRecord,
    DurableSession,
    DurableTaskRun,
    DurableTurn,
    TurnSubmitReceipt,
)
from morrow.core.execution import DurableApproval, DurableToolExecution


class SessionLifecyclePort(Protocol):
    def create_session(
        self, session: DurableSession, *, task: DurableTaskRun | None = None
    ) -> DurableSession: ...

    def get_session(self, workspace_id: str, session_id: str) -> DurableSession | None: ...

    def list_sessions(self, workspace_id: str) -> tuple[DurableSession, ...]: ...

    def save_session(self, workspace_id: str, session: DurableSession) -> DurableSession: ...

    def get_task_run(self, workspace_id: str, task_run_id: str) -> DurableTaskRun | None: ...

    def create_task_run(self, workspace_id: str, task: DurableTaskRun) -> DurableTaskRun: ...


class ConversationJournalPort(Protocol):
    def append_records(
        self, workspace_id: str, records: Sequence[DurableConversationRecord]
    ) -> DurableSession: ...

    def load_records(
        self, workspace_id: str, session_id: str
    ) -> tuple[DurableConversationRecord, ...]: ...


class AgentRunPort(Protocol):
    def create_turn(self, workspace_id: str, turn: DurableTurn) -> DurableTurn: ...

    def get_turn(self, workspace_id: str, turn_id: str) -> DurableTurn | None: ...

    def create_agent_run(self, workspace_id: str, run: DurableAgentRun) -> DurableAgentRun: ...

    def get_agent_run(self, workspace_id: str, agent_run_id: str) -> DurableAgentRun | None: ...


class TurnSubmitReceiptPort(Protocol):
    def get_receipt(
        self, workspace_id: str, session_id: str, client_message_id: str
    ) -> TurnSubmitReceipt | None: ...

    def put_receipt(self, workspace_id: str, receipt: TurnSubmitReceipt) -> TurnSubmitReceipt: ...


class ToolExecutionJournalPort(Protocol):
    def put_execution(
        self, workspace_id: str, execution: DurableToolExecution
    ) -> DurableToolExecution: ...

    def get_execution(
        self, workspace_id: str, tool_execution_id: str
    ) -> DurableToolExecution | None: ...

    def list_executions(
        self, workspace_id: str, *, agent_run_id: str
    ) -> tuple[DurableToolExecution, ...]: ...

    def save_execution(
        self,
        workspace_id: str,
        execution: DurableToolExecution,
        *,
        expected_row_version: int,
    ) -> DurableToolExecution: ...


class ApprovalJournalPort(Protocol):
    def put_approval(self, workspace_id: str, approval: DurableApproval) -> DurableApproval: ...

    def get_approval(self, workspace_id: str, approval_id: str) -> DurableApproval | None: ...

    def get_approval_for_execution(
        self, workspace_id: str, tool_execution_id: str
    ) -> DurableApproval | None: ...

    def save_approval(
        self,
        workspace_id: str,
        approval: DurableApproval,
        *,
        expected_row_version: int,
    ) -> DurableApproval: ...
