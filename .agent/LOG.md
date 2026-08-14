# Execution Log

## 2026-08-13 — Initialize large-plan workflow

- Added `.agent/subplans/` for ordered child plans.
- Added the Large Plans workflow to the repository `AGENTS.md`.
- Added `.agent/subplans/README.md` with naming, contents, and activation rules.
- Added the missing `.agent/PLAN.md`, `.agent/TODO.md`, `.agent/TRACKER.md`, and `.agent/LOG.md` execution files.
- No product code or architecture behavior changed.

## 2026-08-13 — Draft and review the Stage 1 implementation plan

- Initialized a local Git repository on branch `main` and added baseline ignore rules; no commit was created.
- Replaced the placeholder execution plan with an ordered 11-subplan Stage 1A/1B implementation plan driven only by validation gates, with Subplan 01 selected and no implementation task started.
- Removed Stage 1 schedule estimates and retained the explicitly approved gated natural-language configuration scope.
- Ran two read-only Grok reviews with `grok-4.6` at `xhigh` effort and independently checked their findings.
- Closed the first review's exit/Handoff and configuration-gate blockers plus its architecture, state, context, command, and acceptance gaps.
- Closed the second review's shared `config.yaml` aggregate-write risk and initial Handoff decision-uniqueness gap, and incorporated its remaining execution clarifications.
- The third read-only Grok review reported no P0/P1 findings and accepted the plan. Its sole non-blocking suggestion—explicit `/handoff update` cancellation with no fallback or write—was incorporated and independently checked.
- Accepted the Stage 1 plan for execution. No product code was created and Subplan 01 remains selected but not started.

## 2026-08-13 — Subplan 01 baseline completed

- Created the Python package, locked environment, Typer entry point, injectable bootstrap seam, and terminal stream cancellation/EOF pattern.
- Added offline deterministic tests for event contracts, workspace isolation, atomic state behavior, context construction, runtime lifecycle, configuration gating, and terminal cancellation.
- Verified `uv run morrow --help`, `uv run ruff format --check .`, `uv run ruff check .`, compile checks, and `uv run pytest -m 'not live'` (22 passed).
- Activated Subplan 02 for core contract and test-double hardening.

## 2026-08-13 — Subplan 02 contracts completed

- Verified the typed domain models, five-event public lifecycle, unknown-field tolerance, dynamic adapter registration, deterministic provider/clock/ID doubles, and architecture import boundary.
- Default offline verification now includes a socket network guard and 23 passing tests.
- Activated Subplan 03 for workspace identity and safe state storage hardening.

## 2026-08-13 — Subplan 03 state boundaries completed

- Implemented the data-root layout, exact metadata-only workspace resolution path, explicit candidate confirmation, relink, isolated ProjectStateStore facade, version/revision-aware YAML outcomes, atomic replacement with one valid backup, and a REPL-lifetime workspace writer lock.
- Verified corrupt/future-schema/revision-conflict behavior, failed replacement preservation, isolation, relink retention, and 25 offline tests.
- Activated Subplan 04 for Provider onboarding and adapter hardening.

## 2026-08-13 — Subplans 04–05 completed

- Provider configuration is data-driven, credential references are versioned, onboarding explicitly tests before publishing, and local inspection remains offline; a marked OpenCode Go Live smoke test is present.
- ContextBuilder is the sole state-to-model path; structured completion supports JSON extraction, one repair, typed validation, and deterministic Handoff fallback.
- Verified retry-before-visible-output, cancellation, oversized-input rejection, history admission, and 32 offline tests.
- Activated Subplan 06 for REPL and orchestration completion.

## 2026-08-13 — Subplan 06 orchestration completed

- Connected the thin CLI/REPL to SessionOrchestrator, added slash-command ownership to CommandService, safe clean-session `/new` and `/continue`, explicit confirmation for destructive state commands, and cancellation-aware exit handling.
- Deterministic `/config edit`, `/workspace edit`, and `/handoff edit` use the shared ConfigPatch path; local commands remain model/network free.
- Activated Subplan 07 for the Stage 1A Handoff and acceptance gate.

