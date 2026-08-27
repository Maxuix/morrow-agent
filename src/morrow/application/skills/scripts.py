"""Constrained execution of one frozen managed Skill script."""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import stat
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from morrow.adapters.local.process import ProcessAdapterError
from morrow.adapters.skills.managed_store import (
    FrozenSkillPackage,
    ManagedSkillPackageStore,
    SkillPackageError,
)
from morrow.adapters.skills.tree import PackageTreeError, build_canonical_tree
from morrow.core.artifacts import (
    ArtifactKind,
    ArtifactProvenanceKind,
    ArtifactProvenanceRef,
    ArtifactSensitivity,
)
from morrow.core.capabilities import (
    OperationIntent,
    OperationKind,
    RiskFlag,
    ToolCallContext,
    ToolHandlerOutcome,
    ToolRunContext,
)
from morrow.core.diagnostics import PublicDiagnosticError
from morrow.core.domain import ArtifactReference
from morrow.core.execution import tool_declaration
from morrow.core.local_tools import CommandStatus
from morrow.core.models import ToolEffect
from morrow.core.skills.scripts import (
    SCRIPT_MAX_ENV_NAMES,
    SCRIPT_MAX_INPUT_ARTIFACTS,
    SCRIPT_MAX_TIMEOUT_SECONDS,
    SCRIPT_OUTPUT_FILE_MAX_BYTES,
    SCRIPT_PATH_MAX_CHARS,
    SCRIPT_STDIO_MAX_CHARS,
    SCRIPT_TOTAL_OUTPUT_BYTES,
    SkillScriptRequest,
    SkillScriptResult,
    SkillScriptStatus,
)
from morrow.core.skills.selection import SkillSelection
from morrow.runtime.tool_arguments import SCHEMA_DIALECT
from morrow.runtime.tools import (
    ApprovalPreviewBudget,
    RegisteredTool,
    ToolApproval,
    ToolErrorCode,
    ToolExecutionError,
    ToolExecutionPolicy,
    make_tool,
)
from morrow.services.process import SecretRedactor


class SkillScriptExecutionError(PublicDiagnosticError):
    """A Skill script failure with a reviewed, user-safe diagnostic."""


_SKILL_RELATIVE_PATH_PATTERN = (
    r"^(?!/)(?!.*\\)(?!.*\x00)(?!.*(?:^|/)\.{1,2}(?:/|$))"
    r"(?!.*[^\x20-\x7e])[\s\S]+$"
)
_SKILL_SCRIPT_PATH_PATTERN = (
    r"^scripts/(?!$)(?!.*\\)(?!.*\x00)(?!.*(?:^|/)\.{1,2}(?:/|$))"
    r"(?!.*[^\x20-\x7e])[\s\S]+$"
)
_SKILL_ARG_PATTERN = r"^(?!.*\x00)(?!.*[\r\n])[\s\S]+$"
_SKILL_ID_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,63}$"
_SKV_ID_PATTERN = r"^skv_[A-Za-z0-9_-]{8,64}$"
_SELECTION_ID_PATTERN = r"^ssel_[A-Za-z0-9_-]{1,123}$"
_WORKSPACE_ID_PATTERN = r"^ws_[A-Za-z0-9_-]{1,125}$"
_ARTIFACT_ID_PATTERN = r"^art_[A-Za-z0-9_-]{1,124}$"
_DIGEST_PATTERN = r"^[0-9a-f]{64}$"
_ALLOWED_ENV_NAMES = frozenset({"LANG", "LC_ALL", "TZ", "PYTHONIOENCODING", "PYTHONUTF8"})
_SKILL_PROVIDER_ARG_MAX_CHARS = 256

