"""Workbench adapters preserve the learning/memory authorities and bounded queries."""

from test_stage8_core_api import ServerFixture


async def test_knowledge_queries_mode_occ_replay_and_scope(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        root = f"/v1/workspaces/{fx.workspace_id}/knowledge-management/"
        for kind in (
            "learning-status",
            "reviews",
            "candidates",
            "promotions",
            "activations",
            "preference-status",
            "inbox-status",
            "inbox-jobs",
            "proposals",
            "knowledge",
            "selections",
        ):
            response = await fx.client.get(root + kind)
            assert response.status == 200, (kind, response.body)
            assert "prepared_change_json" not in response.body.decode()
            assert "inverse_command_json" not in response.body.decode()
        current = (await fx.client.get(root + "learning-status")).json()["policy"]
        request = {
            "action": "mode",
            "mode": "off",
            "command_id": "cmd_mode",
            "expected_row_version": current["row_version"],
        }
        path = f"/v1/workspaces/{fx.workspace_id}/knowledge-commands/learning"
        assert (await fx.client.post(path, request)).status == 200
        assert (await fx.client.post(path, request)).status == 200
        assert (await fx.client.post(path, {**request, "mode": "review_only"})).status == 409
        assert (
            await fx.client.post(
                path, {**request, "command_id": "cmd_stale", "expected_row_version": 0}
            )
        ).status == 409
        assert (await fx.client.get(root + "reviews?limit=0")).status == 400
        assert (await fx.client.get(root + "reviews?identity=lr_missing")).status == 404
        assert (
            await fx.client.get("/v1/workspaces/ws_other/knowledge-management/reviews")
        ).status == 403
    finally:
        fx.close()


def test_review_and_memory_selection_pages_continue_beyond_first_page(tmp_path):
    from morrow.application.knowledge_management import KnowledgeQuery, query_knowledge
    from morrow.core.memory_selection import MemorySelection
    from test_stage5_review_pipeline import _accepted, _api

    handle, journal, api = _api(tmp_path)
    try:
        task = _accepted(api, journal).value
        outcome = api.list_outcomes(task.task_run_id)[0]
        for i in range(5):
            api.request_learning_review(outcome.outcome_id, command_id=f"cmd_review_{i}")
        first = query_knowledge(api, "reviews", KnowledgeQuery(limit=2))
        second = query_knowledge(
            api, "reviews", KnowledgeQuery(limit=2, cursor=first["next_cursor"])
        )
        assert len(second["items"]) == 2
        assert set(r["review_id"] for r in first["items"]).isdisjoint(
            r["review_id"] for r in second["items"]
        )
        # Empty selections still have immutable identities and must remain inspectable.
        from test_stage5_memory_selection import NOW

        for i in range(3):
            journal.put_memory_selection(
                "ws_1",
                MemorySelection(
                    selection_id=f"msel_page_{i}",
                    workspace_id="ws_1",
                    query_digest="a" * 64,
                    source_memory_revision=0,
                    selected_items=(),
                    item_count=0,
                    omitted_count=0,
                    rendered_chars=0,
                    selection_digest="b" * 64,
                    created_at=NOW,
                ),
            )
        first = query_knowledge(api, "selections", KnowledgeQuery(limit=2))
        second = query_knowledge(
            api, "selections", KnowledgeQuery(limit=2, cursor=first["next_cursor"])
        )
        assert len(second["items"]) == 1
        assert second["items"][0]["selection_id"] == "msel_page_2"
    finally:
        handle.close()


async def test_review_is_supervised_and_does_not_hold_command_bus(tmp_path):
    import asyncio

    from morrow.core.domain import TaskRunStatus
    from morrow.core.learning import CandidateDraftBatch
    from test_stage8_chat_submission import new_session

    fx = ServerFixture(tmp_path)
    try:
        sid, path = await new_session(fx)

        def prepare():
            api = fx.host.context.api
            task = api.task_new(sid, command_id="cmd_task").value
            ready = api.tasks._transition(
                task,
                TaskRunStatus.READY_FOR_ACCEPTANCE,
                reason="answer",
                turn_id=None,
                command_id=None,
            )
            api.task_accept(
                ready.task_run_id, command_id="cmd_accept", expected_row_version=ready.row_version
            )
            review = api.list_learning_review_views().items[0]
            entered = asyncio.Event()
            release = asyncio.Event()

            class Held:
                calls = 0

                async def review(self, context, **kwargs):
                    self.calls += 1
                    entered.set()
                    await release.wait()
                    return CandidateDraftBatch()

            held = Held()
            fx.host.context.products.api.learning_review_runner.reviewer = held
            return review, entered, release, held

        review, entered, release, held = await fx.on_core(prepare)
        body = {
            "action": "review",
            "target": review.review_id,
            "expected_row_version": review.row_version,
            "command_id": "cmd_review_async",
        }
        first = await fx.client.post("/v1/knowledge-commands/learning", body)
        assert first.status == 200, first.body
        await fx.host.execute_preparation(entered.wait)
        # Real Session control can complete while the reviewer is blocked.
        queue = (await fx.client.get(path + "/snapshot")).json()["queue"]
        stopped = await fx.client.post(
            path + "/control",
            {
                "action": "continue_queue",
                "command_id": "cmd_continue_during_review",
                "expected_revision": queue["revision"],
            },
        )
        assert stopped.status == 200, stopped.body
        assert (await fx.client.post("/v1/knowledge-commands/learning", body)).status == 200
        assert held.calls == 1
        await fx.on_core(release.set)
        await fx.host.execute_preparation(
            lambda: fx.host.context.supervisor.wait_driver("admin_cmd_review_async")
        )
        state = (await fx.client.get("/v1/knowledge-jobs/cmd_review_async")).json()
        assert state["status"] == "completed", state
        assert state["result"]["review_status"] == "completed"
        assert (await fx.client.post("/v1/knowledge-commands/learning", body)).status == 200
        assert held.calls == 1
        assert (
            await fx.client.post("/v1/knowledge-commands/learning", {**body, "target": "lrv_other"})
        ).status == 409
    finally:
        fx.close()


async def test_inbox_bulk_previews_share_one_revision_and_replay(tmp_path):
    from morrow.application.preferences.proposals import PreferenceProposalPipeline
    from morrow.core.domain import DurableTurn
    from morrow.core.preference_review import PreferenceReviewOutput
    from test_preference_proposals import NOW, _operation, _source
    from test_stage5_learning_store import _seed_subjects
    from test_stage8_context_management import api_for, management_fixture

    _app, handle, service = management_fixture(tmp_path)
    try:
        journal = service.api.journal
        _seed_subjects(journal)
        journal.create_turn(
            "ws_1",
            DurableTurn(
                turn_id="turn_one",
                session_id="ses_1",
                task_run_id="task_1",
                client_message_id="inbox_seed",
                created_at=NOW,
            ),
        )
        job, evidence = _source()
        journal.put_preference_job_with_evidence("ws_1", job, evidence)
        PreferenceProposalPipeline(
            journal=journal, workspace_id="ws_1", id_source=service.api.id_source, clock=journal.now
        ).persist(
            job,
            evidence,
            PreferenceReviewOutput(
                operations=(
                    _operation("add", statement="Use concise language"),
                    _operation("add", statement="Show runnable examples"),
                )
            ),
        )
        client = api_for(service)
        page = (await client.get("/v1/knowledge-management/proposals?limit=1")).json()
        assert page["next_cursor"] == "1"
        next_page = (await client.get("/v1/knowledge-management/proposals?limit=1&cursor=1")).json()
        ids = [page["items"][0]["proposal_id"], next_page["items"][0]["proposal_id"]]
        previews = [
            (await client.get("/v1/knowledge-management/proposal-preview?identity=" + id)).json()
            for id in ids
        ]
        body = {
            "action": "accept_many",
            "command_id": "cmd_many",
            "proposal_ids": ids,
            "expected_row_versions": {
                p["proposal"]["proposal_id"]: p["expected_row_version"] for p in previews
            },
            "expected_document_revision": previews[0]["expected_document_revision"],
        }
        result = await client.post("/v1/knowledge-commands/inbox", body)
        assert result.status == 200, result.body
        assert (await client.post("/v1/knowledge-commands/inbox", body)).status == 200
        doc = service.api.preference_queries.document("workspace")
        assert doc.revision == 1 and len(doc.entries) == 2
        assert (
            await client.post("/v1/knowledge-commands/inbox", {**body, "expected_row_versions": {}})
        ).status == 409
        assert (
            await client.post(
                "/v1/knowledge-commands/inbox",
                {**body, "command_id": "cmd_invalid", "expected_row_versions": {}},
            )
        ).status == 400
        # Recover directly from the native Writer saga, without the HTTP receipt.
        recovered = service.api.accept_preference_proposals(
            ids,
            command_id="cmd_many",
            expected_row_versions=body["expected_row_versions"],
            expected_document_revision=0,
        )
        assert recovered.replayed
        assert service.api.preference_queries.document("workspace").revision == 1
    finally:
        handle.close()


async def test_profile_activation_preview_undo_and_private_recovery_images(tmp_path):
    from test_stage5_learning_store import _candidate, _evidence, _review, _seed_subjects
    from test_stage8_context_management import api_for, management_fixture

    _app, handle, service = management_fixture(tmp_path)
    try:
        journal = service.api.journal
        _seed_subjects(journal)
        journal.put_learning_review("ws_1", _review())
        journal.put_learning_evidence("ws_1", _evidence())
        from datetime import timedelta

        journal.put_learning_candidate(
            "ws_1",
            _candidate().model_copy(
                update={"expires_at": service.api.clock() + timedelta(days=10)}
            ),
        )
        client = api_for(service)
        initial = await client.post(
            "/v1/management/profile",
            {
                "command_id": "cmd_profile_name",
                "expected_revision": 0,
                "command": {
                    "scope": "workspace",
                    "target": "profile",
                    "operation": "set",
                    "path": "name",
                    "value": "Example",
                },
            },
        )
        assert initial.status == 200, initial.body
        accepted = await client.post(
            "/v1/management/learning-decision/lcn_1",
            {"command_id": "cmd_promote", "action": "accept", "expected_row_version": 1},
        )
        assert accepted.status == 200, accepted.body
        rows = (await client.get("/v1/knowledge-management/activations")).json()["items"]
        assert len(rows) == 1 and "inverse_command_json" not in rows[0]
        identity = rows[0]["activation_id"]
        preview = await client.get("/v1/knowledge-management/undo-preview?identity=" + identity)
        assert preview.status == 200, preview.body
        assert "prepared_change_json" not in preview.body.decode()
        operation = (
            await client.get("/v1/knowledge-management/promotions?status=finalized")
        ).json()["items"][0]
        assert "prepared_change_json" not in operation
        body = {"action": "undo", "target": identity, "command_id": "cmd_undo"}
        assert (await client.post("/v1/knowledge-commands/learning", body)).status == 200
        assert (await client.post("/v1/knowledge-commands/learning", body)).status == 200
        assert service.queries.profile()["profile"]["summary"] is None
    finally:
        handle.close()


async def test_promotion_recovery_actions_preserve_before_after_boundaries(tmp_path, monkeypatch):
    from datetime import timedelta

    from test_stage5_learning_store import _candidate, _evidence, _review, _seed_subjects
    from test_stage8_context_management import api_for, management_fixture

    for action in ("retry", "finalize", "cancel", "abort"):
        _app, handle, service = management_fixture(tmp_path / action)
        try:
            journal = service.api.journal
            _seed_subjects(journal)
            journal.put_learning_review("ws_1", _review())
            journal.put_learning_evidence("ws_1", _evidence())
            journal.put_learning_candidate(
                "ws_1",
                _candidate().model_copy(
                    update={"expires_at": service.api.clock() + timedelta(days=10)}
                ),
            )
            client = api_for(service)
            assert (
                await client.post(
                    "/v1/management/profile",
                    {
                        "command_id": "cmd_seed_profile",
                        "expected_revision": 0,
                        "command": {
                            "scope": "workspace",
                            "target": "profile",
                            "operation": "set",
                            "path": "name",
                            "value": "Recovery fixture",
                        },
                    },
                )
            ).status == 200
            original = service.profile.apply_prepared

            def interrupted(prepared, selected_action=action, apply=original, **kwargs):
                if selected_action == "finalize":
                    apply(prepared, **kwargs)
                raise RuntimeError("fixture interrupted at configuration boundary")

            monkeypatch.setattr(service.profile, "apply_prepared", interrupted)
            failed = await client.post(
                "/v1/management/learning-decision/lcn_1",
                {
                    "command_id": "cmd_promote_interrupted",
                    "action": "accept",
                    "expected_row_version": 1,
                },
            )
            assert failed.status != 200
            monkeypatch.setattr(service.profile, "apply_prepared", original)
            rows = (await client.get("/v1/knowledge-management/promotions")).json()["items"]
            assert len(rows) == 1
            body = {
                "action": "promotion",
                "recovery_action": action,
                "target": rows[0]["operation_id"],
                "command_id": "cmd_recover",
            }
            if action == "finalize":
                forbidden = await client.post(
                    "/v1/knowledge-commands/learning",
                    {**body, "recovery_action": "cancel", "command_id": "cmd_wrong_recovery"},
                )
                assert forbidden.status == 409
            recovered = await client.post("/v1/knowledge-commands/learning", body)
            assert recovered.status == 200, (action, recovered.body)
            assert (await client.post("/v1/knowledge-commands/learning", body)).status == 200
            result = (
                await client.get("/v1/knowledge-management/promotions?identity=" + body["target"])
            ).json()
            assert result["state"] == (
                "finalized" if action in ("retry", "finalize") else "aborted"
            )
            assert service.queries.profile()["profile"]["summary"] == (
                "中文" if action in ("retry", "finalize") else None
            )
        finally:
            handle.close()