## 2026-08-13 — Subplans 07–08 completed

- Stage 1A evidence is recorded in `docs/acceptance/stage-1a-evidence.md`; offline gates pass and the explicit Live test is registered but not run without a user credential.
- Added conservative natural-language configuration gating, mixed-task and forbidden-field rejection, shared ConfigPatch validation, three-layer snapshot refresh, `/config` deterministic edits/resets, and safe Handoff/profile command paths.
- Verified 39 offline tests and activated Subplan 09 for safe session/state transitions.

## 2026-08-13 — Subplans 09–10 completed

- Completed safe dirty-session transitions, scoped Profile/Handoff reset/clear, explicit Handoff update/edit, Provider configure/test, offline model inspection, relink, future-schema read-only startup handling, and backup inspection.
- Added Stage 1B traceability in `docs/acceptance/stage-1b-evidence.md`; the complete offline suite reached 41 passing tests with one expected Live deselection.
- Activated Subplan 11 for final acceptance and delivery documentation.

## 2026-08-13 — Stage 1 implementation plan completed

- Final offline acceptance passed: 42 non-Live tests, strict marker collection, Ruff format/check, compile check, CLI help, local offline commands, secret-sentinel scan, and no prebuilt Stage 2 capability modules.
- Added README setup/command/recovery guidance plus Stage 1A and Stage 1B traceability evidence.
- The marked OpenCode Go Live smoke test and real-terminal/manual checklist are present but not executed because no real API Key or separate manual test projects were supplied; this is recorded rather than treated as a pass.
- Stage 1 implementation is complete for the available environment; next action is independent Grok review followed by Sol Max analysis.

## 2026-08-13 — Stage 1 review remediation reopened

- Grok reviewed the generated code and found two P0 blockers: nested Git workspaces could incorrectly reuse a registered parent, and a failed dirty-session save during `/new` or `/continue` could reset the live session.
- Sol Max received the complete Grok report and confirmed those P0s plus Stage 1 gate P1 issues around provider probes, writer-lock wiring, command result propagation, configuration/reset safety, streaming/cancellation, state ownership, and recovery.
- Reopened subplan 11 for implementation, regression coverage, and a fresh acceptance run.

## 2026-08-13 — Review remediation and final offline acceptance

- Fixed Git workspace identity boundaries, dirty-session save-failure preservation, non-empty Provider probes, REPL-lifetime writer locking, command result value propagation, reset snapshot refresh, configuration preview/confirmation, real event streaming, cancellation handling, Decision removal, environment credential lookup, empty model completion handling, provider API model mapping, and lock-safe backup-preserving clears.
- Moved startup state publication behind `WorkspaceStateService`, moved shared preference merge rules into core, and moved structured completion into the runtime boundary; terminal and CLI state mutations now route through application/service use cases.
- Added regression coverage for nested Git repositories, repository-root candidates, independent save failure, streaming order, empty completion, Decision removal, independent Handoff isolation, and non-empty Provider probes.
- Final validation passed: `ruff format --check`, `ruff check`, `pytest -m 'not live'` (48 passed, 1 expected Live deselection), strict-marker collection (49 tests), compileall, CLI help, and Stage 2 boundary checks.
- Stage 1 is complete for the available environment. The real OpenCode Go request and real-terminal/manual checklist remain pending a credentialed external run and isolated test projects; they are not represented as passed.

## 2026-08-13 — Live Provider and terminal smoke evidence received

- User ran the full local suite: `49 passed in 4.56s`.
- User ran the Live test with a real credential: `1 passed, 48 deselected in 5.24s`.
- User completed an initial real isolated workspace smoke run: workspace registration, Provider onboarding, Profile/Handoff initialization, visible model response, and `/exit` all succeeded; the run exposed that the newly created Handoff was automatically loaded despite the `/continue` prompt.
- Removed that automatic load path. The corrected manual acceptance then passed: independent start confirmation, `/continue`, `/handoff update`, long-response Ctrl+C with continued conversation, Ctrl+D exit, and second-project/relink verification.

## 2026-08-13 — Stage 1 external acceptance completed