SKILL_SCRIPT_PROVIDER_SCHEMA = {
    "$schema": SCHEMA_DIALECT,
    "type": "object",
    "properties": {
        "selection_id": {"type": "string", "pattern": _SELECTION_ID_PATTERN},
        "skill_id": {"type": "string", "pattern": _SKILL_ID_PATTERN},
        "version_id": {"type": "string", "pattern": _SKV_ID_PATTERN},
        "tree_digest": {"type": "string", "pattern": _DIGEST_PATTERN},
        "source_kind": {
            "type": "string",
            "enum": ["builtin", "user_authored", "generated", "imported"],
        },
        "scope_id": {
            "anyOf": [
                {"type": "string", "pattern": _WORKSPACE_ID_PATTERN},
                {"type": "null"},
            ]
        },
        "script_path": {
            "type": "string",
            "minLength": len("scripts/") + 1,
            "maxLength": SCRIPT_PATH_MAX_CHARS,
            "pattern": _SKILL_SCRIPT_PATH_PATTERN,
        },
        "argv": {
            "type": "array",
            "maxItems": 16,
            "items": {
                "type": "string",
                "minLength": 1,
                "maxLength": _SKILL_PROVIDER_ARG_MAX_CHARS,
                "pattern": _SKILL_ARG_PATTERN,
            },
        },
        "environment_names": {
            "type": "array",
            "maxItems": SCRIPT_MAX_ENV_NAMES,
            "uniqueItems": True,
            "items": {"type": "string", "enum": sorted(_ALLOWED_ENV_NAMES)},
        },
        "input_artifact_ids": {
            "type": "array",
            "maxItems": SCRIPT_MAX_INPUT_ARTIFACTS,
            "uniqueItems": True,
            "items": {"type": "string", "pattern": _ARTIFACT_ID_PATTERN},
        },
        "output_paths": {
            "type": "array",
            "maxItems": 1,
            "uniqueItems": True,
            "items": {
                "type": "string",
                "minLength": 1,
                "maxLength": SCRIPT_PATH_MAX_CHARS,
                "pattern": _SKILL_RELATIVE_PATH_PATTERN,
            },
        },
        "timeout_seconds": {
            "type": "number",
            "exclusiveMinimum": 0,
            "maximum": SCRIPT_MAX_TIMEOUT_SECONDS,
        },
    },
    "required": ["selection_id", "skill_id", "version_id", "tree_digest", "script_path"],
    "additionalProperties": False,
}


@dataclass(frozen=True, slots=True)
class SkillScriptPlan:
    request: SkillScriptRequest
    selection: SkillSelection
    package: FrozenSkillPackage
    script_sha256: str
    risk_flags: tuple[RiskFlag, ...]


