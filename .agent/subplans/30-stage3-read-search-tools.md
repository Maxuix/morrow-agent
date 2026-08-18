# Subplan 30 — Stage 3B.2 Workspace Read and Search Tools

> Status: pending; not active
> Depends on: Subplan 29 completed and accepted

## Goal

Implement the first real project capability: workspace-confined directory listing, text
reading, file discovery, and text search with Pi-grade bounded output and deterministic
offline adapters. Register only these read-only tools in production.

## Scope and expected ownership

- `src/morrow/core/local_tools.py`: file revision/read/list/search result models;
- `src/morrow/services/files.py`: WorkspacePathResolver and WorkspaceFileService;
- `src/morrow/services/search.py`: file discovery/search semantics;
- `src/morrow/adapters/local/filesystem.py`: stdlib filesystem operations;
- `src/morrow/adapters/local/search.py`: installed-rg and Python fallback operations;
- `src/morrow/application/local_tools.py`: strict argument models, descriptions, factories;
- `src/morrow/bootstrap.py`: production registration;
- focused path/file/search/tool/AgentLoop/product tests.

No file mutation, command execution, sandbox, Git command, network, or public-event change.

## Executable tasks

### S3.30.1 — Build WorkspacePathResolver

- Compare Pi `read`/`truncate`/`ls`/`find`/`grep` operations at the fixed commit and record
  which output ergonomics Morrow keeps versus which path/content behaviors it hardens.
- Accept only bounded non-empty workspace-relative path strings.
- Reject absolute, tilde, drive/UNC, NUL, traversal, outside resolution, and invalid target
  types with stable errors that reveal no outside content.
- Resolve existing read symlinks only when the final target remains inside the workspace.
- Provide a stricter mutation-resolution API for Subplan 31 that rejects symlink components.
- Never follow directory symlinks during list/find/search; allow a symlinked regular-file read
  only when its real target is inside the workspace and not protected.
- Add lexical, realpath, nearest-parent, case/alias, internal/external symlink, and simulated
  swap tests against outside sentinels.

### S3.30.2 — Implement bounded list/read services

- Accept only regular files for reads and directories for listing; reject device/FIFO/socket.
- Apply SensitiveResourcePolicy before content access. Return only a protected metadata marker
  for known credential/private-key paths or magic headers; never content, snippets, or hashes.
- Strictly decode UTF-8, report BOM/newline/revision metadata, and reject binary/invalid data.
- Stream/window reads with a 400-line/8-KiB text ceiling, then reduce by whole lines until the
  complete success envelope fits the exact ToolCallContext result budget.
- Return 1-based line range, original line/byte facts, truncation, and exact next start line.
- List entries in stable `(casefolded path, raw path)` order with relative path, type, size, and
  bounded depth/entry count; include dotfiles but never traverse `.git` by default.
- Reject source files above the Stage 3 admission limit with a clear ordinary error.

### S3.30.3 — Implement find/search operations

- Write `docs/decisions/stage-3-search-adapter.md` before implementation, fixing rg/fallback,
  ignore, path, timeout, scan, parity, and no-download decisions from the master plan.
- `find_files` supports bounded name/glob matching under a validated relative root.
- `search_text` supports explicit literal/regex, case mode, optional glob, bounded context,
  and stable path/line/snippet results.
- Use `shutil.which("rg")` for an installed rg adapter and the master-plan fixed argv:
  `--json --hidden --no-config --color=never`, no `--follow`/`--no-ignore`, explicit internal
  exclusion globs/mode/case/validated glob, `--`, and one resolved root. Remove
  `RIPGREP_CONFIG_PATH`; invoke no shell and no network/download path.
- Respect workspace `.gitignore`, `.ignore`, and `.rgignore` only to subtract results. Internal
  exclusions and SensitiveResourcePolicy still win, and ignore syntax never changes root authority.
- Give rg 10 seconds and semantic output bounds. Give the Python fallback at most 10,000 files,
  32 MiB scanned bytes, and 10 seconds; never wait for the outer 120-second timeout.
- Test the declared literal/common-regex/case/glob/hidden/ignored parity corpus and expose the
  actual engine plus truncation/budget reason in results.
- Exclude protected content from both rg and Python fallback results; a filename may appear
  only as bounded protected metadata, never with a match snippet.
- Treat no matches as a successful empty result.

### S3.30.4 — Add Provider tool factories and production registration

- Add strict schemas/descriptions for `list_directory`, `read_file`, `find_files`, and
  `search_text`.
- Map validated arguments through thin handlers to injected services.
- Resolve every call to `workspace_read`; all three Stage 3 presets allow it automatically.
- Register the four tools only for function-tool-capable Adapters.
- Remove `lookup_record` and `calculate` from production registration at this cutover; retain
  their factories and Stage 2 behavior in tests through explicit fixture registries.
- Replace the Stage 2 substring keyword guard with an exact allowed production inventory plus
  still-forbidden capability families (network/browser/MCP/Skill/Git writes/delete/Full Access).
- Rebuild/snapshot the system boundary from the new ToolSet; continue forbidding writes,
  process, sandbox, Git writes, network, MCP, Skills, persistence, and Full Access.

### S3.30.5 — Accept the read/search user path

- Drive Fake Provider through list → search → bounded read → continuation → final explanation.
- Exercise single-call and multi-call cycles at the 16,000/56,000 baseline; every successful
  result stays valid JSON with semantic continuation and `ToolExecutionOutcome.truncated=False`.
- Verify later model requests contain the exact legal ToolMessages and no outside/secret data.
- Exercise real bootstrap/terminal composition with no approval prompt for reads.
- Prove unsupported Adapters remain ordinary chat with no tool surface.
- Prove cancellation, output budget, repeated-call loop detection, and recovery remain intact.

## Completion criteria

- All four read/search tools are present in production with strict schemas and stable results.
- Production inventory is exactly `update_configuration` plus the four read/search tools;
  demo lookup/calculation tools are test-only.
- Workspace escape, external symlink, special file, binary, invalid UTF-8, and oversized-file
  cases fail without disclosure.
- Protected credential/private-key files and misleading magic-header files never disclose
  content through read, search, continuation, truncation, or error messages.
- Read continuation and all list/find/search truncation metadata are actionable and bounded.
- Installed-rg and Python fallback behavior is deterministic for the supported parity corpus;
  fixed budgets/ignore rules match the ADR and no helper is downloaded.
- Capability-derived system prompt and exact boundary allowlist match the registered inventory.
- No write, Shell, Git-write, network, sandbox, Full Access, or persistence capability enters.
- Focused tests, full offline suite, quality gates, CLI help, and `git diff --check` pass.

## Delivered result

Morrow can safely locate and read the minimum code context needed for a real task while
remaining unable to modify or execute the project.
