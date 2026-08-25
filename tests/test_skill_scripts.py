"""Constrained Skill script execution regressions."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from morrow.adapters.local.process import HostProcessAdapter
from morrow.adapters.skills.managed_store import ManagedSkillPackageStore, prepare_local_skill
from morrow.application.skills.scripts import (
    SkillScriptExecutionError,
    SkillScriptExecutionService,
    make_skill_script_tool,
)
from morrow.core.capabilities import (
    PermissionProfile,
    PolicyVerdict,
    ToolRunContext,
    WorkspaceCapability,
)
from morrow.core.models import FunctionToolCall
from morrow.core.skills.scripts import SkillScriptRequest, SkillScriptStatus
from morrow.core.skills.selection import SkillSelection
from morrow.core.skills.trust import SourceKind
from morrow.runtime.capabilities import CapabilityPolicy
from morrow.runtime.tools import ToolErrorCode, ToolExecutor, ToolRegistry
from morrow.testing import make_run_policy


class _Artifacts:
    def __init__(self) -> None:
        self.values: list[bytes] = []
        self.inputs = {"art_input_1": b"input-value\n"}

    def publish_bytes(self, content: bytes, **_kwargs):
        self.values.append(content)
        return SimpleNamespace(artifact_id=f"art_script_{len(self.values)}")

    def read(self, artifact_id: str, **_kwargs):
        return SimpleNamespace(content=self.inputs[artifact_id])


def _package(
    tmp_path: Path,
    *,
    script_source: str | None = None,
    requested_permissions: tuple[str, ...] = (),
) -> tuple[ManagedSkillPackageStore, str]:
    source = tmp_path / "source"
    (source / "scripts").mkdir(parents=True)
    (source / "SKILL.md").write_text(
        "---\nname: Script Skill\ndescription: bounded script\nversion: 0.1.0\n---\n",
        encoding="utf-8",
    )
    if requested_permissions:
        (source / "morrow.yaml").write_text(
            "morrow.requested_permissions:\n"
            + "".join(f"  - {permission}\n" for permission in requested_permissions),
            encoding="utf-8",
        )
    script = source / "scripts" / "run.py"
    script.write_text(script_source or _default_script(), encoding="utf-8")
    script.chmod(0o700)
    store = ManagedSkillPackageStore(tmp_path / "data")
    prepared = prepare_local_skill(source, source_kind=SourceKind.GENERATED, scope_id="ws_script")
    version = "skv_script12345678"
    store.publish(prepared, version_id=version)
    return store, version


def _default_script() -> str:
    return (
        "#!/usr/bin/env python3\n"
        "from pathlib import Path\n"
        "print('api_key: super-secret')\n"
        "Path('outputs/result.txt').write_text('token: super-secret\\n', encoding='utf-8')\n"
    )


def _request(package, version: str, *, output_paths: tuple[str, ...] = ()):
    return SkillScriptRequest(
        selection_id="ssel_script12345678",
        skill_id="script-skill",
        version_id=version,
        tree_digest=package.tree.tree_digest,
        source_kind=SourceKind.GENERATED,
        scope_id="ws_script",
        script_path="scripts/run.py",
        output_paths=output_paths,
    )


class _SelectionJournal:
    def __init__(self, selection: SkillSelection) -> None:
        self.selection = selection

    def get_skill_selection(self, _workspace_id: str, selection_id: str):
        return self.selection if selection_id == self.selection.selection_id else None


def _selection_journal(request: SkillScriptRequest) -> _SelectionJournal:
    return _SelectionJournal(
        SkillSelection(
            selection_id=request.selection_id,
            agent_run_id="arun_script12345678",
            skill_id=request.skill_id,
            version_id=request.version_id,
            scope="workspace" if request.scope_id else "global",
            scope_id=request.scope_id,
            source_kind=request.source_kind,
            tree_digest=request.tree_digest,
        )
    )


def test_skill_script_requires_sandbox_and_exact_frozen_package(tmp_path) -> None:
    store, version = _package(tmp_path)
    package = store.read_frozen_package(
        skill_id="script-skill",
        version_id=version,
        source_kind=SourceKind.GENERATED,
        scope_id="ws_script",
    )
    request = _request(package, version, output_paths=("result.txt",))
    unavailable = SkillScriptExecutionService(store, workspace_id="ws_script")
    with pytest.raises(SkillScriptExecutionError, match="sandbox"):
        unavailable.preflight(request, agent_run_id="arun_script12345678")

    artifacts = _Artifacts()
    service = SkillScriptExecutionService(
        store,
        workspace_id="ws_script",
        journal=_selection_journal(request),
        artifacts=artifacts,
        adapter_factory=lambda _root: HostProcessAdapter(),
        sandbox_available=True,
        secrets=("super-secret",),
    )
    plan = service.preflight(request, agent_run_id="arun_script12345678")
    result, fact = asyncio.run(
        service.execute(
            plan,
            run=ToolRunContext(run_id="arun_script12345678", session_id="ses_script12345678"),
            call_id="call_script",
            tool_name="run_skill_script",
            ordinal=1,
            approval_verdict=PolicyVerdict.ALLOW,
            result_limit=16 * 1024,
        )
    )
    assert result.status is SkillScriptStatus.SUCCEEDED
    assert result.stdout == "<redacted>\n"
    assert result.output_artifact_refs[0].artifact_id == "art_script_1"
    assert artifacts.values == [b"<redacted>\n"]
    assert fact.command_class == "skill_script"


def test_skill_script_requires_durable_selection_evidence(tmp_path) -> None:
    store, version = _package(tmp_path)
    package = store.read_frozen_package(
        skill_id="script-skill",
        version_id=version,
        source_kind=SourceKind.GENERATED,
        scope_id="ws_script",
    )
    request = _request(package, version)
    service = SkillScriptExecutionService(
        store,
        workspace_id="ws_script",
        adapter_factory=lambda _root: HostProcessAdapter(),
        sandbox_available=True,
    )
    with pytest.raises(SkillScriptExecutionError, match="selection evidence") as error:
        service.preflight(request, agent_run_id="arun_script12345678")
    assert error.value.code == "selection_missing"


@pytest.mark.asyncio
async def test_skill_script_tool_preserves_safe_preflight_diagnostic(tmp_path) -> None:
    store, version = _package(tmp_path)
    package = store.read_frozen_package(
        skill_id="script-skill",
        version_id=version,
        source_kind=SourceKind.GENERATED,
        scope_id="ws_script",
    )
    request = _request(package, version)
    service = SkillScriptExecutionService(
        store,
        workspace_id="ws_script",
        adapter_factory=lambda _root: HostProcessAdapter(),
        sandbox_available=True,
    )
    registry = ToolRegistry()
    registry.register(make_skill_script_tool(service))
    executor = ToolExecutor(
        registry.snapshot(),
        make_run_policy(),
        capability_policy=CapabilityPolicy(
            PermissionProfile(),
            WorkspaceCapability(workspace_id="ws_script", root=tmp_path),
            sandbox_available=True,
        ),
    )

    outcome = await executor.execute_with_context(
        FunctionToolCall(
            id="call_missing_selection",
            name="run_skill_script",
            arguments=request.model_dump_json(),
        ),
        run_context=ToolRunContext(run_id="arun_script12345678", session_id="ses_script12345678"),
        ordinal=1,
        total=1,
    )

    assert outcome.error_code is ToolErrorCode.NOT_FOUND
    error = json.loads(outcome.envelope)["error"]
    assert error["message"] == ("selection_missing: Skill selection evidence is unavailable")


def test_skill_script_rejects_undeclared_output_and_package_drift(tmp_path) -> None:
    store, version = _package(tmp_path)
    package = store.read_frozen_package(
        skill_id="script-skill",
        version_id=version,
        source_kind=SourceKind.GENERATED,
        scope_id="ws_script",
    )
    request = _request(package, version)
    service = SkillScriptExecutionService(
        store,
        workspace_id="ws_script",
        journal=_selection_journal(request),
        adapter_factory=lambda _root: HostProcessAdapter(),
        sandbox_available=True,
    )
    plan = service.preflight(request, agent_run_id="arun_script12345678")
    with pytest.raises(SkillScriptExecutionError, match="undeclared"):
        asyncio.run(
            service.execute(
                plan,
                run=ToolRunContext(run_id="arun_script12345678", session_id="ses_script12345678"),
                call_id="call_script",
                tool_name="run_skill_script",
                ordinal=1,
                approval_verdict=PolicyVerdict.ALLOW,
                result_limit=16 * 1024,
            )
        )

    package_root = (
        store.version_path(
            skill_id="script-skill",
            version_id=version,
            source_kind=SourceKind.GENERATED,
            scope_id="ws_script",
        )
        / "package"
        / "scripts"
        / "run.py"
    )
    package_root.write_text(
        package_root.read_text(encoding="utf-8") + "# drift\n", encoding="utf-8"
    )
    with pytest.raises(SkillScriptExecutionError, match="changed"):
        service.preflight(request, agent_run_id="arun_script12345678")


def test_skill_script_contract_rejects_shell_escape_and_overlapping_outputs() -> None:
    base = {
        "selection_id": "ssel_script12345678",
        "skill_id": "script-skill",
        "version_id": "skv_script12345678",
        "tree_digest": "0" * 64,
        "script_path": "scripts/run.py",
    }
    with pytest.raises(ValidationError):
        SkillScriptRequest.model_validate({**base, "shell": "cat secret"}, strict=True)
    with pytest.raises(ValidationError):
        SkillScriptRequest.model_validate({**base, "argv": ("bad\nvalue",)}, strict=True)
    with pytest.raises(ValidationError, match="overlap"):
        SkillScriptRequest.model_validate(
            {**base, "output_paths": ("result.txt", "result.txt/child")}, strict=True
        )


def _execute(service, plan, call_id: str):
    return asyncio.run(
        service.execute(
            plan,
            run=ToolRunContext(run_id="arun_script12345678", session_id="ses_script12345678"),
            call_id=call_id,
            tool_name="run_skill_script",
            ordinal=1,
            approval_verdict=PolicyVerdict.ALLOW,
            result_limit=16 * 1024,
        )
    )


def test_skill_script_rejects_root_escape_symlink_and_input_mutation(tmp_path) -> None:
    escape_store, escape_version = _package(
        tmp_path / "escape",
        script_source=(
            "#!/usr/bin/env python3\n"
            "from pathlib import Path\n"
            "Path('outside.txt').write_text('not an output', encoding='utf-8')\n"
        ),
    )
    escape_package = escape_store.read_frozen_package(
        skill_id="script-skill",
        version_id=escape_version,
        source_kind=SourceKind.GENERATED,
        scope_id="ws_script",
    )
    escape_request = _request(escape_package, escape_version)
    escape_service = SkillScriptExecutionService(
        escape_store,
        workspace_id="ws_script",
        journal=_selection_journal(escape_request),
        adapter_factory=lambda _root: HostProcessAdapter(),
        sandbox_available=True,
    )
    with pytest.raises(SkillScriptExecutionError, match="outside"):
        _execute(
            escape_service,
            escape_service.preflight(escape_request, agent_run_id="arun_script12345678"),
            "call_escape",
        )

    symlink_store, symlink_version = _package(
        tmp_path / "symlink",
        script_source=(
            "#!/usr/bin/env python3\n"
            "from pathlib import Path\n"
            "Path('outputs/result.txt').symlink_to('/etc/hosts')\n"
        ),
    )
    symlink_package = symlink_store.read_frozen_package(
        skill_id="script-skill",
        version_id=symlink_version,
        source_kind=SourceKind.GENERATED,
        scope_id="ws_script",
    )
    symlink_request = _request(symlink_package, symlink_version, output_paths=("result.txt",))
    symlink_service = SkillScriptExecutionService(
        symlink_store,
        workspace_id="ws_script",
        journal=_selection_journal(symlink_request),
        adapter_factory=lambda _root: HostProcessAdapter(),
        sandbox_available=True,
    )
    with pytest.raises(SkillScriptExecutionError, match="regular file"):
        _execute(
            symlink_service,
            symlink_service.preflight(symlink_request, agent_run_id="arun_script12345678"),
            "call_symlink",
        )

    input_store, input_version = _package(
        tmp_path / "input",
        script_source=(
            "#!/usr/bin/env python3\n"
            "from pathlib import Path\n"
            "path = Path('inputs/input-0.artifact')\n"
            "path.chmod(0o600)\n"
            "path.write_text('tampered', encoding='utf-8')\n"
        ),
    )
    input_package = input_store.read_frozen_package(
        skill_id="script-skill",
        version_id=input_version,
        source_kind=SourceKind.GENERATED,
        scope_id="ws_script",
    )
    input_request = _request(input_package, input_version).model_copy(
        update={"input_artifact_ids": ("art_input_1",)}
    )
    input_service = SkillScriptExecutionService(
        input_store,
        workspace_id="ws_script",
        journal=_selection_journal(input_request),
        artifacts=_Artifacts(),
        adapter_factory=lambda _root: HostProcessAdapter(),
        sandbox_available=True,
    )
    with pytest.raises(SkillScriptExecutionError, match="input Artifact"):
        _execute(
            input_service,
            input_service.preflight(input_request, agent_run_id="arun_script12345678"),
            "call_input",
        )


@pytest.mark.asyncio
async def test_skill_script_permission_request_is_denied_before_handler(tmp_path) -> None:
    store, version = _package(tmp_path, requested_permissions=("network",))
    package = store.read_frozen_package(
        skill_id="script-skill",
        version_id=version,
        source_kind=SourceKind.GENERATED,
        scope_id="ws_script",
    )
    request = _request(package, version)
    service = SkillScriptExecutionService(
        store,
        workspace_id="ws_script",
        journal=_selection_journal(request),
        adapter_factory=lambda _root: HostProcessAdapter(),
        sandbox_available=True,
    )
    registry = ToolRegistry()
    registry.register(make_skill_script_tool(service))
    executor = ToolExecutor(
        registry.snapshot(),
        make_run_policy(),
        capability_policy=CapabilityPolicy(
            PermissionProfile(),
            WorkspaceCapability(workspace_id="ws_script", root=tmp_path),
            sandbox_available=True,
        ),
    )
    outcome = await executor.execute_with_context(
        FunctionToolCall(
            id="call_permission",
            name="run_skill_script",
            arguments=request.model_dump_json(),
        ),
        run_context=ToolRunContext(run_id="arun_script12345678", session_id="ses_script12345678"),
        ordinal=1,
        total=1,
    )
    assert outcome.error_code is ToolErrorCode.PERMISSION_DENIED


def test_skill_script_timeout_is_bounded_and_cleans_temporary_state(tmp_path) -> None:
    store, version = _package(
        tmp_path / "timeout",
        script_source="#!/usr/bin/env python3\nimport time\ntime.sleep(0.2)\n",
    )
    package = store.read_frozen_package(
        skill_id="script-skill",
        version_id=version,
        source_kind=SourceKind.GENERATED,
        scope_id="ws_script",
    )
    request = _request(package, version).model_copy(update={"timeout_seconds": 0.01})
    service = SkillScriptExecutionService(
        store,
        workspace_id="ws_script",
        journal=_selection_journal(request),
        adapter_factory=lambda _root: HostProcessAdapter(),
        sandbox_available=True,
        temp_parent=tmp_path,
    )
    result, _fact = _execute(
        service,
        service.preflight(request, agent_run_id="arun_script12345678"),
        "call_timeout",
    )
    assert result.status is SkillScriptStatus.TIMED_OUT
    assert not any(path.name.startswith(".morrow-skill-script-") for path in tmp_path.iterdir())
