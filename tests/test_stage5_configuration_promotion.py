from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStore
from morrow.application.api import OperationalApplicationService
from morrow.application.configuration import ConfigurationCommand
from morrow.bootstrap import build_application
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.domain import (
    DurableSession,
    DurableTaskOutcome,
    DurableTaskRun,
    DurableTaskRunTransition,
    TaskOutcomeTrigger,
    TaskRunStatus,
    canonical_json_bytes,
    sha256_digest,
)
from morrow.core.learning import (
    LearningCandidate,
    LearningCandidateOperation,
    LearningCandidateStatus,
    LearningCandidateType,
    LearningConfidenceBand,
    LearningEvidence,
    LearningEvidenceActor,
    LearningEvidenceAuthority,
    LearningEvidenceExplicitness,
    LearningEvidencePolarity,
    LearningEvidenceSourceKind,
    LearningReview,
    LearningReviewStatus,
    LearningReviewTrigger,
    LearningScope,
    LearningSensitivity,
    PreferenceCandidatePayload,
    ProfileCandidatePayload,
)
from morrow.core.learning_commands import AcceptLearningCandidateCommand
from morrow.core.learning_payloads import LearningCandidateDraft
from morrow.core.models import Preferences, Profile, StatePresence
from morrow.runtime.session import Session
from morrow.services.preferences import ConfigPatchService, ConfigurationConflictError
from morrow.testing import FixedClock, FixedIdSource

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _service(tmp_path, *, session=None):
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    return (
        app,
        identity,
        ConfigPatchService(
            app.project_store,
            app.global_store,
            identity.workspace_id,
            session,
        ),
    )


def test_prepared_configuration_proves_before_state_and_replays_after_state(tmp_path):
    app, identity, service = _service(tmp_path)
    command = ConfigurationCommand(
        scope="workspace",
        target="preferences",
        operation="set",
        path="language",
        value="中文",
    )

    prepared = service.prepare(command)
    assert prepared.expected_revision == 0
    assert prepared.before_presence is StatePresence.MISSING
    assert prepared.expected_applied_revision == 1
    assert prepared.changed is True
    first = service.apply_prepared(prepared, operation_id="pop_1")
    replay = service.apply_prepared(prepared, operation_id="pop_1")

    assert first.revision == replay.revision == 1
    assert (
        app.project_store.load_preferences(identity.workspace_id).value.preferences.language
        == "中文"
    )


def test_prepared_configuration_rejects_later_same_content_revision_as_drift(tmp_path):
    app, identity, service = _service(tmp_path)
    command = ConfigurationCommand(
        scope="workspace",
        target="preferences",
        operation="set",
        path="language",
        value="中文",
    )
    prepared = service.prepare(command)
    service.apply_command(command)
    service.apply_command(
        ConfigurationCommand(
            scope="workspace",
            target="preferences",
            operation="set",
            path="language",
            value="English",
        )
    )
    service.apply_command(command)

    with pytest.raises(ConfigurationConflictError):
        service.apply_prepared(prepared, operation_id="pop_stale")


def test_prepared_configuration_preserves_session_and_global_revisions(tmp_path):
    session = Session(
        session_id="ses_1",
        global_preferences=Preferences(language="English"),
        profile=Profile(name="demo"),
    )
    app, identity, service = _service(tmp_path, session=session)
    app.project_store.write_profile(identity.workspace_id, Profile(name="demo"))
    global_command = ConfigurationCommand(
        scope="global",
        target="preferences",
        operation="set",
        path="language",
        value="中文",
    )
    workspace_command = ConfigurationCommand(
        scope="workspace",
        target="profile",
        operation="set",
        path="summary",
        value="summary",
    )

    service.apply_command(global_command)
    service.apply_command(workspace_command)

    assert session.global_preferences_revision == 1
    assert session.global_preferences.language == "中文"
    assert session.profile_revision == 2
    assert session.profile_presence is StatePresence.PRESENT
    assert session.profile.summary == "summary"


