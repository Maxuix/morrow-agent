"""Offline management API/CLI parity and immutable resolved-context acceptance."""

import json

import pytest

from morrow.application.management_requests import PreferenceWriteRequest
from test_stage8_core_api import ServerFixture


@pytest.mark.asyncio
async def test_management_queries_and_preference_write_replay_stale(tmp_path):
    fixture = ServerFixture(tmp_path)
    try:
        client = fixture.client
        for kind in (
            "context",
            "preferences",
            "profile",
            "learning",
            "knowledge",
            "skills",
            "skill-drafts",
        ):
            response = await client.get("/v1/management/" + kind)
            status, body = response.status, response.json()
            assert status == 200, (kind, body)
            assert "credential" not in json.dumps(body).lower()
        request = {
            "command_id": "cmd_preference_gui",
            "arguments": {
                "scope": "workspace",
                "expected_revision": 0,
                "operations": [{"operation": "add", "statement": "Use concise Chinese."}],
            },
        }
        response = await client.post("/v1/management/preferences", request)
        status, body = response.status, response.json()
        assert status == 200, body
        assert body["result"]["revision"] == 1
        response = await client.post("/v1/management/preferences", request)
        status, replay = response.status, response.json()
        assert status == 200, replay
        assert replay["result"]["status"] == "replayed"
        response = await client.post(
            "/v1/management/preferences", {**request, "command_id": "cmd_stale"}
        )
        status, body = response.status, response.json()
        assert status == 409, body
        response = await client.get("/v1/management/context")
        status, current = response.status, response.json()
        assert status == 200
        assert current["status"] == "not_started"
        assert current["preferences"] == []
        direct = await fixture.on_core(
            lambda: fixture.host.context.context_management.execute(
                "preferences", PreferenceWriteRequest.model_validate(request)
            )
        )
        assert direct == replay["result"]
    finally:
        fixture.close()


def management_fixture(tmp_path):
    """Provider-free shared CLI/API services, deterministic IDs and time."""
    from morrow.adapters.state.journal import SqliteOperationalJournal
    from morrow.adapters.state.operational import OperationalStore
    from morrow.adapters.state.preference_yaml import PreferenceYamlStore
    from morrow.application.api import OperationalApplicationService
    from morrow.application.management import ManagementService
    from morrow.application.preferences.inbox import PreferenceInbox
    from morrow.application.preferences.queries import PreferenceQueries
    from morrow.application.preferences.tool import PreferenceManagementService
    from morrow.application.preferences.writer import PreferenceWriter
    from morrow.bootstrap import build_application, build_skill_services
    from morrow.services.profile_configuration import ConfigPatchService
    from morrow.testing import FixedClock, FixedIdSource
    from test_skill_drafts import NOW

    app = build_application(state_root=tmp_path / "state")
    app.id_source = FixedIdSource()
    handle = OperationalStore(app.data_root.root, clock=FixedClock(NOW)).initialize()
    journal = SqliteOperationalJournal(handle)
    writer = PreferenceWriter(
        PreferenceYamlStore(app.data_root.root),
        journal,
        "ws_1",
        id_source=app.id_source,
        clock=journal.now,
    )
    queries = PreferenceQueries(writer.yaml_store, "ws_1")
    profile = ConfigPatchService(app.project_store, app.global_store, "ws_1")
    api = OperationalApplicationService(
        journal=journal,
        workspace_id="ws_1",
        id_source=app.id_source,
        preference_inbox=PreferenceInbox(journal=journal, workspace_id="ws_1", writer=writer),
        preference_queries=queries,
        config_service=profile,
    )
    skills = build_skill_services(app, workspace_id="ws_1", journal=journal)
    service = ManagementService(api, PreferenceManagementService(writer, queries), profile, skills)
    return app, handle, service


