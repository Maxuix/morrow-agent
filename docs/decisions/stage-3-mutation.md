# Stage 3 mutation adapter decision

## Fixed reference

Subplan 31 uses Pi coding-agent `0.84.2` at commit
`209bc7b9a89b01c8fd05861cf5bbdda3e300037a` as a behavioral reference for `edit`, `write` and
same-file mutation serialization. This note records the independent Morrow adaptation; it is
not a dependency on Pi source or runtime.

## Borrowed behavior

- Keep a small composable mutation surface: exact patch, controlled file write, and an explicit
  change projection.
- Compute all exact edit matches against one source snapshot, reject missing/non-unique matches
  and overlaps, and publish edits in one operation.
- Serialize operations targeting the same file and return a bounded unified Diff suitable for a
  model/tool cycle.

## Deliberately strengthened for Morrow

- A read SHA-256 is the write authority; stale or externally changed content returns `conflict`
  instead of last-write-wins behavior.
- Mutation paths reject every symlink component, parent creation is limited to four levels, and
  publication uses an exclusive same-directory temporary file, `fsync`, atomic replacement and
  a directory-handle recheck.
- Permission policy decides whether a structured mutation is automatic or requires approval;
  approval sees actual bounded Diff lines rather than a stats-only summary.
- Protected credential/private-key paths and content are rejected before mutation and rechecked
  after publication. Only bounded metadata, revisions, Diff and ChangeSet facts remain local.
- Uniform LF, CRLF, or CR source style and BOM are preserved. A mixed-newline source is rejected
  with a typed `unsupported_newline` result before patch/replace, so no edit silently normalizes
  the whole file.

## Rejected behavior

- No fuzzy matching, guessed context, absolute/outside paths, silent full-file fallback, delete,
  rename, chmod, link operations, Host command execution, network access or inherited full-access
  authority.
- No second task state machine or public event lifecycle; the existing `AgentLoop`, ToolCycle,
  `ConversationLog` ownership and generic `ToolExecutor` remain authoritative.
