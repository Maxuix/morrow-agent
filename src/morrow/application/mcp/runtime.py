"""Lazy, per-AgentRun MCP runtime and ordinary ToolExecutor registrations."""

from __future__ import annotations

import asyncio
import inspect
import os
import stat
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from mcp.types import CallToolResult

from morrow.adapters.mcp.stdio_client import McpAdapterError, McpStdioClient
from morrow.application.mcp.policy import evaluate_mcp_policy
from morrow.application.mcp.results import McpResultNormalizer
from morrow.core.capabilities import (
    OperationIntent,
    OperationKind,
    PolicyDecision,
    PolicyVerdict,
    RiskFlag,
    ToolHandlerOutcome,
)
from morrow.core.domain import ArtifactReference, canonical_json_bytes, sha256_digest
from morrow.core.execution import (
    EffectClass,
    MissingCompletionPolicy,
    ToolExecutionDisposition,
    ToolRecoveryDeclaration,
)
from morrow.core.mcp import (
    MCP_SCHEMA_DIALECT,
    McpApprovalMode,
    McpCatalogSnapshot,
    McpCatalogStatus,
    McpCwdPolicy,
    McpLaunchRisk,
    McpLaunchSnapshot,
    McpNormalizedResult,
    McpReviewEvidence,
    McpServerDefinition,
    McpToolCatalogEntry,
    McpToolCatalogStatus,
    McpToolSnapshot,
    McpWorkspaceVisibility,
    mcp_server_config_digest,
    normalize_json_schema,
)
from morrow.core.models import ToolEffect
from morrow.core.ports import IdSource
from morrow.runtime.policy import ToolApproval, ToolExecutionPolicy
from morrow.runtime.tool_arguments import JsonSchemaArgumentsValidator
from morrow.runtime.tools import (
    ApprovalPreviewBudget,
    RegisteredTool,
    ToolErrorCode,
    ToolExecutionError,
    make_tool,
)


class McpRuntimeClient(Protocol):
    async def connect(self) -> object: ...

    async def list_tools(self) -> Sequence[object]: ...

    async def call_tool(self, remote_name: str, arguments: dict[str, object]) -> CallToolResult: ...

    async def close(self) -> None: ...


McpClientFactory = Callable[[McpServerDefinition], McpRuntimeClient | Awaitable[McpRuntimeClient]]
McpNormalizerFactory = Callable[[str], McpResultNormalizer]


class McpRuntimeError(RuntimeError):
    """Stable runtime boundary error; SDK and process details never escape."""

    def __init__(
        self, code: str, phase: str, message: str = "MCP runtime operation failed"
    ) -> None:
        super().__init__(message)
        self.code = code
        self.phase = phase
        self.message = message


def executable_identity_digest(executable: str) -> str:
    """Hash bounded executable identity facts, never executable content or errors."""

    path = Path(executable)
    try:
        link_info = path.lstat()
        payload = {
            "path": str(path),
            "link_mode": stat.S_IMODE(link_info.st_mode),
            "link_size": link_info.st_size,
            "link_mtime_ns": link_info.st_mtime_ns,
            "link_dev": link_info.st_dev,
            "link_ino": link_info.st_ino,
            "is_symlink": stat.S_ISLNK(link_info.st_mode),
        }
        if stat.S_ISLNK(link_info.st_mode):
            target = os.readlink(path)
            payload["link_target"] = target[:4096]
        info = path.stat()
        payload.update(
            {
                "size": info.st_size,
                "mtime_ns": info.st_mtime_ns,
                "mode": stat.S_IMODE(info.st_mode),
                "dev": info.st_dev,
                "ino": info.st_ino,
                "is_file": stat.S_ISREG(info.st_mode),
            }
        )
    except OSError:
        payload = {"path": str(path), "missing": True}
    return sha256_digest(canonical_json_bytes(payload))


