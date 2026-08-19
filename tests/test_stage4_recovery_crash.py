"""Recovery discovery, decisions, and subprocess crash classification."""

from __future__ import annotations

import multiprocessing
import os
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import BusyRetryPolicy, OperationalStore
from morrow.application.recovery import RecoveryService
from morrow.application.turns import SessionPersistence
from morrow.core.capabilities import AccessScope, ApprovalMode, ProcessIsolation
from morrow.core.domain import (
    AgentRunSnapshot,
    DurableAgentRun,
    DurableSession,
    DurableTaskRun,
    DurableTurn,
    SessionHealth,
    SourceRevisionRef,
    sha256_digest,
)
from morrow.core.execution import (
    DurableToolExecution,
    EffectClass,
    FileMutationEvidence,
    PreparedIntent,
    RecoveryClassification,
    ToolExecutionState,
    transition_execution,
)
from morrow.core.models import ModelRef, Preferences, Profile
from morrow.core.permissions import (
    CapabilityGrant,
    CapabilityIsolation,
    CapabilityName,
    GrantSource,
    IsolationLabel,
    PermissionSnapshot,
    capability_grant_digest,
)
from morrow.core.recovery import FileObservation, RecoveryResolution
from morrow.core.store import StoreOpenMode
from morrow.runtime.conversation import ConversationLog
from morrow.runtime.session import Session
from morrow.testing import FixedClock, FixedIdSource


def _retry() -> BusyRetryPolicy:
    return BusyRetryPolicy(busy_timeout_ms=0, sleep=lambda _delay: None, rng=random.Random(0))


def _digest(label: str) -> str:
    return sha256_digest(label)


def _snapshot() -> AgentRunSnapshot:
    return AgentRunSnapshot(
        profile=Profile(name="demo"),
        preferences=Preferences(language="中文"),
        model=ModelRef(provider_id="p", model_id="m"),
        provider_id="p",
        source_revisions=(
            SourceRevisionRef(
                kind="workspace_profile", revision=1, content_sha256=_digest("profile")
            ),
        ),
        run_policy_digest=_digest("policy"),
        tool_schema_digest=_digest("tools"),
        permission_profile_digest=_digest("perms"),
        runtime_instance_id="host-1",
    )


def _open(tmp_path: Path):
    store = OperationalStore(
        tmp_path / "state",
        retry_policy=_retry(),
        clock=FixedClock(),
        maintenance_timeout=0,
    )
    handle = store.initialize()
    return store, handle, SqliteOperationalJournal(handle)


def _seed(journal: SqliteOperationalJournal, workspace_id: str = "ws_1") -> None:
    journal.create_session(
        DurableSession(session_id="ses_1", workspace_id=workspace_id),
        task=DurableTaskRun(task_run_id="task_1", session_id="ses_1", workspace_id=workspace_id),
    )
    journal.create_turn(
        workspace_id,
        DurableTurn(
            turn_id="turn_1",
            session_id="ses_1",
            task_run_id="task_1",
            client_message_id="client-1",
        ),
    )
    journal.create_agent_run(
        workspace_id,
        DurableAgentRun(
            agent_run_id="arun_1",
            turn_id="turn_1",
            session_id="ses_1",
            snapshot=_snapshot(),
        ),
    )


def _intent(**overrides) -> PreparedIntent:
    values = {
        "tool_name": "read_file",
        "call_id": "call1",
        "ordinal": 1,
        "arguments_digest": _digest("args"),
        "schema_digest": _digest("schema"),
        "permission_context_digest": _digest("perms"),
        "effect_class": EffectClass.BOUNDED_READ,
    }
    values.update(overrides)
    return PreparedIntent(**values)