def api_for(service):
    """In-process transport adapter; services stay on their owning test thread."""
    from types import SimpleNamespace

    from fixtures.core_api_client import CoreApiVerificationClient
    from morrow.server.app import create_asgi_app

    class Host:
        context = SimpleNamespace(
            workspace_id="ws_1",
            journal=service.api.journal,
            api=service.api,
            context_management=service,
            runtime=SimpleNamespace(),
            hub=SimpleNamespace(publish=lambda _: None),
        )

        async def execute_command(self, callback):
            return callback()

        async def execute_query(self, callback):
            return callback()

    return CoreApiVerificationClient(create_asgi_app(Host(), auth_token="test"), token="test")


def audit_bytes(service):
    from morrow.core.domain import canonical_json_bytes

    events = service.api.journal.list_application_events("ws_1", limit=100)
    return canonical_json_bytes([e.model_dump(mode="json") for e in events])


@pytest.mark.asyncio
async def test_cli_gui_byte_identical_preference_lifecycle(tmp_path, monkeypatch):
    from datetime import datetime

    from typer.testing import CliRunner

    from morrow.adapters.skills import managed_store
    from morrow.adapters.state import preference_yaml_io
    from morrow.interfaces import cli, management_cli
    from test_skill_drafts import NOW

    class SameTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(preference_yaml_io, "datetime", SameTime)
    monkeypatch.setattr(managed_store, "_now", lambda: NOW)
    a_app, a_handle, a = management_fixture(tmp_path / "api")
    b_app, b_handle, b = management_fixture(tmp_path / "cli")

    class BorrowedHandle:
        def close(self):
            pass

    monkeypatch.setattr(
        cli, "_state_services", lambda **_: (b_app, BorrowedHandle(), b.api, None, None)
    )
    client = api_for(a)
    try:
        for index, operation in enumerate(("add", "replace", "disable", "enable", "remove")):
            request = {
                "command_id": f"cmd_parity_{index}",
                "arguments": {
                    "scope": "workspace",
                    "expected_revision": index,
                    "operations": [
                        {
                            "operation": operation,
                            "preference_id": None if operation == "add" else "pref_1",
                            "statement": "Answer in Chinese."
                            if operation == "add"
                            else "Answer concisely."
                            if operation == "replace"
                            else None,
                        }
                    ],
                },
            }
            response = await client.post("/v1/management/preferences", request)
            assert response.status == 200, response.json()
            path = tmp_path / "command.json"
            path.write_text(json.dumps(request))
            output = CliRunner().invoke(
                management_cli.management_app,
                ["command", "preferences", str(path), "--workspace-id", "ws_1"],
            )
            assert output.exit_code == 0, output.output
            assert json.loads(output.output) == response.json()["result"]
            assert a.queries.preferences("workspace") == b.queries.preferences("workspace")
            assert audit_bytes(a) == audit_bytes(b)
        assert a.queries.preferences("workspace")["document"]["entries"][0]["status"] == "deleted"
    finally:
        a_handle.close()
        b_handle.close()


@pytest.mark.asyncio
async def test_skill_lifecycle_scopes_occ_replay_and_draft_separation(tmp_path):
    from test_skill_drafts import _accepted_candidate
    from test_skill_lifecycle import _source

    _app, handle, service = management_fixture(tmp_path)
    client = api_for(service)
    try:
        source = _source(tmp_path)
        first = service.skills.lifecycle.install(source, scope_id="ws_1", confirmed=True)
        second = service.skills.lifecycle.install(source, scope_id="ws_1", confirmed=True)
        _accepted_candidate(service.api.journal)
        draft = service.skills.drafts.create_from_candidate("lcn_skill_draft")
        assert len(service.query("skills")["skills"]) == 1
        assert not service.query("skills")["skills"][0]["enabled"]
        assert len(service.query("skill-drafts")["drafts"]) == 1
        assert "package_ref" not in json.dumps(service.query("skill-drafts"))
        assert service.query("skills", scope="global")["skills"] == []
        for index, action in enumerate(("enable", "pin", "update", "rollback", "disable")):
            digest = service.skill_queries.binding_digest("workspace")
            request = {
                "command_id": f"cmd_skill_{index}",
                "action": action,
                "scope": "workspace",
                "expected_digest": digest,
                "version_id": first.version_id
                if action == "pin"
                else second.version_id
                if action == "update"
                else None,
            }
            response = await client.post("/v1/management/skill-binding/demo-skill", request)
            assert response.status == 200, response.json()
            events = audit_bytes(service)
            replay = await client.post("/v1/management/skill-binding/demo-skill", request)
            assert replay.status == 200, replay.json()
            assert replay.json()["result"]["status"] == "replayed"
            assert audit_bytes(service) == events
        stale = await client.post(
            "/v1/management/skill-binding/demo-skill",
            {
                "command_id": "cmd_stale_skill",
                "action": "enable",
                "expected_digest": "0" * 64,
            },
        )
        assert stale.status == 409
        accepted = await client.post(
            "/v1/management/skill-draft/" + draft.draft_id,
            {
                "command_id": "cmd_accept_draft",
                "action": "accept",
                "expected_row_version": draft.row_version,
            },
        )
        assert accepted.status == 200, accepted.json()
        assert "package_ref" not in json.dumps(accepted.json())
        assert all(not row["enabled"] for row in service.query("skills")["skills"])
        assert service.query("skill-drafts")["drafts"][0]["draft"]["status"] == "accepted"
    finally:
        handle.close()


