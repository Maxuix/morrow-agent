# Progress Tracker

## Current status

Subplan 90 was explicitly resumed from the consolidated local `main`. The harness self-check,
permission matrix, historical capacity audit, reduced-plan preflight and capacity check passed.
The explicit risk-denial repair, cumulative prior-campaign capacity guard, full reservation check
and explicit 14-run reduced single-repetition variant are implemented. Pi-aligned transient Provider
classification, partial-usage evaluation and Pi-compatible optional tool-call text normalization are
now implemented. The latest Provider tests passed `74` with one explicit Live test skipped, and the
complete offline gate passed `1365 passed, 2 deselected`.

## Active task

The ordinary bundled retry default is aligned with Pi at three and committed as `c0b14b9`. Fresh
reduced plan r14 passed plan-check, source/evidence preflight and permission equivalence, but its
full remaining-schedule capacity check blocked before admission. After the user's additional
30,000,000-token authorization, fresh r15 was admitted and all 14 entries were executed. After the
Provider retry repair and successful short probes, fresh r16 was pinned at `main@90b0e9b` and all
14 entries were executed once.

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

Retain r16 as the latest reduced evaluation. Do not silently retry any admitted run key. Full
Subplan 90 completion still requires a separately authorized primary two-repetition campaign with
complete mandatory usage and enough capacity; the current ceiling leaves only `6,122,491` tokens.
The requested DeepSeek replacement is paused before campaign creation: its first Morrow no-tool
probe returned `invalid_response` after one model attempt with no usage. The authorized Pi probe
then completed normally with 536 tokens and complete usage, isolating the blocker to Morrow's
Adapter response handling. The compatibility path is now repaired and re-probed successfully.
The retained r17 attempt stopped after three runs at its original 21M capacity boundary. The
user-authorized cumulative 30M r18 plan then stopped after its first admission when a sixth model
request returned `invalid_response`. Safe exact-size sampling reproduced the cause as
whitespace-only optional content accompanying valid tool calls. The generic repair is verified.
Next, commit it and refreeze a new DeepSeek 14-run plan from the new clean source; do not resume or
rewrite r17/r18 admissions.

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