def _promotion_subjects(
    tmp_path,
    *,
    profile_candidate: bool = False,
    candidate_scope: LearningScope = LearningScope.WORKSPACE,
):
    app = build_application(
        state_root=tmp_path / "state",
        credentials=MemoryCredentialStore(),
        id_source=FixedIdSource(),
    )
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    workspace_id = identity.workspace_id
    store = OperationalStore(
        app.data_root.root,
        clock=FixedClock(NOW),
        maintenance_timeout=0,
    )
    handle = store.initialize()
    journal = SqliteOperationalJournal(handle)
    journal.create_session(
        DurableSession(session_id="ses_1", workspace_id=workspace_id),
        task=DurableTaskRun(
            task_run_id="task_1",
            session_id="ses_1",
            workspace_id=workspace_id,
        ),
    )
    journal.transition_task_run(
        workspace_id,
        "task_1",
        target=TaskRunStatus.READY_FOR_ACCEPTANCE,
        transition=DurableTaskRunTransition(
            transition_id="ttr_1",
            workspace_id=workspace_id,
            session_id="ses_1",
            task_run_id="task_1",
            from_status=TaskRunStatus.OPEN,
            to_status=TaskRunStatus.READY_FOR_ACCEPTANCE,
            reason="answer ready",
            created_at=NOW,
        ),
        expected_row_version=1,
    )
    journal.transition_task_run(
        workspace_id,
        "task_1",
        target=TaskRunStatus.ACCEPTED,
        transition=DurableTaskRunTransition(
            transition_id="ttr_2",
            workspace_id=workspace_id,
            session_id="ses_1",
            task_run_id="task_1",
            from_status=TaskRunStatus.READY_FOR_ACCEPTANCE,
            to_status=TaskRunStatus.ACCEPTED,
            reason="user accepted",
            created_at=NOW,
        ),
        expected_row_version=2,
    )
    journal.put_task_outcome(
        workspace_id,
        DurableTaskOutcome(
            outcome_id="out_1",
            workspace_id=workspace_id,
            session_id="ses_1",
            task_run_id="task_1",
            version=1,
            trigger=TaskOutcomeTrigger.ACCEPTANCE,
            task_status=TaskRunStatus.ACCEPTED,
            summary="任务已接受。",
            created_at=NOW,
        ),
    )
    snapshot = canonical_json_bytes({"mode": "review_only"}).decode("utf-8")
    journal.put_learning_review(
        workspace_id,
        LearningReview(
            review_id="lrv_1",
            workspace_id=workspace_id,
            task_run_id="task_1",
            task_outcome_id="out_1",
            trigger=LearningReviewTrigger.TASK_ACCEPTED,
            policy_snapshot_json=snapshot,
            policy_digest=sha256_digest(snapshot),
            status=LearningReviewStatus.COMPLETED,
            created_at=NOW,
        ),
    )
    evidence = LearningEvidence(
        evidence_id="lev_1",
        workspace_id=workspace_id,
        origin_review_id="lrv_1",
        task_run_id="task_1",
        source_kind=LearningEvidenceSourceKind.USER_TURN,
        source_id="turn_1",
        source_pointer="user.content",
        actor=LearningEvidenceActor.USER,
        authority=LearningEvidenceAuthority.USER_EXPLICIT_PERSISTENT,
        explicitness=LearningEvidenceExplicitness.EXPLICIT,
        polarity=LearningEvidencePolarity.POSITIVE,
        scope_hint=LearningScope.WORKSPACE,
        excerpt_redacted="以后默认使用中文。",
        content_digest="a" * 64,
        observed_at=NOW,
        created_at=NOW,
    )
    journal.put_learning_evidence(workspace_id, evidence)
    if profile_candidate:
        app.project_store.write_profile(workspace_id, Profile(name="demo"))
        candidate_type = LearningCandidateType.PROFILE
        semantic_key = "profile.summary"
        proposed_payload = ProfileCandidatePayload(path="summary", value="用于测试的项目")
    else:
        candidate_type = LearningCandidateType.PREFERENCE
        semantic_key = "preference.language"
        proposed_payload = PreferenceCandidatePayload(path="language", value="中文")
    draft = LearningCandidateDraft(
        candidate_type=candidate_type,
        operation=LearningCandidateOperation.SET,
        semantic_key=semantic_key,
        proposed_scope=candidate_scope,
        proposed_payload=proposed_payload,
        evidence_ids=("lev_1",),
        temporary_or_durable="durable",
    )
    candidate = LearningCandidate.from_draft(
        candidate_id="lcn_1",
        workspace_id=workspace_id,
        origin_review_id="lrv_1",
        draft=draft,
        confidence_band=LearningConfidenceBand.HIGH,
        confidence_basis=("explicit_user_intent",),
        sensitivity=LearningSensitivity.NORMAL,
        expires_at=NOW + timedelta(days=30),
        now=NOW,
    )
    journal.put_learning_candidate(workspace_id, candidate)
    service = ConfigPatchService(app.project_store, app.global_store, workspace_id)
    api = OperationalApplicationService(
        journal=journal,
        workspace_id=workspace_id,
        id_source=FixedIdSource(),
        clock=lambda: NOW,
        config_service=service,
    )
    return app, identity, handle, journal, api, candidate