@pytest.mark.asyncio
async def test_profile_commands_and_non_user_fields_rejected(tmp_path):
    _app, handle, service = management_fixture(tmp_path)
    client = api_for(service)
    try:
        for i, command in enumerate(
            (
                {"operation": "set", "path": "name", "value": "Sample"},
                {"operation": "set", "path": "summary", "value": "Project summary"},
                {"operation": "append", "path": "conventions", "value": "Run tests"},
                {"operation": "remove", "path": "conventions", "value": "Run tests"},
                {"operation": "unset", "path": "summary"},
                {"operation": "reset"},
            )
        ):
            request = {
                "command_id": f"cmd_profile_{i}",
                "expected_revision": i,
                "command": {"scope": "workspace", "target": "profile", **command},
            }
            response = await client.post("/v1/management/profile", request)
            assert response.status == 200, response.json()
            replay = await client.post("/v1/management/profile", request)
            assert replay.status == 200
            assert service.query("profile")["revision"] == i + 1
        assert service.query("profile")["profile"] is None
        for mutation in ({"target": "provider"}, {"path": "credential_ref"}, {"scope": "global"}):
            response = await client.post(
                "/v1/management/profile",
                {
                    "command_id": "cmd_bad",
                    "expected_revision": 6,
                    "command": {
                        "scope": "workspace",
                        "target": "profile",
                        "operation": "set",
                        "path": "name",
                        "value": "safe",
                        **mutation,
                    },
                },
            )
            assert response.status == 400
    finally:
        handle.close()


@pytest.mark.asyncio
async def test_resolved_workflow_context_is_frozen_and_task_scoped(tmp_path):
    from test_stage8_core_api import (
        create_session_and_task,
        publish_pipeline,
        start_run,
        wait_for_run,
    )

    fixture = ServerFixture(tmp_path, scripts=[["first leaf"], ["second leaf"]])
    try:
        await fixture.client.post(
            "/v1/management/preferences",
            {
                "command_id": "cmd_before",
                "arguments": {
                    "scope": "workspace",
                    "expected_revision": 0,
                    "operations": [{"operation": "add", "statement": "Original rule"}],
                },
            },
        )
        revision = await publish_pipeline(fixture)
        sid, tid, version = await create_session_and_task(fixture.client)
        response = await start_run(fixture.client, revision.workflow_revision_id, sid, tid, version)
        run_id = response.json()["result"]["run"]["workflow_run_id"]
        await wait_for_run(fixture.client, run_id, "completed")
        before = await fixture.client.get("/v1/management/context?task_run_id=" + tid)
        assert before.status == 200, before.json()
        assert before.json()["preferences"][0]["statement"] == "Original rule"
        assert len(before.json()["available_runs"]) == 2
        await fixture.client.post(
            "/v1/management/preferences",
            {
                "command_id": "cmd_after",
                "arguments": {
                    "scope": "workspace",
                    "expected_revision": 1,
                    "operations": [{"operation": "add", "statement": "New rule"}],
                },
            },
        )
        after = await fixture.client.get("/v1/management/context?task_run_id=" + tid)
        assert after.json() == before.json()
        sid2, tid2, _ = await create_session_and_task(fixture.client)
        empty = await fixture.client.get("/v1/management/context?task_run_id=" + tid2)
        assert empty.json()["status"] == "not_started"
        foreign = await fixture.client.get(
            "/v1/management/context?task_run_id="
            + tid2
            + "&agent_run_id="
            + before.json()["agent_run_id"]
        )
        assert foreign.status == 404
    finally:
        fixture.close()