def _toolset_digest(entries: Sequence[McpToolCatalogEntry]) -> str:
    payload = [
        {
            "remote_name": entry.remote_name,
            "local_name": entry.local_name,
            "input_schema_digest": entry.input_schema_digest,
            "output_schema_digest": entry.output_schema_digest,
            "effect": entry.risk_mapping.effect.value if entry.risk_mapping else None,
            "approval": entry.risk_mapping.approval.value if entry.risk_mapping else None,
        }
        for entry in sorted(entries, key=lambda item: item.remote_name)
        if entry.status is McpToolCatalogStatus.READY and entry.risk_mapping is not None
    ]
    return sha256_digest(canonical_json_bytes(payload))


def _launch_risk_flags(
    definition: McpServerDefinition, snapshot: McpLaunchSnapshot
) -> tuple[RiskFlag, ...]:
    flags: list[RiskFlag] = []
    if snapshot.network_risk:
        flags.append(RiskFlag.NETWORK)
    if snapshot.credential_risk:
        flags.append(RiskFlag.CREDENTIAL_ACCESS)
    if snapshot.outside_workspace_risk:
        flags.append(RiskFlag.OUTSIDE_WORKSPACE)
    if McpLaunchRisk.LOOPBACK in definition.requested_launch_risks:
        flags.append(RiskFlag.LOOPBACK)
    if McpLaunchRisk.PRIVILEGE_ESCALATION in definition.requested_launch_risks:
        flags.append(RiskFlag.PRIVILEGE_ESCALATION)
    if McpLaunchRisk.GIT_WRITE in definition.requested_launch_risks:
        flags.append(RiskFlag.GIT_WRITE)
    return tuple(dict.fromkeys(flags))


def _launch_intent(definition: McpServerDefinition, snapshot: McpLaunchSnapshot) -> OperationIntent:
    kind = (
        OperationKind.EXTERNAL_EFFECT
        if McpLaunchRisk.EXTERNAL_EFFECT in definition.requested_launch_risks
        else OperationKind.INTERNAL_READ
    )
    return OperationIntent(
        kind=kind,
        risk_flags=_launch_risk_flags(definition, snapshot),
        preview_summary=(f"启动 MCP Server {definition.server_id}",),
    )


def _tool_intent(entry: McpToolCatalogEntry, launch_risks: tuple[RiskFlag, ...]) -> OperationIntent:
    assert entry.risk_mapping is not None
    effect = entry.risk_mapping.effect
    if effect is ToolEffect.PERSISTENT_WRITE:
        kind = OperationKind.EXTERNAL_EFFECT
    elif effect is ToolEffect.SESSION_WRITE:
        kind = OperationKind.WORKSPACE_WRITE
    else:
        kind = OperationKind.WORKSPACE_READ
    flags = list(launch_risks)
    if effect is ToolEffect.PERSISTENT_WRITE:
        flags.append(RiskFlag.MUTATION_APPROVAL_REQUIRED)
    return OperationIntent(
        kind=kind,
        effect=effect,
        risk_flags=tuple(dict.fromkeys(flags)),
        preview_summary=(f"调用 MCP 工具 {entry.remote_name}",),
    )


def _recovery_declaration(entry: McpToolCatalogEntry) -> ToolRecoveryDeclaration:
    assert entry.risk_mapping is not None
    return ToolRecoveryDeclaration(
        tool_name=entry.local_name,
        effect_class=(
            EffectClass.BOUNDED_EXTERNAL_READ
            if entry.risk_mapping.effect is ToolEffect.NONE
            else EffectClass.UNCONFINED_EXTERNAL_EFFECT
        ),
        missing_handler_completed=MissingCompletionPolicy.OUTCOME_UNKNOWN,
    )


@dataclass(frozen=True)
class McpToolBinding:
    definition: McpServerDefinition
    catalog: McpCatalogSnapshot
    launch_snapshot: McpLaunchSnapshot
    tool_snapshot: McpToolSnapshot
    entry: McpToolCatalogEntry
    launch_intent: OperationIntent
    tool_intent: OperationIntent
    review: McpReviewEvidence | None = None


