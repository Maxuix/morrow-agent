"""Safe workspace baselines and runtime-owned completion checks."""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from morrow.core.capabilities import ChangeToolFact, ValidationFact
from morrow.core.completion import (
    CompletionBasis,
    CompletionCheckResult,
    CompletionOutcome,
    CompletionVerifierResult,
    OutcomeContract,
    ValidationCheckResult,
    ValidationStatus,
    VerifierStatus,
    WorkspaceBaseline,
    WorkspaceBaselineEntry,
    WorkspaceBaselineStatus,
)
from morrow.services.files import WorkspaceFileService

MAX_BASELINE_ENTRIES = 256
MAX_BASELINE_FILE_BYTES = 64 * 1024 * 1024
MAX_BASELINE_TOTAL_BYTES = 256 * 1024 * 1024
MAX_BASELINE_PATH_CHARS = 24 * 1024
_EXCLUDED_COMPONENTS = frozenset(
    {
        ".git",
        ".morrow",
        ".venv",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".tox",
        ".nox",
        ".cache",
        "node_modules",
        "dist",
        "build",
        "coverage",
    }
)


class CompletionVerifier(Protocol):
    def verify(self, result: CompletionCheckResult) -> CompletionVerifierResult | bool: ...


class WorkspaceBaselineService:
    """Capture a bounded, no-follow hash manifest of workspace leaf objects."""

    def __init__(
        self,
        files: WorkspaceFileService,
        *,
        max_entries: int = MAX_BASELINE_ENTRIES,
        max_file_bytes: int = MAX_BASELINE_FILE_BYTES,
        max_total_bytes: int = MAX_BASELINE_TOTAL_BYTES,
    ) -> None:
        if min(max_entries, max_file_bytes, max_total_bytes) < 1:
            raise ValueError("baseline limits must be positive")
        self.files = files
        self.max_entries = min(max_entries, MAX_BASELINE_ENTRIES)
        self.max_file_bytes = min(max_file_bytes, MAX_BASELINE_FILE_BYTES)
        self.max_total_bytes = min(max_total_bytes, MAX_BASELINE_TOTAL_BYTES)

    def prepare(self) -> WorkspaceBaseline:
        return self._scan()

    capture = prepare

    def _scan(self) -> WorkspaceBaseline:
        root = self.files.resolver.root
        entries: list[WorkspaceBaselineEntry] = []
        try:
            root_descriptor = self._open_directory(root)
        except OSError:
            return self._inconclusive(entries, root, "baseline_scan_failed")
        stack: list[tuple[str, int]] = [(".", root_descriptor)]
        total_bytes = 0
        total_path_chars = 0
        status = WorkspaceBaselineStatus.COMPLETE
        reason_code: str | None = None
        try:
            while stack:
                relative_dir, descriptor = stack.pop()
                try:
                    try:
                        children = self._directory_names(descriptor)
                    except OSError:
                        return self._inconclusive(entries, root, "baseline_scan_failed")
                    for child_name in children:
                        relative = (
                            child_name if relative_dir == "." else f"{relative_dir}/{child_name}"
                        )
                        if self._excluded(
                            relative
                        ) or self.files.sensitive_policy.is_protected_path(relative):
                            continue
                        try:
                            child_stat = os.stat(
                                child_name,
                                dir_fd=descriptor,
                                follow_symlinks=False,
                            )
                        except OSError:
                            status = WorkspaceBaselineStatus.INCONCLUSIVE
                            reason_code = "baseline_stat_failed"
                            continue
                        if stat.S_ISDIR(child_stat.st_mode):
                            child_descriptor: int | None = None
                            try:
                                child_descriptor = self._open_directory(
                                    child_name, dir_fd=descriptor
                                )
                                opened_stat = os.fstat(child_descriptor)
                                if (
                                    opened_stat.st_dev,
                                    opened_stat.st_ino,
                                    stat.S_IFMT(opened_stat.st_mode),
                                ) != (
                                    child_stat.st_dev,
                                    child_stat.st_ino,
                                    stat.S_IFMT(child_stat.st_mode),
                                ):
                                    os.close(child_descriptor)
                                    return self._inconclusive(
                                        entries, root, "baseline_directory_changed"
                                    )
                            except OSError:
                                if child_descriptor is not None:
                                    try:
                                        os.close(child_descriptor)
                                    except OSError:
                                        pass
                                return self._inconclusive(
                                    entries, root, "baseline_directory_open_failed"
                                )
                            stack.append((relative, child_descriptor))
                            continue
                        if not (
                            stat.S_ISREG(child_stat.st_mode) or stat.S_ISLNK(child_stat.st_mode)
                        ):
                            continue
                        if len(entries) >= self.max_entries:
                            status = WorkspaceBaselineStatus.TRUNCATED
                            reason_code = "baseline_scan_limit"
                            self._close_stack(stack)
                            break
                        try:
                            if stat.S_ISLNK(child_stat.st_mode):
                                target = os.readlink(child_name, dir_fd=descriptor)
                                encoded_target = target.encode("utf-8", errors="surrogateescape")
                                digest = hashlib.sha256(
                                    b"morrow-symlink\0" + encoded_target
                                ).hexdigest()
                                size = len(encoded_target)
                                kind = "symlink"
                            else:
                                size, digest = self._hash_regular(
                                    child_name,
                                    child_stat.st_size,
                                    dir_fd=descriptor,
                                    expected_stat=child_stat,
                                )
                                kind = "file"
                        except _BaselineLimit:
                            status = WorkspaceBaselineStatus.INCONCLUSIVE
                            reason_code = "baseline_file_too_large"
                            continue
                        except _BaselineRace:
                            status = WorkspaceBaselineStatus.INCONCLUSIVE
                            reason_code = "baseline_file_changed"
                            continue
                        except OSError:
                            status = WorkspaceBaselineStatus.INCONCLUSIVE
                            reason_code = "baseline_read_failed"
                            continue
                        if total_bytes + size > self.max_total_bytes:
                            status = WorkspaceBaselineStatus.TRUNCATED
                            reason_code = "baseline_byte_limit"
                            self._close_stack(stack)
                            break
                        if total_path_chars + len(relative) > MAX_BASELINE_PATH_CHARS:
                            status = WorkspaceBaselineStatus.TRUNCATED
                            reason_code = "baseline_path_limit"
                            self._close_stack(stack)
                            break
                        total_bytes += size
                        total_path_chars += len(relative)
                        try:
                            entry = WorkspaceBaselineEntry(
                                path=relative,
                                kind=kind,
                                sha256=digest,
                                size=size,
                            )
                        except ValueError:
                            status = WorkspaceBaselineStatus.INCONCLUSIVE
                            reason_code = "baseline_path_invalid"
                            continue
                        entries.append(entry)
                finally:
                    os.close(descriptor)
        finally:
            for _relative_dir, descriptor in stack:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        repository_state, git_head = self._repository_evidence(root)
        return WorkspaceBaseline(
            status=status,
            entries=tuple(sorted(entries, key=lambda entry: entry.path)),
            repository_state=repository_state,
            git_head=git_head,
            reason_code=reason_code,
        )

    @staticmethod
    def _open_directory(path: str | Path, *, dir_fd: int | None = None) -> int:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        return os.open(path, flags, dir_fd=dir_fd)

    @staticmethod
    def _directory_names(descriptor: int) -> tuple[str, ...]:
        try:
            if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise OSError("baseline path is not a directory")
            with os.scandir(descriptor) as iterator:
                return tuple(sorted(entry.name for entry in iterator))
        except (OSError, TypeError):
            raise OSError("baseline directory cannot be scanned") from None

    @staticmethod
    def _close_stack(stack: list[tuple[str, int]]) -> None:
        while stack:
            _relative_dir, descriptor = stack.pop()
            try:
                os.close(descriptor)
            except OSError:
                pass

    @staticmethod
    def _inconclusive(
        entries: list[WorkspaceBaselineEntry], root: Path, reason_code: str
    ) -> WorkspaceBaseline:
        repository_state, git_head = WorkspaceBaselineService._repository_evidence(root)
        return WorkspaceBaseline(
            status=WorkspaceBaselineStatus.INCONCLUSIVE,
            entries=tuple(sorted(entries, key=lambda entry: entry.path)),
            repository_state=repository_state,
            git_head=git_head,
            reason_code=reason_code,
        )

    @staticmethod
    def _repository_evidence(root: Path) -> tuple[str, str | None]:
        try:
            git_entry = os.lstat(root / ".git")
        except FileNotFoundError:
            return "filesystem", None
        except OSError:
            return "unknown", None
        if not (stat.S_ISDIR(git_entry.st_mode) or stat.S_ISREG(git_entry.st_mode)):
            return "unknown", None
        try:
            git_fd = WorkspaceBaselineService._open_directory(root / ".git")
        except OSError:
            if not stat.S_ISREG(git_entry.st_mode):
                return "git", None
            return "git", WorkspaceBaselineService._hash_git_pointer(root / ".git")
        try:
            head_fd = os.open("HEAD", os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=git_fd)
            try:
                raw = os.read(head_fd, 257)
            finally:
                os.close(head_fd)
        except OSError:
            return "git", None
        finally:
            os.close(git_fd)
        if len(raw) > 256:
            return "git", None
        return "git", hashlib.sha256(b"morrow-git-head\0" + raw).hexdigest()

    @staticmethod
    def _hash_git_pointer(path: Path) -> str | None:
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                raw = os.read(fd, 257)
            finally:
                os.close(fd)
        except OSError:
            return None
        if len(raw) > 256:
            return None
        return hashlib.sha256(b"morrow-gitdir-pointer\0" + raw).hexdigest()

    def _hash_regular(
        self,
        path: str | Path,
        expected_size: int,
        *,
        dir_fd: int | None = None,
        expected_stat: os.stat_result | None = None,
    ) -> tuple[int, str]:
        if expected_size > self.max_file_bytes:
            raise _BaselineLimit
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, dir_fd=dir_fd)
        try:
            opened = os.fstat(fd)
            if not stat.S_ISREG(opened.st_mode) or opened.st_size > self.max_file_bytes:
                raise _BaselineLimit
            if expected_stat is not None and (
                opened.st_dev,
                opened.st_ino,
                opened.st_size,
                opened.st_mtime_ns,
            ) != (
                expected_stat.st_dev,
                expected_stat.st_ino,
                expected_stat.st_size,
                expected_stat.st_mtime_ns,
            ):
                raise _BaselineRace
            digest = hashlib.sha256()
            size = 0
            with os.fdopen(fd, "rb", closefd=False) as stream:
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > self.max_file_bytes:
                        raise _BaselineLimit
                    digest.update(chunk)
            closed = os.fstat(fd)
            if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (
                closed.st_dev,
                closed.st_ino,
                closed.st_size,
                closed.st_mtime_ns,
            ):
                raise _BaselineRace
            return size, digest.hexdigest()
        finally:
            os.close(fd)

    @staticmethod
    def _excluded(relative: str) -> bool:
        return any(part in _EXCLUDED_COMPONENTS for part in relative.split("/"))


