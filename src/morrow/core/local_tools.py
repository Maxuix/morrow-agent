"""Strict local result contracts for workspace read, search, and mutation tools."""

from __future__ import annotations

from enum import StrEnum

from pydantic import ConfigDict, Field, field_validator, model_validator

from morrow.core.models import ProtocolModel


class LocalToolModel(ProtocolModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class FileRevision(LocalToolModel):
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0, le=8 * 1024 * 1024)
    mtime_ns: int = Field(ge=0)


class LocalFileKind(StrEnum):
    FILE = "file"
    DIRECTORY = "directory"
    SYMLINK = "symlink"
    SPECIAL = "special"


class DirectoryEntry(LocalToolModel):
    path: str = Field(min_length=1, max_length=512)
    kind: LocalFileKind
    size: int = Field(ge=0, le=10**15)
    protected: bool = False


class ProtectedPath(LocalToolModel):
    path: str = Field(min_length=1, max_length=512)
    protected: bool = True


class DirectoryListingResult(LocalToolModel):
    path: str = Field(min_length=1, max_length=512)
    entries: tuple[DirectoryEntry, ...] = ()
    depth: int = Field(ge=1, le=4)
    truncated: bool = False
    protected_paths: tuple[ProtectedPath, ...] = ()


class NewlineStyle(StrEnum):
    NONE = "none"
    LF = "lf"
    CRLF = "crlf"
    CR = "cr"
    MIXED = "mixed"


class ReadFileResult(LocalToolModel):
    path: str = Field(min_length=1, max_length=512)
    text: str = Field(max_length=8 * 1024)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=0)
    total_lines: int = Field(ge=0)
    original_bytes: int = Field(ge=0, le=8 * 1024 * 1024)
    original_lines: int = Field(ge=0)
    revision: FileRevision | None = None
    bom: bool = False
    newline: NewlineStyle = NewlineStyle.NONE
    truncated: bool = False
    next_start_line: int | None = Field(default=None, ge=1)
    protected: bool = False

    @field_validator("end_line")
    @classmethod
    def end_not_before_start(cls, value: int, info) -> int:
        start = info.data.get("start_line")
        if start is not None and value < start - 1:
            raise ValueError("end_line must not precede the empty window before start_line")
        return value