class McpRunBridge:
    """One server connection owned by one LazyMcpRunPool."""

    def __init__(
        self,
        definition: McpServerDefinition,
        catalog: McpCatalogSnapshot,
        *,
        client_factory: McpClientFactory,
        normalizer: McpResultNormalizer,
        expected_executable_digest: str,
    ) -> None:
        self.definition = definition
        self.catalog = catalog
        self.client_factory = client_factory
        self.normalizer = normalizer
        self.expected_executable_digest = expected_executable_digest
        self.client: McpRuntimeClient | None = None
        self.started = False
        self.degraded = False
        self._closing = False

    async def start(self) -> None:
        if self.started:
            return
        if self.degraded:
            raise McpRuntimeError("server_degraded", "start")
        try:
            if executable_identity_digest(self.definition.executable) != (
                self.expected_executable_digest
            ):
                raise McpRuntimeError("executable_drift", "start")
            value = self.client_factory(self.definition)
            client = await value if inspect.isawaitable(value) else value
            self.client = client
            await client.connect()
            remote_tools = tuple(await client.list_tools())
            self._verify_catalog(remote_tools)
            self.started = True
        except asyncio.CancelledError:
            await self._close_best_effort()
            raise
        except McpAdapterError as exc:
            self.degraded = True
            await self._close_best_effort()
            raise McpRuntimeError(exc.code, exc.phase) from exc
        except McpRuntimeError:
            self.degraded = True
            await self._close_best_effort()
            raise
        except Exception as exc:
            self.degraded = True
            await self._close_best_effort()
            raise McpRuntimeError("start_failed", "start") from exc

    def _verify_catalog(self, remote_tools: Sequence[object]) -> None:
        expected = {entry.remote_name: entry for entry in self.catalog.tools}
        actual = {str(getattr(item, "name", "")): item for item in remote_tools}
        if set(actual) != set(expected):
            raise McpRuntimeError("catalog_drift", "catalog")
        for name, entry in expected.items():
            item = actual[name]
            if entry.status is not McpToolCatalogStatus.READY or entry.input_schema_digest is None:
                continue
            raw_schema = getattr(item, "input_schema", None)
            if not isinstance(raw_schema, dict):
                raise McpRuntimeError("catalog_drift", "catalog")
            try:
                if (
                    entry.input_schema_digest is not None
                    and normalize_json_schema(raw_schema).digest != entry.input_schema_digest
                ):
                    raise McpRuntimeError("catalog_drift", "catalog")
                if entry.output_schema_digest is not None:
                    output_schema = getattr(item, "output_schema", None)
                    if (
                        not isinstance(output_schema, dict)
                        or normalize_json_schema(output_schema, label="output").digest
                        != entry.output_schema_digest
                    ):
                        raise McpRuntimeError("catalog_drift", "catalog")
            except (TypeError, ValueError):
                raise McpRuntimeError("catalog_drift", "catalog") from None

    async def call(self, remote_name: str, arguments: dict[str, object]) -> McpNormalizedResult:
        entry = next((item for item in self.catalog.tools if item.remote_name == remote_name), None)
        if (
            entry is None
            or entry.status is not McpToolCatalogStatus.READY
            or entry.risk_mapping is None
            or not entry.risk_mapping.enabled
            or entry.risk_mapping.approval is McpApprovalMode.DENY
        ):
            raise McpRuntimeError("tool_not_allowed", "call")
        if not isinstance(arguments, dict):
            raise McpRuntimeError("invalid_arguments", "call")
        await self.start()
        client = self.client
        if client is None:
            raise McpRuntimeError("not_started", "call")
        try:
            result = await asyncio.wait_for(
                client.call_tool(remote_name, arguments),
                self.definition.timeout_ms / 1000,
            )
            return self.normalizer.normalize(result)
        except asyncio.CancelledError:
            await asyncio.shield(self.close())
            raise
        except McpAdapterError as exc:
            self.degraded = True
            await self._close_best_effort()
            raise McpRuntimeError(exc.code, exc.phase) from exc
        except TimeoutError as exc:
            self.degraded = True
            await self._close_best_effort()
            raise McpRuntimeError("timeout", "call") from exc
        except McpRuntimeError:
            self.degraded = True
            await self._close_best_effort()
            raise
        except Exception as exc:
            self.degraded = True
            await self._close_best_effort()
            raise McpRuntimeError("call_failed", "call") from exc

    async def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        client, self.client = self.client, None
        self.started = False
        if client is None:
            return
        try:
            await client.close()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise McpRuntimeError("close_failed", "close") from exc

    async def _close_best_effort(self) -> None:
        try:
            await self.close()
        except McpRuntimeError:
            return


