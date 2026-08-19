"""Turn-submit coordination. ConversationLog remains the only chat writer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStoreSession
from morrow.core.domain import (
    AGENT_RUN_ID_PREFIX,
    COMMAND_ID_PREFIX,
    TASK_RUN_ID_PREFIX,
    AgentRunSnapshot,
    DurableAgentRun,
    DurableSession,
    DurableTaskRun,
    DurableTurn,
    SessionHealth,
    SourceRevisionRef,
    TurnSubmitDisposition,
    TurnSubmitReceipt,
    canonical_json_bytes,
    sha256_digest,
)
from morrow.core.models import ModelRef, Preferences, UserMessage
from morrow.core.ports import IdSource
from morrow.runtime.conversation import (
    ConversationAppend,
    ConversationLog,
    ConversationLogError,
    TurnTerminalRecord,
)
from morrow.runtime.durable_log import DurableConversationWriter, restore_conversation_log
from morrow.runtime.session import Session


@dataclass(frozen=True)
class TurnSubmitResult:
    kind: Literal["accepted", "closed_replay", "recovery", "conflict"]
    turn_id: str | None
    receipt: TurnSubmitReceipt | None = None
    assistant_text: str | None = None


def request_digest(user_input: str) -> str:
    return sha256_digest(canonical_json_bytes({"content": user_input}))


def build_agent_run_snapshot(
    session: Session,
    *,
    model: ModelRef,
    run_policy,
    tools,
    runtime_instance_id: str,
) -> AgentRunSnapshot:
    revisions: list[SourceRevisionRef] = []
    if session.profile is not None:
        revisions.append(
            SourceRevisionRef(
                kind="workspace_profile",
                revision=session.profile_revision,
                content_sha256=sha256_digest(
                    canonical_json_bytes(session.profile.model_dump(mode="json"))
                ),
            )
        )
    if session.workspace_preferences != Preferences():
        revisions.append(
            SourceRevisionRef(
                kind="workspace_preferences",
                revision=session.preferences_revision,
                content_sha256=sha256_digest(
                    canonical_json_bytes(session.workspace_preferences.model_dump(mode="json"))
                ),
            )
        )
    tool_payload = [tool.model_dump(mode="json") for tool in tools]
    return AgentRunSnapshot(
        profile=session.profile,
        preferences=session.preferences,
        model=model,
        provider_id=model.provider_id,
        source_revisions=tuple(revisions),
        run_policy_digest=sha256_digest(canonical_json_bytes(run_policy.model_dump(mode="json"))),
        tool_schema_digest=sha256_digest(canonical_json_bytes(tool_payload)),
        permission_profile_digest=sha256_digest(
            canonical_json_bytes(session.permission_profile.model_dump(mode="json"))
        ),
        runtime_instance_id=runtime_instance_id,
    )


class SessionPersistence:
    """Journal-backed Session committer and turn-submit coordinator."""

    def __init__(
        self,
        *,
        workspace_id: str,
        journal: SqliteOperationalJournal,
        store_session: OperationalStoreSession,
        id_source: IdSource,
        model: ModelRef,
        run_policy,
        runtime_instance_id: str,
    ) -> None:
        self.workspace_id = workspace_id
        self.journal = journal
        self.store_session = store_session
        self.id_source = id_source
        self.model = model
        self.run_policy = run_policy
        self.runtime_instance_id = runtime_instance_id
        self.writer: DurableConversationWriter | None = None
        self._session: Session | None = None
        self._last_client_message_id: str | None = None

    def attach(self, session: Session) -> None:
        self._session = session
        self.writer = DurableConversationWriter(
            session.log,
            self.journal,
            workspace_id=self.workspace_id,
            session_id=session.session_id,
            id_source=self.id_source,
        )
        session.committer = self

    def commit(self, planned: ConversationAppend) -> None:
        if self.writer is None:
            raise RuntimeError("session persistence is not attached")
        self.writer.commit(planned)
        if planned.added and isinstance(planned.added[-1], TurnTerminalRecord):
            self.close_open_receipt()

    def close_open_receipt(self) -> None:
        if self._session is None or self._last_client_message_id is None:
            return
        receipt = self.journal.get_receipt(
            self.workspace_id, self._session.session_id, self._last_client_message_id
        )
        if receipt is None or receipt.disposition is TurnSubmitDisposition.ACCEPTED_CLOSED:
            return
        self.journal.update_receipt(
            self.workspace_id,
            receipt.model_copy(update={"disposition": TurnSubmitDisposition.ACCEPTED_CLOSED}),
        )

    def submit_user(
        self,
        session: Session,
        user_input: str,
        client_message_id: str,
        *,
        turn_id: str,
        agent_run_id: str,
        tools: tuple = (),
    ) -> TurnSubmitResult:
        digest = request_digest(user_input)
        existing = self.journal.get_receipt(
            self.workspace_id, session.session_id, client_message_id
        )
        if existing is not None:
            if existing.request_digest != digest:
                return TurnSubmitResult("conflict", existing.turn_id, existing)
            if existing.disposition is TurnSubmitDisposition.ACCEPTED_CLOSED:
                return TurnSubmitResult(
                    "closed_replay",
                    existing.turn_id,
                    existing,
                    assistant_text=_last_assistant_text(session.log),
                )
            session.health = SessionHealth.NEEDS_RECOVERY
            return TurnSubmitResult("recovery", existing.turn_id, existing)

        planned = session.log.plan_begin_turn(UserMessage(content=user_input))
        snapshot = build_agent_run_snapshot(
            session,
            model=self.model,
            run_policy=self.run_policy,
            tools=tools,
            runtime_instance_id=self.runtime_instance_id,
        )
        command_id = self.id_source.new_id(COMMAND_ID_PREFIX)
        writer = self.writer
        if writer is None:
            raise RuntimeError("session persistence is not attached")

        def work(txn: SqliteOperationalJournal) -> str:
            row = txn.get_session(self.workspace_id, session.session_id)
            if row is None:
                row = txn.create_session(
                    DurableSession(session_id=session.session_id, workspace_id=self.workspace_id)
                )
            task_id = row.current_task_run_id
            if task_id is None:
                task = txn.create_task_run(
                    self.workspace_id,
                    DurableTaskRun(
                        task_run_id=self.id_source.new_id(TASK_RUN_ID_PREFIX),
                        session_id=session.session_id,
                        workspace_id=self.workspace_id,
                    ),
                )
                task_id = task.task_run_id
            txn.create_turn(
                self.workspace_id,
                DurableTurn(
                    turn_id=turn_id,
                    session_id=session.session_id,
                    task_run_id=task_id,
                    client_message_id=client_message_id,
                ),
            )
            txn.create_agent_run(
                self.workspace_id,
                DurableAgentRun(
                    agent_run_id=agent_run_id or self.id_source.new_id(AGENT_RUN_ID_PREFIX),
                    turn_id=turn_id,
                    session_id=session.session_id,
                    snapshot=snapshot,
                ),
            )
            txn.put_receipt(
                self.workspace_id,
                TurnSubmitReceipt(
                    session_id=session.session_id,
                    client_message_id=client_message_id,
                    request_digest=digest,
                    disposition=TurnSubmitDisposition.ACCEPTED_OPEN,
                    turn_id=turn_id,
                    command_id=command_id,
                ),
            )
            writer.persist(planned)
            return turn_id

        accepted_turn = self.journal.transact(work)
        session.log.apply_committed(planned)
        self._last_client_message_id = client_message_id
        session.dirty = True
        receipt = self.journal.get_receipt(self.workspace_id, session.session_id, client_message_id)
        return TurnSubmitResult("accepted", accepted_turn, receipt)

    def start_new_session(self, session: Session, session_id: str) -> None:
        self.journal.create_session(
            DurableSession(session_id=session_id, workspace_id=self.workspace_id)
        )
        session.reset(session_id)
        session.log = ConversationLog()
        self.attach(session)

    def restore_into(self, session: Session) -> None:
        row = self.journal.get_session(self.workspace_id, session.session_id)
        if row is None:
            self.journal.create_session(
                DurableSession(session_id=session.session_id, workspace_id=self.workspace_id)
            )
            self.attach(session)
            return
        session.lifecycle = row.lifecycle
        session.health = row.health
        try:
            session.log = restore_conversation_log(
                self.journal, self.workspace_id, session.session_id
            )
        except ConversationLogError:
            session.log = ConversationLog()
            session.health = SessionHealth.QUARANTINED
        else:
            if session.log.has_active_turn and session.health is SessionHealth.OK:
                session.health = SessionHealth.NEEDS_RECOVERY
        if session.health is SessionHealth.NEEDS_RECOVERY:
            self.journal.save_session(
                self.workspace_id,
                row.model_copy(update={"health": SessionHealth.NEEDS_RECOVERY}),
            )
        elif session.health is SessionHealth.QUARANTINED:
            self.journal.save_session(
                self.workspace_id,
                row.model_copy(update={"health": SessionHealth.QUARANTINED}),
            )
        self.attach(session)

    def close(self) -> None:
        self.store_session.close()


def _last_assistant_text(log: ConversationLog) -> str | None:
    for message in reversed(log.messages_view()):
        if message.role == "assistant" and message.content:
            return message.content
    return None
