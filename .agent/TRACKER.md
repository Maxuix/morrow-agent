# Progress Tracker

## Current status

Subplan 90 is paused without changing its immutable r19/r20 evidence. The user explicitly opened
Subplan 93 to remove v1 bounded runtime compatibility, including its strict repeated-cycle detector,
legacy override selection and injected-Provider fallback. Implementation is active on
`refactor/remove-v1-bounded`; no Live request is authorized or required.

Subplan 90 was explicitly resumed from the consolidated local `main`. The harness self-check,
permission matrix, historical capacity audit, reduced-plan preflight and capacity check passed.
The explicit risk-denial repair, cumulative prior-campaign capacity guard, full reservation check
and explicit 14-run reduced single-repetition variant are implemented. Pi-aligned transient Provider
classification, partial-usage evaluation and Pi-compatible optional tool-call text normalization are
now implemented. The reduced continuation contract is also implemented and binds an exact immutable
parent prefix before any tail admission. Its evaluator tests passed `67`, and the complete offline
gate passed `1369 passed, 2 deselected`. The r20 continuation then finalized all eight tail entries;
r19+r20 combine into 14/14 structurally valid reduced-campaign bundles.

The post-evaluation context-policy repair is implemented and verified. New configured-Provider runs
default to long-horizon v2 unless an explicit legacy v1 override requires compatibility. Unknown
windows compact at 256 KiB; exact context/output capabilities drive token accounting and reserve.
S7P-09 isolated Morrow admission now freezes the plan's generic context/output capabilities instead
of copying `capabilities: null`. Existing v1 snapshots and r19/r20 evidence remain unchanged.

Unexpected AgentLoop failures now retain one fixed internal source on the existing terminal
observation. The public event lifecycle and stop code remain unchanged, and observation failure is
explicitly covered as fail-open. No new module, callback framework, policy decision, or schema
migration was introduced.

## Active task

Subplan 93 implementation and validation are complete. The focused runtime/policy/preparation suite
passes `139`; the final complete offline gate passes `1362 passed, 2 deselected`. Ruff format/check,
compileall, both CLI help commands and `git diff --check` pass. Integration into local `main` is the
remaining mechanical step; no Live request was run.

## Implemented boundary

- Accepted outcomes create Learning Reviews regardless of recorded tool failures or unresolved
  items; the background Reviewer decides whether there is anything to save.
- Production Learning Review composition reuses the main Agent model, maximum run time and context
  budget. Separate Learning timeout/lease settings were removed.
- Preference Review jobs JSON serializes datetime fields through the public CLI.
- Managed Skill projection at `3670860` passed a real explicit Skill run; its test Binding was
  removed afterward.

- Core `bash` accepts shell commands without semantic Git/network/destructive classification.
- Explicit `network`, `git_write` and `privilege_escalation` risk flags are denied before the
  direct registered-command fast path; ordinary command content still is not parsed.
- Registered workspace writes, including delete/move/rename and sandbox promotion, do not wait for
  heuristic approval.
- File, search, Git diff/status and sandbox snapshots do not hide workspace content based on names
  such as `.env`, `secret`, `credentials` or PEM-like fixture text.
- Command output redacts exact active credential values, not generic token-shaped source strings.
- Project instructions load one root file by precedence; malformed/large/unreadable context warns
  and skips, and nested paths do not affect discovery or recovery.
- Workspace escape, external symlinks, read-only sessions, revision conflicts, atomic publication,
  timeouts/cancellation/output limits, Full Access grant+approval and Skill/MCP policy remain.

## Next action

Commit and fast-forward integrate the verified Subplan 93 branch, retire it cleanly, and leave
S7P-09 paused until a future campaign is separately authorized and refrozen.

## Blockers

- The r15 reduced pilot used the added 30,000,000-token authorization, raising the active ceiling to
  `80,000,000`. Its cumulative conservative accounting is `52,877,509` tokens, leaving
  `27,122,491`; the 14 admissions therefore stayed within capacity.
- All 14 r15 bundles revalidate, but the Provider/runtime boundary remains the blocker: Morrow is
  `FAIL_RUNTIME` on 10/10 entries with unavailable token usage, Pi is `FAIL_RUNTIME` on 3/4 and
  `BLOCKED_ENV` on 1/4, and the comparison gate rejects incomplete mandatory metrics. No PASS claim
  is made.
- All 14 r16 bundles revalidate. Morrow is 1 `PASS`, 2 `FAIL_MODEL`, 1 `BUDGET_EXHAUSTED` and
  6 `FAIL_RUNTIME`; Pi is 2 `FAIL_MODEL` and 2 `FAIL_RUNTIME`. Mimo completed several formal tasks,
  so it is usable but unreliable under this workload. The standard summary remains incomplete
  because the approved reduced variant has one repetition and some Morrow usage is unavailable.
- Cumulative conservative accounting after r16 is `73,877,509 / 80,000,000`, leaving `6,122,491`.
  No further full or reduced campaign fits without a new budget/scope decision.
- `opencode-go/deepseek-v4-flash` is configured and active in Morrow, and Pi's catalog exposes the
  same exact model. The first Morrow readiness request failed in 1.7 seconds as
  `invalid_response`, with zero tool calls and unavailable usage. No DeepSeek formal admission was
  consumed.
- The matching Pi no-tool probe completed in 7.5 seconds with 506 input, 30 output and 536 total
  tokens. This rules out a general OpenCode credential/model outage for the probe and identifies
  Morrow's Adapter response contract as the current blocker.
- Structural inspection confirmed the exact incompatibility. DeepSeek streamed usage on nine
  chunks with fixed prompt tokens and increasing completion/total tokens; Morrow's `_merge_usage`
  requires repeated non-null values to be identical, raises `ValueError` on the first increase and
  classifies it as terminal `invalid_response`. Visible content and `stop` were both present.
- The repaired Adapter treats every valid OpenAI-compatible usage payload as a replacement snapshot,
  matching Pi 0.84.2. Malformed usage makes telemetry unavailable but cannot invalidate otherwise
  valid text/tool/finish evidence. The behavior is model-neutral. The repaired real Morrow probe
  completed in one round with 3,150 input, 21 output and 3,171 total tokens.
- r18 request 6 retained complete usage but failed `AssistantMessage.content` validation because
  OpenCode paired valid tool calls with whitespace-only optional text. Equal-size diagnostic samples
  reproduced the same Pydantic field failure. After normalization, a Live equal-size sample observed
  the same Provider variant and completed normally with a valid tool call.
- r19 has six immutable finalized entries. Its sixth Pi run alone consumed 21,553,066 tokens, so the
  original 30M plan is over ceiling and cannot admit entry seven. The approved 50M continuation has
  now completed all eight remaining entries. Final cumulative conservative accounting is
  `47,987,509 / 50,000,000`, leaving `2,012,491`.
- Combined known bundle usage is 25,738,704 tokens, but three Morrow bundles preserve unavailable
  total-token fields. This blocks the mandatory complete-usage comparison gate even though all
  admission, schedule and bundle-integrity checks pass.