def seed_mutation(service, kind, action):
    """Arrange identical domain authority on both transports, then return a command."""
    from datetime import timedelta

    from morrow.application.preferences.proposals import PreferenceProposalPipeline
    from morrow.core.domain import DurableTurn
    from morrow.core.learning import (
        LearningCandidate,
        LearningCandidateDraft,
        ProjectKnowledgeCandidatePayload,
    )
    from morrow.core.preference_review import PreferenceReviewOutput
    from test_preference_proposals import _operation
    from test_preference_proposals import _source as preference_source
    from test_skill_drafts import NOW, _accepted_candidate
    from test_stage5_learning_store import _candidate, _evidence, _review, _seed_subjects
    from test_stage5_memory_selection import _knowledge_subject

    j = service.api.journal
    body = {"command_id": "cmd_mutation_parity"}
    target = None
    if kind == "profile":
        body.update(
            expected_revision=0,
            command={
                "scope": "workspace",
                "target": "profile",
                "operation": "set",
                "path": "name",
                "value": "Parity profile",
            },
        )
    elif kind == "preference-decision":
        _seed_subjects(j)
        j.create_turn(
            "ws_1",
            DurableTurn(
                turn_id="turn_one",
                session_id="ses_1",
                task_run_id="task_1",
                client_message_id="client_one",
                created_at=NOW,
            ),
        )
        job, evidence = preference_source()
        j.preference_journal.put_preference_job_with_evidence("ws_1", job, evidence)
        result = PreferenceProposalPipeline(
            journal=j.preference_journal,
            workspace_id="ws_1",
            id_source=service.api.id_source,
            clock=service.api.clock,
        ).persist(
            job,
            evidence,
            PreferenceReviewOutput(operations=(_operation("add", statement="Proposed rule"),)),
        )
        p = result.proposals[0]
        target = p.proposal_id
        body.update(
            action="accept" if action == "edit" else action,
            expected_row_version=1,
            expected_document_revision=0,
            edit="Edited rule" if action == "edit" else None,
        )
    elif kind == "learning-decision":
        _seed_subjects(j)
        j.put_learning_review("ws_1", _review())
        j.put_learning_evidence("ws_1", _evidence())
        sample = _candidate()
        candidate = LearningCandidate.from_draft(
            candidate_id="lcn_1",
            workspace_id="ws_1",
            origin_review_id="lrv_1",
            draft=LearningCandidateDraft(
                candidate_type="project_knowledge",
                operation="set",
                semantic_key="architecture.storage",
                proposed_scope="workspace",
                proposed_payload=ProjectKnowledgeCandidatePayload(
                    category="architecture",
                    semantic_key="architecture.storage",
                    statement="SQLite stores operational state.",
                ),
                evidence_ids=("lev_1",),
                temporary_or_durable="durable",
            ),
            confidence_band=sample.confidence_band,
            confidence_basis=sample.confidence_basis,
            sensitivity=sample.sensitivity,
            expires_at=NOW + timedelta(days=30),
            now=NOW,
        )
        j.put_learning_candidate("ws_1", candidate)
        target = candidate.candidate_id
        body.update(action="accept" if action == "edit" else action, expected_row_version=1)
        if action == "edit":
            body["final_payload"] = {
                **candidate.proposed_payload.model_dump(mode="json"),
                "statement": "SQLite stores immutable operational evidence.",
            }
    elif kind == "knowledge":
        _knowledge_subject(j)
        head = j.get_project_knowledge_head("ws_1", "knw_1")
        if action == "enable":
            from morrow.core.learning_commands import DisableProjectKnowledgeCommand

            service.api.memory.disable_knowledge(
                DisableProjectKnowledgeCommand(
                    workspace_id="ws_1",
                    knowledge_id="knw_1",
                    expected_row_version=head.row_version,
                    command_id="cmd_seed_disable",
                )
            )
            head = j.get_project_knowledge_head("ws_1", "knw_1")
        target = head.knowledge_id
        body.update(action=action, expected_row_version=head.row_version)
    else:
        _accepted_candidate(j)
        if kind == "skill-draft-create":
            body["candidate_id"] = "lcn_skill_draft"
        else:
            draft = service.skills.drafts.create_from_candidate("lcn_skill_draft")
            target = draft.draft_id
            body.update(action=action, expected_row_version=draft.row_version)
            if kind == "skill-binding":
                installed = service.skills.drafts.accept(
                    draft.draft_id, command_id="cmd_seed_install"
                )
                target = installed.skill_id
                body = {
                    "command_id": "cmd_mutation_parity",
                    "action": action,
                    "scope": "workspace",
                    "expected_digest": service.skill_queries.binding_digest("workspace"),
                    "version_id": installed.version_id if action in {"pin", "update"} else None,
                }
            elif action == "edit":
                body["skill_md"] = (
                    "---\nname: Report Writer\ndescription: Review report evidence\n---\n# Report Writer\nInspect the request and summarize the report.\n"
                )
    return body, target


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,action",
    [
        ("profile", "set"),
        *[("preference-decision", a) for a in ("accept", "edit", "reject", "suppress")],
        *[("learning-decision", a) for a in ("accept", "edit", "reject", "suppress")],
        *[("knowledge", a) for a in ("disable", "enable", "dispute", "delete")],
        *[("skill-binding", a) for a in ("enable", "disable", "pin", "update")],
        ("skill-draft-create", "create"),
        *[("skill-draft", a) for a in ("edit", "validate", "accept", "reject")],
    ],
)
async def test_all_management_mutation_classes_cli_gui_parity(tmp_path, monkeypatch, kind, action):
    from datetime import datetime

    from typer.testing import CliRunner

    from morrow.adapters.skills import managed_store
    from morrow.adapters.state import preference_yaml_io
    from morrow.interfaces import cli, management_cli
    from test_skill_drafts import NOW

    class SameTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(preference_yaml_io, "datetime", SameTime)
    monkeypatch.setattr(managed_store, "_now", lambda: NOW)
    a_app, a_handle, a = management_fixture(tmp_path / "api")
    b_app, b_handle, b = management_fixture(tmp_path / "cli")

    class BorrowedHandle:
        def close(self):
            pass

    monkeypatch.setattr(
        cli, "_state_services", lambda **_: (b_app, BorrowedHandle(), b.api, None, None)
    )
    try:
        request, target = seed_mutation(a, kind, action)
        other, other_target = seed_mutation(b, kind, action)
        assert request == other and target == other_target
        response = await api_for(a).post(
            "/v1/management/" + kind + ("/" + target if target else ""), request
        )
        assert response.status == 200, response.json()
        path = tmp_path / "command.json"
        path.write_text(json.dumps(request))
        args = ["command", kind, str(path), "--workspace-id", "ws_1"]
        if target:
            args.extend(["--target", target])
        output = CliRunner().invoke(management_cli.management_app, args)
        assert output.exit_code == 0, output.output
        assert json.loads(output.output) == response.json()["result"]
        assert audit_bytes(a) == audit_bytes(b)
        for query in ("preferences", "profile", "learning", "knowledge", "skills", "skill-drafts"):
            assert a.query(query) == b.query(query), query
    finally:
        a_handle.close()
        b_handle.close()