- Codex directly controlled the credentialed terminal after Keychain access was authorized.
- Verified that a newly discovered Handoff is displayed but not loaded into the initial independent session.
- Verified `/exit` confirmation for a dirty independent session, explicit `/continue` loading, `/handoff update`, long-response Ctrl+C recovery, continued conversation after cancellation, and Ctrl+D exit with Handoff persistence.
- Verified `workspace relink` to a second temporary directory while retaining the original workspace ID and Handoff state.
- Stage 1 offline, Live, and manual acceptance is complete; Stage 2 implementation is unblocked.
- Stage 2 has not started. The next action is a separate Stage 2 discussion and scope/plan definition; no Stage 2 implementation should be inferred from the Stage 1 completion.

## 2026-08-14 — 独立复核：外部 P1/P2 清单逐条验证

对 Stage 2 启动前提交的 P1/P2 问题清单做代码核对与运行复现，全部 14 项 P1 与 7 项 P2 均确认存在（复现细节见会话报告）：

- 身份不唯一：同一 candidate 可重复 confirm 出两个 workspace ID；relink 不按 Git 根判重，可让两个 ID 指向同一仓库根。
- 清除破坏 revision 单调性：clear 返回 rev+1 但落盘回到 rev0，陈旧 expected_revision=0 写入可成功（复现 rev1/rev2→clear 报 rev3→陈旧写入成功为 rev1）。
- 事件/会话 ID：id(object()) 同一回合产生重复 event_id；每次 bootstrap 新建 FixedIdSource，生产会话全为 ses_1；注入 id_source 时 workspace 工厂抛 TypeError。
- 生命周期/完成语义：Provider 直接抛异常只发 turn.started；OpenAI 适配器把 finish_reason=length 等映射为 STOP，截断文本进历史并视为成功。
- 配置门误拦截普通聊天（“请解释 provider 是什么”零调用被拒）；config_patch+patch=None 退回普通聊天；预览文案不含 scope/target/字段/值；`/config edit`、`/workspace edit`、`/handoff edit` 预览前直接写盘。
- 环境变量凭据不参与 build_active（Keyring 无值时报凭据不可用）；configure --base-url 在无环境变量时强制重新输入密钥。
- structured 修复调用重复使用完整 timeout（0.1 预算实测 0.18s），修复请求不含原始指令与 Schema 目标。
- dirty 独立会话 EOF→/exit→EOF 忙循环（实测 491ms 内 30000 次循环，进程不退出）。
- 只读降级未拦截 /continue（read_only=True 仍可载入 Handoff）。
- 测试证据高估：无“连续十轮”测试；writer lock 为同进程双实例；Live 测试只断言可见文本；CLI 自动化仅 provider list/model current；dirty EOF、异常生命周期、环境凭据、重复身份等路径无覆盖。
- P2：历史裁剪可产生孤立 assistant；Handoff 接受空 current_goal；清除绕过临时文件/fsync 发布流程且不 fsync 父目录；provider test 失败不写 error_code 且 CLI 退出码 0；provider show 只查 credential_ref；README“所有状态写入”表述与实际不符；ROADMAP 仍写“Live/人工复核待执行”与 PLAN/TRACKER 冲突。

未修改任何代码与验收文档；待决策是否重开 Stage 1 修复。

## 2026-08-14 — Stage 1 remediation plan approved

- Reopened Stage 1 and split the 14 confirmed P1 plus 7 confirmed P2 findings into ordered Subplans 12–16: workspace identity/durable state, runtime/terminal/read-only, configuration/Provider/structured completion, context/domain invariants, and final truth reconciliation.
- Updated the active roadmap and architecture contracts before implementation: version-2 present/cleared workspace envelopes, canonical relink identity, workspace-state degraded boundaries, exact Provider finish/error semantics, credential precedence/rotation, deterministic Handoff fallback sourcing, and post-remediation Stage gates.
- Grok 4.6 xhigh performed three read-only plan reviews. Two `CHANGES REQUIRED` verdicts were incorporated; the final verdict was `APPROVE WITH NON-BLOCKING SUGGESTIONS`, with no blocking findings. The three final clarifications (two-axis state load results, unloaded-Handoff independent save, and idempotent-clear revision wording) were incorporated without changing product scope.
- No product code or tests were changed/run during planning. Stage 2 remains blocked until Subplans 12–16 and the rebuilt offline/Live/manual evidence pass.