def _execution(intent: PreparedIntent | None = None, **overrides) -> DurableToolExecution:
    prepared = intent or _intent()
    values = {
        "tool_execution_id": "tex_1",
        "workspace_id": "ws_1",
        "session_id": "ses_1",
        "task_run_id": "task_1",
        "turn_id": "turn_1",
        "agent_run_id": "arun_1",
        "call_id": prepared.call_id,
        "ordinal": prepared.ordinal,
        "tool_name": prepared.tool_name,
        "intent": prepared,
    }
    values.update(overrides)
    return DurableToolExecution(**values)


def test_discover_classifies_interrupted_read_as_safe_to_retry(tmp_path: Path):
    _store, handle, journal = _open(tmp_path)
    try:
        _seed(journal)
        prepared = journal.put_execution("ws_1", _execution())
        executing = transition_execution(
            prepared,
            ToolExecutionState.EXECUTING,
            expected_row_version=1,
            now=datetime(2026, 1, 1, tzinfo=UTC),
        )
        journal.save_execution("ws_1", executing, expected_row_version=1)
        service = RecoveryService(journal, workspace_id="ws_1", id_source=FixedIdSource())
        report = service.discover("ses_1", ConversationLog())
        assert report is not None
        assert report.items[0].classification is RecoveryClassification.SAFE_TO_RETRY
        assert RecoveryResolution.RETRY in report.items[0].allowed_resolutions
    finally:
        handle.close()


def test_discover_classifies_host_command_as_unknown(tmp_path: Path):
    _store, handle, journal = _open(tmp_path)
    try:
        _seed(journal)
        intent = _intent(
            tool_name="run_command",
            effect_class=EffectClass.UNCONFINED_EXTERNAL_EFFECT,
        )
        prepared = journal.put_execution("ws_1", _execution(intent, tool_name="run_command"))
        executing = transition_execution(
            prepared,
            ToolExecutionState.EXECUTING,
            expected_row_version=1,
            now=datetime(2026, 1, 1, tzinfo=UTC),
        )
        journal.save_execution("ws_1", executing, expected_row_version=1)
        service = RecoveryService(journal, workspace_id="ws_1", id_source=FixedIdSource())
        report = service.discover("ses_1", ConversationLog())
        assert report.items[0].classification is RecoveryClassification.OUTCOME_UNKNOWN
        assert RecoveryResolution.RETRY not in report.items[0].allowed_resolutions
    finally:
        handle.close()