class SkillScriptExecutionService:
    """Execute only a selected version from a private, verified package copy."""

    _SAFE_ENV_NAMES = _ALLOWED_ENV_NAMES

    def __init__(
        self,
        package_store: ManagedSkillPackageStore,
        *,
        workspace_id: str,
        journal=None,
        artifacts=None,
        adapter_factory: Callable[[Path], object] | None = None,
        sandbox_available: bool = False,
        environment: Mapping[str, str] | None = None,
        secrets: tuple[str, ...] = (),
        temp_parent: Path | None = None,
    ) -> None:
        self.package_store = package_store
        self.workspace_id = workspace_id
        self.journal = journal
        self.artifacts = artifacts
        self.adapter_factory = adapter_factory
        self.sandbox_available = sandbox_available
        self.environment = {
            key: value
            for key, value in (environment or {}).items()
            if key in self._SAFE_ENV_NAMES and isinstance(value, str) and "\x00" not in value
        }
        self.redactor = SecretRedactor(secrets)
        self.temp_parent = temp_parent
        self._plans: dict[tuple[str, str], SkillScriptPlan] = {}

    def preflight(self, request: SkillScriptRequest, *, agent_run_id: str) -> SkillScriptPlan:
        if not self.sandbox_available or self.adapter_factory is None:
            raise SkillScriptExecutionError(
                "sandbox_unavailable", "Skill script execution requires an available sandbox"
            )
        selection = self._selection(request, agent_run_id)
        try:
            package = self.package_store.read_frozen_package(
                skill_id=selection.skill_id,
                version_id=selection.version_id,
                source_kind=selection.source_kind,
                scope_id=selection.scope_id,
                expected_tree_digest=selection.tree_digest,
            )
        except (OSError, SkillPackageError, ValueError) as exc:
            code = "package_drift" if _is_package_drift(exc) else "package_unavailable"
            message = (
                "selected Skill package changed"
                if code == "package_drift"
                else "selected Skill package is unavailable"
            )
            raise SkillScriptExecutionError(code, message) from exc
        entry = next(
            (item for item in package.tree.entries if item.relative_path == request.script_path),
            None,
        )
        if entry is None:
            raise SkillScriptExecutionError(
                "script_not_found", "selected Skill script is unavailable"
            )
        if not entry.exec_mode:
            raise SkillScriptExecutionError(
                "script_not_executable", "selected Skill script is not executable"
            )
        for name in request.environment_names:
            if name not in self._SAFE_ENV_NAMES:
                raise SkillScriptExecutionError(
                    "environment_denied", "requested environment name is not permitted"
                )
            if name not in self.environment:
                raise SkillScriptExecutionError(
                    "environment_unavailable", "requested environment name is unavailable"
                )
        risk_flags = self._risk_flags(package)
        return SkillScriptPlan(
            request=request,
            selection=selection,
            package=package,
            script_sha256=entry.sha256,
            risk_flags=tuple(sorted(risk_flags, key=lambda item: item.value)),
        )

    def cache_plan(self, run_id: str, call_id: str, plan: SkillScriptPlan) -> None:
        self._plans[(run_id, call_id)] = plan

    def cached_plan(self, run_id: str, call_id: str) -> SkillScriptPlan | None:
        return self._plans.get((run_id, call_id))

    def discard_plan(self, run_id: str, call_id: str) -> None:
        self._plans.pop((run_id, call_id), None)

    def intent(self, plan: SkillScriptPlan) -> OperationIntent:
        request = plan.request
        relative = (
            f"skill/{plan.selection.skill_id}/{plan.selection.version_id}/{request.script_path}"
        )
        return OperationIntent(
            kind=OperationKind.PROCESS,
            effect=ToolEffect.SESSION_WRITE,
            relative_paths=(relative,),
            command_class="skill_script",
            risk_flags=plan.risk_flags,
            requires_host=False,
            requires_sandbox=True,
            preview_summary=(
                "在冻结 Skill 包的原生沙箱副本中执行",
                f"Skill：{plan.selection.skill_id}@{plan.selection.version_id}",
                f"脚本：{request.script_path}",
                f"输入 Artifact：{len(request.input_artifact_ids)} 个",
                f"声明输出：{len(request.output_paths)} 个",
                f"超时上限：{request.timeout_seconds:g} 秒",
            ),
        )

    async def execute(
        self,
        plan: SkillScriptPlan,
        *,
        run: ToolRunContext,
        call_id: str,
        tool_name: str,
        ordinal: int,
        approval_verdict,
        result_limit: int,
    ) -> tuple[SkillScriptResult, object]:
        if not self.sandbox_available or self.adapter_factory is None:
            raise SkillScriptExecutionError(
                "sandbox_unavailable", "Skill script execution requires an available sandbox"
            )
        try:
            package = self.package_store.read_frozen_package(
                skill_id=plan.selection.skill_id,
                version_id=plan.selection.version_id,
                source_kind=plan.selection.source_kind,
                scope_id=plan.selection.scope_id,
                expected_tree_digest=plan.selection.tree_digest,
            )
        except (OSError, SkillPackageError, ValueError) as exc:
            raise SkillScriptExecutionError(
                "package_drift", "selected Skill package changed before execution"
            ) from exc
        entry = next(
            (
                item
                for item in package.tree.entries
                if item.relative_path == plan.request.script_path
            ),
            None,
        )
        if entry is None or entry.sha256 != plan.script_sha256 or not entry.exec_mode:
            raise SkillScriptExecutionError(
                "package_drift", "selected Skill script changed before execution"
            )
        root = Path(tempfile.mkdtemp(prefix=".morrow-skill-script-", dir=self.temp_parent))
        try:
            input_hashes = self._materialize(package, plan.request, root)
            command = (f"skill/{plan.request.script_path}", *plan.request.argv)
            environment = self._environment(plan.request, root)
            adapter = self.adapter_factory(root)
            if hasattr(adapter, "run_identity"):
                adapter.run_identity = (run.run_id, call_id)
            output = await adapter.run(
                argv=command,
                shell=None,
                cwd=root,
                timeout_seconds=plan.request.timeout_seconds,
                environment=environment,
                output_limit=min(SCRIPT_STDIO_MAX_CHARS, max(256, result_limit)),
                redaction_overlap=self.redactor.max_secret_length,
            )
            change_set = getattr(adapter, "last_change_set", None)
            if change_set is not None:
                run.retain_change_set(change_set.change_set_id, change_set)
            self._assert_isolated_root(root)
            output_files = self._collect_outputs(
                root,
                plan.request.output_paths,
                change_set=change_set,
            )
            self._assert_inputs_unchanged(root, input_hashes)
            try:
                if build_canonical_tree(root / "skill").tree_digest != package.tree.tree_digest:
                    raise SkillScriptExecutionError(
                        "sandbox_violation", "Skill script changed its frozen package"
                    )
            except (OSError, PackageTreeError) as exc:
                raise SkillScriptExecutionError(
                    "sandbox_violation", "Skill script package view is unavailable"
                ) from exc
            output_refs, produced_paths, output_flags, output_redactions = self._publish_outputs(
                output_files,
                plan,
                run,
            )
            stdout, stdout_flags, stdout_redactions = self.redactor.redact(output.stdout_tail)
            stderr, stderr_flags, stderr_redactions = self.redactor.redact(output.stderr_tail)
            stdout, stdout_truncated = _bounded_tail(stdout, SCRIPT_STDIO_MAX_CHARS)
            stderr, stderr_truncated = _bounded_tail(stderr, SCRIPT_STDIO_MAX_CHARS)
            status = _script_status(output.status, output.returncode)
            result = SkillScriptResult(
                selection_id=plan.selection.selection_id,
                skill_id=plan.selection.skill_id,
                version_id=plan.selection.version_id,
                tree_digest=plan.selection.tree_digest,
                script_path=plan.request.script_path,
                status=status,
                exit_code=output.returncode if output.status is CommandStatus.EXITED else None,
                stdout=stdout,
                stderr=stderr,
                stdout_truncated=output.stdout_truncated or stdout_truncated,
                stderr_truncated=output.stderr_truncated or stderr_truncated,
                output_artifact_refs=tuple(output_refs),
                produced_output_paths=tuple(produced_paths),
                redaction_flags=tuple(
                    dict.fromkeys(
                        (
                            *stdout_flags,
                            *stderr_flags,
                            *output_flags,
                        )
                    )
                ),
                redaction_count=(stdout_redactions + stderr_redactions + output_redactions),
                duration_ms=output.duration_ms,
            )
            from morrow.core.capabilities import CommandToolFact

            fact = CommandToolFact(
                call_id=call_id,
                tool_name=tool_name,
                ordinal=ordinal,
                relative_paths=(f"skill/{plan.request.script_path}",),
                approval_verdict=approval_verdict,
                command_class="skill_script",
                status=output.status.value,
                exit_code=result.exit_code,
                duration_ms=result.duration_ms,
                output_truncated=result.stdout_truncated or result.stderr_truncated,
                redaction_flags=result.redaction_flags,
                redaction_count=result.redaction_count,
            )
            return result, fact
        except asyncio.CancelledError:
            raise
        except ProcessAdapterError as exc:
            code = "timeout" if exc.code in {"timeout", "sandbox_timeout"} else exc.code
            raise SkillScriptExecutionError(code, "Skill script process failed") from exc
        finally:
            try:
                self._cleanup(root)
            except OSError as exc:
                raise SkillScriptExecutionError(
                    "cleanup_failed", "Skill script temporary state could not be cleaned"
                ) from exc

    def _selection(self, request: SkillScriptRequest, agent_run_id: str) -> SkillSelection:
        if self.journal is None:
            raise SkillScriptExecutionError(
                "selection_missing", "Skill selection evidence is unavailable"
            )
        selection = self.journal.get_skill_selection(self.workspace_id, request.selection_id)
        if selection is None:
            raise SkillScriptExecutionError(
                "selection_missing", "Skill selection evidence is unavailable"
            )
        if (
            selection.agent_run_id != agent_run_id
            or selection.skill_id != request.skill_id
            or selection.version_id != request.version_id
            or selection.tree_digest != request.tree_digest
            or selection.source_kind is not request.source_kind
            or selection.scope_id != request.scope_id
        ):
            raise SkillScriptExecutionError(
                "selection_mismatch", "Skill script request does not match frozen selection"
            )
        return selection

    @staticmethod
    def _risk_flags(package: FrozenSkillPackage) -> set[RiskFlag]:
        flags: set[RiskFlag] = set()
        for raw in package.manifest.requested_permissions:
            permission = raw.casefold().replace("-", "_").replace(" ", "_")
            if permission in {"network", "network_access"}:
                flags.add(RiskFlag.NETWORK)
            elif permission in {"loopback", "loopback_access"}:
                flags.add(RiskFlag.LOOPBACK)
            elif permission in {"credential", "credentials", "credential_access", "secrets"}:
                flags.add(RiskFlag.CREDENTIAL_ACCESS)
            elif permission in {"outside_workspace", "outside_workspace_access"}:
                flags.add(RiskFlag.OUTSIDE_WORKSPACE)
            elif permission in {"destructive", "destructive_write"}:
                flags.add(RiskFlag.DESTRUCTIVE)
            elif permission in {"git_write", "git_mutation"}:
                flags.add(RiskFlag.GIT_WRITE)
            elif permission in {"privilege_escalation", "admin"}:
                flags.add(RiskFlag.PRIVILEGE_ESCALATION)
            elif permission in {"morrow_state", "protected_resource", "project_write"}:
                flags.add(RiskFlag.PROTECTED_RESOURCE)
        return flags

    def _materialize(
        self, package: FrozenSkillPackage, request: SkillScriptRequest, root: Path
    ) -> dict[str, str]:
        skill_root = root / "skill"
        inputs_root = root / "inputs"
        outputs_root = root / "outputs"
        for path in (skill_root, inputs_root, outputs_root):
            path.mkdir(mode=0o700)
        for entry in package.tree.entries:
            raw = package.contents.get(entry.relative_path)
            if raw is None:
                raise SkillScriptExecutionError(
                    "package_unavailable", "Skill package bytes are incomplete"
                )
            destination = skill_root / entry.relative_path
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            mode = 0o500 if entry.exec_mode else 0o400
            _write_private(destination, raw, mode)
        for directory in sorted(
            (path for path in skill_root.rglob("*") if path.is_dir()),
            key=lambda item: len(item.parts),
            reverse=True,
        ):
            os.chmod(directory, 0o500)
        os.chmod(skill_root, 0o500)
        input_hashes: dict[str, str] = {}
        for index, artifact_id in enumerate(request.input_artifact_ids):
            if self.artifacts is None:
                raise SkillScriptExecutionError(
                    "artifact_unavailable", "Skill script input Artifact service is unavailable"
                )
            try:
                read = self.artifacts.read(artifact_id, max_bytes=SCRIPT_OUTPUT_FILE_MAX_BYTES)
                raw = read.content
            except Exception as exc:
                raise SkillScriptExecutionError(
                    "artifact_unavailable", "Skill script input Artifact is unavailable"
                ) from exc
            if not isinstance(raw, bytes) or len(raw) > SCRIPT_OUTPUT_FILE_MAX_BYTES:
                raise SkillScriptExecutionError(
                    "input_budget", "Skill script input Artifact exceeds its byte budget"
                )
            relative = f"input-{index}.artifact"
            destination = inputs_root / relative
            _write_private(destination, raw, 0o400)
            input_hashes[relative] = hashlib.sha256(raw).hexdigest()
        return input_hashes

    def _environment(self, request: SkillScriptRequest, root: Path) -> dict[str, str]:
        values = {
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "MORROW_SKILL_ROOT": "skill",
            "MORROW_OUTPUT_DIR": "outputs",
        }
        for name in request.environment_names:
            values[name] = self.environment[name]
        for index in range(len(request.input_artifact_ids)):
            values[f"MORROW_INPUT_{index}"] = f"inputs/input-{index}.artifact"
        del root
        return values

    def _collect_outputs(
        self,
        root: Path,
        declared: tuple[str, ...],
        *,
        change_set=None,
    ) -> dict[str, bytes]:
        expected = {f"outputs/{path}": path for path in declared}
        if change_set is not None:
            if change_set.truncated:
                raise SkillScriptExecutionError(
                    "sandbox_violation", "Skill script sandbox changes were truncated"
                )
            changes = {item.relative_path: item for item in change_set.changes}
            changed_output_paths = set(changes) & set(expected)
            unexpected = [
                path for path in changes if path != "outputs" and not path.startswith("outputs/")
            ]
            undeclared = [
                path for path in changes if path.startswith("outputs/") and path not in expected
            ]
            if unexpected or undeclared:
                raise SkillScriptExecutionError(
                    "sandbox_violation", "Skill script changed an undeclared path"
                )
            values: dict[str, bytes] = {}
            for relative in sorted(changed_output_paths):
                change = changes[relative]
                if change.content is None or change.operation == "deleted":
                    code = (
                        "output_budget"
                        if change.changed_bytes > SCRIPT_OUTPUT_FILE_MAX_BYTES
                        else "output_invalid"
                    )
                    raise SkillScriptExecutionError(
                        code, "Skill script output is missing, deleted, or not bounded"
                    )
                content = change.content.encode("utf-8")
                if len(content) > SCRIPT_OUTPUT_FILE_MAX_BYTES:
                    raise SkillScriptExecutionError(
                        "output_budget", "Skill script output exceeds its byte budget"
                    )
                values[expected[relative]] = content
            if set(declared) != set(values):
                missing = set(declared) - set(values)
                if missing:
                    raise SkillScriptExecutionError(
                        "output_missing", "a declared Skill script output was not produced"
                    )
            if sum(len(value) for value in values.values()) > SCRIPT_TOTAL_OUTPUT_BYTES:
                raise SkillScriptExecutionError(
                    "output_budget", "Skill script outputs exceed their byte budget"
                )
            return values
        outputs = root / "outputs"
        _require_real_directory(outputs, label="Skill script output root")
        values = {}
        for current, directories, files in os.walk(outputs):
            directories.sort()
            files.sort()
            for name in directories:
                path = Path(current) / name
                info = os.lstat(path)
                if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                    raise SkillScriptExecutionError(
                        "output_invalid", "Skill script output contains an unsafe directory"
                    )
                relative = path.relative_to(outputs).as_posix()
                if not any(item.startswith(f"{relative}/") for item in declared):
                    raise SkillScriptExecutionError(
                        "sandbox_violation", "Skill script created an undeclared output"
                    )
            for name in files:
                path = Path(current) / name
                relative = path.relative_to(outputs).as_posix()
                if relative not in declared:
                    raise SkillScriptExecutionError(
                        "sandbox_violation", "Skill script created an undeclared output"
                    )
                values[relative] = _read_private(path, SCRIPT_OUTPUT_FILE_MAX_BYTES)
        if set(values) != set(declared):
            raise SkillScriptExecutionError(
                "output_missing", "a declared Skill script output was not produced"
            )
        if sum(len(value) for value in values.values()) > SCRIPT_TOTAL_OUTPUT_BYTES:
            raise SkillScriptExecutionError(
                "output_budget", "Skill script outputs exceed their byte budget"
            )
        return values

    @staticmethod
    def _assert_inputs_unchanged(root: Path, hashes: Mapping[str, str]) -> None:
        inputs = root / "inputs"
        _require_real_directory(inputs, label="Skill script input root")
        seen: set[str] = set()
        try:
            entries = sorted(os.scandir(inputs), key=lambda item: item.name)
        except OSError as exc:
            raise SkillScriptExecutionError(
                "sandbox_violation", "Skill script input root is unavailable"
            ) from exc
        for entry in entries:
            info = entry.stat(follow_symlinks=False)
            if (
                stat.S_ISLNK(info.st_mode)
                or not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or entry.name not in hashes
            ):
                raise SkillScriptExecutionError(
                    "sandbox_violation", "Skill script changed its input Artifact set"
                )
            seen.add(entry.name)
        if seen != set(hashes):
            raise SkillScriptExecutionError(
                "sandbox_violation", "Skill script changed its input Artifact set"
            )
        for relative, expected in hashes.items():
            try:
                current = _read_private(inputs / relative, SCRIPT_OUTPUT_FILE_MAX_BYTES)
            except SkillScriptExecutionError as exc:
                raise SkillScriptExecutionError(
                    "sandbox_violation", "Skill script changed an input Artifact"
                ) from exc
            if hashlib.sha256(current).hexdigest() != expected:
                raise SkillScriptExecutionError(
                    "sandbox_violation", "Skill script changed an input Artifact"
                )

    @staticmethod
    def _assert_isolated_root(root: Path) -> None:
        try:
            entries = sorted(os.scandir(root), key=lambda item: item.name)
        except OSError as exc:
            raise SkillScriptExecutionError(
                "sandbox_violation", "Skill script temporary root is unavailable"
            ) from exc
        expected = {"skill", "inputs", "outputs"}
        if {entry.name for entry in entries} != expected:
            raise SkillScriptExecutionError(
                "sandbox_violation", "Skill script wrote outside its isolated roots"
            )
        for name in expected:
            _require_real_directory(root / name, label="Skill script isolated root")

    def _publish_outputs(
        self,
        values: Mapping[str, bytes],
        plan: SkillScriptPlan,
        run: ToolRunContext,
    ) -> tuple[list[ArtifactReference], list[str], tuple[str, ...], int]:
        if not values:
            return [], [], (), 0
        if self.artifacts is None:
            raise SkillScriptExecutionError(
                "artifact_unavailable", "Skill script output Artifact service is unavailable"
            )
        refs: list[ArtifactReference] = []
        flags: list[str] = []
        redactions = 0
        for relative in sorted(values):
            text, output_flags, count = self.redactor.redact(values[relative])
            redacted = text.encode("utf-8")
            try:
                metadata = self.artifacts.publish_bytes(
                    redacted,
                    kind=ArtifactKind.COMMAND_OUTPUT,
                    session_id=run.session_id,
                    sensitivity=ArtifactSensitivity.REDACTED,
                    provenance_refs=(
                        ArtifactProvenanceRef(
                            kind=ArtifactProvenanceKind.AGENT_RUN,
                            reference_id=run.run_id,
                            role="skill_script_output",
                        ),
                    ),
                    already_redacted=True,
                )
            except Exception as exc:
                raise SkillScriptExecutionError(
                    "artifact_publish_failed", "Skill script output Artifact publication failed"
                ) from exc
            refs.append(ArtifactReference(artifact_id=metadata.artifact_id, role=relative))
            flags.extend(output_flags)
            redactions += count
        return refs, sorted(values), tuple(dict.fromkeys(flags)), redactions

    def _cleanup(self, root: Path) -> None:
        if not root.name.startswith(".morrow-skill-script-"):
            raise OSError("unsafe Skill script temporary root")
        if not root.exists():
            return
        # The package view is intentionally read-only while the script runs.
        # Restore owner write bits before removing it; directory write permission
        # is required to unlink the frozen package contents.
        for current, directories, files in os.walk(root, topdown=False, followlinks=False):
            for name in files:
                path = Path(current) / name
                if path.is_symlink():
                    path.unlink()
                else:
                    os.chmod(path, 0o700)
            for name in directories:
                path = Path(current) / name
                if path.is_symlink():
                    path.unlink()
                else:
                    os.chmod(path, 0o700)
        os.chmod(root, 0o700)
        shutil.rmtree(root)