class LazyMcpRunPool:
    """Lazy server pool scoped to exactly one prepared AgentRun."""

    def __init__(
        self,
        definitions: Mapping[str, McpServerDefinition],
        catalogs: Mapping[str, McpCatalogSnapshot],
        launch_snapshots: Mapping[str, McpLaunchSnapshot],
        *,
        client_factory: McpClientFactory | None = None,
        normalizer_factory: McpNormalizerFactory | None = None,
    ) -> None:
        self.definitions = dict(definitions)
        self.catalogs = dict(catalogs)
        self.launch_snapshots = dict(launch_snapshots)
        self.client_factory = client_factory or (lambda definition: McpStdioClient(definition))
        self.normalizer_factory = normalizer_factory or (lambda _server_id: McpResultNormalizer())
        self._bridges: dict[str, McpRunBridge] = {}
        self._degraded: set[str] = set()
        self._lock = asyncio.Lock()
        self._closed = False

    async def _bridge(self, server_id: str) -> McpRunBridge:
        if self._closed:
            raise McpRuntimeError("pool_closed", "start")
        if server_id in self._degraded:
            raise McpRuntimeError("server_degraded", "start")
        bridge = self._bridges.get(server_id)
        if bridge is not None:
            return bridge
        definition = self.definitions.get(server_id)
        catalog = self.catalogs.get(server_id)
        launch = self.launch_snapshots.get(server_id)
        if definition is None or catalog is None or launch is None:
            self._degraded.add(server_id)
            raise McpRuntimeError("snapshot_missing", "start")
        bridge = McpRunBridge(
            definition,
            catalog,
            client_factory=self.client_factory,
            normalizer=self.normalizer_factory(server_id),
            expected_executable_digest=launch.executable_digest,
        )
        try:
            await bridge.start()
        except McpRuntimeError:
            self._degraded.add(server_id)
            raise
        self._bridges[server_id] = bridge
        return bridge

    async def call(
        self, server_id: str, remote_name: str, arguments: dict[str, object]
    ) -> McpNormalizedResult:
        async with self._lock:
            bridge = await self._bridge(server_id)
        try:
            return await bridge.call(remote_name, arguments)
        except McpRuntimeError:
            self._degraded.add(server_id)
            raise

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        bridges = tuple(self._bridges.values())
        self._bridges.clear()
        for bridge in bridges:
            try:
                await bridge.close()
            except McpRuntimeError:
                self._degraded.add(bridge.definition.server_id)

    def status_facts(self) -> dict[str, object]:
        return {
            "configured_servers": len(self.definitions),
            "started_servers": sum(bridge.started for bridge in self._bridges.values()),
            "degraded_servers": tuple(sorted(self._degraded)),
            "closed": self._closed,
        }


@dataclass(frozen=True)
class PreparedMcpRun:
    definitions: tuple[McpServerDefinition, ...]
    catalogs: tuple[McpCatalogSnapshot, ...]
    launch_snapshots: tuple[McpLaunchSnapshot, ...]
    tool_snapshots: tuple[McpToolSnapshot, ...]
    bindings: tuple[McpToolBinding, ...]
    pool: LazyMcpRunPool
    review_evidence: tuple[McpReviewEvidence, ...] = ()

    @property
    def snapshot_ids(self) -> tuple[str, ...]:
        return tuple(snapshot.launch_snapshot_id for snapshot in self.launch_snapshots)