def test_management_reads_do_not_expire_or_write_and_paginate(tmp_path):
    from morrow.core.learning import LearningCandidateStatus
    from test_stage5_learning_store import _candidate, _evidence, _review, _seed_subjects

    _app, handle, service = management_fixture(tmp_path)
    try:
        j = service.api.journal
        _seed_subjects(j)
        j.put_learning_review("ws_1", _review())
        j.put_learning_evidence("ws_1", _evidence())
        for i in range(105):
            # All are expired; reading a writable Core must still be read-only.
            sample = _candidate()
            payload = sample.proposed_payload.model_copy(update={"value": f"Proposal {i}"})
            j.put_learning_candidate(
                "ws_1",
                sample.model_copy(
                    update={
                        "candidate_id": f"lcn_page_{i:03}",
                        "proposed_payload": payload,
                        "fingerprint": sample.fingerprint_for(
                            candidate_type=sample.candidate_type,
                            scope=sample.proposed_scope,
                            semantic_key=sample.semantic_key,
                            operation=sample.operation,
                            proposed_payload=payload,
                        ),
                    }
                ),
            )
        before = audit_bytes(service)
        pages = [service.query("learning", page=i) for i in range(3)]
        assert [len(p["candidates"]) for p in pages] == [50, 50, 5]
        assert pages[0]["next_cursor"] == "50"
        assert pages[2]["next_cursor"] is None
        assert len({c["candidate"]["candidate_id"] for p in pages for c in p["candidates"]}) == 105
        assert all(c["expired"] for p in pages for c in p["candidates"])
        assert service.query("context")["pending_learning_count"] == 0
        assert j.count_learning_candidates("ws_1", status=LearningCandidateStatus.PROPOSED) == 105
        assert audit_bytes(service) == before
    finally:
        handle.close()