class ExactEdit(LocalToolModel):
    old_text: str = Field(min_length=1, max_length=64 * 1024)
    new_text: str = Field(max_length=64 * 1024)

    @field_validator("old_text", "new_text")
    @classmethod
    def text_is_safe(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("edit text must not contain NUL")
        return value


class MutationMode(StrEnum):
    CREATE = "create"
    REPLACE = "replace"


class MutationOperation(StrEnum):
    CREATE = "create"
    PATCH = "patch"
    REPLACE = "replace"


class MutationStatus(StrEnum):
    CREATED = "created"
    MODIFIED = "modified"
    UNCHANGED = "unchanged"


class MutationResult(LocalToolModel):
    path: str = Field(min_length=1, max_length=512)
    operation: MutationOperation
    status: MutationStatus
    before_revision: FileRevision | None = None
    after_revision: FileRevision | None = None
    changed_lines: int = Field(ge=0, le=100_000)
    changed_bytes: int = Field(ge=0, le=10_000_000)
    diff: str = Field(max_length=4 * 1024)
    diff_truncated: bool = False
    change_set_id: str = Field(min_length=1, max_length=128)
    auxiliary_paths: tuple[str, ...] = ()
    protected: bool = False


class ChangeSetResult(LocalToolModel):
    entries: tuple[MutationResult, ...] = ()
    truncated: bool = False


class CommandStatus(StrEnum):
    EXITED = "exited"
    SIGNALED = "signaled"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class CommandRequest(LocalToolModel):
    """Provider-independent command request admitted by the Host process service."""

    argv: tuple[str, ...] | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description='命令参数数组，例如 ["python3", "run_acceptance.py"]。必须与 shell 二选一。',
    )
    shell: str | None = Field(
        default=None,
        max_length=16 * 1024,
        description="单一 shell 字符串。必须与 argv 二选一；不要用来安装依赖或访问网络。",
    )
    cwd: str = "."
    timeout_seconds: float = Field(default=90.0, gt=0, le=90)

    @field_validator("argv")
    @classmethod
    def valid_argv(cls, values: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if values is None:
            return None
        for value in values:
            if not value or len(value) > 4096 or "\x00" in value:
                raise ValueError("argv entries must be bounded, non-empty and NUL-free")
        return values

    @field_validator("shell", "cwd")
    @classmethod
    def valid_text_fields(cls, value: str | None) -> str | None:
        if value is not None and "\x00" in value:
            raise ValueError("command fields must be NUL-free")
        return value

    @model_validator(mode="after")
    def exactly_one_command_form(self) -> CommandRequest:
        has_argv = self.argv is not None
        has_shell = self.shell is not None and bool(self.shell.strip())
        if has_argv == has_shell:
            raise ValueError("exactly one of argv or shell is required")
        if self.argv is not None and sum(len(value) for value in self.argv) > 16 * 1024:
            raise ValueError("argv is too large")
        return self


class CommandResult(LocalToolModel):
    """Bounded, sanitized Host process result; command text is intentionally absent."""

    status: CommandStatus
    exit_code: int | None = None
    signal: int | None = Field(default=None, ge=1, le=255)
    stdout: str = Field(max_length=16 * 1024)
    stderr: str = Field(max_length=16 * 1024)
    stdout_original_bytes: int = Field(ge=0, le=10**12)
    stdout_original_lines: int = Field(ge=0, le=10**10)
    stderr_original_bytes: int = Field(ge=0, le=10**12)
    stderr_original_lines: int = Field(ge=0, le=10**10)
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    output_truncated: bool = False
    duration_ms: int = Field(ge=0, le=120_000)
    command_class: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    cwd: str = Field(min_length=1, max_length=512)
    redaction_flags: tuple[str, ...] = ()
    redaction_count: int = Field(default=0, ge=0, le=100_000)
    sandbox_change_set_id: str | None = Field(default=None, max_length=128)
    sandbox_changed_paths: tuple[str, ...] = ()
    sandbox_changes_truncated: bool = False


class GitRepositoryState(StrEnum):
    NOT_REPOSITORY = "not_repository"
    CLEAN = "clean"
    DIRTY = "dirty"
    CONFLICT = "conflict"
    EXTERNAL_METADATA = "external_git_metadata"


class GitEntryKind(StrEnum):
    ORDINARY = "ordinary"
    RENAMED = "renamed"
    UNMERGED = "unmerged"
    UNTRACKED = "untracked"


class GitStatusEntry(LocalToolModel):
    path: str = Field(min_length=1, max_length=512)
    kind: GitEntryKind
    index_status: str = Field(min_length=1, max_length=1)
    worktree_status: str = Field(min_length=1, max_length=1)
    original_path: str | None = Field(default=None, max_length=512)
    protected: bool = False


class GitStatusResult(LocalToolModel):
    repository: bool
    repository_state: GitRepositoryState
    root: str = Field(min_length=1, max_length=512)
    branch: str | None = Field(default=None, max_length=256)
    head: str | None = Field(default=None, max_length=128)
    detached: bool = False
    entries: tuple[GitStatusEntry, ...] = ()
    staged_count: int = Field(default=0, ge=0, le=100_000)
    unstaged_count: int = Field(default=0, ge=0, le=100_000)
    untracked_count: int = Field(default=0, ge=0, le=100_000)
    conflict_count: int = Field(default=0, ge=0, le=100_000)
    protected_count: int = Field(default=0, ge=0, le=100_000)
    truncated: bool = False


class GitDiffResult(LocalToolModel):
    repository: bool
    repository_state: GitRepositoryState
    staged: bool = False
    paths: tuple[str, ...] = ()
    diff: str = Field(max_length=16 * 1024)
    protected_paths: tuple[ProtectedPath, ...] = ()
    truncated: bool = False


class FindFilesResult(LocalToolModel):
    path: str = Field(min_length=1, max_length=512)
    pattern: str = Field(min_length=1, max_length=128)
    paths: tuple[str, ...] = ()
    truncated: bool = False
    protected_paths: tuple[ProtectedPath, ...] = ()


class SearchMatch(LocalToolModel):
    path: str = Field(min_length=1, max_length=512)
    line: int = Field(ge=1)
    column: int = Field(ge=1)
    snippet: str = Field(min_length=1, max_length=512)
    before: tuple[str, ...] = ()
    after: tuple[str, ...] = ()


class SearchEngine(StrEnum):
    RG = "rg"
    PYTHON = "python"


class SearchCase(StrEnum):
    SENSITIVE = "sensitive"
    INSENSITIVE = "insensitive"
    SMART = "smart"


class SearchQuery(LocalToolModel):
    pattern: str = Field(min_length=1, max_length=256)
    literal: bool = True
    case: SearchCase = SearchCase.SMART
    glob: str | None = Field(default=None, max_length=128)
    context_lines: int = Field(default=0, ge=0, le=3)
    max_results: int = Field(default=100, ge=1, le=100)


class SearchTextResult(LocalToolModel):
    path: str = Field(min_length=1, max_length=512)
    pattern: str = Field(min_length=1, max_length=256)
    matches: tuple[SearchMatch, ...] = ()
    engine: SearchEngine
    truncated: bool = False
    budget_reason: str | None = Field(default=None, max_length=64)
    protected_paths: tuple[ProtectedPath, ...] = ()