def make_skill_script_tool(service: SkillScriptExecutionService) -> RegisteredTool:
    """Expose one script operation through the standard ToolExecutor policy path."""

    def resolve(arguments: SkillScriptRequest, context: ToolCallContext) -> OperationIntent:
        try:
            plan = service.preflight(arguments, agent_run_id=context.run.run_id)
        except SkillScriptExecutionError as exc:
            raise _tool_error(exc) from exc
        service.cache_plan(context.run.run_id, context.call_id, plan)
        return service.intent(plan)

    def preview(arguments: SkillScriptRequest, context: ToolCallContext) -> tuple[str, ...]:
        del arguments
        plan = service.cached_plan(context.run.run_id, context.call_id)
        return (
            service.intent(plan).preview_summary
            if plan is not None
            else ("无法生成 Skill 脚本预览",)
        )

    async def handler(
        arguments: SkillScriptRequest, context: ToolCallContext
    ) -> ToolHandlerOutcome:
        del arguments
        plan = service.cached_plan(context.run.run_id, context.call_id)
        if plan is None:
            raise ToolExecutionError(ToolErrorCode.PREFLIGHT_FAILED, "Skill 脚本预检不存在")
        try:
            result, fact = await service.execute(
                plan,
                run=context.run,
                call_id=context.call_id,
                tool_name=context.tool_name,
                ordinal=context.ordinal,
                approval_verdict=context.approval_verdict,
                result_limit=context.result_limit,
            )
        except SkillScriptExecutionError as exc:
            raise _tool_error(exc) from exc
        return ToolHandlerOutcome(payload=result.model_dump(mode="json"), facts=(fact,))

    return make_tool(
        name="run_skill_script",
        description=(
            "在已选定 Skill 版本的冻结包副本中执行一个可执行脚本。"
            "只接受脚本相对路径和 argv，不接受 shell；输入只能来自 Artifact，"
            "输出必须预先声明并作为 Artifact 保存。"
        ),
        arguments_model=SkillScriptRequest,
        provider_schema=SKILL_SCRIPT_PROVIDER_SCHEMA,
        handler=handler,
        context_handler=handler,
        intent_resolver=resolve,
        context_approval_preview=preview,
        context_cleanup=lambda context: service.discard_plan(context.run.run_id, context.call_id),
        execution_policy=ToolExecutionPolicy(
            effect=ToolEffect.SESSION_WRITE,
            approval=ToolApproval.REQUIRED,
        ),
        approval_preview_budget=ApprovalPreviewBudget(
            max_lines=8, max_line_chars=200, max_bytes=1600
        ),
        recovery_declaration=tool_declaration("run_skill_script"),
    )


