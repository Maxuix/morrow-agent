"""Subplan 6 contracts: durable Draft OCC, diagnostics and Agent copy/edit."""

from __future__ import annotations

import pytest

from morrow.core.agent_runs import AgentDefinitionRef
from test_stage7_serial_scheduler import agent_source, pair_source
from test_stage8_core_api import ServerFixture


async def _publish_agent(fx, *, command_prefix: str = "editor"):
    source = agent_source().model_dump(mode="json")
    created = await fx.client.post(
        "/v1/agent-definitions",
        {
            "command_id": f"cmd_{command_prefix}_agent_create",
            "source": source,
            "expected_source_revision": 0,
        },
    )
    assert created.status == 200, created.body
    published = await fx.client.post(
        "/v1/agent-definitions/helper/publish",
        {
            "command_id": f"cmd_{command_prefix}_agent_publish",
            "expected_head_revision": 0,
        },
    )
    assert published.status == 200, published.body
    version = published.json()["result"]["version"]
    return AgentDefinitionRef(
        definition_id=version["source"]["definition_id"],
        version_id=version["version_id"],
        content_hash=version["content_hash"],
    )


@pytest.mark.asyncio
async def test_draft_edits_validate_with_locators_and_only_freeze_creates_revision(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        ref = await _publish_agent(fx)
        legal = pair_source(ref).model_dump(mode="json")
        created = await fx.client.post(
            "/v1/workflow-drafts",
            {
                "command_id": "cmd_editor_draft_create",
                "draft_id": "wdraft_editor",
                "source": legal,
                "expected_source_revision": 0,
            },
        )
        assert created.status == 200, created.body
        draft = created.json()["result"]["workflow_draft"]["draft"]
        assert (draft["status"], draft["row_version"]) == ("valid", 1)
        assert (
            await fx.client.get("/v1/catalog/workflow-revisions?definition_id=pipeline")
        ).json()["workflow_revisions"] == []

        invalid = {
            **legal,
            "nodes": [node for node in legal["nodes"] if node["node_id"] == "gamma"],
        }
        updated = await fx.client.request(
            "PUT",
            "/v1/workflow-drafts/wdraft_editor",
            body={
                "command_id": "cmd_editor_draft_invalid",
                "source": invalid,
                "expected_row_version": 1,
            },
        )
        assert updated.status == 200, updated.body
        invalid_view = updated.json()["result"]["workflow_draft"]["draft"]
        assert invalid_view["status"] == "invalid"
        located = {
            (item["code"], item["node_id"], item["edge_id"]) for item in invalid_view["diagnostics"]
        }
        assert ("edge_endpoint_invalid", None, "gamma->alpha") in located
        assert ("structure_invalid", "alpha", None) in located

        rejected_freeze = await fx.client.post(
            "/v1/workflow-drafts/wdraft_editor/freeze",
            {"command_id": "cmd_editor_invalid_freeze", "expected_row_version": 2},
        )
        assert rejected_freeze.status == 400
        failure = rejected_freeze.json()["error"]
        assert failure["code"] == "workflow_compilation_failed"
        assert {
            (item["code"], item["node_id"], item["edge_id"]) for item in failure["diagnostics"]
        } == {
            (item["code"], item["node_id"], item["edge_id"])
            for item in invalid_view["diagnostics"]
            if item["severity"] == "error"
        }
        current = (await fx.client.get("/v1/workflow-drafts/wdraft_editor")).json()[
            "workflow_draft"
        ]["draft"]
        assert (current["status"], current["row_version"]) == ("invalid", 2)
        assert (
            await fx.client.get("/v1/catalog/workflow-revisions?definition_id=pipeline")
        ).json()["workflow_revisions"] == []

        stale = await fx.client.request(
            "PUT",
            "/v1/workflow-drafts/wdraft_editor",
            body={"source": legal, "expected_row_version": 1},
        )
        assert (stale.status, stale.json()["error"]["code"]) == (409, "stale")

        repaired = await fx.client.request(
            "PUT",
            "/v1/workflow-drafts/wdraft_editor",
            body={"source": legal, "expected_row_version": 2},
        )
        assert repaired.status == 200, repaired.body
        repaired_draft = repaired.json()["result"]["workflow_draft"]["draft"]
        assert (repaired_draft["status"], repaired_draft["row_version"]) == ("valid", 3)

        frozen = await fx.client.post(
            "/v1/workflow-drafts/wdraft_editor/freeze",
            {"command_id": "cmd_editor_freeze", "expected_row_version": 3},
        )
        assert frozen.status == 200, frozen.body
        result = frozen.json()["result"]
        assert result["workflow_draft"]["draft"]["status"] == "frozen"
        assert result["workflow_revision"]["workflow_revision_id"].startswith("wrev_")
        revisions = (
            await fx.client.get("/v1/catalog/workflow-revisions?definition_id=pipeline")
        ).json()["workflow_revisions"]
        assert len(revisions) == 1

        replay = await fx.client.post(
            "/v1/workflow-drafts/wdraft_editor/freeze",
            {"command_id": "cmd_editor_freeze", "expected_row_version": 3},
        )
        assert replay.status == 200
        assert replay.json()["receipt"]["disposition"] == "replay"
        assert (
            replay.json()["result"]["workflow_revision"]["workflow_revision_id"]
            == result["workflow_revision"]["workflow_revision_id"]
        )
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_distinct_drafts_freeze_across_unrelated_source_writes(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        ref = await _publish_agent(fx, command_prefix="siblings")
        first_source = pair_source(ref).model_copy(
            update={"workflow_definition_id": "first_pipeline", "name": "First"}
        )
        second_source = pair_source(ref).model_copy(
            update={"workflow_definition_id": "second_pipeline", "name": "Second"}
        )
        for draft_id, source in (
            ("wdraft_first", first_source),
            ("wdraft_second", second_source),
        ):
            created = await fx.client.post(
                "/v1/workflow-drafts",
                {
                    "draft_id": draft_id,
                    "source": source.model_dump(mode="json"),
                    "expected_source_revision": 0,
                },
            )
            assert created.status == 200, created.body

        first = await fx.client.post(
            "/v1/workflow-drafts/wdraft_first/freeze",
            {"command_id": "cmd_first_freeze", "expected_row_version": 1},
        )
        second = await fx.client.post(
            "/v1/workflow-drafts/wdraft_second/freeze",
            {"command_id": "cmd_second_freeze", "expected_row_version": 1},
        )

        assert first.status == 200, first.body
        assert second.status == 200, second.body
        assert second.json()["result"]["workflow_revision"]["source_revision"] == 2
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_existing_definition_draft_rebases_over_unrelated_source_write(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        ref = await _publish_agent(fx, command_prefix="rebase")
        original = pair_source(ref)
        await fx.on_core(
            lambda: fx.host.context.management.create_workflow_source(
                original, expected_source_revision=0
            )
        )
        edited = original.model_copy(update={"name": "Edited pipeline"})
        sibling = original.model_copy(
            update={"workflow_definition_id": "sibling_pipeline", "name": "Sibling"}
        )
        for draft_id, source in (
            ("wdraft_existing", edited),
            ("wdraft_sibling", sibling),
        ):
            created = await fx.client.post(
                "/v1/workflow-drafts",
                {
                    "draft_id": draft_id,
                    "source": source.model_dump(mode="json"),
                    "expected_source_revision": 1,
                },
            )
            assert created.status == 200, created.body

        sibling_freeze = await fx.client.post(
            "/v1/workflow-drafts/wdraft_sibling/freeze",
            {"command_id": "cmd_sibling_freeze", "expected_row_version": 1},
        )
        assert sibling_freeze.status == 200, sibling_freeze.body
        existing_view = await fx.client.get("/v1/workflow-drafts/wdraft_existing")
        assert existing_view.json()["workflow_draft"]["stale_reasons"] == []

        existing_freeze = await fx.client.post(
            "/v1/workflow-drafts/wdraft_existing/freeze",
            {"command_id": "cmd_existing_freeze", "expected_row_version": 1},
        )
        assert existing_freeze.status == 200, existing_freeze.body
        assert existing_freeze.json()["result"]["workflow_revision"]["source_revision"] == 3
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_existing_definition_draft_rejects_same_identity_source_change(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        ref = await _publish_agent(fx, command_prefix="identity_conflict")
        original = pair_source(ref)
        await fx.on_core(
            lambda: fx.host.context.management.create_workflow_source(
                original, expected_source_revision=0
            )
        )
        draft_source = original.model_copy(update={"name": "Draft edit"})
        created = await fx.client.post(
            "/v1/workflow-drafts",
            {
                "draft_id": "wdraft_identity_conflict",
                "source": draft_source.model_dump(mode="json"),
                "expected_source_revision": 1,
            },
        )
        assert created.status == 200, created.body
        foreign_source = original.model_copy(update={"name": "Foreign edit"})
        await fx.on_core(
            lambda: fx.host.context.management.update_workflow_source(
                foreign_source, expected_source_revision=1
            )
        )

        view = await fx.client.get("/v1/workflow-drafts/wdraft_identity_conflict")
        assert view.json()["workflow_draft"]["stale_reasons"] == ["workflow_source_changed"]
        frozen = await fx.client.post(
            "/v1/workflow-drafts/wdraft_identity_conflict/freeze",
            {"command_id": "cmd_identity_conflict_freeze", "expected_row_version": 1},
        )
        assert (frozen.status, frozen.json()["error"]["code"]) == (409, "stale")
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_agent_copy_records_parent_and_rejects_non_schema_security_fields(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        ref = await _publish_agent(fx, command_prefix="copy")
        parent = await fx.client.get(f"/v1/catalog/agent-versions/{ref.version_id}")
        assert parent.status == 200

        clone = agent_source(
            definition_id="helper_copy",
            name="Helper Copy",
            derived_from_version_id=ref.version_id,
            derived_from_definition_id="helper",
            derived_from_source_hash=ref.content_hash,
        ).model_dump(mode="json")
        created = await fx.client.post(
            "/v1/agent-definitions",
            {
                "command_id": "cmd_copy_create",
                "source": clone,
                "expected_source_revision": 1,
            },
        )
        assert created.status == 200, created.body
        published = await fx.client.post(
            "/v1/agent-definitions/helper_copy/publish",
            {"command_id": "cmd_copy_publish", "expected_head_revision": 0},
        )
        assert published.status == 200, published.body
        copied = published.json()["result"]["agent_definition"]
        assert copied["published_version"]["source"]["derived_from_version_id"] == ref.version_id
        assert copied["published_version"]["source"]["derived_from_source_hash"] == ref.content_hash
        assert copied["definition_id"] == "helper_copy"

        smuggled = {**clone, "credential": "must-not-enter-definition"}
        denied = await fx.client.request(
            "PUT",
            "/v1/agent-definitions/helper_copy",
            body={"source": smuggled, "expected_source_revision": 2},
        )
        assert denied.status == 400

        mismatched_parent = {
            **clone,
            "definition_id": "helper_bad_copy",
            "derived_from_source_hash": "b" * 64,
        }
        denied_parent = await fx.client.post(
            "/v1/agent-definitions",
            {"source": mismatched_parent, "expected_source_revision": 2},
        )
        assert denied_parent.status == 400
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_agent_copy_accepts_desired_ahead_and_unpublished_parent_provenance(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        ref = await _publish_agent(fx, command_prefix="ahead")
        desired_ahead = agent_source(
            definition_id="helper",
            name="Helper Ahead",
            role_prompt="Use the newer desired source.",
        )
        updated = await fx.client.request(
            "PUT",
            "/v1/agent-definitions/helper",
            body={"source": desired_ahead.model_dump(mode="json"), "expected_source_revision": 1},
        )
        assert updated.status == 200, updated.body
        assert updated.json()["result"]["agent_definition"]["desired_ahead_of_published"] is True
        desired_copy = agent_source(
            definition_id="desired_ahead_copy",
            name="Desired Ahead Copy",
            derived_from_definition_id="helper",
            derived_from_source_hash=desired_ahead.content_hash,
        )
        copied = await fx.client.post(
            "/v1/agent-definitions",
            {"source": desired_copy.model_dump(mode="json"), "expected_source_revision": 2},
        )
        assert copied.status == 200, copied.body
        copied_source = copied.json()["result"]["agent_definition"]["source"]
        assert copied_source["derived_from_version_id"] is None
        assert copied_source["derived_from_source_hash"] != ref.content_hash

        parent = agent_source(definition_id="draft_parent", name="Draft Parent")
        created_parent = await fx.client.post(
            "/v1/agent-definitions",
            {"source": parent.model_dump(mode="json"), "expected_source_revision": 3},
        )
        assert created_parent.status == 200, created_parent.body
        unpublished_copy = agent_source(
            definition_id="unpublished_copy",
            name="Unpublished Copy",
            derived_from_definition_id=parent.definition_id,
            derived_from_source_hash=parent.content_hash,
        )
        created_copy = await fx.client.post(
            "/v1/agent-definitions",
            {"source": unpublished_copy.model_dump(mode="json"), "expected_source_revision": 4},
        )
        assert created_copy.status == 200, created_copy.body
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_revalidation_blocks_agent_disabled_during_edit(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        ref = await _publish_agent(fx, command_prefix="disabled")
        source = pair_source(ref).model_dump(mode="json")
        created = await fx.client.post(
            "/v1/workflow-drafts",
            {
                "draft_id": "wdraft_disabled",
                "source": source,
                "expected_source_revision": 0,
            },
        )
        assert created.status == 200
        await fx.on_core(
            lambda: fx.host.context.management.set_agent_enabled(
                "helper", enabled=False, expected_head_revision=1
            )
        )

        revalidated = await fx.client.post(
            "/v1/workflow-drafts/wdraft_disabled/validate",
            {"command_id": "cmd_disabled_validate", "expected_row_version": 1},
        )
        assert revalidated.status == 200, revalidated.body
        draft = revalidated.json()["result"]["workflow_draft"]["draft"]
        assert draft["status"] == "invalid"
        diagnostics = {(item["code"], item["node_id"]) for item in draft["diagnostics"]}
        assert diagnostics == {
            ("agent_definition_disabled", "alpha"),
            ("agent_definition_disabled", "gamma"),
        }
        replay = await fx.client.post(
            "/v1/workflow-drafts/wdraft_disabled/validate",
            {"command_id": "cmd_disabled_validate", "expected_row_version": 1},
        )
        assert replay.status == 200
        assert replay.json()["receipt"]["disposition"] == "replay"
        freeze = await fx.client.post(
            "/v1/workflow-drafts/wdraft_disabled/freeze",
            {"expected_row_version": 2},
        )
        assert freeze.status == 400
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_freeze_recovers_after_publication_commit_without_revalidating(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        ref = await _publish_agent(fx, command_prefix="recover")
        source = pair_source(ref)
        created = await fx.client.post(
            "/v1/workflow-drafts",
            {
                "draft_id": "wdraft_recover",
                "source": source.model_dump(mode="json"),
                "expected_source_revision": 0,
            },
        )
        assert created.status == 200

        def commit_publication_then_change_catalog():
            management = fx.host.context.management
            written = management.create_workflow_source(source, expected_source_revision=0)
            publication = management.workflow_publication.publish(
                source,
                source_revision=written.source_revision,
                expected_head_revision=0,
                command_id="cmd_recover_freeze",
                active_model=management.active_model,
            )
            management.set_agent_enabled("helper", enabled=False, expected_head_revision=1)
            return publication.revision.workflow_revision_id

        revision_id = await fx.on_core(commit_publication_then_change_catalog)
        recovered = await fx.client.post(
            "/v1/workflow-drafts/wdraft_recover/freeze",
            {"command_id": "cmd_recover_freeze", "expected_row_version": 1},
        )
        assert recovered.status == 200, recovered.body
        result = recovered.json()["result"]
        assert result["workflow_draft"]["draft"]["status"] == "frozen"
        assert result["workflow_revision"]["workflow_revision_id"] == revision_id
    finally:
        fx.close()