## 2026-08-14 — Subplan 12 identity regressions reproduced

- Added regression coverage for repeated confirmation of a stale candidate, concurrent confirmation from separate processes, and relink into an already-owned effective Git root.
- The targeted red run failed on all three confirmed defects: duplicate sequential IDs, a concurrent confirmation error, and an accepted relink collision.
- Refactored index mutations so confirmation and relink decide canonical ownership while holding the index lock. Repeated and concurrent claims now return one authoritative ID, and relink publishes the nearest Git root or rejects an existing owner without changing index bytes.
- Identity validation passed: 17 workspace/state tests plus targeted Ruff check and format check.

## 2026-08-14 — Subplan 12 version-2 workspace envelope implemented

- Added explicit missing/cleared/present load presence for workspace Preferences, Profile, and Handoff while keeping load status limited to ok/corrupt/unsupported schema.
- Clear now publishes a payload-free version-2 tombstone through the validated atomic path, preserves the prior present document as backup, retains its revision, rejects stale recreation, and is idempotent for missing/already-cleared state.
- Version-1 workspace documents remain byte-unchanged on read and upgrade only on successful mutation; corrupt and future-schema documents remain byte-preserved and non-overwritable.
- The complete non-Live suite passed with 70 tests and one expected Live deselection before durability coverage began.

## 2026-08-14 — Subplan 12 durability and multiprocess coverage completed

- Added parent-directory synchronization after backup and primary replacement, with injected failure coverage for temporary write, file fsync, replacement, and directory fsync on both present writes and clears.
- Added genuine separate-process coverage for the REPL-lifetime workspace writer lock and competing Handoff writes/clears; exactly one competing mutation publishes and the other receives a revision conflict.
- Workspace backup inspection now distinguishes a missing primary, a cleared primary, a present backup, a missing backup, corrupt state, and unsupported schema through the typed load result.
- The targeted state/workspace suite passed with 44 tests, including multiprocess cases, and targeted Ruff check/format passed after formatting the new tests.

## 2026-08-14 — Subplan 12 completed

- Final targeted validation passed with 44 state/workspace tests.
- Final aggregate validation passed with 82 non-Live tests and one expected Live deselection, repository-wide Ruff format/check, and compileall over `src` and `tests`.
- Activated Subplan 13. Stage 2 remains blocked pending Subplans 13–16 and rebuilt final-tree acceptance evidence.

## 2026-08-14 — Subplan 13 ID regressions reproduced

- Added regressions for event uniqueness across turns and one injected application ID source driving workspace and session IDs.
- The targeted red run reproduced duplicate default event IDs and the zero-argument workspace factory `TypeError`; the session-ID assertion remains blocked behind that factory defect.
- Added one production `RandomIdSource` per application composition and threaded it through workspace, session creation/reset, runtime turns, and public events; production no longer imports `FixedIdSource`.
- ID validation passed in a 69-test targeted integration set plus targeted Ruff check/format.

## 2026-08-14 — Subplan 13 runtime lifecycle and finish mapping completed

- `AgentRuntime` now emits one start and one completion for direct Provider exceptions, adapter errors, abnormal/missing finishes, cancellation, empty visible output, and normal completion; partial failed output never enters assistant history.
- The OpenAI-compatible adapter accepts only an explicit `stop`, maps truncation/content-filter/tool/missing/malformed stream endings to `invalid_response`, and isolates reasoning-only content.
- Targeted runtime/adapter validation passed with 27 non-Live tests and one expected Live deselection; targeted Ruff check/format passed.

## 2026-08-14 — Subplan 13 terminal regressions reproduced

