"""Subplan 73 MCP runtime, policy, result and ordinary-tool seam tests."""

from __future__ import annotations

import asyncio
import base64
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp.types import (
    CallToolResult,
    EmbeddedResource,
    ImageContent,
    ResourceLink,
    TextContent,
    TextResourceContents,
)

from morrow.adapters.mcp.stdio_client import McpStdioClient
from morrow.application.mcp.catalog import build_catalog_snapshot
from morrow.application.mcp.policy import evaluate_mcp_policy
from morrow.application.mcp.results import McpResultNormalizer
from morrow.application.mcp.runtime import (
    McpRuntimeError,
    prepare_mcp_run,
    register_mcp_tools,
    rehydrate_mcp_run,
)
from morrow.core.capabilities import (
    PermissionProfile,
    PolicyVerdict,
    RiskFlag,
    WorkspaceCapability,
)
from morrow.core.mcp import (
    McpCwdPolicy,
    McpLaunchRisk,
    McpReviewEvidence,
    McpReviewRisk,
    McpServerDefinition,
    McpToolPolicy,
    McpToolRiskMapping,
    McpWorkspaceVisibility,
)
from morrow.core.models import FunctionToolCall, ToolApprovalDecision, ToolEffect
from morrow.runtime.capabilities import CapabilityPolicy
from morrow.runtime.tools import ToolExecutor, ToolRegistry
from morrow.testing import FixedIdSource, make_run_policy

FAKE_SERVER = Path(__file__).parent / "spikes" / "fake_mcp_stdio_server.py"


def _definition(
    *,
    server_id: str = "mcp_fake",
    tool_names: tuple[str, ...] = ("echo",),
    timeout_ms: int = 15_000,
    risks: tuple[McpLaunchRisk, ...] = (),
) -> McpServerDefinition:
    return McpServerDefinition(
        server_id=server_id,
        display_name="Fake",
        executable=sys.executable,
        argv=(str(FAKE_SERVER.resolve()),),
        cwd_policy=McpCwdPolicy.MANAGED,
        timeout_ms=timeout_ms,
        enabled=True,
        requested_launch_risks=risks,
        tool_policy=McpToolPolicy(
            allowlist=tool_names,
            mappings=tuple(
                McpToolRiskMapping(remote_name=name, effect=ToolEffect.NONE) for name in tool_names
            ),
        ),
    )


def _catalog(definition: McpServerDefinition):
    discovery = asyncio.run(McpStdioClient(definition).discover())
    return build_catalog_snapshot(definition, discovery)


def test_lazy_pool_reuses_one_server_and_normalizes_fake_call() -> None:
    definition = _definition()
    catalog = _catalog(definition)
    created: list[McpStdioClient] = []

    def factory(current):
        client = McpStdioClient(current)
        created.append(client)
        return client

    prepared = prepare_mcp_run(
        (definition,),
        {definition.server_id: catalog},
        workspace_id="ws_1",
        agent_run_id="arun_1",
        id_source=FixedIdSource(),
        client_factory=factory,
    )

    async def exercise():
        first = await prepared.pool.call("mcp_fake", "echo", {"text": "hello"})
        second = await prepared.pool.call("mcp_fake", "echo", {"text": "again"})
        await prepared.pool.close()
        return first, second

    first, second = asyncio.run(exercise())
    assert first.text_parts == ("hello",)
    assert second.text_parts == ("again",)
    assert len(created) == 1
    assert prepared.pool.status_facts()["closed"] is True


