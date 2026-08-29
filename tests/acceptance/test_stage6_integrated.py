"""Offline Stage 6 acceptance across the governed Skill state boundaries."""

from __future__ import annotations

import json
from pathlib import Path

from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStore
from morrow.application.backup import OperationalBackupService
from morrow.application.doctor import OperationalDoctor
from morrow.bootstrap import build_application, build_skill_services
from morrow.core.domain import (
    AgentRunSnapshot,
    DurableAgentRun,
    DurableSession,
    DurableTaskRun,
    DurableTurn,
)
from morrow.core.models import ModelRef
from morrow.core.skills.drafts import SkillDraftStatus
from morrow.testing import FixedClock, FixedIdSource
from test_skill_drafts import _accepted_candidate

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "stage6"
HANDWRITTEN = FIXTURE_ROOT / "handwritten-skill"


def _isolated_runtime(tmp_path: Path):
    app = build_application(
        state_root=tmp_path / "state",
        credentials=MemoryCredentialStore(),
        id_source=FixedIdSource(),
    )
    store = OperationalStore(
        app.data_root.root,
        clock=FixedClock(),
        maintenance_timeout=0,
    )
    handle = store.initialize()
    journal = SqliteOperationalJournal(handle)
    task = DurableTaskRun(
        task_run_id="task_acceptance",
        session_id="ses_acceptance",
        workspace_id="ws_acceptance",
    )
    journal.create_session(
        DurableSession(session_id="ses_acceptance", workspace_id="ws_acceptance"),
        task=task,
    )
    journal.create_turn(
        "ws_acceptance",
        DurableTurn(
            turn_id="turn_acceptance",
            session_id="ses_acceptance",
            task_run_id=task.task_run_id,
            client_message_id="acceptance-client",
        ),
    )
    journal.create_agent_run(
        "ws_acceptance",
        DurableAgentRun(
            agent_run_id="arun_acceptance",
            turn_id="turn_acceptance",
            session_id="ses_acceptance",
            snapshot=AgentRunSnapshot(
                model=ModelRef(provider_id="offline", model_id="fixture"),
                provider_id="offline",
                run_policy_digest="a" * 64,
                tool_schema_digest="b" * 64,
                permission_profile_digest="c" * 64,
                runtime_instance_id="stage6-acceptance",
            ),
        ),
    )
    return app, store, handle, journal


def test_handwritten_skill_scope_selection_usage_doctor_and_backup_restore(tmp_path: Path) -> None:
    app, store, handle, journal = _isolated_runtime(tmp_path)
    try:
        services = build_skill_services(
            app,
            journal=journal,
            workspace_id="ws_acceptance",
        )
        other = build_skill_services(
            app,
            journal=journal,
            workspace_id="ws_other",
        )

        validation = services.lifecycle.validate(HANDWRITTEN, scope_id="ws_acceptance")
        assert validation.valid is True
        first = services.lifecycle.install(
            HANDWRITTEN,
            scope_id="ws_acceptance",
            confirmed=True,
        )
        services.lifecycle.enable("acceptance-skill", scope_id="ws_acceptance")
        second = services.lifecycle.install(
            HANDWRITTEN,
            scope_id="ws_acceptance",
            confirmed=True,
        )
        services.lifecycle.pin(
            "acceptance-skill",
            first.version_id,
            scope_id="ws_acceptance",
        )
        rollback = services.lifecycle.rollback("acceptance-skill", scope_id="ws_acceptance")
        assert rollback.pinned_version_id == second.version_id

        plan = services.selection.select(
            agent_run_id="arun_acceptance",
            workspace_id="ws_acceptance",
            user_input="skill: acceptance-skill",
        )
        assert len(plan.selections) == 1
        selection = plan.selections[0]
        resource = services.resources.read(selection, "references/guide.md")
        assert resource.content and resource.disposition == "inline"

        journal.transact(lambda txn: txn.put_skill_selection("ws_acceptance", selection))
        usage = services.usage.record(
            agent_run_id="arun_acceptance",
            skill_id=selection.skill_id,
            version_id=selection.version_id,
            selection_id=selection.selection_id,
            activation_reason="ignored; selection evidence is authoritative",
            status="succeeded",
            usage_id="sug_acceptance",
        )
        assert usage.selection_id == selection.selection_id
        assert other.catalog.scan_scope("ws_other").entry("acceptance-skill") is None

        doctor = OperationalDoctor(store).inspect("ws_acceptance")
        codes = {issue.code for issue in doctor.issues}
        assert "skill_package_drift" not in codes
        assert "skill_selection_evidence" not in codes
        assert "skill_usage_version" not in codes

        backup = OperationalBackupService(store, journal=journal)
        report = backup.create("stage6-integrated")
        bundle = store.layout.backups_dir / report.bundle_name
        assert backup.verify(bundle).ok
        assert {item.version_id for item in report.skill_versions} == {
            first.version_id,
            second.version_id,
        }
        restored = backup.restore(bundle, tmp_path / "restored")
        assert restored.ok
        assert OperationalStore(tmp_path / "restored").classify().ok

        services.lifecycle.disable("acceptance-skill", scope_id="ws_acceptance")
        assert not services.selection.select(
            agent_run_id="arun_after_disable",
            workspace_id="ws_acceptance",
            user_input="skill: acceptance-skill",
        ).selections
    finally:
        handle.close()


def test_generated_draft_requires_review_then_explicit_enable(tmp_path: Path) -> None:
    app = build_application(
        state_root=tmp_path / "state",
        credentials=MemoryCredentialStore(),
        id_source=FixedIdSource(),
    )
    store = OperationalStore(app.data_root.root, clock=FixedClock(), maintenance_timeout=0)
    handle = store.initialize()
    journal = SqliteOperationalJournal(handle)
    services = build_skill_services(app, journal=journal, workspace_id="ws_1")
    try:
        candidate = _accepted_candidate(journal)
        draft = services.drafts.create_from_candidate(candidate.candidate_id)
        assert draft.status is SkillDraftStatus.VALIDATED
        edited = services.drafts.edit(
            draft.draft_id,
            skill_md=(
                "---\nname: Report Writer\ndescription: draft a concise report\n"
                "version: 0.2.0\n---\nEdited offline instructions.\n"
            ),
        )
        assert edited.status is SkillDraftStatus.VALIDATED
        accepted = services.drafts.accept(edited.draft_id)
        assert accepted.version_id
        assert services.bindings.load("workspace", scope_id="ws_1").value.bindings == ()
        services.lifecycle.enable("report-writer", scope_id="ws_1")
        binding = services.bindings.load("workspace", scope_id="ws_1").value.bindings
        assert binding[0].enabled is True
        assert (
            json.loads((FIXTURE_ROOT / "generated-draft" / "candidate.json").read_text())["title"]
            == "Report Writer"
        )
    finally:
        handle.close()