def test_preference_candidate_saga_applies_yaml_and_replays_without_duplicate_activation(tmp_path):
    app, identity, handle, journal, api, candidate = _promotion_subjects(tmp_path)
    try:
        from morrow.core.learning_commands import AcceptLearningCandidateCommand

        command = AcceptLearningCandidateCommand(
            workspace_id=identity.workspace_id,
            candidate_id=candidate.candidate_id,
            expected_row_version=1,
            command_id="cmd_config_1",
        )
        result = api.accept_learning_candidate(command)
        replay = api.accept_learning_candidate(command)

        assert result.value.outcome == "activated"
        assert result.value.target == "preferences"
        assert result.value.activation_id is not None
        assert result.value.revision == 1
        assert replay.receipt is not None and replay.receipt.disposition.value == "replay"
        assert len(journal.list_configuration_activations(identity.workspace_id)) == 1
        assert (
            app.project_store.load_preferences(identity.workspace_id).value.preferences.language
            == "中文"
        )
        assert [
            event.event_type for event in journal.list_application_events(identity.workspace_id)
        ][-3:] == [
            "learning.candidate_accepted",
            "configuration.activated",
            "memory.record_activated",
        ]
        assert (
            journal.get_learning_candidate(identity.workspace_id, candidate.candidate_id).status
            is LearningCandidateStatus.ACCEPTED
        )
    finally:
        handle.close()


def test_configuration_activation_undo_creates_reverse_provenance_only_when_current(tmp_path):
    app, identity, handle, journal, api, candidate = _promotion_subjects(tmp_path)
    try:
        from morrow.core.configuration_promotion import ConfigurationActivationStatus
        from morrow.core.learning_commands import AcceptLearningCandidateCommand

        accepted = api.accept_learning_candidate(
            AcceptLearningCandidateCommand(
                workspace_id=identity.workspace_id,
                candidate_id=candidate.candidate_id,
                expected_row_version=1,
                command_id="cmd_config_undo_accept",
            )
        )
        activation_id = accepted.value.activation_id
        assert activation_id is not None
        preview = api.preview_learning_undo(activation_id)
        assert preview.command.operation == "unset"
        undone = api.undo_learning_activation(activation_id, command_id="cmd_config_undo")
        replay = api.undo_learning_activation(activation_id, command_id="cmd_config_undo")

        original = journal.get_configuration_activation(identity.workspace_id, activation_id)
        reverse = journal.get_configuration_activation(
            identity.workspace_id, undone.value.activation_id
        )
        assert undone.value.outcome == "reversed"
        assert replay.value.outcome == "reversed"
        assert replay.receipt is not None
        assert original.status is ConfigurationActivationStatus.REVERSED
        assert reverse.reverses_activation_id == activation_id
        assert (
            app.project_store.load_preferences(identity.workspace_id).value.preferences.language
            is None
        )
    finally:
        handle.close()


def test_profile_candidate_saga_updates_existing_workspace_profile(tmp_path):
    app, identity, handle, journal, api, candidate = _promotion_subjects(
        tmp_path, profile_candidate=True
    )
    try:
        from morrow.core.learning_commands import AcceptLearningCandidateCommand

        result = api.accept_learning_candidate(
            AcceptLearningCandidateCommand(
                workspace_id=identity.workspace_id,
                candidate_id=candidate.candidate_id,
                expected_row_version=1,
                command_id="cmd_profile_config",
            )
        )

        assert result.value.target == "profile"
        assert result.value.path == "summary"
        assert (
            app.project_store.load_profile(identity.workspace_id).value.profile.summary
            == "用于测试的项目"
        )
        assert len(journal.list_configuration_activations(identity.workspace_id)) == 1
    finally:
        handle.close()