def test_mcp_review_only_converts_eligible_denial_and_binds_exact_run() -> None:
    policy = CapabilityPolicy(
        PermissionProfile(),
        WorkspaceCapability(workspace_id="ws_1", root=Path("/workspace")),
    )
    definition = _definition(risks=(McpLaunchRisk.NETWORK,))
    catalog = _catalog(definition)
    prepared = prepare_mcp_run(
        (definition,),
        {definition.server_id: catalog},
        workspace_id="ws_1",
        agent_run_id="arun_1",
        id_source=FixedIdSource(),
    )
    launch = prepared.launch_snapshots[0]
    binding = prepared.bindings[0]
    review = McpReviewEvidence(
        review_id="mrev_1",
        workspace_id="ws_1",
        agent_run_id="arun_1",
        server_id="mcp_fake",
        config_digest=launch.config_digest,
        catalog_digest=launch.catalog_digest or "0" * 64,
        toolset_digest=launch.toolset_digest or "0" * 64,
        risks=(McpReviewRisk.NETWORK,),
    )
    approved = evaluate_mcp_policy(
        policy,
        launch_intent=binding.launch_intent,
        tool_intent=binding.tool_intent,
        review=review,
        workspace_id="ws_1",
        agent_run_id="arun_1",
        server_id="mcp_fake",
        config_digest=launch.config_digest,
        catalog_digest=launch.catalog_digest or "0" * 64,
        toolset_digest=launch.toolset_digest or "0" * 64,
    )
    assert approved.verdict is PolicyVerdict.REQUIRE_APPROVAL
    assert "mcp_review_required" in approved.reason_codes

    stale = review.model_copy(update={"agent_run_id": "arun_2"})
    denied = evaluate_mcp_policy(
        policy,
        launch_intent=binding.launch_intent,
        tool_intent=binding.tool_intent,
        review=stale,
        workspace_id="ws_1",
        agent_run_id="arun_1",
        server_id="mcp_fake",
        config_digest=launch.config_digest,
        catalog_digest=launch.catalog_digest or "0" * 64,
        toolset_digest=launch.toolset_digest or "0" * 64,
    )
    assert denied.verdict is PolicyVerdict.DENY

    outside = binding.launch_intent.model_copy(update={"risk_flags": (RiskFlag.OUTSIDE_WORKSPACE,)})
    still_denied = evaluate_mcp_policy(
        policy,
        launch_intent=outside,
        tool_intent=binding.tool_intent,
        review=review,
        workspace_id="ws_1",
        agent_run_id="arun_1",
        server_id="mcp_fake",
        config_digest=launch.config_digest,
        catalog_digest=launch.catalog_digest or "0" * 64,
        toolset_digest=launch.toolset_digest or "0" * 64,
    )
    assert still_denied.verdict is PolicyVerdict.DENY


def test_result_normalizer_keeps_links_as_locators_and_binary_as_artifact_refs() -> None:
    published: list[tuple[bytes, str | None, str]] = []

    def publish(content: bytes, mime_type: str | None, role: str):
        published.append((content, mime_type, role))
        from morrow.core.mcp import McpArtifactRef

        return McpArtifactRef(
            artifact_id=f"art_{len(published)}",
            role=role,
            mime_type=mime_type,
            byte_size=len(content),
        )

    result = CallToolResult(
        content=[
            TextContent(text="safe"),
            ImageContent(data=base64.b64encode(b"image").decode(), mimeType="image/png"),
            ResourceLink(name="report", uri="https://example.test/report"),
            EmbeddedResource(resource=TextResourceContents(uri="mcp://embedded", text="embedded")),
            TextContent(text="token=sk-123456789012345678901234"),
        ],
        structuredContent={"ok": True},
    )
    normalized = McpResultNormalizer(publish_artifact=publish).normalize(result)
    assert normalized.text_parts == ("safe",)
    assert len(normalized.image_refs) == 1
    assert normalized.resource_links[0].uri == "https://example.test/report"
    assert len(normalized.embedded_resource_refs) == 1
    assert normalized.omitted_count == 1
    assert [item[2] for item in published] == ["image", "embedded_resource"]


def test_mcp_registration_uses_ordinary_tool_executor_and_policy_approval() -> None:
    definition = _definition()
    catalog = _catalog(definition)
    prepared = prepare_mcp_run(
        (definition,),
        {definition.server_id: catalog},
        workspace_id="ws_1",
        agent_run_id="arun_1",
        id_source=FixedIdSource(),
    )
    policy = CapabilityPolicy(
        PermissionProfile(),
        WorkspaceCapability(workspace_id="ws_1", root=Path("/workspace")),
    )
    registry = ToolRegistry()
    register_mcp_tools(
        registry,
        prepared,
        capability_policy=policy,
        workspace_id="ws_1",
        agent_run_id="arun_1",
    )
    assert [item.function.name for item in registry.definitions()] == ["mcp__fake__echo"]

    class Approval:
        async def request(self, _request):
            return ToolApprovalDecision(approved=True)

    executor = ToolExecutor(
        registry.snapshot(),
        make_run_policy(),
        approval_port=Approval(),
        capability_policy=policy,
    )

    async def exercise():
        from morrow.core.models import FunctionToolCall

        try:
            return await executor.execute(
                FunctionToolCall(
                    id="call-1",
                    name="mcp__fake__echo",
                    arguments='{"text":"ordinary"}',
                )
            )
        finally:
            await prepared.pool.close()

    outcome = asyncio.run(exercise())
    assert outcome.ok is True
    assert '"ordinary"' in outcome.envelope