def test_resolved_context_keeps_selected_knowledge_after_head_is_disabled(tmp_path):
    from morrow.application.context_management import ContextManagementQueries
    from morrow.core.learning_commands import DisableProjectKnowledgeCommand
    from test_stage5_memory_inspection import _admitted

    handle, journal, api, _session = _admitted(tmp_path)
    try:
        queries = ContextManagementQueries(api, None)
        before = queries.resolved(agent_run_id="arun_inspection")
        assert before["knowledge"][0]["revision"]["knowledge_revision_id"] == "krv_1"
        head = journal.get_project_knowledge_head("ws_1", "knw_1")
        api.memory.disable_knowledge(
            DisableProjectKnowledgeCommand(
                workspace_id="ws_1",
                knowledge_id="knw_1",
                expected_row_version=head.row_version,
                command_id="cmd_disable_after_selection",
            )
        )
        assert queries.resolved(agent_run_id="arun_inspection") == before
    finally:
        handle.close()


def test_draft_review_text_diff_and_drift_fail_closed(tmp_path):
    from test_skill_drafts import _accepted_candidate

    _app, handle, service = management_fixture(tmp_path)
    try:
        _accepted_candidate(service.api.journal)
        draft = service.skills.drafts.create_from_candidate("lcn_skill_draft")
        first = service.query("skill-drafts")["drafts"][0]
        assert "inspect the request" in first["skill_md"]
        assert first["editable"] is True
        edited = service.skills.drafts.edit(
            draft.draft_id, skill_md=first["skill_md"] + "\nReview the evidence.\n"
        )
        second = next(
            d
            for d in service.query("skill-drafts")["drafts"]
            if d["draft"]["draft_id"] == edited.draft_id
        )
        assert "+Review the evidence." in second["text_diff"]
        assert second["diff"]["changed"] == ["SKILL.md"]
        root = service.skills.drafts.validation.package_root(edited)
        (root / "SKILL.md").write_text(first["skill_md"])
        drifted = next(
            d
            for d in service.query("skill-drafts")["drafts"]
            if d["draft"]["draft_id"] == edited.draft_id
        )
        assert drifted["inspection_error"] == "draft_unavailable"
        assert drifted["validation"] is None and drifted["editable"] is False
        assert not service.query("skills")["skills"]
    finally:
        handle.close()