def test_global_preference_requires_explicit_scope_and_can_then_be_promoted(tmp_path):
    _app, identity, handle, journal, api, candidate = _promotion_subjects(
        tmp_path, candidate_scope=LearningScope.GLOBAL
    )
    try:
        implicit = api.preview_learning_candidate_decision(candidate.candidate_id)
        explicit = api.preview_learning_candidate_decision(
            candidate.candidate_id, scope=LearningScope.GLOBAL
        )

        assert implicit.available is False
        assert implicit.reason == "configuration_invalid"
        assert explicit.available is True
        assert explicit.scope is LearningScope.GLOBAL
        result = api.accept_learning_candidate(
            AcceptLearningCandidateCommand(
                workspace_id=identity.workspace_id,
                candidate_id=candidate.candidate_id,
                expected_row_version=1,
                command_id="cmd_global_config",
                scope=LearningScope.GLOBAL,
            )
        )
        assert result.value.scope is LearningScope.GLOBAL
        assert journal.list_configuration_activations(identity.workspace_id)[0].scope is (
            LearningScope.GLOBAL
        )
    finally:
        handle.close()


def test_configuration_undo_refuses_a_later_direct_yaml_edit(tmp_path):
    app, identity, handle, journal, api, candidate = _promotion_subjects(tmp_path)
    try:
        from morrow.core.learning_commands import AcceptLearningCandidateCommand

        accepted = api.accept_learning_candidate(
            AcceptLearningCandidateCommand(
                workspace_id=identity.workspace_id,
                candidate_id=candidate.candidate_id,
                expected_row_version=1,
                command_id="cmd_config_undo_conflict_accept",
            )
        )
        ConfigPatchService(
            app.project_store, app.global_store, identity.workspace_id
        ).apply_command(
            ConfigurationCommand(
                scope="workspace",
                target="preferences",
                operation="set",
                path="language",
                value="English",
            )
        )
        with pytest.raises(ApplicationError):
            api.preview_learning_undo(accepted.value.activation_id)
        assert (
            journal.list_configuration_activations(identity.workspace_id)[0].status.value
            == "active"
        )
    finally:
        handle.close()


def test_configuration_saga_replays_after_sqlite_finalize_crash(tmp_path, monkeypatch):
    app, identity, handle, journal, api, candidate = _promotion_subjects(tmp_path)
    try:
        from morrow.core.application import ApplicationError
        from morrow.core.learning_commands import AcceptLearningCandidateCommand

        original_event = journal.put_application_event_in_txn
        fail_once = True

        def fail_after_yaml(*args, **kwargs):
            nonlocal fail_once
            if fail_once:
                fail_once = False
                raise RuntimeError("injected finalize failure")
            return original_event(*args, **kwargs)

        monkeypatch.setattr(journal, "put_application_event_in_txn", fail_after_yaml)
        command = AcceptLearningCandidateCommand(
            workspace_id=identity.workspace_id,
            candidate_id=candidate.candidate_id,
            expected_row_version=1,
            command_id="cmd_config_crash",
        )
        with pytest.raises(ApplicationError):
            api.accept_learning_candidate(command)
        operation = journal.list_promotion_operations(identity.workspace_id)[0]
        assert operation.state.value == "prepared"
        assert [item.operation_id for item in api.list_learning_promotions()] == [
            operation.operation_id
        ]
        assert (
            app.project_store.load_preferences(identity.workspace_id).value.preferences.language
            == "中文"
        )

        with pytest.raises(ApplicationError):
            api.recover_learning_promotion(operation.operation_id, action="abort")
        assert (
            journal.get_promotion_operation(
                identity.workspace_id, operation.operation_id
            ).state.value
            == "prepared"
        )

        replay = api.accept_learning_candidate(command)
        assert replay.value.outcome == "activated"
        assert replay.value.activation_id is not None
        assert (
            journal.list_promotion_operations(identity.workspace_id)[0].state.value == "finalized"
        )
    finally:
        handle.close()