- Added deterministic coverage for clean EOF, dirty independent repeated EOF, closed independent switch prompts, dirty continuation EOF save, cancellation during continuation save, and successful conversation after a cancelled turn.
- The targeted red run reproduced the busy loop as a timeout and the independent switch prompt's uncaught `EOFError`; the other newly covered paths passed.
- Added explicit closed-input outcomes. EOF during required exit or independent switch confirmation now warns once and returns code 2 without saving, resetting, or switching; clean EOF and continuation-save EOF retain their defined behavior.
- Targeted terminal/orchestration/runtime validation passed with 37 tests; targeted Ruff check/format passed.

## 2026-08-14 — Subplan 13 degraded-mode regressions reproduced

- Added integration coverage for corrupt Profile, unsupported Handoff, and corrupt workspace Preferences across `/continue`, workspace mutations, session/global Preferences, chat, Provider inspection, exit, and byte preservation.
- The targeted red run confirmed that Profile/Handoff degradation is not propagated into constructed sessions and workspace-Preferences corruption has no isolated non-overwritable session flag.
- Startup inspection now carries Profile/Handoff workspace-wide degradation and isolated workspace-Preferences degradation into the session. Command/config/Handoff boundaries block only the prohibited continuity and workspace-persistence paths.
- Integration coverage confirms chat, session/global Preferences, Provider inspection, valid counterpart display, and Preferences-only continuity remain available as specified; workspace bytes remain unchanged in workspace-wide degraded exit.
- The complete non-Live suite passed with 107 tests and one expected Live deselection; targeted Ruff check/format passed before final aggregate gates.

## 2026-08-14 — Subplan 13 completed

- Final targeted runtime/adapter/terminal/orchestration validation passed with 51 tests and one expected Live deselection.
- Final aggregate validation passed with 107 non-Live tests and one expected Live deselection, repository-wide Ruff format/check, and compileall over `src` and `tests`.
- Activated Subplan 14. Stage 2 remains blocked pending Subplans 14–16 and rebuilt final-tree acceptance evidence.

## 2026-08-14 — Subplan 14 configuration routing regressions reproduced

- Expanded must-trigger, sensitive-vocabulary must-not-trigger, forbidden persistence, and extraction-shape corpora, plus orchestration call/side-effect assertions.
- The targeted red run produced 13 failures: ordinary Provider/credential/security discussion was rejected, every inconsistent `ConfigExtractionResult` shape validated, and invalid config patches silently fell through to normal streaming instead of repairing/failing closed.
- Forbidden-field checks now run only after local persistence intent is established, and `ConfigExtractionResult` enforces exact result-specific question/patch shapes with a bounded non-empty clarification question.
- Focused routing/shape validation passed with 20 tests; the broader configuration/structured set passed with 43 tests.
- Deterministic `/config edit`, `/workspace edit`, `/handoff edit`, and natural-language extraction now return the same validated pending patch and exact scope/target/operation/field/value preview; no mutation occurs before terminal confirmation.
- Focused single- and multi-operation preview tests pass.
- Added one shared environment-first credential resolver path for active construction, configure, test, and offline show. Non-secret configure reuses the resolved credential and `--replace-credential` refuses while an environment credential masks the store.
- Focused environment credential, base-URL configure, rotation refusal, and existing reconfigure tests pass.
- Structured completion now uses one monotonic deadline across initial and repair calls; the repair prompt retains the original task, target JSON Schema, and sanitized validation type while state still enters through `ContextBuilder`.
- Focused structured completion/Handoff validation passes with seven tests, including the timing bound.
- Added typed `ModelProviderError` propagation into persisted `LastTestResult.error_code`; explicit failed Provider tests now return CLI status 2 with sanitized diagnostics, and offline show reports resolver availability.
- Focused Provider/CLI validation passes with 16 non-Live tests and one expected Live deselection.

## 2026-08-14 — Subplan 14 completed

- Final targeted configuration/Provider/structured/CLI validation passed with 62 tests and one expected Live deselection.
- Final aggregate validation passed with 134 non-Live tests and one expected Live deselection, repository-wide Ruff format/check, and compileall over `src` and `tests`.
- Activated Subplan 15. Stage 2 remains blocked pending Subplans 15–16 and rebuilt final-tree acceptance evidence.

## 2026-08-14 — Subplan 15 atomic context pruning completed