def prepare_mcp_run(
    definitions: Sequence[McpServerDefinition],
    catalogs: Mapping[str, McpCatalogSnapshot],
    *,
    workspace_id: str,
    agent_run_id: str,
    id_source: IdSource,
    client_factory: McpClientFactory | None = None,
    normalizer_factory: McpNormalizerFactory | None = None,
    reviews: Mapping[str, McpReviewEvidence] | None = None,
) -> PreparedMcpRun:
    """Freeze enabled definitions/Catalogs into immutable run-bound evidence."""

    selected = tuple(
        sorted(
            (definition for definition in definitions if definition.enabled),
            key=lambda item: item.server_id,
        )
    )
    if len({definition.server_id for definition in selected}) != len(selected):
        raise McpRuntimeError("duplicate_server", "prepare")
    launch_snapshots: list[McpLaunchSnapshot] = []
    tool_snapshots: list[McpToolSnapshot] = []
    bindings: list[McpToolBinding] = []
    selected_catalogs: list[McpCatalogSnapshot] = []
    definition_map: dict[str, McpServerDefinition] = {}
    catalog_map: dict[str, McpCatalogSnapshot] = {}
    launch_map: dict[str, McpLaunchSnapshot] = {}

    for definition in selected:
        catalog = catalogs.get(definition.server_id)
        if (
            catalog is None
            or catalog.server_id != definition.server_id
            or catalog.status is not McpCatalogStatus.READY
        ):
            raise McpRuntimeError("catalog_unavailable", "prepare")
        if definition.workspace_visibility is not McpWorkspaceVisibility.READ_WRITE:
            raise McpRuntimeError("workspace_visibility_unsupported", "prepare")
        allowed_entries = tuple(
            entry
            for entry in catalog.tools
            if entry.status is McpToolCatalogStatus.READY
            and entry.risk_mapping is not None
            and entry.risk_mapping.enabled
            and entry.remote_name in definition.tool_policy.allowlist
            and entry.risk_mapping.approval is not McpApprovalMode.DENY
        )
        if not allowed_entries:
            raise McpRuntimeError("toolset_empty", "prepare")
        toolset_digest = _toolset_digest(allowed_entries)
        launch = McpLaunchSnapshot(
            launch_snapshot_id=id_source.new_id("mcp_launch"),
            workspace_id=workspace_id,
            agent_run_id=agent_run_id,
            server_id=definition.server_id,
            config_revision=definition.revision,
            config_digest=mcp_server_config_digest(definition),
            catalog_revision=catalog.revision,
            catalog_digest=catalog.catalog_digest,
            transport=definition.transport,
            argv_digest=definition.argv_digest,
            executable_digest=executable_identity_digest(definition.executable),
            cwd_policy=definition.cwd_policy,
            workspace_visibility=definition.workspace_visibility,
            network_risk=McpLaunchRisk.NETWORK in definition.requested_launch_risks,
            credential_risk=bool(definition.credential_refs)
            or McpLaunchRisk.CREDENTIALS in definition.requested_launch_risks,
            outside_workspace_risk=(
                McpLaunchRisk.OUTSIDE_WORKSPACE in definition.requested_launch_risks
                or definition.cwd_policy is McpCwdPolicy.ABSOLUTE
            ),
            allowlisted_remote_tools=tuple(entry.remote_name for entry in allowed_entries),
            toolset_digest=toolset_digest,
        )
        launch_intent = _launch_intent(definition, launch)
        launch_snapshots.append(launch)
        definition_map[definition.server_id] = definition
        catalog_map[definition.server_id] = catalog
        launch_map[definition.server_id] = launch
        selected_catalogs.append(catalog)
        review = (reviews or {}).get(definition.server_id)
        for entry in allowed_entries:
            assert entry.risk_mapping is not None
            recovery = _recovery_declaration(entry)
            tool_snapshot = McpToolSnapshot(
                tool_snapshot_id=id_source.new_id("mcp_tool"),
                launch_snapshot_id=launch.launch_snapshot_id,
                agent_run_id=agent_run_id,
                server_id=definition.server_id,
                remote_name=entry.remote_name,
                local_name=entry.local_name,
                schema_dialect=entry.schema_dialect or MCP_SCHEMA_DIALECT,
                input_schema_digest=entry.input_schema_digest or "0" * 64,
                output_schema_digest=entry.output_schema_digest,
                effect=entry.risk_mapping.effect,
                approval=entry.risk_mapping.approval,
                catalog_revision=catalog.revision,
                recovery_declaration=recovery.model_dump(mode="json"),
            )
            tool_snapshots.append(tool_snapshot)
            bindings.append(
                McpToolBinding(
                    definition=definition,
                    catalog=catalog,
                    launch_snapshot=launch,
                    tool_snapshot=tool_snapshot,
                    entry=entry,
                    launch_intent=launch_intent,
                    tool_intent=_tool_intent(entry, launch_intent.risk_flags),
                    review=review,
                )
            )

    pool = LazyMcpRunPool(
        definition_map,
        catalog_map,
        launch_map,
        client_factory=client_factory,
        normalizer_factory=normalizer_factory,
    )
    return PreparedMcpRun(
        definitions=selected,
        catalogs=tuple(selected_catalogs),
        launch_snapshots=tuple(launch_snapshots),
        tool_snapshots=tuple(tool_snapshots),
        bindings=tuple(bindings),
        pool=pool,
        review_evidence=tuple((reviews or {}).values()),
    )