def test_configuration_accept_honors_command_row_version_token(tmp_path):
    app, identity, handle, journal, api, candidate = _promotion_subjects(tmp_path)
    try:
        with pytest.raises(ApplicationError) as error:
            api.accept_learning_candidate(
                AcceptLearningCandidateCommand(
                    workspace_id=identity.workspace_id,
                    candidate_id=candidate.candidate_id,
                    expected_row_version=2,
                    command_id="cmd_config_stale_token",
                )
            )
        assert error.value.code is ApplicationErrorCode.STALE
        assert journal.list_promotion_operations(identity.workspace_id) == ()
        assert journal.get_learning_candidate(
            identity.workspace_id, candidate.candidate_id
        ).status is (LearningCandidateStatus.PROPOSED)
        assert app.project_store.load_preferences(identity.workspace_id).value is None
    finally:
        handle.close()


def test_configuration_recovery_finalizes_after_state_explicitly(tmp_path, monkeypatch):
    app, identity, handle, journal, api, candidate = _promotion_subjects(tmp_path)
    try:
        original_event = journal.put_application_event_in_txn
        fail_once = True

        def fail_finalize_once(*args, **kwargs):
            nonlocal fail_once
            if fail_once:
                fail_once = False
                raise RuntimeError("injected finalize failure")
            return original_event(*args, **kwargs)

        monkeypatch.setattr(journal, "put_application_event_in_txn", fail_finalize_once)
        command = AcceptLearningCandidateCommand(
            workspace_id=identity.workspace_id,
            candidate_id=candidate.candidate_id,
            expected_row_version=1,
            command_id="cmd_config_explicit_finalize",
        )
        with pytest.raises(ApplicationError):
            api.accept_learning_candidate(command)
        operation = journal.list_promotion_operations(identity.workspace_id)[0]
        assert operation.state.value == "prepared"

        session = Session(session_id="ses_recovery")
        api.learning.promotion.configuration.config_service.session = session
        assert session.workspace_preferences.language is None
        result = api.recover_learning_promotion(operation.operation_id, action="finalize")
        assert result.value.outcome == "activated"
        assert session.workspace_preferences.language == "中文"
        assert journal.get_promotion_operation(
            identity.workspace_id, operation.operation_id
        ).state.value == ("finalized")
    finally:
        handle.close()


def test_configuration_recovery_marks_unknown_yaml_and_supports_explicit_abort(
    tmp_path, monkeypatch
):
    app, identity, handle, journal, api, candidate = _promotion_subjects(tmp_path)
    try:
        service = api.learning.promotion.configuration

        def leave_prepared(*args, **kwargs):
            del args, kwargs
            raise ApplicationError(ApplicationErrorCode.UNAVAILABLE, "injected pause")

        monkeypatch.setattr(service, "_resume", leave_prepared)
        from morrow.core.learning_commands import AcceptLearningCandidateCommand

        command = AcceptLearningCandidateCommand(
            workspace_id=identity.workspace_id,
            candidate_id=candidate.candidate_id,
            expected_row_version=1,
            command_id="cmd_config_recovery",
        )
        with pytest.raises(ApplicationError):
            api.accept_learning_candidate(command)
        operation = journal.list_promotion_operations(identity.workspace_id)[0]
        assert operation.state.value == "prepared"

        ConfigPatchService(
            app.project_store, app.global_store, identity.workspace_id
        ).apply_command(
            ConfigurationCommand(
                scope="workspace",
                target="preferences",
                operation="set",
                path="language",
                value="English",
            )
        )
        with pytest.raises(ApplicationError):
            api.recover_learning_promotion(operation.operation_id, action="retry")
        assert (
            journal.get_promotion_operation(
                identity.workspace_id, operation.operation_id
            ).state.value
            == "needs_resolution"
        )

        aborted = api.recover_learning_promotion(operation.operation_id, action="abort")
        assert aborted.state.value == "aborted"
        assert (
            journal.get_learning_candidate(identity.workspace_id, candidate.candidate_id).status
            is LearningCandidateStatus.PROPOSED
        )
        assert (
            app.project_store.load_preferences(identity.workspace_id).value.preferences.language
            == "English"
        )
    finally:
        handle.close()