def test_timeout_is_terminal_for_one_server_and_never_retried(tmp_path: Path) -> None:
    definition = _definition(tool_names=("slow",), timeout_ms=1_000)
    catalog = _catalog(definition)
    call_log = tmp_path / "calls.jsonl"

    def factory(current):
        return McpStdioClient(
            current,
            environment={"FAKE_MCP_CALL_LOG": str(call_log)},
        )

    prepared = prepare_mcp_run(
        (definition,),
        {definition.server_id: catalog},
        workspace_id="ws_1",
        agent_run_id="arun_1",
        id_source=FixedIdSource(),
        client_factory=factory,
    )

    async def exercise() -> None:
        with pytest.raises(McpRuntimeError) as first:
            await prepared.pool.call("mcp_fake", "slow", {"seconds": 2})
        assert first.value.code == "timeout"
        with pytest.raises(McpRuntimeError) as second:
            await prepared.pool.call("mcp_fake", "slow", {"seconds": 2})
        assert second.value.code == "server_degraded"
        await prepared.pool.close()

    asyncio.run(exercise())
    calls = call_log.read_text(encoding="utf-8").splitlines()
    assert len(calls) == 1


def test_one_degraded_server_does_not_poison_another() -> None:
    bad = _definition(server_id="mcp_bad")
    good = _definition(server_id="mcp_good")
    catalogs = {bad.server_id: _catalog(bad), good.server_id: _catalog(good)}
    created: list[str] = []

    class FakeClient:
        def __init__(self, current, *, fail: bool) -> None:
            self.server_id = current.server_id
            self.entries = catalogs[self.server_id].tools
            self.fail = fail

        async def connect(self) -> None:
            created.append(self.server_id)
            if self.fail:
                raise RuntimeError("deliberate fake crash")

        async def list_tools(self):
            return tuple(
                SimpleNamespace(
                    name=entry.remote_name,
                    input_schema=entry.input_schema,
                    output_schema=entry.output_schema,
                )
                for entry in self.entries
            )

        async def call_tool(self, _remote_name, _arguments):
            return CallToolResult(content=[TextContent(text=self.server_id)])

        async def close(self) -> None:
            return None

    def factory(current):
        return FakeClient(current, fail=current.server_id == "mcp_bad")

    prepared = prepare_mcp_run(
        (bad, good),
        catalogs,
        workspace_id="ws_1",
        agent_run_id="arun_1",
        id_source=FixedIdSource(),
        client_factory=factory,
    )

    async def exercise():
        with pytest.raises(McpRuntimeError) as failure:
            await prepared.pool.call("mcp_bad", "echo", {})
        assert failure.value.code == "start_failed"
        healthy = await prepared.pool.call("mcp_good", "echo", {})
        await prepared.pool.close()
        return healthy

    healthy = asyncio.run(exercise())
    assert healthy.text_parts == ("mcp_good",)
    assert created == ["mcp_bad", "mcp_good"]

    policy = CapabilityPolicy(
        PermissionProfile(),
        WorkspaceCapability(workspace_id="ws_1", root=Path("/workspace")),
    )
    registry = ToolRegistry()
    register_mcp_tools(
        registry,
        prepared,
        capability_policy=policy,
        workspace_id="ws_1",
        agent_run_id="arun_1",
    )

    class Approval:
        async def request(self, _request):
            return ToolApprovalDecision(approved=True)

    executor = ToolExecutor(
        registry.snapshot(),
        make_run_policy(),
        approval_port=Approval(),
        capability_policy=policy,
    )
    outcome = asyncio.run(
        executor.execute(
            FunctionToolCall(id="call-bad", name="mcp__bad__echo", arguments='{"text":"x"}')
        )
    )
    assert outcome.disposition.value == "unknown"


def test_rehydrate_rejects_launch_fact_drift() -> None:
    definition = _definition()
    catalog = _catalog(definition)
    prepared = prepare_mcp_run(
        (definition,),
        {definition.server_id: catalog},
        workspace_id="ws_1",
        agent_run_id="arun_1",
        id_source=FixedIdSource(),
    )
    drifted = definition.model_copy(
        update={"workspace_visibility": McpWorkspaceVisibility.READ_ONLY}
    )

    with pytest.raises(McpRuntimeError) as failure:
        rehydrate_mcp_run(
            (drifted,),
            {definition.server_id: catalog},
            prepared.launch_snapshots,
            prepared.tool_snapshots,
            workspace_id="ws_1",
            agent_run_id="arun_1",
        )
    assert failure.value.code == "snapshot_drift"
