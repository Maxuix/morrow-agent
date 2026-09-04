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
        current = (await fx.client.get("/v1/workflow-drafts/wdraft_editor")).json()[
            "workflow_draft"
        ]["draft"]
        assert (current["status"], current["row_version"]) == ("invalid", 3)
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
            body={"source": legal, "expected_row_version": 3},
        )
        assert repaired.status == 200, repaired.body
        repaired_draft = repaired.json()["result"]["workflow_draft"]["draft"]
        assert (repaired_draft["status"], repaired_draft["row_version"]) == ("valid", 4)

        frozen = await fx.client.post(
            "/v1/workflow-drafts/wdraft_editor/freeze",
            {"command_id": "cmd_editor_freeze", "expected_row_version": 4},
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
            {"command_id": "cmd_editor_freeze", "expected_row_version": 4},
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