def rehydrate_mcp_run(
    definitions: Sequence[McpServerDefinition],
    catalogs: Mapping[str, McpCatalogSnapshot],
    launch_snapshots: Sequence[McpLaunchSnapshot],
    tool_snapshots: Sequence[McpToolSnapshot],
    *,
    workspace_id: str,
    agent_run_id: str,
    client_factory: McpClientFactory | None = None,
    normalizer_factory: McpNormalizerFactory | None = None,
    reviews: Mapping[str, McpReviewEvidence] | None = None,
) -> PreparedMcpRun:
    """Rebuild a pool only when current launch/catalog facts match stored rows."""

    definition_map = {definition.server_id: definition for definition in definitions}
    launch_map = {snapshot.server_id: snapshot for snapshot in launch_snapshots}
    if len(launch_map) != len(launch_snapshots) or not launch_snapshots:
        raise McpRuntimeError("snapshot_invalid", "rehydrate")
    if any(
        snapshot.workspace_id != workspace_id or snapshot.agent_run_id != agent_run_id
        for snapshot in launch_snapshots
    ):
        raise McpRuntimeError("snapshot_subject_mismatch", "rehydrate")
    catalog_map: dict[str, McpCatalogSnapshot] = {}
    bindings: list[McpToolBinding] = []
    selected_definitions: list[McpServerDefinition] = []
    selected_catalogs: list[McpCatalogSnapshot] = []
    by_launch: dict[str, list[McpToolSnapshot]] = {}
    for snapshot in tool_snapshots:
        if snapshot.agent_run_id != agent_run_id:
            raise McpRuntimeError("snapshot_subject_mismatch", "rehydrate")
        by_launch.setdefault(snapshot.launch_snapshot_id, []).append(snapshot)
    if set(by_launch) - {snapshot.launch_snapshot_id for snapshot in launch_snapshots}:
        raise McpRuntimeError("snapshot_drift", "rehydrate")

    for review in (reviews or {}).values():
        launch = launch_map.get(review.server_id)
        if launch is None or not review.matches(
            workspace_id=workspace_id,
            agent_run_id=agent_run_id,
            server_id=review.server_id,
            config_digest=launch.config_digest,
            catalog_digest=launch.catalog_digest or "0" * 64,
            toolset_digest=launch.toolset_digest or "0" * 64,
        ):
            raise McpRuntimeError("review_drift", "rehydrate")

    for launch in launch_snapshots:
        definition = definition_map.get(launch.server_id)
        catalog = catalogs.get(launch.server_id)
        if (
            definition is None
            or not definition.enabled
            or catalog is None
            or catalog.server_id != launch.server_id
            or catalog.status is not McpCatalogStatus.READY
            or launch.config_digest != mcp_server_config_digest(definition)
            or launch.config_revision != definition.revision
            or launch.argv_digest != definition.argv_digest
            or launch.executable_digest != executable_identity_digest(definition.executable)
            or launch.catalog_revision != catalog.revision
            or launch.catalog_digest != catalog.catalog_digest
            or launch.transport is not definition.transport
            or launch.cwd_policy is not definition.cwd_policy
            or launch.workspace_visibility is not definition.workspace_visibility
            or launch.network_risk != (McpLaunchRisk.NETWORK in definition.requested_launch_risks)
            or launch.credential_risk
            != (
                bool(definition.credential_refs)
                or McpLaunchRisk.CREDENTIALS in definition.requested_launch_risks
            )
            or launch.outside_workspace_risk
            != (
                McpLaunchRisk.OUTSIDE_WORKSPACE in definition.requested_launch_risks
                or definition.cwd_policy is McpCwdPolicy.ABSOLUTE
            )
            or launch.toolset_digest is None
        ):
            raise McpRuntimeError("snapshot_drift", "rehydrate")
        allowed_entries = tuple(
            entry
            for entry in catalog.tools
            if entry.status is McpToolCatalogStatus.READY
            and entry.risk_mapping is not None
            and entry.risk_mapping.enabled
            and entry.remote_name in definition.tool_policy.allowlist
            and entry.risk_mapping.approval is not McpApprovalMode.DENY
        )
        if (
            tuple(entry.remote_name for entry in allowed_entries) != launch.allowlisted_remote_tools
            or _toolset_digest(allowed_entries) != launch.toolset_digest
        ):
            raise McpRuntimeError("snapshot_drift", "rehydrate")
        stored_items = tuple(by_launch.get(launch.launch_snapshot_id, ()))
        stored_tools = {item.remote_name: item for item in stored_items}
        if set(stored_tools) != {entry.remote_name for entry in allowed_entries}:
            raise McpRuntimeError("snapshot_drift", "rehydrate")
        if len(stored_tools) != len(stored_items):
            raise McpRuntimeError("snapshot_drift", "rehydrate")
        launch_intent = _launch_intent(definition, launch)
        selected_definitions.append(definition)
        selected_catalogs.append(catalog)
        catalog_map[definition.server_id] = catalog
        for entry in allowed_entries:
            stored = stored_tools[entry.remote_name]
            if (
                stored.server_id != definition.server_id
                or stored.local_name != entry.local_name
                or stored.input_schema_digest != entry.input_schema_digest
                or stored.output_schema_digest != entry.output_schema_digest
                or stored.effect != entry.risk_mapping.effect
                or stored.approval != entry.risk_mapping.approval
                or stored.catalog_revision != catalog.revision
                or stored.schema_dialect != (entry.schema_dialect or MCP_SCHEMA_DIALECT)
                or stored.recovery_declaration
                != _recovery_declaration(entry).model_dump(mode="json")
            ):
                raise McpRuntimeError("snapshot_drift", "rehydrate")
            bindings.append(
                McpToolBinding(
                    definition=definition,
                    catalog=catalog,
                    launch_snapshot=launch,
                    tool_snapshot=stored,
                    entry=entry,
                    launch_intent=launch_intent,
                    tool_intent=_tool_intent(entry, launch_intent.risk_flags),
                    review=(reviews or {}).get(definition.server_id),
                )
            )
    pool = LazyMcpRunPool(
        definitions={item.server_id: item for item in selected_definitions},
        catalogs=catalog_map,
        launch_snapshots=launch_map,
        client_factory=client_factory,
        normalizer_factory=normalizer_factory,
    )
    return PreparedMcpRun(
        definitions=tuple(selected_definitions),
        catalogs=tuple(selected_catalogs),
        launch_snapshots=tuple(launch_snapshots),
        tool_snapshots=tuple(tool_snapshots),
        bindings=tuple(bindings),
        pool=pool,
        review_evidence=tuple((reviews or {}).values()),
    )