def test_file_reconciliation_after_restart(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    target = project / "notes.txt"
    before = b"old\n"
    after = b"new\n"
    target.write_bytes(after)
    evidence = FileMutationEvidence(
        relative_path="notes.txt",
        operation="replace",
        existed_before=True,
        before_sha256=sha256_digest(before),
        expected_after_sha256=sha256_digest(after),
        expected_size=len(after),
        policy_version="files-v1",
        conflict_input_digest=_digest("conflict"),
    )
    _store, handle, journal = _open(tmp_path)
    try:
        _seed(journal)
        intent = _intent(
            tool_name="write_file",
            effect_class=EffectClass.RECONCILEABLE_FILE_WRITE,
            file_evidence=(evidence,),
        )
        prepared = journal.put_execution("ws_1", _execution(intent, tool_name="write_file"))
        executing = transition_execution(
            prepared,
            ToolExecutionState.EXECUTING,
            expected_row_version=1,
            now=datetime(2026, 1, 1, tzinfo=UTC),
        )
        journal.save_execution("ws_1", executing, expected_row_version=1)
        service = RecoveryService(
            journal,
            workspace_id="ws_1",
            id_source=FixedIdSource(),
            workspace_root=project,
        )
        report = service.discover("ses_1", ConversationLog())
        assert report.items[0].classification is RecoveryClassification.COMPLETED
        assert report.items[0].evidence.observation is FileObservation.MATCHES_EXPECTED
    finally:
        handle.close()


def test_recovery_receipt_replays_and_conflicts(tmp_path: Path):
    _store, handle, journal = _open(tmp_path)
    try:
        _seed(journal)
        prepared = journal.put_execution("ws_1", _execution())
        executing = transition_execution(
            prepared,
            ToolExecutionState.EXECUTING,
            expected_row_version=1,
            now=datetime(2026, 1, 1, tzinfo=UTC),
        )
        journal.save_execution("ws_1", executing, expected_row_version=1)
        log = ConversationLog()
        service = RecoveryService(journal, workspace_id="ws_1", id_source=FixedIdSource())
        report = service.discover("ses_1", log)
        item_id = report.items[0].item_id
        updated, receipt, planned = service.decide(
            report,
            command_id="cmd_1",
            resolution=RecoveryResolution.RETRY,
            item_id=item_id,
            log=log,
        )
        saved = service.commit_decision(
            updated, receipt, planned=planned, log=log, writer=None, close_all=False
        )
        again, replay, _planned = service.decide(
            saved,
            command_id="cmd_1",
            resolution=RecoveryResolution.RETRY,
            item_id=item_id,
            log=log,
        )
        assert replay.kind == "replay"
        _same, conflict, _ = service.decide(
            again,
            command_id="cmd_1",
            resolution=RecoveryResolution.ABORT,
            item_id=item_id,
            log=log,
        )
        assert conflict.kind == "conflict"
    finally:
        handle.close()


def test_restore_blocks_new_input_until_recovery(tmp_path: Path):
    store, handle, journal = _open(tmp_path)
    try:
        _seed(journal)
        prepared = journal.put_execution("ws_1", _execution())
        executing = transition_execution(
            prepared,
            ToolExecutionState.EXECUTING,
            expected_row_version=1,
            now=datetime(2026, 1, 1, tzinfo=UTC),
        )
        journal.save_execution("ws_1", executing, expected_row_version=1)
    finally:
        handle.close()

    store = OperationalStore(
        tmp_path / "state", retry_policy=_retry(), clock=FixedClock(), maintenance_timeout=0
    )
    handle = store.open(StoreOpenMode.READ_WRITE)
    try:
        journal = SqliteOperationalJournal(handle)
        session = Session(session_id="ses_1")
        persistence = SessionPersistence(
            workspace_id="ws_1",
            journal=journal,
            store_session=handle,
            id_source=FixedIdSource(),
            model=ModelRef(provider_id="p", model_id="m"),
            run_policy=type("P", (), {"model_dump": lambda self, mode=None: {}})(),
            runtime_instance_id="host-1",
        )
        persistence.restore_into(session)
        assert session.health is SessionHealth.NEEDS_RECOVERY
        result = persistence.submit_user(
            session, "hello again", "client-new", turn_id="turn_9", agent_run_id="arun_9"
        )
        assert result.kind == "recovery"
    finally:
        handle.close()


def test_recovery_resume_creates_an_ungranted_agent_run(tmp_path: Path):
    store, handle, journal = _open(tmp_path)
    try:
        _seed(journal)
        now = store.clock.now()
        grant = CapabilityGrant(
            grant_id="grt_1",
            workspace_id="ws_1",
            task_run_id="task_1",
            agent_run_id="arun_1",
            capabilities=(CapabilityName.UNCONFINED_HOST_PROCESS,),
            granted_by=GrantSource.LOCAL_INTERFACE_COMMAND,
            command_id="cmd_grant",
            reason="prior foreground elevation",
            preview_digest=_digest("preview"),
            created_at=now,
            expires_at=now + timedelta(hours=1),
        )
        journal.put_capability_grant("ws_1", grant)
        journal.freeze_agent_run_permission_snapshot(
            "ws_1",
            "arun_1",
            PermissionSnapshot(
                permission_snapshot_id="psnap_1",
                workspace_id="ws_1",
                session_id="ses_1",
                task_run_id="task_1",
                turn_id="turn_1",
                agent_run_id="arun_1",
                access_scope=AccessScope.FULL_ACCESS,
                approval_mode=ApprovalMode.MANUAL,
                process_isolation=ProcessIsolation.HOST,
                workspace_root_digest=_digest("root"),
                workspace_read_only=False,
                tool_schema_digest=_digest("tools"),
                run_policy_digest=_digest("policy"),
                permission_profile_digest=_digest("perms"),
                source_revisions=_snapshot().source_revisions,
                grant_id=grant.grant_id,
                grant_digest=capability_grant_digest(grant),
                granted_capabilities=grant.capabilities,
                capability_isolations=(
                    CapabilityIsolation(
                        capability=CapabilityName.UNCONFINED_HOST_PROCESS,
                        isolation=IsolationLabel.UNCONFINED_HOST,
                    ),
                ),
                created_at=now,
            ),
        )
        intent = _intent(
            tool_name="run_command",
            effect_class=EffectClass.UNCONFINED_EXTERNAL_EFFECT,
            requires_approval=True,
            preview=("unconfined_host: prior opaque Host command",),
        )
        prepared = journal.put_execution(
            "ws_1",
            _execution(
                intent,
                tool_name="run_command",
                permission_snapshot_id="psnap_1",
                grant_id="grt_1",
                isolation=IsolationLabel.UNCONFINED_HOST,
            ),
        )
        executing = transition_execution(
            prepared,
            ToolExecutionState.EXECUTING,
            expected_row_version=1,
            now=now,
        )
        journal.save_execution("ws_1", executing, expected_row_version=1)
    finally:
        handle.close()

    store = OperationalStore(
        tmp_path / "state", retry_policy=_retry(), clock=FixedClock(), maintenance_timeout=0
    )
    handle = store.open(StoreOpenMode.READ_WRITE)
    try:
        journal = SqliteOperationalJournal(handle)
        session = Session(session_id="ses_1")
        ids = FixedIdSource()
        ids.counts["arun"] = 1
        ids.counts["psnap"] = 1
        persistence = SessionPersistence(
            workspace_id="ws_1",
            journal=journal,
            store_session=handle,
            id_source=ids,
            model=ModelRef(provider_id="p", model_id="m"),
            run_policy=type("P", (), {"model_dump": lambda self, mode=None: {}})(),
            runtime_instance_id="host-2",
            clock=store.clock,
        )
        persistence.restore_into(session)
        assert persistence.open_report is not None
        item = persistence.open_report.items[0]
        persistence.apply_recovery(
            session,
            command_id="cmd_ack",
            resolution=RecoveryResolution.ACKNOWLEDGE,
            item_id=item.item_id,
        )
        resumed = persistence.apply_recovery(
            session,
            command_id="cmd_resume",
            resolution=RecoveryResolution.RESUME,
        )
        assert resumed.status.value == "resolved"
        assert persistence.current_agent_run_id == "arun_2"
        new_run = journal.get_agent_run("ws_1", "arun_2")
        assert new_run is not None
        assert new_run.resume_of_agent_run_id == "arun_1"
        assert new_run.permission_snapshot_id is None
        assert journal.get_permission_snapshot_for_run("ws_1", "arun_2") is None
        assert journal.list_capability_grants("ws_1", agent_run_id="arun_2") == ()
        assert journal.get_agent_run("ws_1", "arun_1").permission_snapshot_id == "psnap_1"
        resumed_snapshot = persistence.freeze_permission_snapshot(session, tools=())
        assert resumed_snapshot.agent_run_id == "arun_2"
        assert resumed_snapshot.grant_id is None
        assert journal.get_permission_snapshot_for_run("ws_1", "arun_2") == resumed_snapshot
    finally:
        handle.close()


def _crash_after_executing(root: str) -> None:
    store = OperationalStore(
        Path(root), retry_policy=_retry(), clock=FixedClock(), maintenance_timeout=0
    )
    handle = store.initialize()
    try:
        journal = SqliteOperationalJournal(handle)
        _seed(journal)
        prepared = journal.put_execution("ws_1", _execution())
        executing = transition_execution(
            prepared,
            ToolExecutionState.EXECUTING,
            expected_row_version=1,
            now=datetime(2026, 1, 1, tzinfo=UTC),
        )
        journal.save_execution("ws_1", executing, expected_row_version=1)
    finally:
        handle.close()
    os._exit(17)


def test_subprocess_crash_after_executing_is_classified(tmp_path: Path):
    root = tmp_path / "state"
    context = multiprocessing.get_context("spawn")
    process = context.Process(target=_crash_after_executing, args=(str(root),))
    process.start()
    process.join(timeout=10)
    assert process.exitcode == 17
    store = OperationalStore(root, retry_policy=_retry(), clock=FixedClock(), maintenance_timeout=0)
    with store.open(StoreOpenMode.READ_WRITE) as handle:
        journal = SqliteOperationalJournal(handle)
        service = RecoveryService(journal, workspace_id="ws_1", id_source=FixedIdSource())
        report = service.discover("ses_1", ConversationLog())
        assert report is not None
        assert report.items[0].classification is RecoveryClassification.SAFE_TO_RETRY


def _crash_states() -> tuple[tuple[str, str], ...]:
    return (
        ("prepared", RecoveryClassification.NEVER_STARTED.value),
        ("executing_host", RecoveryClassification.OUTCOME_UNKNOWN.value),
        ("handler_completed", RecoveryClassification.COMPLETED.value),
    )


def _crash_at_state(root: str, state: str) -> None:
    store = OperationalStore(
        Path(root), retry_policy=_retry(), clock=FixedClock(), maintenance_timeout=0
    )
    handle = store.initialize()
    try:
        journal = SqliteOperationalJournal(handle)
        _seed(journal)
        if state == "executing_host":
            intent = _intent(
                tool_name="run_command",
                effect_class=EffectClass.UNCONFINED_EXTERNAL_EFFECT,
            )
            execution = journal.put_execution("ws_1", _execution(intent, tool_name="run_command"))
            execution = transition_execution(
                execution,
                ToolExecutionState.EXECUTING,
                expected_row_version=1,
                now=datetime(2026, 1, 1, tzinfo=UTC),
            )
            journal.save_execution("ws_1", execution, expected_row_version=1)
        elif state == "handler_completed":
            execution = journal.put_execution("ws_1", _execution())
            execution = transition_execution(
                execution,
                ToolExecutionState.EXECUTING,
                expected_row_version=1,
                now=datetime(2026, 1, 1, tzinfo=UTC),
            )
            execution = journal.save_execution("ws_1", execution, expected_row_version=1)
            from morrow.core.execution import HandlerResultEnvelope, ToolExecutionDisposition

            completed = transition_execution(
                execution,
                ToolExecutionState.HANDLER_COMPLETED,
                expected_row_version=2,
                disposition=ToolExecutionDisposition.SUCCEEDED,
                result_envelope=HandlerResultEnvelope(ok=True),
                now=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
            )
            journal.save_execution("ws_1", completed, expected_row_version=2)
        else:
            journal.put_execution("ws_1", _execution())
    finally:
        handle.close()
    os._exit(17)


@pytest.mark.parametrize("state,expected", _crash_states())
def test_crash_matrix_classifies_committed_boundaries(tmp_path: Path, state: str, expected: str):
    root = tmp_path / "state"
    context = multiprocessing.get_context("spawn")
    process = context.Process(target=_crash_at_state, args=(str(root), state))
    process.start()
    process.join(timeout=10)
    assert process.exitcode == 17
    store = OperationalStore(root, retry_policy=_retry(), clock=FixedClock(), maintenance_timeout=0)
    with store.open(StoreOpenMode.READ_WRITE) as handle:
        journal = SqliteOperationalJournal(handle)
        service = RecoveryService(journal, workspace_id="ws_1", id_source=FixedIdSource())
        report = service.discover("ses_1", ConversationLog())
        assert report.items[0].classification.value == expected