class _BaselineLimit(Exception):
    pass


class _BaselineRace(Exception):
    pass


class CompletionChecker:
    """Evaluate only deterministic runtime evidence, never business correctness."""

    def __init__(self, files: WorkspaceFileService, baseline_service=None) -> None:
        self.files = files
        self.baselines = baseline_service or WorkspaceBaselineService(files)

    def check(
        self,
        contract: OutcomeContract,
        baseline: WorkspaceBaseline,
        *,
        run_context=None,
        validation_facts: tuple[ValidationFact, ...] | None = None,
        unresolved_tool_ids: tuple[str, ...] = (),
        unresolved_calls: tuple[str, ...] = (),
        known_failure_codes: tuple[str, ...] = (),
        verifier: CompletionVerifier | Callable[[CompletionCheckResult], object] | None = None,
    ) -> CompletionCheckResult:
        current = self.baselines.prepare()
        before = {entry.path: entry for entry in baseline.entries}
        after = {entry.path: entry for entry in current.entries}
        changed_paths = tuple(
            sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))
        )
        facts = tuple(validation_facts or ())
        if run_context is not None:
            facts = tuple(fact for fact in run_context.validation_facts)
        failures = self._safe_codes(known_failure_codes)
        unresolved = tuple(unresolved_tool_ids) + tuple(unresolved_calls)
        reasons: list[str] = []
        next_actions: list[str] = []
        validations = self._validation_results(contract, facts, reasons, next_actions)
        unexpected = tuple(
            path
            for path in changed_paths
            if contract.allowed_paths is not None
            and not self._matches_any(path, contract.allowed_paths)
        )
        forbidden = tuple(
            path for path in changed_paths if self._matches_any(path, contract.forbidden_paths)
        )
        if contract.mode.value in {"explanation", "unspecified"} and changed_paths:
            unexpected = changed_paths
        if unexpected:
            reasons.append("unexpected_workspace_change")
            next_actions.append("restore_unexpected_paths")
        if forbidden:
            reasons.append("forbidden_workspace_change")
            next_actions.append("restore_forbidden_paths")
        if unresolved:
            reasons.append("unresolved_tool")
            next_actions.append("close_unresolved_tools")
        # An unspecified/explanation turn may truthfully report a tool failure in
        # its bounded facts without turning ordinary conversational recovery into
        # a failed completion obligation.  A change, required check, or verifier
        # contract must still treat every known failure as blocking evidence.
        if failures and (
            contract.requires_net_change
            or contract.required_validations
            or contract.verifier_id is not None
        ):
            reasons.append("known_failure")
            next_actions.append("resolve_known_failure")
        if contract.requires_net_change:
            if not changed_paths:
                reasons.append("missing_required_change")
                next_actions.append("apply_required_change")
            elif contract.target_paths and not any(
                self._matches_any(path, contract.target_paths) for path in changed_paths
            ):
                reasons.append("missing_required_change")
                next_actions.append("change_target_path")
            if changed_paths and not self._run_attributed(
                changed_paths, run_context, before, after
            ):
                reasons.append("baseline_drift")
                next_actions.append("inspect_workspace_baseline")
        repository_changed = (
            baseline.repository_state != current.repository_state
            or baseline.git_head != current.git_head
        )
        if repository_changed:
            reasons.append("repository_state_changed")
            next_actions.append("inspect_repository_state")
        if baseline.status is not WorkspaceBaselineStatus.COMPLETE:
            reasons.append("baseline_inconclusive")
            next_actions.append("reprepare_workspace_baseline")
        if current.status is not WorkspaceBaselineStatus.COMPLETE:
            reasons.append("workspace_scan_inconclusive")
            next_actions.append("inspect_workspace_scan")

        reasons = list(dict.fromkeys(reasons))
        next_actions = list(dict.fromkeys(next_actions))
        outcome, basis = self._initial_outcome(reasons)
        verifier_status = VerifierStatus.NOT_CONFIGURED
        if not reasons and contract.verifier_id is not None:
            if verifier is None:
                verifier_status = VerifierStatus.INCONCLUSIVE
                reasons.append("verifier_inconclusive")
                next_actions.append("run_configured_verifier")
                outcome, basis = CompletionOutcome.INCONCLUSIVE, CompletionBasis.INCONCLUSIVE
            else:
                provisional = self._result(
                    outcome=CompletionOutcome.PASSED,
                    basis=CompletionBasis.RUNTIME_EVIDENCE_WITHOUT_VERIFIER,
                    changed_paths=changed_paths,
                    contract=contract,
                    unexpected=unexpected,
                    forbidden=forbidden,
                    unresolved=unresolved,
                    validations=validations,
                    failures=failures,
                    verifier_status=VerifierStatus.NOT_CONFIGURED,
                    reasons=(),
                    next_actions=(),
                    baseline_status=current.status,
                )
                verifier_result = self._run_verifier(verifier, provisional)
                verifier_status = verifier_result.status
                if verifier_status is VerifierStatus.PASSED:
                    basis = CompletionBasis.VERIFIED
                elif verifier_status is VerifierStatus.FAILED:
                    reasons.append("verifier_failed")
                    next_actions.append("resolve_verifier_failure")
                    outcome, basis = CompletionOutcome.REJECTED, CompletionBasis.NOT_COMPLETED
                else:
                    reasons.append("verifier_inconclusive")
                    next_actions.append("run_configured_verifier")
                    outcome, basis = CompletionOutcome.INCONCLUSIVE, CompletionBasis.INCONCLUSIVE

        if (
            reasons
            and "baseline_inconclusive" not in reasons
            and "workspace_scan_inconclusive" not in reasons
        ):
            if any(
                reason in reasons
                for reason in (
                    "baseline_drift",
                    "repository_state_changed",
                    "verifier_inconclusive",
                )
            ):
                outcome, basis = CompletionOutcome.INCONCLUSIVE, CompletionBasis.INCONCLUSIVE
            elif outcome is CompletionOutcome.PASSED:
                outcome, basis = CompletionOutcome.REJECTED, CompletionBasis.NOT_COMPLETED
        elif reasons:
            outcome, basis = CompletionOutcome.INCONCLUSIVE, CompletionBasis.INCONCLUSIVE
        return self._result(
            outcome=outcome,
            basis=basis,
            changed_paths=changed_paths,
            contract=contract,
            unexpected=unexpected,
            forbidden=forbidden,
            unresolved=unresolved,
            validations=validations,
            failures=failures,
            verifier_status=verifier_status,
            reasons=tuple(reasons),
            next_actions=tuple(next_actions),
            baseline_status=current.status,
        )

    @staticmethod
    def _initial_outcome(reasons: list[str]) -> tuple[CompletionOutcome, CompletionBasis]:
        if not reasons:
            return CompletionOutcome.PASSED, CompletionBasis.RUNTIME_EVIDENCE_WITHOUT_VERIFIER
        if any(
            reason in reasons
            for reason in ("baseline_drift", "baseline_inconclusive", "repository_state_changed")
        ):
            return CompletionOutcome.INCONCLUSIVE, CompletionBasis.INCONCLUSIVE
        return CompletionOutcome.REJECTED, CompletionBasis.NOT_COMPLETED

    def _validation_results(
        self,
        contract: OutcomeContract,
        facts: tuple[ValidationFact, ...],
        reasons: list[str],
        next_actions: list[str],
    ) -> tuple[ValidationCheckResult, ...]:
        latest: dict[tuple[str, str], ValidationFact] = {}
        for fact in facts:
            if isinstance(fact, ValidationFact):
                latest[(fact.validator_kind, fact.scope)] = fact
        results: list[ValidationCheckResult] = []
        for requirement in contract.required_validations:
            fact = latest.get((requirement.validator_kind, requirement.scope))
            if fact is None:
                result = ValidationCheckResult(
                    requirement=requirement,
                    status=ValidationStatus.NOT_RUN,
                    reason_code="validation_missing",
                )
                reasons.append("validation_missing")
                next_actions.append("run_required_validation")
            else:
                status = {
                    "passed": ValidationStatus.PASSED,
                    "failed": ValidationStatus.FAILED,
                    "timeout": ValidationStatus.TIMEOUT,
                    "cancelled": ValidationStatus.CANCELLED,
                }.get(fact.status, ValidationStatus.INCONCLUSIVE)
                reason = None if status is ValidationStatus.PASSED else f"validation_{status.value}"
                result = ValidationCheckResult(
                    requirement=requirement,
                    status=status,
                    observed=True,
                    reason_code=reason,
                )
                if reason is not None:
                    reasons.append(reason)
                    next_actions.append("rerun_required_validation")
            results.append(result)
        return tuple(results)

    @staticmethod
    def _run_attributed(
        changed_paths: tuple[str, ...],
        run_context,
        before: dict[str, WorkspaceBaselineEntry],
        after: dict[str, WorkspaceBaselineEntry],
    ) -> bool:
        if run_context is None:
            return False
        successful = tuple(
            fact
            for fact in run_context.facts
            if isinstance(fact, ChangeToolFact)
            and fact.status in {"created", "modified", "deleted", "moved", "renamed"}
        )
        for path in changed_paths:
            matching = tuple(fact for fact in successful if path in fact.relative_paths)
            if not matching:
                return False
            expected_revision = before[path].sha256 if path in before else None
            current_revision = after[path].sha256 if path in after else None
            applied = False
            for fact in matching:
                transition: tuple[str | None, str | None] | None = None
                if fact.status == "created":
                    if expected_revision is None and fact.after_revision is not None:
                        transition = (None, fact.after_revision)
                elif fact.status == "modified":
                    if (
                        expected_revision is not None
                        and fact.before_revision == expected_revision
                        and fact.after_revision is not None
                    ):
                        transition = (fact.before_revision, fact.after_revision)
                    elif (
                        expected_revision is not None
                        and fact.before_revision == expected_revision
                        and fact.after_revision is None
                    ):
                        return False
                elif fact.status == "deleted":
                    if (
                        expected_revision is not None
                        and fact.source_path == path
                        and fact.before_revision == expected_revision
                    ):
                        transition = (fact.before_revision, None)
                elif fact.status in {"moved", "renamed"}:
                    if (
                        fact.source_path == path
                        and expected_revision is not None
                        and fact.before_revision == expected_revision
                    ):
                        transition = (fact.before_revision, None)
                    elif (
                        fact.destination_path == path
                        and expected_revision is None
                        and fact.after_revision is not None
                    ):
                        transition = (None, fact.after_revision)
                if transition is None or transition[0] != expected_revision:
                    continue
                expected_revision = transition[1]
                applied = True
            if not applied or expected_revision != current_revision:
                return False
        return True

    @staticmethod
    def _matches_any(path: str, policies: tuple[str, ...]) -> bool:
        return any(CompletionChecker._matches(path, policy) for policy in policies)

    @staticmethod
    def _matches(path: str, policy: str) -> bool:
        return policy == "." or path == policy or path.startswith(policy + "/")

    @staticmethod
    def _safe_codes(values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                value
                for value in values
                if isinstance(value, str) and value and value.isidentifier()
            )
        )[:32]

    @staticmethod
    def _run_verifier(verifier, result: CompletionCheckResult) -> CompletionVerifierResult:
        try:
            value = verifier.verify(result) if hasattr(verifier, "verify") else verifier(result)
        except Exception:
            return CompletionVerifierResult(
                status=VerifierStatus.INCONCLUSIVE,
                reason_code="verifier_error",
            )
        if isinstance(value, CompletionVerifierResult):
            return value
        if value is True:
            return CompletionVerifierResult(status=VerifierStatus.PASSED)
        if value is False:
            return CompletionVerifierResult(
                status=VerifierStatus.FAILED, reason_code="verifier_failed"
            )
        return CompletionVerifierResult(
            status=VerifierStatus.INCONCLUSIVE, reason_code="verifier_invalid"
        )

    @staticmethod
    def _result(
        *,
        outcome: CompletionOutcome,
        basis: CompletionBasis,
        changed_paths: tuple[str, ...],
        contract: OutcomeContract,
        unexpected: tuple[str, ...],
        forbidden: tuple[str, ...],
        unresolved: tuple[str, ...],
        validations: tuple[ValidationCheckResult, ...],
        failures: tuple[str, ...],
        verifier_status: VerifierStatus,
        reasons: tuple[str, ...],
        next_actions: tuple[str, ...],
        baseline_status: WorkspaceBaselineStatus,
    ) -> CompletionCheckResult:
        return CompletionCheckResult(
            outcome=outcome,
            basis=basis,
            changed_paths=changed_paths,
            target_paths=contract.target_paths,
            unexpected_paths=unexpected,
            forbidden_paths=forbidden,
            unresolved_tool_count=min(128, len(unresolved)),
            required_validations=validations,
            known_failure_codes=failures,
            verifier_status=verifier_status,
            verifier_id=contract.verifier_id,
            baseline_status=baseline_status,
            reason_codes=reasons,
            next_action_codes=next_actions,
        )


__all__ = [
    "CompletionChecker",
    "CompletionVerifier",
    "MAX_BASELINE_ENTRIES",
    "WorkspaceBaselineService",
]