- Added regressions for orphan-assistant selection, oversized newest turns, unmatched cancelled/error users, and mandatory fixed/current overflow.
- Context selection now groups atomic completed user/assistant turns plus valid lone users, walks newest to oldest without skipping, and fails mandatory overflow before Provider invocation.
- The targeted context/runtime suite passes with 21 tests.

## 2026-08-14 — Subplan 15 domain invariants implemented

- `Handoff.current_goal` is trimmed and non-empty for every present payload. Workspace document revisions are non-negative and timestamps timezone-aware; violating on-disk documents load as corrupt without byte changes.
- Regression coverage verifies continuation-copy fallback, independent fallback with present/cleared unloaded disk state, fixed safe goal derivation, backup/current compatibility, and payload-free clears.
- Targeted core/state/structured validation passed with 64 tests; focused Handoff validation passed with 10 tests.

## 2026-08-14 — Subplan 15 completed

- Final targeted context/model/state/Handoff validation passed with 87 tests.
- Final aggregate validation passed with 148 non-Live tests and one expected Live deselection, repository-wide Ruff format/check, and compileall over `src` and `tests`.
- Activated Subplan 16. Stage 2 remains blocked pending rebuilt final-tree offline, Live, manual, and documentation evidence.

## 2026-08-14 — Subplan 16 offline acceptance passed; Live/manual blocked

- Added and passed the missing ten-turn ordered-history/stream-delta integration test.
- Final-tree offline acceptance passed: 149 non-Live tests with one expected Live deselection; strict collection registered 150 tests; Ruff format/check, compileall, CLI help, import smoke, Stage 2 boundary, and a 55-test boundary/multiprocess/terminal/CLI subset passed.
- Rebuilt Stage 1A/1B evidence and the confirmed-finding regression matrix, and updated README semantics for preview, credentials, tombstones, durability, and degraded mode.
- The environment reported `live_credential=missing`. The final-tree OpenCode Go and real-terminal/manual checklist was not executed and is recorded as pending. Subplan 16 and Stage 1 remain incomplete; Stage 2 remains blocked.

## 2026-08-14 — Subplan 16 and Stage 1 remediation completed

- The supplied credential was injected only through non-echoing stdin into temporary shells and cleared afterward. The explicit final-tree Live test passed with `1 passed, 149 deselected`; a separate sanitized stream inspection observed five visible deltas, exactly one normal completion, zero errors, and zero public reasoning fields.
- In an isolated temporary state root, empty-state Provider onboarding passed and a real terminal completed ten ordered turns, long-response Ctrl+C, successful post-cancel chat, `/handoff update`, and Ctrl+D exit. Startup displayed but did not auto-load the available Handoff.
- Two ordinary projects and two genuine Git worktrees received distinct identities. Project sentinels and Git status remained unchanged. Moving project A and explicitly relinking retained its workspace ID, Profile, and Handoff; explicit `/continue` loaded the retained revision.
- An intentionally offline continuation produced a sanitized network failure, then Ctrl+D saved a deterministic fallback. The next online startup displayed the incremented revision and explicit `/continue` loaded it.
- Exact credential-sentinel scans over temporary YAML/backups/state, repository text, and project directories passed; no terminal echo, project-content mutation, or product Git/Shell subprocess was observed.
- Reconciled PLAN, TODO, TRACKER, roadmap, Stage 1 roadmap, and Stage 1A/1B acceptance evidence. Subplan 16 and Stage 1 remediation are complete; Stage 2 is unblocked but has not started and requires a separate scope/plan decision.

## 2026-08-14 — Completed remediation synchronized to primary workspace

- Synchronized the completed Subplans 12–16 implementation, tests, acceptance evidence, and execution state from the isolated execution worktree into the primary workspace while preserving the primary `AGENTS.md` workflow update.
- Primary-workspace validation passed: 149 non-Live tests with one expected Live deselection, Ruff format/check, compileall over `src` and `tests`, and `git diff --check`.
- `PLAN.md` and `ROADMAP.md` now record Stage 1 as complete and Stage 2 as unblocked but not started.