def _tool_error(exc: SkillScriptExecutionError) -> ToolExecutionError:
    mapping = {
        "sandbox_unavailable": ToolErrorCode.SANDBOX_UNAVAILABLE,
        "sandbox_violation": ToolErrorCode.SANDBOX_VIOLATION,
        "package_drift": ToolErrorCode.SANDBOX_VIOLATION,
        "package_unavailable": ToolErrorCode.NOT_FOUND,
        "selection_missing": ToolErrorCode.NOT_FOUND,
        "script_not_found": ToolErrorCode.NOT_FOUND,
        "script_not_executable": ToolErrorCode.INVALID_ARGUMENTS,
        "selection_mismatch": ToolErrorCode.INVALID_ARGUMENTS,
        "environment_denied": ToolErrorCode.PERMISSION_DENIED,
        "environment_unavailable": ToolErrorCode.NOT_FOUND,
        "input_budget": ToolErrorCode.OUTPUT_BUDGET,
        "output_invalid": ToolErrorCode.SANDBOX_VIOLATION,
        "output_missing": ToolErrorCode.SANDBOX_VIOLATION,
        "timeout": ToolErrorCode.TIMEOUT,
        "sandbox_timeout": ToolErrorCode.TIMEOUT,
        "output_budget": ToolErrorCode.OUTPUT_BUDGET,
        "artifact_publish_failed": ToolErrorCode.PUBLISH_FAILED,
        "artifact_unavailable": ToolErrorCode.NOT_FOUND,
        "cleanup_failed": ToolErrorCode.PROCESS_CLEANUP_FAILED,
        "sandbox_cleanup_failed": ToolErrorCode.PROCESS_CLEANUP_FAILED,
        "spawn_failed": ToolErrorCode.PROCESS_FAILED,
        "process_failed": ToolErrorCode.PROCESS_FAILED,
    }
    return ToolExecutionError(
        mapping.get(exc.code, ToolErrorCode.INVALID_ARGUMENTS), exc.public_message
    )


