# Stage 3 Search Adapter Decision

Status: accepted for Subplan 30. This decision is local-only and does not add a dependency or
change the bundled agent policy.

## Choice

Morrow uses an installed `rg` executable when `shutil.which("rg")` resolves one, and otherwise
uses a bounded Python standard-library fallback. Morrow never downloads or installs a search
helper. Both engines receive the same validated query and the same `SensitiveResourcePolicy`.

The `rg` invocation is fixed-argv, `shell=False`, with `--json --hidden --no-config
--color=never`; it adds explicit `.git` and Morrow-state exclusions, an explicit literal/regex
mode, case mode, optional validated glob, `--`, the pattern, and one resolved workspace-relative
root. `RIPGREP_CONFIG_PATH` is absent from the child environment. `--follow` and `--no-ignore`
are never used. Workspace ignore files subtract results only; they never grant path authority.

## Bounds and parity

Both engines have a 10-second deadline and a maximum of 100 returned matches. The Python
fallback additionally stops at 10,000 regular files or 32 MiB of scanned bytes and reports a
typed truncation reason. It never follows directory symlinks, rejects binary/invalid UTF-8
content, and suppresses protected paths or magic-header content. For file symlinks it applies
the same policy to both the visible alias and confined resolved target; `.git` and `.morrow`
remain protected even when explicitly selected as a root. Literal search, common regex,
case-sensitive/insensitive/smart case, optional globs, hidden files, ignored files, protected
files, and no-match behavior are covered by the offline parity tests.

No search result contains an absolute path, full file content, a protected snippet, or a raw
subprocess error. The returned engine and truncation reason remain local result metadata so an
Agent can explain bounded search honestly.