def register_mcp_tools(
    registry,
    prepared: PreparedMcpRun,
    *,
    capability_policy,
    workspace_id: str,
    agent_run_id: str,
) -> tuple[RegisteredTool, ...]:
    """Register MCP handlers through the same validator/executor seams."""

    def artifact_references(result: McpNormalizedResult) -> tuple[ArtifactReference, ...]:
        refs: list[ArtifactReference] = []
        for item in (*result.image_refs, *result.audio_refs):
            reference = ArtifactReference(artifact_id=item.artifact_id, role=item.role)
            if reference not in refs:
                refs.append(reference)
        for item in result.embedded_resource_refs:
            reference = ArtifactReference(
                artifact_id=item.artifact_id,
                role="embedded_resource",
            )
            if reference not in refs:
                refs.append(reference)
        return tuple(refs)

    registered: list[RegisteredTool] = []
    for binding in prepared.bindings:
        schema = binding.entry.input_schema
        if not isinstance(schema, dict):
            continue
        try:
            validator = JsonSchemaArgumentsValidator(schema)
        except (TypeError, ValueError):
            raise McpRuntimeError("schema_invalid", "prepare") from None

        async def handler(arguments, context, *, current=binding):
            if not isinstance(arguments, dict):
                raise ToolExecutionError(
                    code=ToolErrorCode.INVALID_ARGUMENTS,
                    message="MCP 工具参数必须是对象",
                )
            try:
                normalized = await prepared.pool.call(
                    current.definition.server_id,
                    current.entry.remote_name,
                    arguments,
                )
                refs = artifact_references(normalized)
                return ToolHandlerOutcome(
                    payload=normalized.model_dump(mode="json"),
                    artifact_refs=refs,
                    mcp_result_artifact_refs=refs,
                )
            except McpRuntimeError as exc:
                raise ToolExecutionError(
                    ToolErrorCode.EXECUTION_FAILED,
                    "MCP 工具调用结果未知",
                    disposition=ToolExecutionDisposition.UNKNOWN,
                ) from exc

        def intent(_arguments, _context, *, current=binding):
            return current.tool_intent

        def policy(_intent, _context, allow_unconfined_host, *, current=binding):
            decision = evaluate_mcp_policy(
                capability_policy,
                launch_intent=current.launch_intent,
                tool_intent=current.tool_intent,
                review=current.review,
                workspace_id=workspace_id,
                agent_run_id=agent_run_id,
                server_id=current.definition.server_id,
                config_digest=current.launch_snapshot.config_digest,
                catalog_digest=current.launch_snapshot.catalog_digest or "0" * 64,
                toolset_digest=current.launch_snapshot.toolset_digest or "0" * 64,
                allow_unconfined_host=allow_unconfined_host,
            )
            if (
                decision.verdict.value == "allow"
                and current.tool_snapshot.approval is McpApprovalMode.REQUIRE_APPROVAL
            ):
                return PolicyDecision(
                    verdict=PolicyVerdict.REQUIRE_APPROVAL,
                    reason_codes=("mcp_tool_review_required",),
                    preview_summary=decision.preview_summary,
                )
            return decision

        tool = make_tool(
            name=binding.entry.local_name,
            description=binding.entry.description or "MCP tool",
            arguments_validator=validator,
            handler=handler,
            execution_policy=ToolExecutionPolicy(
                effect=binding.tool_snapshot.effect,
                approval=(
                    ToolApproval.REQUIRED
                    if binding.tool_snapshot.approval is McpApprovalMode.REQUIRE_APPROVAL
                    else ToolApproval.NEVER
                ),
            ),
            intent_resolver=intent,
            policy_resolver=policy,
            context_handler=handler,
            context_approval_preview=lambda _arguments, _context, current=binding: (
                f"MCP Server {current.definition.server_id}",
                f"remote tool {current.entry.remote_name}",
            ),
            approval_preview_budget=ApprovalPreviewBudget(
                max_lines=4, max_line_chars=120, max_bytes=600
            ),
            recovery_declaration=ToolRecoveryDeclaration.model_validate(
                binding.tool_snapshot.recovery_declaration
            ),
        )
        registry.register(tool)
        registered.append(tool)
    return tuple(registered)


__all__ = [
    "LazyMcpRunPool",
    "McpClientFactory",
    "McpRuntimeError",
    "McpRunBridge",
    "McpToolBinding",
    "PreparedMcpRun",
    "executable_identity_digest",
    "prepare_mcp_run",
    "rehydrate_mcp_run",
    "register_mcp_tools",
]