def _script_status(status: CommandStatus, returncode: int | None) -> SkillScriptStatus:
    if status is CommandStatus.TIMED_OUT:
        return SkillScriptStatus.TIMED_OUT
    if status is CommandStatus.CANCELLED:
        return SkillScriptStatus.CANCELLED
    if status is CommandStatus.EXITED and returncode == 0:
        return SkillScriptStatus.SUCCEEDED
    return SkillScriptStatus.FAILED


def _bounded_tail(value: str, limit: int) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    return value[-limit:], True


def _is_package_drift(error: BaseException) -> bool:
    """Classify a verified-package failure without exposing storage details."""

    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if "drift" in str(current).casefold():
            return True
        current = current.__cause__ or current.__context__
    return False


def _write_private(path: Path, content: bytes, mode: int) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, mode)
    try:
        offset = 0
        while offset < len(content):
            written = os.write(descriptor, content[offset:])
            if written <= 0:
                raise OSError("private Skill script write made no progress")
            offset += written
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_private(path: Path, maximum: int) -> bytes:
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise SkillScriptExecutionError(
            "output_invalid", "Skill script output is unavailable"
        ) from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise SkillScriptExecutionError(
            "output_invalid", "Skill script output is not a regular file"
        )
    if info.st_size > maximum:
        raise SkillScriptExecutionError(
            "output_budget", "Skill script output exceeds its byte budget"
        )
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        raw = os.read(descriptor, maximum + 1)
    finally:
        os.close(descriptor)
    if len(raw) > maximum:
        raise SkillScriptExecutionError(
            "output_budget", "Skill script output exceeds its byte budget"
        )
    return raw


def _require_real_directory(path: Path, *, label: str) -> None:
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise SkillScriptExecutionError("sandbox_violation", f"{label} is unavailable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise SkillScriptExecutionError("sandbox_violation", f"{label} is not a real directory")


__all__ = [
    "SkillScriptExecutionError",
    "SkillScriptExecutionService",
    "SkillScriptPlan",
    "make_skill_script_tool",
]
