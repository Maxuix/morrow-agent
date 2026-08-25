# Execution Log

## 2026-08-21 — Subplan 53.1 v12 selection persistence completed

- Added bounded `MemoryQuery`, immutable `MemorySelection`/`MemorySelectionItem`, closed reason
  codes, and rebuildable `MemorySearchTerm` contracts without introducing a second authority.
- Added the v12 `memory_selections`, `memory_selection_items`, and `memory_search_terms` schema,
  workspace/FK guards, shared-transaction repository, codecs, and narrow persistence port.
- Preserved v1–v11 migration behavior, updated legacy expectations to pin v11 where required, and
  added v11→v12 upgrade, failed-migration rollback, workspace isolation, round-trip, and corruption
  coverage.
- Focused validation passed: 83 tests, Ruff format/check, compileall, and `git diff --check`.
- Marked S53.1 complete and activated S53.2. No Grok review was run because the required review is
  per completed subplan, not per internal task.

## 2026-08-21 — Subplan 53.2 lexical memory projection completed

- Added a pure bounded tokenizer for normalized Latin words, dotted/snake/kebab/camel identifiers,
  paths, meaningful version/number tokens, CJK bigrams, fixed stop tokens, and deterministic
  per-record/query budgets; it performs no filesystem or network work.
- Added an independent transactional term projection service with rebuild, clear, and bounded
  lexical candidate retrieval. Project Knowledge promotion clears superseded revision terms;
  disable/dispute/delete clear the current projection; enable rebuilds it in the same SQLite
  transaction.
- Added mixed-language/token-budget/retrieval/lifecycle coverage and promotion assertions for term
  activation and supersession. Full offline validation passed: 760 passed, 2 skipped, 1 deselected;
  Ruff format/check, compileall, and `git diff --check` also passed.
- Marked S53.2 complete and activated S53.3. The required Grok review remains deferred until all
  S53 tasks are complete, per the user's once-per-subplan instruction.

## 2026-08-21 — Subplan 53.3 deterministic selector completed

- Added an independent selector over bounded active Knowledge heads and rebuildable lexical hits.
  It hard-filters workspace/current revision/status/validity/sensitivity, then applies explicit-key
  priority, requested categories, lexical overlap, bounded confirmation recency, deterministic
  tie-breaks, category diversity, item/character budgets, and explainable reason codes.
- Added canonical untrusted Project Knowledge rendering and content digests shared by selection and
  the future frozen Context projection. Zero-item and omitted-item selections remain valid.
- Added deterministic, diversity, budget, expiry, and prohibited-revision coverage. Full offline
  validation passed: 763 passed, 2 skipped, 1 deselected; Ruff format/check, compileall, and
  `git diff --check` also passed.
- Marked S53.3 complete and activated S53.4. Grok review remains deferred until Subplan 53 is
  complete, as required.

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

## 2026-08-14 — Stage 2 design baseline published

- Published the locked Stage 2 scope and contracts in `docs/roadmap/stage-2-agent-core.md`: OpenAI-compatible request/response wire, adapter-only provider conversion, discriminated messages, process-local ConversationLog, atomic ToolCycle validation, minimal terminal records, ModelCallRunner/AgentLoop ownership, deterministic Tool Executor results, cancellation and budgets, and ContextBuilder result clearing plus legal hard trimming.
- Explicitly deferred persistent history, ContextSummary/LLM compaction, real local tools, real parallel execution, MCP, Skills, and plugins beyond Stage 2.
- Added the Stage 2 implementation order and acceptance matrix while leaving only numeric defaults, demo-tool selection, the JSON Schema validation library, and final public tool-event names for implementation planning.
- Replaced the completed Stage 1 active plan with the completed Stage 2 design-publication plan and reconciled ROADMAP, TODO, and TRACKER status. No production or test code changed.
- Documentation verification passed: balanced fenced blocks, expected section coverage, and `git diff --check`.

## 2026-08-14 — Stage 2 final approval proposal published

- Published `docs/reviews/stage-2-agent-core-final-proposal.md` as a standalone approval draft without replacing the current authoritative roadmap or changing production/test code.
- Reconciled the post-baseline decisions: Adapter-owned fragment assembly, module boundaries, immutable cross-module DTOs, public `tool.status`, AgentStopCode, developer TOML policy, ProviderCapabilities, ToolCycle output budgeting, loop detection, Draft 2020-12 validation, demo tools, system boundary, terminal behavior, and module-based subplans.
- The proposal contains no implementation-time Stage 2 design placeholder; ContextSummary, persistent history, real local tools, MCP, Skills, and plugins remain deferred.
- Documentation validation passed: 1,412 lines, 102 balanced fenced-block markers, required section coverage, valid local link targets, and `git diff --check`.

## 2026-08-14 — Stage 2 proposal review adjudicated

- Verified the independent review against the actual Stage 1 runtime, Session, ContextBuilder, OpenAI-compatible Adapter, Handoff fallback, StructuredCompletion, terminal renderer, boundary test, and Stage 3/4 scope documents.
- Accepted the late-integration, Stage 1 seam, Handoff/StructuredCompletion projection, per-call deadline, mixed-content terminal, Pydantic validation, unused Anthropic fixture, standalone RequestSizer, third demo tool, and implementation-sequencing findings.
- Retained with narrower contracts the explicitly requested module boundaries, developer-owned configuration, minimal terminal records, deterministic old-result clearing, whole-Cycle output bound, repeat-loop early stop, and precise AgentStopCode classification.
- Replaced nine module-gated subplans with four vertical slices whose first slice must run the complete model → tool → model E2E. Clarified one chat history-writing path and added product-facing regression gates.
- Revised the proposal from 1,412 to 1,274 lines. Documentation validation passed with 80 balanced fenced-block markers, required section coverage, valid proposal/review/roadmap links, and `git diff --check`; no production or test code changed.

## 2026-08-15 — Stage 2 executability-review conditions applied

- Applied R1 by moving the capability-based stage-boundary guard rewrite to the first Slice 1 change and correcting the old directory-name/file-name wording.
- Applied R2 by making Session-owned ConversationLog, `run_task()` sole history writes, read-only `Session.messages`, and the thin `run_turn()` delegate Slice 1 deliverables; Slice 2 now only migrates remaining readers and fixtures.
- Resolved C1–C3 with exact skipped-call envelope/event mappings, internal `provider_length` versus `tool_cycle_too_large` stop details, and exact-ModelRef `safe_request_chars` declarations in the developer TOML.
- Made C4 explicit as an offline `Terminal.show_event` event-sequence test in Slice 3 and the acceptance matrix.
- The amended approval draft has 1,304 lines and 82 balanced fenced-block markers. Link targets and `git diff --check` pass. No production code, tests, authoritative roadmap, active implementation plan, or subplans were changed.

## 2026-08-14 — Independent review of the Stage 2 approval draft

- Reviewed the approval draft against the authoritative Stage 2 roadmap, architecture baseline, Stage 1 runtime/context/handoff code, later-stage boundaries, and current agent-framework options.
- Published `docs/reviews/stage-2-agent-core-final-proposal-review.md`.
- Verdict: Stage 2 should proceed, but the draft should not become the implementation contract. Keep the tool-loop invariants; drop or defer the policy stack, loop detector, cycle-volume accounting, Anthropic fixtures, and nine-module plan.
- No production or test code was changed. Implementation remains blocked on a converged Stage 2 spec.

## 2026-08-15 — Executability review of the revised Stage 2 proposal

- Reviewed the revised approval draft (`docs/reviews/stage-2-agent-core-final-proposal.md`, 2026-08-14) against the actual Stage 1 tree: Session write API, `AgentRuntime.run_turn`, ContextBuilder, OpenAI-compatible Adapter, Handoff fallback, StructuredCompletion, terminal renderer, event helpers, boundary test, and pyproject dependencies.
- Every factual claim the revised proposal makes about Stage 1 code was verified accurate, including its correction of the earlier review (the terminal does not re-render `turn.completed.text`; `completion_payload` carries no `text` field today). Migration surface is small (14 `accept_*` references across 5 files) and the plan needs zero new dependencies.
- Published `docs/reviews/stage-2-agent-core-revised-proposal-review.md`. Verdict: executable, recommend conditional approval.
- Two required amendments, both confined to the slice plan: move the stage-boundary test rewrite into Slice 1 (or forbid new `tools/`/`loop/` directories in Slices 1–3), and pull "Session holds ConversationLog with a single write path" forward into Slice 1 instead of Slice 2. Also listed four one-sentence clarifications (skipped→envelope mapping, `model_output_limit` dual cause, `safe_request_chars` data source, offline-testable terminal segmentation) and a six-item reconciliation list for merging the proposal into the authoritative roadmap.
- No production or test code was changed.

## 2026-08-15 — Stage 2 implementation plan activated

- Converted the approved Stage 2 design into one active master plan and four ordered vertical subplans: Walking Skeleton; History/Context/Product Projections; Guardrails/Policy/Observability; and Acceptance/Delivery.
- Made the Slice 1 order executable: capability-based stage guards first, then strict wire/Adapter work, Session-owned ConversationLog and the sole AgentLoop history path, minimal tools, and the first two-tool-step E2E.
- Kept production tools disabled until the Slice 3 policy/cancellation/budget gate, avoiding an unbounded intermediate product path while still requiring an integrated Slice 1 E2E.
- Assigned full ToolCycle/context/Handoff projections to Slice 2; configured limits, Cycle bounds, cancellation closure, progress-aware retry, loop detection and terminal events to Slice 3; and final Stage 1/2/package/terminal evidence to Slice 4.
- Promoted the revised proposal from pending approval record to approved decision history and reconciled the formal Stage 2 roadmap: thin `run_turn()`, chronological result clearing, Pydantic validation, developer policy, Cycle bounds, loop stopping, public tool/stop events and the four-slice order now have one authority.
- Removed completed Stage 1 subplans from the active subplan directory; Git history remains their archive. No production or test code was changed.

## 2026-08-16 — Stage 2 implementation-plan review applied

- Verified all seven review findings against the active plans and current Stage 1 code. Each finding was valid; the first two exposed real intermediate-slice invariant failures rather than wording issues.
- Moved the minimal no-tools AgentLoop into the same Slice 1 change as Session/ConversationLog migration, so no temporary writer exists before AgentLoop. The later tool extension now owns minimum `cancelled`/`internal` synthetic closure and next-turn recovery from its first E2E.
- Clarified Slice 2’s explicit 24000 Stage 1 context-limit bridge and required ContextBuilder to have no default; Slice 3 removes that bridge and the retry=1 bridge when RunPolicy lands.
- Locked fatal public error payloads to `message + stop_code`, required the following completion to carry the same code, and tasked `PUBLIC_EVENT_TYPES`, `completion_payload` and Stage 1 assertion migration.
- Locked the initial production exact-model table as empty, added longest-loop-pattern validation, and added combined model-attempt/tool-round precedence acceptance.
- Updated the formal Stage 2 roadmap, master plan, Subplans 17–20, TODO and TRACKER. No production or test code was changed.

## 2026-08-16 — S2.17.1 Stage 1 baseline frozen

- Baseline validation before any production change: `uv run pytest -q` → 149 passed, 1 skipped (Live opt-in only, gated behind `MORROW_OPENCODE_GO_API_KEY`); `uv run ruff format --check .` → 57 files formatted; `uv run ruff check .` → clean; `uv run python -m compileall -q src tests` → clean; `git diff --check` → clean.
- History/runtime migration inventory captured with `rg`:
  - Writers: `Session.accept_user`/`accept_assistant` defined at `runtime/session.py:28,33`; production call sites `runtime/agent.py:43` and `runtime/agent.py:139`; `reset()` clears `messages` at `session.py:39`.
  - Readers: `services/handoff.py:32,38` and `application/context.py:80` read `session.messages`; `runtime/agent.py:79` consumes `context.messages`; `runtime/structured.py:54-55` infers message type from `context.messages[0]` (must migrate to explicit variants).
  - Orchestrator routes ordinary chat through `runtime.run_turn` at `application/orchestrator.py:80` — single chat entry exists already.
  - Tests: 12 `accept_*` call sites plus `session.messages` assertions across test_terminal/test_preferences_and_orchestration/test_context_runtime/test_structured_and_handoff; 13 `run_turn` invocations in test_context_runtime.py.
- Worktree check: only `.agent/` planning docs and `docs/` changed (the Stage 2 plan set itself); no production or test file overlaps Stage 2 work.

## 2026-08-16 — S2.17.3 core wire protocol introduced

- Added to `morrow.core.models`: frozen `ProtocolModel` base (extras rejected, assignment blocked); discriminated `Message` union over `SystemMessage`/`UserMessage`/`AssistantMessage`/`ToolMessage`; `FunctionToolCall` and nested `ToolFunction`/`ToolDefinition`; separate internal `ModelFinishReason` (stop/tool_calls/length/content_filter) from public `FinishReason`.
- Protocol enforcement: tool names match `[A-Za-z0-9_-]{1,64}`; call/tool IDs and descriptions non-empty; Assistant requires non-empty content or ≥1 call with unique IDs; arguments stay an untouched string; ordered collections validate to tuples.
- `ModelEvent.completed` now carries the fully assembled `AssistantMessage` plus normalized model finish reason; vendor reasons never validate.
- Migrated every explicit construction site: `runtime/session.py`, `application/context.py`, `runtime/structured.py` (type inference removed, explicit `UserMessage`), `services/provider.py`, `adapters/models/openai_compatible.py` (canonical per-variant `serialize_message` with explicit field whitelist), `runtime/agent.py` (final text authority is the assembled message content), `testing.py` scripted provider, and the Stage 1 test suites (17 construction sites).
- Validation: 168 passed, 1 skipped (Live opt-in); `ruff format --check`, `ruff check`, `compileall`, `git diff --check` all clean. Fourteen new focused protocol contract tests in `tests/test_core_contracts.py`.

## 2026-08-16 — S2.17.4 provider port and OpenAI-compatible accumulation

- Extended `ModelProvider.stream` with an optional ordered `tools` tuple; text-only `complete()` and default streaming send none. `ScriptedModelProvider` records `stream_tools`.
- Canonical request serialization: `serialize_tool` joins the existing `serialize_message` whitelist; the Adapter sends `tools` + `tool_choice="auto"` only when tools are present.
- Added `StreamAccumulator` (adapter-owned): ignores usage-only chunks, accepts one logical choice, accumulates text in order, assembles interleaved tool-call fragments by vendor index, keeps the first non-empty ID (rejecting conflicts), concatenates name/arguments in arrival order, tolerates only `function` fragment types, sorts completed calls by index, normalizes stop-with-calls to `tool_calls`, rejects duplicate/missing IDs, empty names, invalid types, non-string arguments and missing/conflicting finish. `length`/`content_filter` normalize into `ModelFinishReason` completions without an assembled message; unknown finishes stay errors.
- `made_progress` (any text or tool fragment observed) is tracked on the accumulator for later retry policy; fragments never reach Runtime.
- Validation: 16 new table-driven fake-SDK tests (pure calls, mixed content, interleaved calls, malformed streams, argument fidelity, request whitelist, usage-only chunks, serialize-after-assemble round trip). Full suite 184 passed, 1 skipped; ruff format/check, compileall, `git diff --check` clean.

## 2026-08-16 — S2.17.5 ConversationLog authority and no-tools AgentLoop landed atomically

- Added `morrow/runtime/conversation.py`: frozen `MessageRecord`/`TurnTerminalRecord`/`ConversationSnapshot` DTOs (log sequence independent of AgentEvent sequence) and a process-local `ConversationLog` enforcing one active turn, one opening User, strictly ordered tool results (first-pending-first), and no terminal while a ToolCycle is open.
- `Session` now owns the Log; `messages` is a read-only derived tuple; `accept_user`/`accept_assistant` were removed. Reset clears the Log and session preferences without touching persisted Handoff.
- `morrow/runtime/agent.py` rewritten around a pure `ModelCallRunner` (one interpreted attempt: progress tracking, completion classification, error normalization; never touches Session) plus `AgentLoop.run_task()` owning begin-turn, final Assistant/terminal appends, one-start/one-completion lifecycle, cancellation → cancelled terminal, and the explicit Stage 1 zero-progress transient-retry bridge (`max_transient_retries`, default 1). `AgentRuntime.run_turn()` is now a thin no-tools delegate onto the same loop; SessionOrchestrator chat path unchanged through it.
- Dirty semantics migrated: the real User marks dirty at begin-turn; only Handoff publication (handoff.py:90), reset or explicit discard clears it.
- Test migration: 16 Stage 1 sites moved from `accept_user`/`Session(messages=[...])` to a `seed_user_turn` testing helper that mirrors AgentLoop writes. Ten new focused tests in `tests/test_conversation_and_loop.py` (log legality, monotonic sequences, deeply read-only views, no public writer, reset semantics, delegate equivalence, cancelled-terminal + next-turn recovery, context-overflow terminal). Stage-boundary guard extended: Session construction/restart never reads or restores ConversationLog.
- Validation: 195 passed, 1 skipped (Live opt-in); ruff format/check, compileall, `git diff --check` clean. Remaining `session.messages` consumers (ContextBuilder, HandoffService) are read-only readers pending Subplan 18.

## 2026-08-16 — S2.17.6 minimal registry, executor and demo tools

- Added `morrow/runtime/tools.py`: frozen `RegisteredTool`; `ToolRegistry` (unique registration, name-sorted definitions) with `snapshot()` producing an immutable task-frozen `ToolSet`; `ToolExecutor` producing exactly one bounded `ToolExecutionOutcome` per call.
- Deterministic compact JSON envelopes (`sort_keys`, compact separators, 200-char bounded messages). `ToolErrorCode` covers invalid_arguments/unknown_tool/not_found/division_by_zero/execution_failed plus the synthetic `cancelled`/`internal` closure codes; `asyncio.CancelledError` is re-raised to AgentLoop; tracebacks and raw exceptions never leak.
- Arguments parse via `model_validate_json(..., strict=True)` with extra=forbid models; handlers run only after validation. ToolDefinition parameters are generated from the argument models.
- Demo tools: `lookup_record(dataset: plans|regions, key)` over an injected immutable mapping and `calculate(operation, values[2..32])` with ordered left-to-right arithmetic, finite-only numbers, no eval.
- Boundary guard tightened as promised: demo registry names are exactly `lookup_record`+`calculate`; executing both under NetworkGuard leaves a temporary workspace byte-identical. Production bootstrap still wires no tools.
- Validation: 19 new tests in `tests/test_tools.py` (duplicates, snapshot isolation, not-found, malformed/strict/extra/range/non-finite arguments, divide-by-zero, unknown tool, bounded handler failure without auto-retry, CancelledError re-raise, envelope determinism, schema generation). Full suite 216 passed, 1 skipped; all gates clean.

## 2026-08-16 — S2.17.7 AgentLoop extended through tools with minimum closure

- `AgentLoop` accepts an injectable `ToolExecutor` (default `None`: tools stay disabled in production bootstrap). Continuation requests reuse the first ContextPack plus only records appended since, so provider payloads stay legal without touching history reduction (Slice 2 owns semantic units).
- Tool round: accepted batch admitted via one `append_assistant`, calls executed in original order with one result envelope per call, model invoked again; final text appends the Assistant then terminal completed; exactly one start/completion per task.
- Minimum closure in the shared handlers: `asyncio.CancelledError` preserves completed results, appends one `cancelled` envelope per unresolved call in original order, then terminal cancelled; unexpected post-admission exceptions append bounded `internal` envelopes, emit one fatal internal error, then terminal failed. No terminal or next User can land while a Cycle is open (log invariant).
- Fixed a real Slice 1 integration bug found by the closure tests: `ContextBuilder._chars` crashed on pure tool-call Assistant messages (`content=None`) after a cancelled tool turn. Full ToolCycle context legality (dropping ToolMessage-less pairs from projections) remains Subplan 18 work.
- `ScriptedModelProvider` now emits scripted `AssistantMessage` completions (finish reason derived from tool_calls) and records per-call tools.
- Validation: 7 focused tests in `tests/test_agent_tool_loop.py` — tool round + final text, multi-call ordering, cancel-before-first-result and cancel-after-partial with preserved results, unexpected post-admission exception with internal closure, no-tools rejection before admission, no auto-retry of failed tools; each closed path allows a healthy next turn. Full suite 223 passed, 1 skipped; all gates clean.

## 2026-08-16 — S2.17.8/S2.17.9 vertical slice proven and Subplan 17 closed

- Offline E2E (`tests/test_stage2_e2e.py`) proves the full story through the demo tool set: lookup plan price → lookup region tax → calculate the 3-month tax-inclusive total (282.03) → final text. Asserts ≥2 tool rounds, four ordered provider requests each ending with the previous result, exact call/result pairing, one start/completion pair, no terminal records in any provider payload, tools announced on every request, and one history source (`session.messages == log.messages_view()`).
- Plain ordinary chat E2E runs through `SessionOrchestrator` → `AgentRuntime.run_turn()` → the same AgentLoop with zero tools sent; mixed-content coverage proves intermediate tool-call Assistant text stays in history while only the final no-tools Assistant completes the turn.
- Integrated cancellation (gated executor + task.cancel mid-batch) preserves earlier results, closes the unresolved call with one `cancelled` envelope, records terminal cancelled, and the next user turn succeeds without Session reset; the integrated internal-failure path mirrors this with one `internal` envelope and one fatal error event before terminal failed.
- Slice gates: focused Core/Adapter/Conversation/Tool/Loop/E2E set → 103 passed; full offline suite → 228 passed, 1 Live opt-in skip; `ruff format --check`, `ruff check`, `compileall`, capability boundary suite and `git diff --check` all clean. No unexpected skips or xfails.
- Completion criteria verified: multi-call ordered pairing, no stranded Cycle on cancel/exception paths, no mutable `Session.messages`, no independent `run_turn()` writer, malformed provider output never enters the Log, explicit Adapter whitelists, deterministic side-effect-free demo tools, and no Stage 3/4/5 capability.
- Subplan 17 marked complete; Subplan 18 (History, Context, and Product Projections) activated with fresh TODO tasks. ARCHITECTURE.md runtime section updated to the single AgentLoop/ConversationLog authority model. The 24000 context limit and retry=1 remain the two sanctioned Stage 1 compatibility bridges until S2.18.3/S2.19.1.

## 2026-08-17 — S2.18.1 ConversationLog grammar completed

- ConversationLog now enforces the full User-led public-turn grammar: ordered closed ToolCycles, at most one final no-tools Assistant, and a terminal; successful turns cannot close without that final Assistant, while cancelled/failed turns can close after accepted calls are resolved.
- Added immutable derived `ToolCycleView`/`PublicTurnView`, strict snapshot validation, per-turn call-ID uniqueness, interrupted-call IDs on terminal records, and Runtime propagation of the exact synthetically closed IDs.
- Focused Conversation/AgentLoop/tool E2E validation passed: 30 tests. Ruff identified one import-order-only issue, corrected before continuing.

## 2026-08-17 — S2.18.2–S2.18.5 context projections and reduction completed

- Replaced the generic mutable context list with frozen `ContextRequest`/`ContextPack` and explicit chat, structured and Handoff-fallback projections. Structured/fallback Views exclude ToolMessage and tool-call Assistant content by construction; chat keeps fixed boundary and dynamic user state as separate System messages.
- Added Adapter-owned canonical request sizing over the exact serialized messages/tools wire. `ContextBuilder` now requires an injected estimator and explicit limit; production composition contains the sole named 24000 compatibility bridge, and AgentLoop repeats size/pairing validation immediately before every Provider dispatch.
- Added pure oldest-first whole-Cycle result clearing with the fixed placeholder, then legal hard trimming by oldest whole public turn and oldest whole closed Cycle in the current turn. Protected-set overflow raises typed `context_budget`; source Log snapshots remain unchanged.
- Focused affected regression passed (132 passed, 1 Live opt-in skip), new projection/reduction set passed (61 tests with overlapping runtime coverage), and the added continuation pre-dispatch rejection passed. Ruff format/check passed after mechanical formatting/import fixes.

## 2026-08-17 — S2.18.6/S2.18.7 product projections migrated

- StructuredCompletion now always requests the structured projection, sends no tools, and repeats canonical size/pairing validation after appending its instruction or repair prompt. Handoff generation inherits that path; deterministic fallback consumes only the explicit latest-User/latest-completed-final-Assistant projection.
- Removed the final production consumer of generic `session.messages`; it remains only a read-only compatibility projection and test assertion surface. Added product coverage for `/new`/`/continue` semantics with prior tool history, persisted Handoff preservation, and tool-safe natural-language config extraction.
- Payload assertions prove ToolMessage, raw result envelopes and intermediate/mixed tool-call Assistant text do not enter structured, config or Handoff fallback paths. Product-focused regression passed: 70 tests.

## 2026-08-17 — S2.18.8 Subplan 18 closed

- Full offline suite passed: 249 tests passed, 1 explicit Live opt-in test skipped for missing `MORROW_OPENCODE_GO_API_KEY`; no unexpected skip/xfail.
- Parent gates passed: `ruff format --check .` (64 files), `ruff check .`, `python -m compileall -q src tests`, capability boundary suite (8 passed), and `git diff --check`.
- Provider capture inspection is covered directly: chat payload pairing survives clearing/trimming, terminal records never serialize, placeholders never write back to the Log, and structured/config/Handoff payloads contain no ToolMessage, result envelope or intermediate tool-call Assistant content.
- Subplan 18 marked complete and Subplan 19 activated. Production tools remain disabled pending the policy/guardrail gate.

## 2026-08-17 — S2.19.1 developer policy landed

- Added strict frozen `AgentPolicy`, resolved `RunPolicy` and `ProviderToolSupport`, loaded from bundled `morrow/resources/agent-policy.toml` with clear missing/invalid failures. The production exact-model table is intentionally empty.
- Effective request/result/Cycle limits use exact `provider_id/model_id` lookup or the 160000 unknown-model fallback and the approved min/ratio formulas. Combination validation covers per-cycle/total calls, tool/run time, retry/attempts and longest loop pattern feasibility.
- Adapter registry now carries only explicit tool protocol and multi-call metadata; bootstrap resolves a RunPolicy and injects it into ContextBuilder/AgentLoop. The retry=1 and context=24000 compatibility symbols/signatures are removed from production and tests.
- Focused policy/composition/Provider/boundary regression passed: 100 passed, 1 explicit Live opt-in skip; changed-file Ruff checks passed.

## 2026-08-17 — S2.19.2–S2.19.9 bounded production loop integrated

- AgentLoop now freezes RunPolicy/deadline/counters per task and enforces cancellation → deadline → model attempts → tool rounds → context before each request. Batch admission enforces Provider multi-call support, per-cycle/total call counts and minimum Cycle closure capacity.
- ToolExecutor uses the resolved result/Cycle policy, caps stable validation details, converts timeout/failures deterministically and truncates large successes into valid bounded JSON with `original_chars`. Each serial call uses the remaining global deadline.
- One shared synthetic-closure path now covers cancellation/internal/deadline/budget exits. Commit-point tests cover model progress, completed-before-acceptance, before/during/between/after tools, and cancellation after final Assistant commit; accepted history remains closed and recoverable.
- Retries require transient zero-progress failures. Repeated current-turn Cycle suffixes (including A×3 and AB×3) stop early while changed arguments/results continue.
- Public lifecycle now uses exact AgentStopCode error/completion matching and bounded `tool.status`; terminal rendering segments mixed text/tool/final output without replay or call/result leakage.
- Bootstrap resolves unknown models to 160000, enables only `lookup_record` and `calculate` for OpenAI function-tool capability, and falls back to plain chat for unsupported Adapters. Focused Subplan 19 gate: 84 passed.
- Offline wheel build succeeded and inspection confirmed `morrow/resources/agent-policy.toml` is present in the built artifact.

## 2026-08-17 — S2.19.10 Subplan 19 closed

- Full offline suite passed on the guarded production tree: 288 passed, 1 explicit Live opt-in skip; no unexpected skip/xfail.
- Parent gates passed: `ruff format --check .` (69 files), `ruff check .`, `python -m compileall -q src tests`, capability boundary suite (9 passed), and `git diff --check`.
- Public event/terminal sentinel coverage confirms full arguments/results, call IDs in terminal output, tracebacks, reasoning and credentials are absent. Synthetic Log envelopes remain bounded and every accepted batch closes before terminal.
- Subplan 19 marked complete and Subplan 20 activated for final evidence, package/product acceptance and documentation reconciliation.

## 2026-08-17 — S2.20.1 acceptance evidence matrix established

- Added `docs/acceptance/stage-2-evidence.md` and mapped every roadmap/proposal acceptance branch to its owning slice, exact automated tests, required package/manual/Live check, provisional observed result and remaining limitation.
- Compound definition-of-done criteria now reference their constituent evidence rows; none is treated as complete until all mandatory final-tree checks are green.
- Recorded explicit Stage 3/4/5 exclusions and capability-boundary evidence. Earlier 288-pass slice output is provenance only; the document reserves exact final results for S2.20.9.

## 2026-08-17 — S2.20.2 protocol and Adapter acceptance

- Audited the full fake-SDK chunk matrix and added direct Adapter cases for a tool call whose ID never arrives and a function name that violates the Core contract; both classify as invalid Provider responses.
- Protocol/Core/progress acceptance passed: 59 passed with the one explicit Live Provider test deselected. Coverage includes request tool omission/inclusion, text/pure/mixed calls, interleaving, usage-only chunks, all malformed fragment classes, finish normalization, raw-argument fidelity, serializer round trip, metadata isolation and progress-aware retry classification.

## 2026-08-17 — S2.20.3 ConversationLog and Context acceptance

- Conversation grammar, immutable snapshots, reset/restart behavior, purpose-safe projections, atomic result clearing, whole-boundary trimming, protected context and final-wire validation acceptance passed: 53 tests.
- Direct captured-request tests confirm chat retains legal tool history while Structured/config/Handoff paths exclude ToolMessage envelopes and intermediate tool-call Assistant content; builds leave the source Log, Session and Handoff unchanged.

## 2026-08-17 — S2.20.4 ToolExecutor acceptance

- Added direct coverage for a success result whose minimum truncation envelope cannot fit the assigned result budget: it returns a bounded `output_failed` result.
- Added a full AgentLoop multi-call test that derives the expected Cycle allocation and proves every call receives the same bounded result limit after minimum-envelope pre-admission.
- Tool registry/executor, AgentLoop allocation and one-result closure acceptance passed: 38 tests.

## 2026-08-17 — S2.20.5 AgentLoop/time acceptance

- Added an exact timeout-capture test proving a serial call receives `min(tool_timeout, remaining_run_time)` rather than the nominal tool timeout.
- Tightened the AB×3 loop test so the longest configured pattern completes exactly at the six-round hard cap and still reports `loop_detected` before a subsequent hard-round check; the distinct model-attempt precedence case remains green.
- AgentLoop budgets, commit-point cancellation, retry progress, synthetic closure, loop detection, public lifecycle and terminal segmentation acceptance passed: 54 tests.

## 2026-08-17 — S2.20.6 Stage 1 product regression

- Re-ran the complete Stage 1 CLI, Provider/configuration, context/runtime, structured/Handoff, orchestration, workspace/state and terminal surface on the integrated Stage 2 tree: 186 passed, one explicit Live test deselected.
- Ten-turn streaming, empty/abnormal responses, retry/cancellation, natural-language configuration, repair/deadline behavior, explicit session/Handoff transitions, degraded modes, backups/locking, EOF/Ctrl+C and the migrated public `stop_code` contract remain green.

## 2026-08-17 — S2.20.7 package and product acceptance

- Built the wheel offline, then installed it with all 33 resolved declared dependencies into a fresh CPython 3.12 venv. Import, CLI help, bundled `agent-policy.toml` discovery and policy loading passed. A prior strictly offline dependency install could not resolve uncached `keyring`; it did not exercise the artifact and is recorded as an environment-cache limitation rather than a pass.
- Added a real `run_repl` product acceptance test with the production bootstrap, Terminal renderer, SessionOrchestrator, demo executor and scripted Provider: mixed text, deterministic tool error, model recovery, healthy follow-up, Handoff update, `/new` and clean exit all pass.
- Corrected the Scripted Provider to emit text deltas for mixed-content Assistant messages, matching Adapter behavior. Product/policy/boundary/E2E/cancellation acceptance passed: 17 tests; terminal/events/state/Handoff sentinel scans and process-local reset checks passed.

## 2026-08-17 — S2.20.8 optional Live decision

- Secret-safe presence check found no `MORROW_OPENCODE_GO_API_KEY`; the optional real-Provider function-calling smoke was not run and no Live result is claimed. Per the approved gate, credential absence is not a Stage 2 failure.

## 2026-08-17 — S2.20.9 final quality gates

- Final offline suite: 294 passed, one Live test deselected; strict collection: 295 tests including the opt-in Live marker.
- Ruff format: 70 files already formatted; Ruff lint, compileall and `git diff --check` passed.
- Rebuilt the final wheel offline and installed all 33 declared packages from cache into a fresh CPython 3.12 environment. Installed import, CLI help, bundled policy discovery/loading and wheel inventory (45 files) passed.
- Final capability/side-effect/product sentinel gate passed: 10 tests. The production-source sentinel scan had no matches. No mandatory gate, unexpected skip/xfail or confirmed P1-equivalent defect remains.

## 2026-08-17 — S2.20.10 Stage 2 completed

- Reconciled README with the actual tool-step UX, stop/cancellation behavior and no-local-side-effect boundary; updated ARCHITECTURE to the AgentLoop/ConversationLog/ContextBuilder/ToolExecutor/RunPolicy ownership graph and current runtime flow.
- Marked Stage 2 complete in the overall and detailed roadmaps and linked the final acceptance evidence. Stage 3 remains explicitly unstarted.
- All mandatory completion criteria are directly evidenced and green. Optional Live was not run because no explicit compatible credential was available. Subplan 20 and the Stage 2 implementation plan are complete.

## 2026-08-17 — S2.20.11 final review remediation completed

- Added focused regressions and fixed six review findings: the total deadline now cancels a hanging Provider stream; every model continuation rebuilds context from the latest ConversationSnapshot; equal result allocation accounts for canonical outer-JSON escaping; cancellation/budget/internal synthetic results reuse the assigned bound; inconsistent finish/message shapes fail closed; and calculator overflow cannot emit non-standard `Infinity` JSON.
- Added `*.swp` to `.gitignore`. The existing swap file belongs to a live Vim process and was intentionally not deleted.
- Focused runtime/tool regression passed: 58 tests. Final offline suite passed: 300 tests with one explicit Live test deselected; strict collection found 301 tests. Ruff format/check, compileall, capability/product sentinel tests (10 passed) and `git diff --check` passed.
- Rebuilt the 45-file wheel and installed 33 packages offline into a fresh uv-managed CPython 3.12.13 environment; installed import, bundled policy load and CLI help passed. An initial packaging command correctly produced the wheel but used an unavailable system `python3.12` and lacked fail-fast behavior, so it was discarded and rerun successfully under strict shell failure handling.
- All mandatory Stage 2 gates are green and Stage 2 is complete. Optional Live remains unrun because no explicit compatible credential is available; Stage 3 remains unstarted.

## 2026-08-17 — Rewrite root Agents.md as always-on rules

- Replaced the always-on execution SOP with project invariants: authority order, exact validation commands, Always/Ask/Never boundaries, and triggered `.agent/` read-write rules.
- Moved large-plan split, activation and retirement detail into `.agent/subplans/README.md` as the single home.
- Question, review and exploration sessions no longer require TODO/TRACKER updates; the current user request overrides TRACKER.
- No product code or architecture behavior changed. Stage 3 remains unstarted.

## 2026-08-17 — S2.20.12 post-acceptance review defects fixed

- NL config extraction now treats `ContextBudgetError` as `clarification_required`; `complete_structured` wraps the same overflow as `StructuredCompletionError` instead of leaking `ValueError` into the REPL.
- ConversationLog uniqueness is per ToolCycle. A second Cycle may reuse vendor IDs such as `call_0`. History admission failures finish as `invalid_response`, not `internal`.
- The run deadline wraps only Provider `anext`, not public yields, so a slow consumer cannot be cancelled by `asyncio.timeout`.
- `run_task` now closes an active turn in `finally` when the consumer `aclose`s at a yield (`GeneratorExit`), so the next `begin_turn` can proceed.
- Focused regressions passed (113). Offline suite: 308 passed, 1 Live deselected. Ruff format/check, compileall, and Stage 2 boundary/product sentinel tests (10 passed) are green.

## 2026-08-17 — Handoff removal refactor planned

- Accepted the product decision to remove the transitional Handoff feature before persistent
  Session architecture, rather than deepen its coupling to configuration tools, future
  storage, Fork, or additional interfaces.
- Audited all current references: Handoff reaches 12 production files and 11 test files,
  plus current and historical documentation surfaces.
- Activated a four-part removal plan: product/runtime removal; domain/state/config excision;
  documentation and historical reconciliation; final acceptance and delivery.
- Locked the post-removal boundary: ConversationLog remains process-local, dirty `/new` and
  `/exit` require explicit discard confirmation, no replacement checkpoint or persistent
  Session enters, and legacy `handoff.yaml(.bak)` files are ignored but never deleted.
- Retired completed Stage 2 Subplans 17–20 from the active directory; commit `831c4ea`
  remains their reproducible historical baseline. No production or test code was changed.

## 2026-08-17 — Handoff removal plan review remediated

- Verified the external review against current code and tests. Its central findings were
  correct: the original 21/22 boundary left direct and natural-language Handoff writes
  reachable after the runtime slice, the focused test list omitted affected composition and
  core tests, and several lifecycle/read-only contracts were under-specified.
- Moved every production caller into Subplan 21, including `ALLOWED_PATHS`, intent-gate,
  patch dispatch/session update, workspace inspection/onboarding, CLI/bootstrap and
  terminal paths. Subplan 22 now deletes only uncalled domain/port/YAML/schema definitions.
- Locked ordinary unknown-command behavior for `/handoff` and `/continue`, exact dirty
  `/new`/`/exit` and EOF exit codes, no Provider/state writes, narrow Profile/Preferences
  degradation, legacy-file byte preservation, and a named `SessionApplication` composition
  result.
- Added the package/CLI/terminal tagline surface, generic-state-test retargeting, precise
  negative-scan rules, full offline gates after every subplan, auditable documentation
  classification, and an explicit offline Scripted Provider final acceptance scenario.
- Planning files only were changed; implementation remains unstarted.

## 2026-08-17 — Handoff runtime and domain removal completed

- Removed every startup, context, command, terminal lifecycle, configuration, onboarding,
  bootstrap, and product-tagline Handoff path. Dirty `/new` and `/exit` now use explicit
  process-local discard confirmation; `/handoff` and `/continue` are ordinary unknown
  commands.
- Replaced the positional bootstrap tuple with named `SessionApplication`, deleted the
  Handoff service, and preserved generic structured completion in `tests/test_structured.py`.
- Removed the Handoff/Decision domain types, config target, ProjectStateStore methods, and
  YAML adapter methods. Legacy files remain ignored and byte-identical.
- Subplan 22 gate passed: 287 offline tests, one Live test deselected; Ruff format/check,
  compileall, CLI help, and `git diff --check` passed. The rebuilt wheel has no Handoff
  package entry.

## 2026-08-17 — Handoff Removal Refactor completed

- Reconciled README, ARCHITECTURE, ROADMAP, Stage 4 entry conditions, and historical Stage
  1/2 documents. Added an exhaustive reference classification and final removal evidence.
- Final product/boundary/legacy suite passed (18); Agent-core/capability suite passed (100).
  Final offline suite passed (287, one explicit Live test deselected); strict collection
  found 288 tests.
- Precise production-source scan returned zero matches. The reviewed test allowlist contains
  only unknown-command, removed-symbol, fail-on-legacy-access, and byte-sentinel assertions.
- Built the 44-entry wheel and installed 33 packages offline into fresh CPython 3.12.13.
  Import, bundled policy discovery/load, removed-symbol check, and installed CLI help passed.
- Ruff format/check, compileall, Markdown link audit, CLI help, and `git diff --check` passed.
  Optional Live was not run because no compatible credential was present. Stage 3 remains
  unstarted and Stage 4 remains unimplemented.

## 2026-08-17 — Handoff removal post-review suggestions resolved

- Removed unused `provider_service` and `workspace_service` wiring from CommandService and
  bootstrap; removed duplicate SessionApplication assignments in the degraded-state test.
- Added both context-projection negative sentinel tests to the reference classification and
  final evidence allowlist.
- Corrected the subplan index to say no plan is active and replaced the stale live Stage 2
  plan link with its historical `831c4ea` location.
- Focused review regression passed (81). Full offline suite passed (287, one Live test
  deselected); Ruff format/check, compileall, CLI help, and `git diff --check` passed.

## 2026-08-17 — Natural-language configuration tooling plan drafted

- Committed the completed Handoff Removal Refactor first as `cbc3d6d`
  (`refactor: remove handoff continuity bridge`), leaving a clean implementation baseline
  before creating the next plan.
- Accepted one ordinary AgentLoop for all non-Slash input. A standard
  `update_configuration` FunctionToolCall is the only executable natural-language
  configuration-intent signal; keyword routing and the separate structured configuration
  completion are removed during the atomic product cutover.
- Reconciled the design with the architecture tool boundary: configuration handlers are thin
  adapters over ConfigPatchService; Provider wire remains standard; local risk and approval
  metadata stays in Registry/Executor; no AgentLoop, ToolExecutor, Orchestrator, event, or
  Provider adapter receives a configuration-name branch.
- Limited the current configuration surface to global/workspace/session Preferences and
  workspace Profile. Handoff, Provider/credential/model/security/AgentPolicy/workspace
  identity and all unrelated Stage 3/4 capabilities remain excluded.
- Split implementation into ordered Subplans 25–28: generic Tool Policy/Approval foundation;
  shared configuration service and directly tested tool; atomic single-chain product
  integration; intent evaluation and final acceptance/delivery.
- Planning files only changed after the baseline commit. Stage 3 implementation remains
  unstarted; no subplan or executable task is active until the user explicitly authorizes
  Subplan 25.

## 2026-08-17 — Configuration tooling plan review remediated

- Verified all 19 review findings against the current ToolExecutor, AgentLoop,
  SessionOrchestrator, ConfigPatchService, CommandService, terminal/CLI composition, state
  stores, architecture/roadmap wording, and exact production-tool tests. The report's P0/P1
  findings exist; P2 items are valid contract/documentation gaps rather than false positives.
- Removed the impossible Subplan 25 Orchestrator scan and moved that assertion to the atomic
  cutover. Chose full canonical Pydantic JSON Schema as an explicit Provider-wire change
  while preserving demo-tool outcomes, events, ToolMessages, and history.
- Locked construction-time ApprovalPort injection through a shared Terminal/PromptSession,
  whole-turn Ctrl+C/EOF cancellation, cancellable prompt timeouts, running-before-preview,
  request field minimization, and effect-as-display-only semantics.
- Locked exact reset representations, the no-op/remove matrix, session `revision: null`,
  pre-approval degraded-state checks, minimal service results, a typed command/result API,
  and an unchanged legacy ConfigPatch extraction schema through Subplan 26.
- Made per-call approval and deliberate partial persistence explicit; documented dirty/logged
  natural-language turns versus out-of-loop Slash commands, exact inventory-test updates,
  generic SYSTEM_BOUNDARY wording, zero production `complete_structured` callers after
  cutover, the current 120-second timeout recovery path, Chinese tool description, and no new
  Slash list syntax.
- Planning documents only were changed. Stage 3 remains unstarted and will remain incomplete
  after this slice; file/search/edit/Shell capabilities require separate authorization.

## 2026-08-17 — Subplan 25 generic tool policy and approval foundation completed

- Activated Subplan 25 after explicit user authorization. Added Core `ToolEffect`, immutable
  `ToolApprovalRequest`/`ToolApprovalDecision`, and async `ApprovalPort`; Runtime now owns
  immutable `ToolExecutionPolicy` and Registry metadata with construction-time injection.
- `ToolExecutor` validates arguments before sanitized local preview and approval, fails closed
  with bounded `approval_unavailable`/`approval_rejected` outcomes, propagates cancellation,
  and never branches on concrete tool names or configuration domains. Existing demo tools keep
  `none/never` defaults and no Provider-visible local metadata.
- Tool argument generation now preserves the complete Pydantic JSON Schema, including strict
  extras, nested definitions, enums, and required fields. Added direct approval, cancellation,
  bounded-result, schema, and ToolCycle closure tests.
- Focused regression passed (104 selected with one Live deselected); full offline suite passed
  (295 passed, one Live test deselected). Ruff format/check, compileall, CLI help, and
  `git diff --check` all passed. Subplan 26 is activated; production configuration routing is
  intentionally unchanged until the later atomic cutover.

## 2026-08-17 — Subplan 26 configuration service and standard tool completed

- Added application-owned strict `UpdateConfigurationArguments`, `ConfigurationCommand`,
  and minimal `ConfigurationChangeResult`. The flat tool contract covers only session,
  workspace, and global Preferences plus workspace Profile; reset is absent from legacy
  `ConfigPatch` and sensitive/provider targets are not model fields.
- Consolidated validation, reset tombstones, no-op classification, degraded-state preflight,
  revision handling, Session projection refresh, and one-publication legacy patch behavior in
  `ConfigPatchService`. Slash edit/reset paths now delegate to the same typed service while
  retaining their existing preview/confirmation grammar.
- Added the unregistered `update_configuration` factory with Chinese intent/scope rules,
  required persistent-write approval metadata, sanitized preflight preview, thin service
  delegation, bounded safe results, and stable domain-error mapping.
- Focused configuration/service/state/command/tool regression passed (128); full offline suite
  passed (313, one Live test deselected). Ruff format/check, compileall, CLI help, and
  `git diff --check` passed. Subplan 27 is activated for the atomic production cutover.

## 2026-08-17 — Subplan 27 single-chain product integration completed

- Added the terminal `ApprovalPort` adapter and CLI composition now creates one shared
  `Terminal`/`PromptSession`, injects the adapter into the generic `ToolExecutor`, and passes
  the same UI objects to `run_repl`. Denial, EOF/Ctrl+C cancellation, prompt timeout, and
  bounded terminal-only previews remain outside events and ConversationLog.
- Registered `update_configuration` beside `lookup_record` and `calculate` only for
  function-tool-capable Adapters. Removed the production Gate/extractor/structured
  configuration route; generic structured-completion infrastructure remains with zero
  production callers. Unsupported Adapters remain tool-free and `/config` remains explicit.
- Added ordinary-loop coverage for persistence, ordinary/one-turn/negative/quoted/hypothetical/
  ambiguous inputs, mixed work/configuration, serial per-call approval, partial persistence,
  cancellation closure, state projection refresh, and real offline REPL approval/dirty-history
  behavior. Updated current README, architecture, and Stage 3 roadmap wording.
- Focused cutover regression passed (52); configuration/terminal/product acceptance passed
  (41 after timeout coverage); full offline suite passed (298, one Live test deselected).
  Ruff format/check, compileall, CLI help, and `git diff --check` passed. Subplan 28 is active
  for final acceptance and package evidence.

## 2026-08-17 — Subplan 28 final acceptance completed

- Added `docs/acceptance/configuration-tooling-evidence.md`, mapping the master definition of
  done to direct tests, source scans, product scenarios, package checks, and observed results.
  Scripted Provider intent cases are explicitly recorded as plumbing evidence only; optional
  Live intent evaluation was not run without a compatible credential and explicit request.
- Reconciled README, `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`, and the Stage 3 roadmap to
  describe the delivered first stateful configuration-tool slice while keeping file/search/
  edit/Shell, persistent Session, memory, Skills, MCP, and background work incomplete.
- Strict collection passed with 300 tests. Final offline suite passed with 299 tests and one
  Live test deselected. Ruff format/check, compileall, CLI help, `git diff --check`, 60-link
  Markdown audit, precise source/capability scans, and the fresh wheel/package scans passed.
- Built fresh wheel `dist/morrow_agent-0.1.0-py3-none-any.whl` (45 entries,
  SHA-256 `9a664e67b62173073da654aa131c5bb1c5822d3c0c532506ae2d190109a420a`). Installed it
  into a fresh Python 3.13 virtual environment with offline dependency reuse; import,
  bundled-policy discovery, configuration-module discovery, and installed `morrow --help`
  passed. The master plan is complete; no implementation subplan remains active.

## 2026-08-18 — Stage 3 implementation plan drafted

- Re-checked the current repository, architecture, roadmap, tests, and completed configuration-
  tooling plan before drafting Stage 3. No local file, search, edit, Shell, Git, or native-sandbox
  project tool was treated as already implemented.
- Locked permissions as independent access-scope, approval-mode, and process-isolation axes.
  Stage 3 plans `manual`, restricted `auto-safe`, and fail-closed native `auto-sandboxed`;
  unrestricted Full Access remains reserved for Stage 4.
- Selected Pi coding agent release `0.84.2` / commit
  `209bc7b9a89b01c8fd05861cf5bbdda3e300037a` as the primary fixed behavioral reference, with
  Hermes as secondary evidence. Morrow keeps its own Core/Application/Runtime/Adapter ownership.
- Defined the policy-decision contract, workspace path rules, bounded tool contracts, process and
  Git boundaries, sensitive-resource/output protections, honest Host non-isolation boundary,
  error taxonomy, validation gates, hold points, and final acceptance evidence.
- Split execution into ordered Subplans 29–34: policy/workspace foundation; read/search; file
  mutation and diff; host process execution; native sandbox; read-only Git and final acceptance.
  No subplan was activated and `TODO.md` intentionally contains no executable task.
- Removed completed Subplans 25–28 from the active subplan directory; their contents remain in Git
  history. Planning files only were changed. No source code, test code, implementation command, or
  implementation validation was performed or claimed.

## 2026-08-18 — Stage 3 plan review remediated

- Verified all 14 findings in `docs/reviews/stage-3-implementation-plan-review.md` against the
  current system prompt, RunPolicy/result truncation, ToolExecutor/approval contracts, bootstrap
  inventory, workspace identity, terminal/event surfaces, configuration schema, and boundary tests.
- Confirmed Issues 1–5 as implementation blockers and Issues 6–13 as real plan gaps worth fixing.
  Issue 14's current schema already excludes permission fields, but the missing anti-escalation
  regression was added to Subplan 29.
- Replaced the incompatible 2,000-line/50-KiB read promise with a 400-line/8-KiB ceiling plus
  dynamic semantic envelope budgeting. Added capability-derived system prompts, exact Auto Safe
  per-call/cumulative thresholds, four-level safe parent creation, and visible Diff approval.
- Added pre-activation Gate P0 and kept Auto Sandboxed as a Stage 3 completion requirement, matching
  the user's prior scope choice. Locked CoW/no-hardlink snapshotting, standard-library toolchain,
  15/75/15/10-second phase budgets, current-host macOS evidence, and conditional Linux claims.
- Removed demo lookup/calculation tools from the Stage 3 production endpoint, added the current-run
  approval-required sandbox-promotion subset tool, fixed rg/fallback/ADR budgets, confined Git
  metadata, locked ToolFact/terminal-summary/local-metrics fields, and replaced substring boundary
  tests with exact inventory plus forbidden capability families.
- Planning documents only were changed. Gate P0 was not run, no subplan was activated, and no
  production/test implementation or implementation validation was performed.

## 2026-08-18 — Gate P0 passed; Subplan 29 activated

- Ran the pre-activation Gate P0 before changing production or test code. The host is Darwin
  25.6.0, macOS 26.6.1, arm64, with `/usr/bin/sandbox-exec`; the writable data volume is APFS
  `/dev/disk3s5` at `/System/Volumes/Data`, while the system volume is a read-only APFS snapshot.
- The host-level restricted probe ran Python 3.14.5 from the existing Framework installation.
  Task-private temporary writes succeeded; workspace and Home writes, `.ssh` directory reads,
  loopback bind, and external TCP connect were denied. The environment was launched with a
  minimal variable set. No repository or persistent Host state changed.
- `clonefile` succeeded on the APFS data volume, produced a different inode on the same device,
  and a full source-file comparison stayed unchanged after mutating the clone. No hard link,
  Docker, helper installation, or Host fallback was used. The first nested Codex-sandbox probe
  returned `sandbox_apply: Operation not permitted`; the approved host-level rerun passed.
- Updated `PLAN.md`, `subplans/README.md`, `TODO.md`, and `TRACKER.md` to activate Subplan 29.
  S3.29.1 baseline capture was then completed; S3.29.2 capability models are now active.

## 2026-08-18 — Subplan 29 S3.29.1 baseline captured

- Current source baseline remains `d9df0d2` (`099d804` is the planning-only HEAD). The production
  function-tool inventory is exactly `lookup_record`, `calculate`, and `update_configuration` for
  function-capable adapters; unsupported adapters expose no tools. `ToolExecutor` currently
  orders validated arguments → static `ToolExecutionPolicy` approval preview/approval → handler →
  JSON envelope/truncation, with serial calls owned by `AgentLoop` and one `tool.status` lifecycle.
- The baseline public limits are the bundled unknown-model fallback of 160,000 request characters,
  16,000 per result, 56,000 per ToolCycle, and 120 seconds per tool. Approval previews are capped
  at 8 lines × 200 characters. `SYSTEM_BOUNDARY` is a fixed Stage 2 message; there is no
  capability-derived policy, workspace capability, ToolRunContext, ToolFact, or SensitiveResourcePolicy.
- The full offline baseline passed with 310 tests and one Live test deselected. Ruff format/check,
  compileall, CLI help, and `git diff --check` also passed. The working tree had only the planned
  activation-document changes.
- Fixed Pi commit `209bc7b9a89b01c8fd05861cf5bbdda3e300037a` confirms the useful behavioral references:
  pluggable filesystem operations, bounded line/byte truncation with continuation metadata,
  exact/unique mutation handling, visible Diff, serialized same-file writes, bounded process
  output/timeout/cancellation, and faux-provider offline tests. Morrow adopts those ergonomics,
  strengthens them with workspace confinement, revision/atomic publication, local policy and
  native isolation, and rejects Pi's inherited Host permissions, unrestricted command/network
  assumptions, and direct last-write-wins mutation model.

## 2026-08-18 — Subplan 29 accepted; Subplan 30 activated

- Implemented strict local `PermissionProfile`, `WorkspaceCapability`, `OperationIntent`,
  `CapabilityPolicy`, bounded approval metadata, and the tagged `ToolFact` contract. Production
  composition freezes the selected profile and confirmed workspace root; Full Access and Auto
  Sandboxed remain fail-closed, and no project filesystem/process/Git tool was registered.
- Migrated the production three-tool path to intent resolution, policy evaluation, semantic
  result envelopes, and process-local ordered facts without changing public events or
  ConversationLog ownership. The system boundary is now derived from the current ToolSet.
- Added regression coverage for policy truth tables, denial ordering, semantic budgets, facts
  isolation, prompt/tool inventory alignment, CLI preset fail-closed behavior, read-only
  intersection, and configuration non-escalation. Full offline suite: 335 passed, 1 deselected;
  collection: 336 tests; Ruff format/check, compileall, CLI help, and `git diff --check` passed.
- Activated Subplan 30 for workspace-confined list/read/find/search implementation. Its current
  production boundary is still the three existing tools until the read/search cutover is green.

## 2026-08-18 — Subplan 30 accepted; Subplan 31 activated

- Added strict local file/revision/read/list/find/search result models, a frozen
  `WorkspacePathResolver`, stdlib filesystem adapter, `WorkspaceFileService`, rg/Python search
  adapters, and `WorkspaceSearchService`. Reads reject escapes, external symlinks, special files,
  binary/invalid UTF-8, and admitted-size overflow; protected paths and magic headers return only
  bounded metadata.
- Added `list_directory`, `read_file`, `find_files`, and `search_text` with context-aware semantic
  budgets, actionable read continuation, stable ordering, no directory-symlink traversal, fixed
  rg argv/no-config/no-download behavior, bounded Python fallback, ignore subtraction, and the
  search ADR at `docs/decisions/stage-3-search-adapter.md`. Production now exposes exactly these
  four read tools plus `update_configuration`; demo tools remain fixture-only.
- Added path/symlink/special-file/text/protected-content/search parity/fallback/result-budget and
  Fake Provider list-search-read-continue acceptance coverage. Full offline suite: 354 passed,
  1 deselected; collection: 355 tests; Ruff format/check, compileall, CLI help, and
  `git diff --check` passed.
- Activated Subplan 31 for exact SHA-256 revision mutation, atomic publication, actual Diff, and
  current-run ChangeSet facts. Process, sandbox, Git, network, and destructive capabilities stay
  outside the active boundary.

## 2026-08-18 — Subplan 31 accepted; Subplan 32 activated

- Added strict exact-edit, mutation mode/status/result, and current-run ChangeSet contracts. Patch
  and replace require the exact source SHA-256; create requires absence; matching is unique and
  non-overlapping, with no fuzzy or last-write-wins fallback. BOM, newline style, final-newline
  choices, and existing file mode are preserved for patch/replace.
- Added bounded create-parent handling (at most four levels), symlink-component rejection,
  per-target serialization, directory-handle-aware atomic publication, temporary-file cleanup,
  revalidation, protected-content checks, actual unified Diff, and generic ChangeToolFact wiring.
  `show_changes` reads only the current ToolRunContext ChangeSet and does not persist or derive
  paths from assistant prose.
- Registered `apply_patch`, `write_file`, and `show_changes` beside the four read/search tools and
  configuration. Manual writes prompt with actual bounded Diff; Auto Safe allows only the exact
  small structured thresholds and routes over-threshold edits/bytes/lines/files or replace calls
  to approval. Delete/rename/chmod/link, process, sandbox, Git, network, and Full Access remain
  absent.
- Added `docs/decisions/stage-3-mutation.md`, production Fake Provider coverage for search → read
  → patch → show changes, stale recovery, Manual approval, Auto Safe, cancellation, failure and
  parent-swap cases, plus threshold and protected-content tests. Full offline suite: 373 passed,
  1 deselected; Ruff format/check, compileall, CLI help, and `git diff --check` passed.
- Activated Subplan 32 for bounded Host process execution. Host commands remain approval-required
  and non-isolated; native sandbox and Git tools stay unregistered.

## 2026-08-18 — Subplan 32 accepted; Subplan 33 activated

- Added strict `CommandRequest`/`CommandResult` contracts and `run_command` through the existing
  ToolExecutor/ToolCycle. Host execution uses a minimal allowlisted environment, `stdin=DEVNULL`,
  concurrent bounded stdout/stderr tails, deterministic invalid-UTF-8 handling, exact/token
  redaction, process-group timeout/cancellation cleanup, and typed spawn/cleanup failures.
- Added structural preflight for workspace/cwd, protected resources, outside paths, network and
  loopback, package/destructive operations, Git redirection/write, shell bypasses and privilege
  escalation. Manual and Auto Safe always approve Host commands; Auto Sandboxed rejects the Host
  adapter even if a native backend is available. No public event or ConversationLog contract changed.
- Added the fixed-Pi process comparison at `docs/decisions/stage-3-process.md`, production
  inventory/prompt/boundary updates, signal/descendant/cancellation/output/redaction/environment
  tests, and Fake Provider failure-recovery coverage. Full offline suite: 384 passed,
  1 deselected; Ruff format/check, compileall, CLI help, and `git diff --check` passed.
- Activated Subplan 33 for native sandbox capability revalidation, snapshots, platform adapters,
  isolation tests, and fail-closed Auto Sandboxed integration. Host remains approval-required;
  Git tools remain unregistered.

## 2026-08-18 — Subplan 33 accepted; Subplan 34 activated

- Revalidated Gate P0 on macOS 26.6.1 / Darwin 25.6.0 / arm64 with `/usr/bin/sandbox-exec`,
  the system Python 3.14 runtime, APFS `clonefile`, and blocked workspace/Home/`.ssh`/network
  probe cases. The nested Codex Seatbelt limitation was kept separate from host-level evidence.
- Implemented and tested bounded task-private snapshots, protected/cache/VCS exclusion, internal
  symlink preservation, native Seatbelt and fixed bubblewrap builders, private environment and
  phase budgets, sandbox Diff facts, and current-run approval-required conflict-safe promotion.
  Auto Sandboxed has no Host fallback; Linux runtime remains unsupported pending a real runner.
- Host-level macOS escape/isolation tests passed 2/2. After replacing a startup race in the
  descendant-timeout test with process readiness synchronization, the full offline suite passed
  387 tests with 2 intentional nested-environment skips and 1 deselected; Ruff format/check,
  compileall, CLI help, and `git diff --check` also passed.
- Updated the sandbox ADR, README, ARCHITECTURE, roadmap, and execution-state documents, then
  activated Subplan 34 for read-only Git inspection and final Stage 3 acceptance. Git writes,
  external metadata, network, and Full Access remain outside scope.

## 2026-08-18 — Subplan 34 accepted; Stage 3 complete on claimed macOS platform

- Implemented bounded read-only `git_status`/`git_diff` with frozen workspace and Git metadata
  confinement, fixed config/environment/argv, disabled pager/external diff/textconv/fsmonitor/
  prompts/optional locks, protected-file Diff suppression, typed non-repository/external-metadata/
  timeout results, and no Git write surface. Added the fixed-Pi comparison ADR.
- Locked the exact production inventory: 11 common tools plus only current-run approval-required
  `promote_sandbox_changes` for supported Auto Sandboxed composition. Demo lookup/calculate remain
  fixture-only; Provider schemas are strict and local policy metadata stays out of Provider wire.
- Added two Fake Provider product stories (Python failure/recovery and nested text with a pre-existing
  user change), real REPL fact-summary coverage, bounded process-local `RunMetricsSnapshot` with an
  explicit composition disable switch, and the requirement-to-evidence matrix.
- Final gates: `397 passed, 2 skipped, 1 deselected`; `400 tests collected`; Ruff format/check,
  compileall, CLI help, and `git diff --check` passed. Host-level macOS Seatbelt acceptance passed
  `2 passed`. Wheel build succeeded with 61 files and SHA256
  `794da836c4b2896ee31e2746a148992c78dcdb21b72092765d935d24d7e20378`; fresh-venv wheel import,
  bundled policy load, and installed CLI help passed using verified offline runtime dependencies.
- Updated README, ARCHITECTURE, ROADMAP, Stage 3 roadmap, execution state, and
  `docs/acceptance/stage-3-local-code-agent-evidence.md`. Linux remains unsupported without a
  real runner; Live/network, Full Access, persistent AgentRun/Artifact, and Stage 4 work remain
  outside this execution.

## 2026-08-18 — Stage 3 external implementation review remediated

- Verified all 12 findings from external reviewer `01a014c5-7a97-7101-b3df-6803112795b6`
  against the current uncommitted Stage 3 tree before editing. All nine bugs, two suggestions,
  and one nit were actionable; no roadmap-only capability was treated as a defect.
- Closed file/content protection gaps by applying sensitive-path policy to both aliases and
  resolved in-workspace file-symlink targets, protecting `.git`/`.morrow`, and recognizing
  PKCS#8, RSA, EC, DSA, encrypted PEM, OpenSSH, and PGP private-key markers across file, search,
  mutation, snapshot, and Git paths.
- Allowed legal empty read windows past EOF and after semantic budget trimming; mapped
  `not_found`, `git_failed`, and mixed-newline failures to stable tool errors. Patch/replace now
  reject mixed-newline sources without changing their bytes rather than normalizing the file.
- Added shell-wrapped Git command detection and bounded redacted command text to Host approval
  previews. Sandbox promotion now records every applied `MutationResult` in the injected
  `ChangeSetService`, so `show_changes` reports promoted files.
- Snapshot prepare/collect now reserve ownership before the worker starts, cooperate with an
  explicit cancellation token, settle timed-out workers, and clean the reserved root. Linux
  bubblewrap retains rule-construction tests with PID namespace isolation but probes unsupported
  until a real Linux runner passes. The verbose Auto Safe schema-history comment was condensed.
- Focused remediation slice passed `65 passed, 2 skipped`; full offline gate passed
  `404 passed, 2 skipped, 1 deselected`, with `407 tests collected`. Ruff format/check,
  compileall, CLI help, and `git diff --check` passed. The two real macOS Seatbelt tests passed
  at host level (`2 passed`).
- Rebuilt the 61-file wheel with SHA256
  `070424b6958ed41a0580d31354ea48e59360755f33038f6e65bf41ed5bd8686e`; a fresh no-deps venv
  import, bundled-policy, installed sensitive-policy, and CLI smoke passed. Build isolation used
  approved PyPI access only to resolve declared hatchling requirements; product/Provider network,
  Live credentials, commit, and push remained outside this execution.

## 2026-08-19 — Stage 3 final recheck and transition to Stage 4 planning

- Reproduced the persistent Mimo environment through `scripts/morrow-mimo`: `provider presets`,
  `model current`, `provider show opencode-go`, and the real `provider test opencode-go` all
  passed; the latter read the existing macOS Keychain credential and returned `连接成功`.
- Found and fixed a wrapper edge case where `provider presets` received the persistence-only
  `--state-root` option; the wrapper now preserves `~/.morrow` for stateful commands and leaves
  preset discovery option-free.
- Re-ran the full offline suite: `418 passed, 2 skipped, 1 deselected` from 421 collected;
  Ruff check/format, Compileall, CLI help, and `git diff --check` passed. The two host-level
  macOS Seatbelt tests passed. A rebuilt 61-file wheel had SHA256
  `926f1655b495b2c26d2505169f66a51d2c24c1a01937158c751f53e1eb19108a`; fresh Python 3.13
  no-deps install, bundled policy checks, and installed CLI help passed using verified runtime
  dependencies.
- Updated the acceptance/evidence reports with final counts and Mimo results, closed Stage 3 on
  macOS, and opened Stage 4 planning without activating persistence, Full Access, or another
  production capability. No push was performed.

## 2026-08-19 — Stage 4 master plan activated; Subplan 35 active

- Reconciled the three Stage 4 research documents against the current Stage 3 code and activated a
  single implementation route: standard-library SQLite Operational Store, filesystem Artifacts,
  durable ConversationLog/tool journal boundaries, explicit recovery, foreground TaskOutcome,
  deterministic checkpoints, conversation-only Fork, auditable grants, and Full Access Manual.
- Split execution into ordered Subplans 35–45. Subplan 35 is contract/ADR/spike work only; Stage 4
  production persistence begins no earlier than accepted activation of Subplan 36.
- Removed completed Stage 3 Subplans 29–34 from the active subplan directory while retaining them in
  Git history. Updated PLAN, TODO, TRACKER, subplan index, README, ARCHITECTURE, ROADMAP, and the
  stable Stage 4 contract; the three user-provided research files remain untouched.
- Explicitly deferred Controlled Full Access Auto, raw auto, event-delivery workers/outbox, run
  claims, in-flight steering, automatic history repair, full/raw command-output retention, FTS/
  embeddings, workspace/code rewind, background work, and Stage 5 learning.
- Documentation consistency checks and `git diff --check` passed. No production source, tests,
  dependency, schema, public event lifecycle, bundled policy default, or runtime behavior changed.

## 2026-08-19 — S4.35.2 Operational Store ADR and sqlite3 spike

- Inspected current `DataRoot` (`~/.morrow` / `--state-root`), YAML authorities, and
  workspace-scoped `WorkspaceWriterLock`. The existing lock cannot serialize a shared operational
  database; a distinct `locks/operational-store.lock` is required.
- Accepted `docs/decisions/stage-4-operational-store.md`: one data-root
  `store/operational.sqlite`, reserved `artifacts/` and `backups/operational/`, stdlib `sqlite3`,
  `BEGIN IMMEDIATE`, WAL, `synchronous=FULL`, 250 ms busy timeout, 8 injected retries,
  `application_id=0x4D4F5257`, matching `store_identity`, future/foreign/empty refusal, and
  `Connection.backup()`. Identity is checked before any `journal_mode` change so a foreign file is
  not rewritten into WAL.
- Spike `tests/test_stage4_operational_store_spike.py` proved commit-after-`_exit`, uncommitted
  rollback, WAL reader snapshot, typed contention, bounded retry without sleep, exclusive
  maintenance lock, dead-owner lock release, future/foreign/empty refusal with intact bytes, and
  online backup during concurrent writes. Validation: `15 passed`; Ruff format/check; compileall;
  `git diff --check`. Temporary data roots only.
- No `src/morrow` production adapter, schema, dependency, public event, or runtime behavior change.
  Next task is S4.35.3 domain/ownership ADR.

## 2026-08-19 — Stage 4 conditional plan review remediated; activation still gated

- Verified `docs/reviews/stage-4-plan-review.md` against the Stage 3 runtime and accepted all five
  P0 findings plus the material P1/P2 ownership and evidence corrections. In particular, the current
  ConversationLog mutates its in-memory sequence before persistence exists, `turn.started` precedes
  User append, production `run_turn()` may have a ToolExecutor, and Host/sandbox restart evidence
  cannot prove safe replay.
- Added accepted planning ADRs for domain/conversation ownership, durable execution/recovery,
  Artifact/checkpoint/fork, permission grants, and reference adoption, plus the S4.35.8 fault
  matrix. They lock one Turn/UserMessage per client command rather than one model call forever,
  `ready_for_acceptance` as non-terminal, validate→COMMIT→projection conversation writes, error-only
  recovery closure, three separate order namespaces, explicit payload ceilings, and regrant for a
  crash-created AgentRun.
- Narrowed the Operational Store ADR and Subplans 36–45 around the reserved v1–v9 schema map.
  Subplan 36 now owns unproved migration/thread/sidecar/retry evidence; 37 owns only an open current
  Task pointer and bounded no-tool durability; 38 excludes application events; 39 treats missing
  Host/native-sandbox completion as unknown; 40 does not depend on Artifact; 43 excludes grant
  doctor checks; and 44 exposes only manually approved `unconfined_host_process` elevation.
- Marked all three research drafts as superseded decision input and corrected their missing paths.
  No upstream code/schema/fixture/asset is adopted; Auto, rewind, Outbox, RunClaim, nonce, FTS, and
  automatic history repair remain rejected or deferred.
- Validation with a task-private uv cache: Operational Store spike `15 passed`; Ruff format check
  `107 files already formatted`; Ruff check passed; compileall passed; local Markdown links and
  `git diff --check` passed. No production source, test, dependency, schema, public-event lifecycle,
  policy default, or runtime behavior changed. Subplan 35 remains active pending explicit review
  acceptance; Subplan 36 remains inactive.

## 2026-08-19 — Subplan 35 accepted; Subplan 36 activated

- The user explicitly accepted the validated Stage 4 review remediation. Commit `20fb43e` preserves
  the complete ADR, fault-matrix, research-demotion, and Subplan 36–45 contract update.
- Closed S4.35.8 and retired the completed Subplan 35 task file from the active directory. Activated
  Subplan 36 and S4.36.1 for the v1 Operational Store foundation only.
- No production adapter/schema or runtime behavior changed during activation. Conversation, Task,
  tool, Artifact, application-event, grant, Full Access, public-event, and bundled-policy work
  remain gated by their later subplans or hold points.

## 2026-08-19 — Subplan 36 Operational Store foundation completed

- Implemented typed Operational Store paths on `DataRoot`, Core open/health/error contracts, and a
  stdlib `sqlite3` adapter: owner-thread connections, required pragmas, `BEGIN IMMEDIATE` writes,
  BUSY/LOCKED-only bounded retry, global maintenance lock, checksummed v1–v9 migration registry,
  future/corrupt/foreign refusal, and online `Connection.backup()` without credentials or Artifacts.
- Public `StorageError` codes stay free of SQL and sensitive paths. Daily `read_write` open does not
  full-scan; create/migrate/backup/diagnose run integrity checks. YAML bootstrap still does not open
  the store. Added the Stage 3-to-v1 fixture under `tests/fixtures/stage3_data_root/`.
- Validation: `tests/test_operational_store.py` 30 passed; spike 15 passed; Ruff format/check,
  compileall, and `git diff --check` passed; offline suite `465 passed, 1 deselected`.
- Activated Subplan 37 for durable no-tool Session/Task/Turn/AgentRun history. Business schemas
  v2–v9 and later runtime behavior remain inactive.

## 2026-08-19 — S4.37.1 domain contracts

- Added `src/morrow/core/domain.py` with independent Session lifecycle/health, open-only TaskRun
  status, prefixed opaque IDs, three sequence namespaces, budgeted AgentRun snapshots, and
  turn-submit receipts. `client_message_id` is a command field, not UserMessage content.
- Focused tests in `tests/test_stage4_domain.py` passed with Ruff check on the new files.

## 2026-08-19 — S4.37.2 v2 journal schema and ports

- Bumped the production schema to v2 with Session, open TaskRun pointer, Turn, AgentRun snapshot,
  conversation records, and turn-submit receipts. Queries are workspace-scoped; conversation
  positions are unique and monotonic; failed appends do not advance the session pointer.
- Added `SqliteOperationalJournal` implementing the narrow Core ports plus a re-entrant `transact`
  for later ConversationLog commits. Existing store tests now use production v2 FKs and keep a
  v1-only registry for migration faults.
- Focused journal/store/domain tests passed (45) along with conversation and core contract
  regressions. AgentLoop is not wired yet.

## 2026-08-19 — S4.37.3 durable ConversationLog append boundary

- `ConversationLog` now plans a candidate, validates it against the immutable snapshot, and applies
  only through `apply_committed`. Existing `begin_turn`/`append_*`/`finish_turn` helpers still use
  that path for process-local tests.
- `DurableConversationWriter.commit()` persists planned records and installs the projection from
  the committed journal. A failed persist leaves memory and `conversation_position` unchanged.
- Conversation/agent regression tests and the new durable-log tests passed.

## 2026-08-19 — Subplan 37 durable no-tool Session completed

- AgentLoop commits Turn/User before `turn.started`. `client_message_id` receipts replay closed
  turns, return recovery for open/interrupted duplicates, and conflict on a different payload.
  Restart restores legal snapshots; invalid sequences quarantine Session health without rewriting
  lifecycle. `/new` creates a new Session without deleting the old one; persisted `/exit` does not
  ask to discard history. Tool-cycle payloads are redacted at rest.
- Validation: Ruff format/check, compileall, `git diff --check`, and offline suite
  `489 passed, 1 deselected`.
- Activated Subplan 38 for the tool execution journal and durable Approval.

## 2026-08-19 — Subplan 38 tool journal and durable Approval completed

- Added EffectClass, recovery declarations, ToolExecution/Approval transition models, payload
  budgets, and the named one-shot FaultInjector. `ToolEffect` is not used for crash safety;
  `run_command` Host/sandbox default to outcome_unknown.
- Schema v3 adds `tool_executions` and `approvals` with intent hashes, row versions, expiry, and
  consume-only-when-approved checks. v1 and v2 stores migrate forward.
- AgentLoop persists the Assistant ToolCall plus ordered intents before dispatch. File intents store
  before/expected-after hashes. Approval consume and `executing` are one transaction. Handler
  completion and ToolCycle close are separate. Terminal shows the durable `approval_id`.
- Production composition fails if a registered tool lacks a declaration. Provider call IDs are
  aliased at rest; handler is not called when intent commit fails.
- Validation: Ruff format/check, compileall, `git diff --check`, and offline suite
  `511 passed, 1 deselected`.
- Activated Subplan 39 for recovery classification and the crash harness.

## 2026-08-19 — Subplan 39 recovery classification and crash harness completed

- Added RecoveryReport/Item/Decision contracts, 64 KiB budget, secret refusal, and legal
  resolutions. The classifier is pure over durable state plus current hash/revision observations.
- Schema v4 stores recovery reports and command receipts. Restart discovery marks `needs_recovery`
  for open Turns or non-closed executions. New input is blocked until recovery is resolved.
- File reconciliation uses before/expected-after SHA-256 and size, never mtime. Host and native
  sandbox executions without `handler_completed` are always `outcome_unknown` and cannot retry.
  Multi-file promotion observations classify independently.
- ConversationLog recovery-close appends only interrupted/error ToolMessages, optionally a
  non-success terminal, and never a success envelope. Decisions are idempotent by command receipt.
- Subprocess `os._exit` fixtures classify prepared, executing-read, executing-host, and
  handler_completed boundaries without wall-clock sleeps.
- Validation: Ruff format/check, compileall, `git diff --check`, and offline suite
  `531 passed, 1 deselected`.
- Activated Subplan 40 for the TaskRun lifecycle and versioned TaskOutcome.

## 2026-08-19 — Subplan 40 TaskRun lifecycle and TaskOutcome completed

- Added v5 TaskRun states/transitions, optimistic row versions, attempts, transition audit, current
  pointer atomicity, command receipts, and the application TaskService. Ordinary final answers move
  to `ready_for_acceptance`; explicit acceptance/terminal close/snapshot are the only Outcome triggers.
- Added immutable bounded TaskOutcome evidence with a typed first-Turn user-goal reference, changed
  paths, validation/side-effect facts, unresolved items, feedback, and durable Turn/ToolExecution/
  transition references. No raw arguments, results, reasoning, credentials, or automatic final-answer
  Outcome rows are persisted.
- Rebuilt `task_runs` safely in v5 while preserving v4 child foreign keys and added migration coverage;
  terminal-task follow-up creates and persists a new current TaskRun across restart.
- Requested Grok `/review` twice as required. The first attempt was blocked by proxy DNS/filesystem
  permission errors; the controlled retry read the full local diff but the reviewer request failed on
  the Grok proxy before returning findings. Independent review found and fixed the terminal follow-up
  current-pointer bug, incomplete command digests, missing Outcome/receipt metadata checks, migration
  pragma leakage, active-task replacement/transition contract gaps, and stale architecture wording.
- Validation: focused TaskRun/journal/domain tests 24 passed; offline suite `536 passed, 2 skipped,
  1 deselected`; Ruff format/check, compileall, and `git diff --check` passed.
- Activated Subplan 41 for the Artifact Store and durable payload budgets.

## 2026-08-19 — Subplan 41 Artifact Store and durable payload budgets completed

- Added strict Artifact kinds, sensitivity/retention/state/provenance models and locked metadata,
  excerpt, per-Artifact, and per-TaskRun byte ceilings. Schema v6 stores Artifact metadata plus
  ToolExecution/TaskOutcome references while preserving prior v5 payloads.
- Added the managed `artifacts/` and `artifacts/tmp/` filesystem adapter with opaque ID paths,
  0700/0600 permissions, private temp writes, file and parent fsync, atomic publication, hash/size
  verification, `O_NOFOLLOW` same-descriptor reads, visible staging/missing/corrupt states, and
  read-only orphan/retention reports. No automatic deletion was added.
- Integrated bounded redacted `run_command` output as a command-output Artifact without copying the
  complete result into durable ConversationLog rows. Safe `<redacted>` JSON fields remain publishable;
  raw secret assignments are rejected before metadata reserve. Tool and TaskOutcome references are
  scope-checked and persisted atomically.
- Added exact UTF-8, permission, symlink/hardlink, disk-failure, crash/fault-point, aggregate-budget,
  restart, reference, and retention tests. Final offline validation: `544 passed, 2 skipped,
  1 deselected`; Ruff format/check, compileall, and `git diff --check` passed.
- Requested Grok `/review` twice as required. Both runs collected the local diff and began review, but
  the Grok proxy repeatedly failed settings/telemetry requests and never returned a final report;
  no Grok files changed. Independent review fixed the safe-redacted-command-output rejection and
  same-path verification race. A preliminary concern about closed execution dropping refs was checked
  against `transition_execution`'s preserving `model_copy` behavior and was not applicable.
- Activated Subplan 42 for deterministic context checkpoints and conversation fork.

## 2026-08-19 — Subplan 42 Context Checkpoint and Session Fork completed

- Added v7 immutable `ContextCheckpoint` and `SessionLineage` contracts with bounded deterministic
  sections, omission reasons, source ranges, Artifact references, budget facts, and secret refusal.
- Added durable checkpoint rows/reference edges and Session parent/cut provenance. Effective history
  restores a parent immutable prefix plus child-local records without copying or mutating parent rows;
  child positions remain appendable after the initial fork cut.
- Added `ContextCheckpointService` deterministic compaction/regeneration and `SessionForkService`,
  including closed-boundary checks, optional checkpoint cuts, missing/corrupt Artifact reference
  fallback, crash fault points, and production `SessionApplication` wiring. ContextBuilder consumes
  the latest restored checkpoint while preserving complete retained Turns and post-checkpoint input.
- Added context/fork, migration, artifact-reference, fault-boundary, regeneration, parent/child
  isolation, and auto-restore coverage. Final offline validation: `561 passed, 2 skipped,
  1 deselected`; Ruff format/check, compileall, and `git diff --check` passed.
- Requested Grok `/review` twice for this subplan. Both calls inspected/started against the local
  diff but the Grok proxy timed out before returning a final report; neither call modified files.
  Independent review fixed unvalidated post-update checkpoint metadata budgets, regeneration source
  drift, unavailable Artifact handling, child position invariants, effective-record sequence checks,
  and added lineage/query and production checkpoint wiring.
- Activated Subplan 43 for the Command/Query/Event, CLI, doctor, and backup surface.

## 2026-08-19 — Subplan 43 Command/Query/Event, CLI, doctor, and backup completed

- Bumped the production Operational Store to v8 with bounded versioned `application_events` and
  generic application command receipts. Added typed Core DTOs, stable application errors, cursor
  queries, optimistic row versions, workspace isolation, and same-transaction event/receipt writes
  over the existing Session, Task, Turn, Approval, Recovery, Artifact, Checkpoint, and Fork services.
- Kept the public runtime event lifecycle and ConversationLog ownership unchanged. The REPL slash
  parser remains a thin adapter; CLI Session/Task/Artifact/Recovery/state commands use the unified
  application boundary. Added read-only doctor checks for store, history, tasks/executions,
  checkpoints/forks, Artifacts, references, and event cursors with bounded sanitized reports.
- Added online SQLite backup bundles with Artifact manifest/copy, manifest digest sidecar, restore
  verification, explicit missing/corrupt/changed/unexpected states, credential exclusion checks,
  and a default dry-run cleanup that refuses unsafe or metadata-owned targets and read-only stores.
  Read-only Artifact inspection no longer creates or chmods managed directories.
- Independent review after two Grok `/review` attempts found no remaining blocker. Grok's first
  attempt timed out on proxy/network export; the final attempt read the local 25-file diff and
  started the reviewer but timed out without a findings report, so there were no Grok findings to
  adopt. Independent fixes covered stable invalid-input mapping, Recovery receipt replay handling,
  post-rollback in-memory projection refresh, doctor issue aggregation/path redaction, backup
  manifest tamper/extra-file detection, missing-store doctor CLI behavior, and exact cleanup scope.
- Validation: offline suite `570 passed, 2 skipped, 1 deselected`; focused application/backup/
  cleanup/CLI suite `9 passed`; Ruff format/check, compileall, `morrow --help`, and `git diff --check`
  passed. Activated Subplan 44 for CapabilityGrant and Full Access Manual.

## 2026-08-20 — Subplan 44 CapabilityGrant and Full Access Manual completed

- Added v9 CapabilityGrant and immutable PermissionSnapshot evidence with local-interface-only
  grant creation, Full Access Manual profile gating, explicit unconfined Host warning/approval,
  per-execution evidence, expiry, revocation, cancellation requests, and read-only doctor checks.
- Kept Stage 4 intentionally narrow: only `unconfined_host_process` is elevated; structured
  workspace tools retain their existing boundaries, Full Access Auto remains unsupported, and
  crash-resumed AgentRuns inherit neither grants nor permission snapshots.
- The single successful Grok `/review` found no blocker. Its actionable suggestions were reviewed
  and fixed: freeze uses the durable AgentRun snapshot, revoked grants cannot stamp new elevated
  executions and close pending work, warning digests are canonical, Host cancellation after entry
  records UNKNOWN, execution rechecks grant activity, and grant errors carry typed codes.
- Final Subplan44 validation: offline suite `600 passed, 2 skipped, 1 deselected`; Ruff format/check,
  compileall, `morrow --help`, and `git diff --check` passed. A later redundant Grok retry was not
  used after the user clarified that one review-fix cycle is preferred; the retry ended with a
  remote usage-balance error and made no workspace changes.

## 2026-08-20 — Activated Subplan 45 Stage 4 acceptance

- Subplan 44 was committed as `3e54dee` only after the full offline and quality gates passed.
- Activated Subplan 45 for integrated product stories, fault/migration/package acceptance, the
  requirement-to-evidence document, and final Stage 4 documentation truth. Stage 5 remains inactive.

## 2026-08-20 — Subplan 45 Stage 4 acceptance completed

- Final integrated acceptance passed: `140 passed, 2 skipped`; the two skips are nested-environment
  host-level Seatbelt tests, with the host-level Stage 3 evidence retained separately.
- Final offline suite passed with `600 passed, 2 skipped, 1 deselected`; Ruff format/check, compileall,
  CLI help, and `git diff --check` passed. The wheel rebuilt successfully with SHA-256
  `1a71fe0f60f43ee05ea4a325e630616c9317b8e5e98c300507ee8969cabb1182`; isolated install and durable
  Session recovery had already passed from the same production code.
- Reconciled the roadmap, architecture, Stage 4 route, acceptance evidence, and execution state. Stage 4
  is closed; Stage 5 remains inactive pending an explicit user request. No new production capability was
  added during acceptance, and no additional Grok review loop was run after the single Subplan44
  review-fix cycle.

## 2026-08-20 — Stage 4 final Grok review and single review-fix

- Ran the requested one-time read-only Grok review over the complete Stage 4 implementation and saved
  [`docs/reviews/stage-4-final-grok-review.md`](../docs/reviews/stage-4-final-grok-review.md). No second
  Grok review was run.
- Confirmed and fixed the report's B1/B2/H1–H4 findings plus O1/O3/O4/O6/O7: explicit Session resume,
  same-transaction recovery lifecycle, `/recovery`, removal of the no-op public RETRY path, item-scoped
  recovery closure, stable opaque durable tool-call correlations, fail-closed backup verification,
  secret-token false-positive reduction, lifecycle-validator clarification, and recoverable-session
  startup guidance.
- Final validation after fixes: offline suite `605 passed, 2 skipped, 1 deselected`; focused review-fix
  regression `70 passed`; Ruff format/check, compileall, CLI help, `git diff --check`, wheel build, and
  isolated wheel import/help passed. Stage 4 remains closed and Stage 5 remains inactive.

## 2026-08-20 — Activated Subplan 46 architecture boundary refactor

- User explicitly authorized remediation of verified God Class and dependency-boundary debt after
  Recovery workflow fix `8587622`.
- Locked behavior-preserving scope: one Recovery lifecycle writer, domain collaborators behind the
  compatible operational API, narrow journal ports over one SQLite transaction session, phased
  AgentLoop helpers, grouped CLI modules, and architecture regression tests.
- Stage 5 remains inactive; no schema, capability, policy-default, public-event, or network change is
  authorized.

## 2026-08-20 — S4.46.1 Recovery ownership consolidated

- Removed the duplicate `SessionPersistence.apply_recovery` command/lifecycle implementation;
  production and tests now use `OperationalApplicationService.resolve_recovery` as the sole writer.
- Recovery-created AgentRuns use the current runtime instance snapshot when a live Session
  persistence context exists, while standalone CLI recovery retains the prior frozen snapshot.
- Focused Recovery/API/terminal validation passed: `31 passed`; Ruff check and format passed.

## 2026-08-20 — S4.46.2 application command domains extracted

- Kept `OperationalApplicationService` as the compatible client facade while extracting Recovery
  and Permission/Approval command transactions into explicit domain collaborators.
- Reduced `application/api.py` from 1,598 to 1,058 lines; Recovery and permission implementations
  are now independently owned and tested without changing command signatures or transaction scope.
- Focused API/permission/recovery validation passed: `35 passed`; Ruff check and format passed.

## 2026-08-20 — S4.46.3–S4.46.5 dependency and runtime seams completed

- Domain Artifact, Task, Checkpoint/Fork, Grant, Recovery, and durable conversation services now
  type against Core journal ports instead of the concrete SQLite adapter. One concrete adapter is
  intentionally retained for shared cross-domain transactions rather than replaced by independent
  connections or repository-local commits.
- Extracted ordered public-event rendering from the AgentLoop transition method while preserving
  the single loop and ConversationLog owner. Focused loop/lifecycle validation passed: `73 passed`.
- Standalone CLI Recovery no longer reaches through `api.journal`; the Recovery application handler
  owns durable log restoration and writer construction. Added AST architecture gates for Core layer
  direction, journal-port adoption, single Recovery command ownership, and CLI API encapsulation.
- Focused port/architecture validation passed: `68 passed`; focused CLI/Recovery validation passed:
  `43 passed`; Ruff and CLI help gates passed.

## 2026-08-20 — Subplan 46 completed

- Full offline suite passed: `613 passed, 1 deselected`.
- Ruff format/check, compileall, main CLI help, Recovery CLI help, and `git diff --check` passed.
- The high-risk God Class coupling is contained through single Recovery ownership, application
  collaborators, narrow journal ports, explicit runtime/CLI seams, and architecture regression
  gates. Physical source partitioning remains optional follow-up work, not a correctness blocker.
- Stage 4 remains closed; Stage 5 remains inactive.

## 2026-08-20 — S4.47.1–S4.47.5 real-user remediation implemented

- Replaced workspace-local, path-unlink Artifact cleanup with data-root-global authority checks,
  trusted descriptor-bound directory validation, a non-replayable transactional final check, and
  rename-only retained quarantine. Normal apply truthfully reports `removed=0` and
  `quarantined=1`; original bytes are never unlinked or truncated.
- Restored production Fork-child continuation while preserving the initial no-inherited-task rule.
  Tightened Session admission to ACTIVE + health OK, archive/current-task invariants, Doctor
  contradiction detection, and strictly monotonic outer-transaction `updated_at` tokens.
- Added CLI page metadata/JSON output, Doctor non-OK exit 2, stable lifecycle/health errors, and
  Grant stale consistency. Reconciled the roadmap, ADRs, architecture, README, acceptance evidence,
  and the historical real-user report without rewriting the original observations.
- At this checkpoint S4.47.6 remained active; final focused/offline counts, Ruff, compileall, CLI
  help, final diff check, evidence reconciliation, and verified commits were still required.

## 2026-08-20 — Provisional S4.47.6 gate before independent final review

- Closed RUT-001 through RUT-008 with data-root-global rename-only Artifact quarantine, usable Fork
  children, ACTIVE + health OK foreground admission, archive/current-task invariants, monotonic
  transaction-scoped Session timestamps, truthful Doctor/CLI behavior, and stable errors.
- Final focused RUT/Stage 4 regression across 14 files passed: `199 passed in 5.70s`.
- Full offline suite passed: `663 passed, 1 deselected in 15.96s`. Ruff format reported
  `164 files already formatted`; Ruff check and compileall passed.
- Main CLI help and cleanup CLI help exited 0. Cleanup help states that apply moves validated
  candidates into a private quarantine and does not destroy original bytes. `git diff --check`
  passed.
- Reconciled the historical report, remediation matrix, acceptance evidence, ADRs, Stage 4 roadmap,
  architecture, README, and execution state. A later independent final review superseded this
  closeout after finding the repeat-resume Recovery issue below.

## 2026-08-20 — Subplan 47 final-review Recovery defense completed

- Independent final review found that a resolved RecoveryReport could be submitted under a new
  command ID, clear a later quarantined/read-only Session health state, and create another resume
  AgentRun. The existing same-command receipt replay remained correct, but report terminality was
  not a sufficient new-command guard.
- Fixed the public and transactional boundaries: the same command receipt replays; a new command
  against a non-OPEN report is stably rejected; the write transaction rechecks the durable report
  status; and `resume_recovery()` requires the Session to remain ACTIVE + health OK. No rejected
  command creates a receipt, clears later health, or creates a duplicate AgentRun.
- The original independent reviewer re-reviewed the final implementation and reported no remaining
  P0/P1. Verified source/test progress is committed at `73f24de`.
- Final focused RUT/Stage 4 regressions passed across 14 files: `199 passed in 5.83s`. The full
  host-level non-live suite passed `663 passed, 1 deselected in 12.26s` with every Seatbelt test
  executed and `0 skipped`.
- Ruff format reported `164 files already formatted`; Ruff check, compileall, main CLI help,
  cleanup CLI help, and `git diff --check` all exited 0. Cleanup help still states that apply moves
  candidates into private quarantine without destroying original bytes.
- Subplan 47 and Stage 4 remediation are closed. Stage 5 remains inactive.

## 2026-08-20 — Activated Subplan 48 pre-Stage 5 boundary refactor

- User authorized follow-up after an architecture audit confirmed remaining God Method/Class,
  hidden runtime-protocol, private-facade, and duplicated composition debt.
- Activated behavior-preserving work on `refactor/pre-stage5-boundaries`. Stage 5 remains inactive;
  no schema, capability, bundled policy, public-event, network, Skill, MCP, or credential change is
  authorized.
- S48.1 starts with the explicit durable runtime contract while preserving AgentLoop and
  ConversationLog ownership.

## 2026-08-20 — S48.1 explicit durable runtime contract completed

- Added `DurableRunCoordinator` as the complete production persistence contract alongside the
  deliberately narrow process-local `SessionCommitter` path.
- AgentLoop no longer discovers durable operations through `getattr`, reaches through the
  committer to its Journal/clock/fault injector, or probes for the standard ToolExecutor context
  method. SessionPersistence now exposes time, fault checks, and active-grant evidence through the
  explicit contract.
- Added an architecture regression requiring AgentLoop to avoid the committer/Journal and requiring
  SessionPersistence to provide every declared coordinator operation.
- Validation passed across 221 focused runtime, persistence, permission, recovery, API, CLI, and
  product regressions. Touched-file Ruff check/format, compileall, and `git diff --check` passed.

## 2026-08-20 — S48.2 AgentLoop state and tool execution extraction completed

- Replaced the large mutable-local cluster in `run_task()` with a typed per-run state object.
- Extracted policy denial, durable approval, permission recheck, handler timeout/cancellation,
  grant evidence, fault injection, and handler-completed persistence into `ToolCycleExecutor`.
  AgentLoop still emits every public event and commits every ToolMessage/ConversationLog change.
- Made ToolExecutor approval dispatch an explicit runtime method and added architecture protection
  preventing the ToolCycle collaborator from importing conversation or event ownership.
- `run_task()` decreased from 609 lines/112 branch nodes at audit time to 484 lines/90 branch nodes;
  the extracted `execute_call()` is 129 lines/17 branch nodes with typed inputs and output.
- Full offline validation passed: `663 passed, 2 skipped, 1 deselected`. The skips are the existing
  nested-sandbox Seatbelt cases. Full Ruff format/check, compileall, CLI help, and
  `git diff --check` passed.

## 2026-08-20 — S48.3 permission boundary checkpoint

- Extracted immutable PermissionSnapshot construction, run-bound grant selection, execution
  evidence validation, handler-entry revalidation, and active-grant checks into an explicit
  `RunPermissionCoordinator` backed by a narrow composite journal port.
- SessionPersistence retains compatibility methods but delegates permission authority; its source
  footprint decreased from 908 lines at audit time to 822 lines at this checkpoint.
- Added an architecture guard preventing concrete SQLite coupling in the coordinator and preventing
  permission construction/validation logic from returning to SessionPersistence.
- Permission, tool-persistence, recovery, conversation, and architecture regressions passed:
  `51 passed`. Touched-file Ruff format/check, compileall, and `git diff --check` passed.

## 2026-08-20 — S48.3 durable-tool boundary checkpoint

- Split durable tool ownership into an execution coordinator for approval/state transitions and a
  conversation persistence collaborator for atomic Assistant/Tool record and execution writes.
  AgentLoop still owns public events and plans every ConversationLog mutation.
- SessionPersistence retains the complete runtime compatibility surface through thin delegates;
  durable permission/tool implementation logic no longer imports the concrete SQLite adapter.
- SessionPersistence decreased to 606 lines at this checkpoint. The extracted classes are bounded
  to 246 lines/10 methods and 123 lines/3 methods rather than recreating one new God Class.
- Full offline validation passed: `665 passed, 2 skipped, 1 deselected`; the skips are the known
  nested-sandbox Seatbelt cases. Full Ruff format/check, compileall, CLI help, architecture guards,
  and `git diff --check` passed.

## 2026-08-20 — S48.3 SessionPersistence decomposition completed

- Extracted Turn admission/replay and terminal Task transactions into `TurnSubmissionCoordinator`;
  extracted context/log/Recovery projection restoration into `SessionRestoreCoordinator` with one
  typed `DurableTurnState` shared by the compatible facade.
- Replaced direct application API, Recovery, and command mutations of `_session`,
  `_last_client_message_id`, and current Task/Agent fields with explicit synchronization methods.
- SessionPersistence decreased from 908 lines at audit time to 346 lines/33 mostly thin facade and
  compatibility methods. Turn submission is 276 lines/7 methods and restoration 140 lines/7
  methods, each with a narrow journal port and explicit transaction/state ownership.
- Full offline validation passed: `667 passed, 2 skipped, 1 deselected`; the skips are the known
  nested-sandbox Seatbelt cases. Ruff reported 169 files formatted; Ruff check, compileall, CLI
  help, architecture guards, and `git diff --check` passed.

## 2026-08-20 — S48.4 operational journal partition completed

- Introduced one `SqliteJournalBackend` and explicit transaction context owning executor,
  replayability, timestamp, and touched-Session mutation state for all bounded repositories.
- Split application events, artifacts, context checkpoints, conversation/Turn receipts,
  AgentRun permissions, Recovery, Tasks, and durable tools into cohesive repositories that accept
  the shared backend and narrow callbacks rather than the parent journal facade.
- Reduced `journal.py` from 3503 to 931 lines. Its 832-line facade class retains 102 compatibility
  methods, but only 24 are longer than eight lines; remaining substantive SQL owns the Session
  aggregate, with a 70-line maximum method. Context and conversation repositories are 294 and 365
  lines, avoiding both a replacement God Class and one-class-per-method fragmentation.
- Removed application cleanup and backup reach-through to private SQLite session/executor state;
  added public write-capability, active-transaction, and schema-version queries.
- Added architecture regressions for repository delegation, parent-facade independence, and the
  public transaction boundary. Full offline validation passed: `670 passed, 2 skipped,
  1 deselected`; Ruff format/check, compileall, CLI help, state cleanup help, and
  `git diff --check` passed.

## 2026-08-20 — S48.5 application context and composition completed

- Added a 194-line `ApplicationCommandContext` that explicitly supplies the journal, workspace,
  clock, ID source, Task/Recovery services, persistence, and shared idempotency/event/receipt/error
  bookkeeping needed by transactional command handlers.
- Permission and Recovery application services now receive that context and no longer retain the
  complete `OperationalApplicationService` parent facade. Architecture tests prevent regression
  to parent-facade injection.
- Added shared `build_operational_services()` and `build_operational_api()` composition roots.
  Interactive bootstrap and headless CLI commands now construct artifacts, checkpoints, forks,
  Recovery, doctor, backup, and the application API through the same path; CLI no longer directly
  instantiates those services.
- Kept the 982-line application API as the deliberate public command/query facade. Its complex
  permission and Recovery commands are already delegated; further one-class-per-command splitting
  would add protocols and navigation without isolating another independent lifecycle.
- Full offline validation passed: `672 passed, 2 skipped, 1 deselected`; Ruff format/check,
  compileall, CLI help, state cleanup help, architecture guards, and `git diff --check` passed.

## 2026-08-20 — S48.6 closeout verified

- Removed the unreferenced API-level request-digest re-export and the unused log-projection and
  exception-translation facade wrappers. Retained Core base ports that are still inherited by
  active composite ports and retained compatibility methods with real production/test callers.
- Reconciled `docs/ARCHITECTURE.md` with the explicit durable runtime coordinator, tool-cycle
  executor, decomposed SessionPersistence, bounded SQLite repositories/shared transaction backend,
  application command context, and shared operational composition roots.
- Against baseline `408da68`, `AgentLoop.run_task()` decreased from 609 lines/112 branch nodes to
  484/90; `SessionPersistence` from 908 lines/112 branches to 346/17; and
  `SqliteOperationalJournal` from 2790 class lines/335 branches to 832/48. The application facade
  decreased from 1014 class lines/98 branches to 918/80 while retaining its public boundary.
- Canonical unsandboxed validation passed: `uv run pytest -m 'not live'` reported
  `674 passed, 1 deselected`, including the real Seatbelt cases. `uv run ruff format --check .`,
  Ruff check, compileall, main CLI help, state cleanup help, and `git diff --check` all exited 0.
- Fast-forwarded `refactor/pre-stage5-boundaries` into local `main` at `bb8f5d5`. The topic branch
  contained eight verified commits and local `main` had no divergent commits. Remote publication
  was withheld because `main` was already 29 commits ahead of `origin/main` before integration;
  pushing would also publish unrelated pre-existing local history.

## 2026-08-20 — Stage 5 executable plan authorized

- The user authorized Stage 5 planning after reviewing the two Learning/Memory design discussions
  against the current code. Production implementation remains unstarted; Subplan 49 is ready.
- Replaced the completed Pre-Stage 5 index with a six-slice Stage 5 route: 49 domain/v10, 50 accepted
  Outcome pipeline, 51 Inbox/Project Knowledge/v11, 52 configuration Promotion Saga, 53
  MemorySelection/context/v12, and 54 production Reviewer/evaluation/acceptance.
- Locked current-code semantics: accepted TaskOutcome is the automatic trigger; no
  `completed`/`corrected` Task state; no background Worker; first release exposes only `off` and
  `review_only`; `/accept` remains Task-only and `/learn accept` is Candidate-only.
- Locked authority and runtime boundaries: YAML remains Active Profile/Preferences authority;
  SQLite owns Learning audit, Project Knowledge, Saga provenance, and selection; only Promotion may
  change Active state; AgentRun freezes the selected memory consumed by ContextBuilder.
- Corrected the Stage 5 roadmap and master roadmap to describe planning status, actual Task states,
  closed `explicit_auto`, Project Knowledge classification for project test commands, and the
  Subplan 49–54 execution sequence.
- At the planning baseline `main` and `origin/main` are synchronized at `61f82f2`. The two untracked
  Stage 5 research documents remain preserved user files and were not adopted or modified.

## 2026-08-21 — Subplan 49 Learning foundation verified

- Implemented the governed Core Learning domain: `LearningPolicy`, `LearningReview`, typed Evidence,
  discriminated Candidate payloads, deterministic fingerprints/confidence inputs, Suppression, and
  fail-closed safety codes. Added narrow Learning/Reviewer ports, bounded `LearningContext`, and
  deterministic no-tool test fixtures outside production composition.
- Added checksummed Operational Store v10 migration and shared-backend SQLite repositories for policy,
  reviews/evidence, candidates, links, and suppressions. Preserved YAML as the Profile/Preferences
  Active authority; the application boundary exposes only `off` and `review_only`, with default
  `review_only`, optimistic row versions, idempotent command receipts, and sanitized events.
- Split the Learning adapter into bounded policy, review/evidence, candidate/suppression mixins plus a
  small codec/composition module. Added architecture guards and migration, isolation, corruption,
  lease, OCC, multibyte excerpt, and policy atomicity regressions.
- The requested Grok `/review` completed after one permission-blocked attempt and one escalated run.
  Grok identified one P0, two P1, and six P2 items; the feasible issues were independently verified
  and fixed once: UTF-8 excerpt bounds, Candidate evidence-link OCC/ownership/terminal guards,
  fingerprint binding, evidence/context budgets, payload shape/truncation, default-policy OCC token,
  and the closed `source_kind` SQL constraint. No second review was run for this subplan.
- Final validation: `707 passed, 2 skipped, 1 deselected` for `pytest -m 'not live'`; Ruff format/check,
  compileall, `morrow --help`, and `git diff --check` passed. The two skips are nested macOS Seatbelt
  tests unavailable inside the Codex sandbox.

## 2026-08-21 — Subplan 50 accepted Outcome pipeline implementation gate

- Implemented the accepted TaskOutcome → pending Review → bounded Evidence/LearningContext →
  validated Candidate pipeline. Acceptance and explicit re-review remain transactional and
  idempotent; Review execution is a one-shot foreground claim/lease/finalize flow with no worker,
  and provider/model failures leave the accepted TaskOutcome unchanged.
- Added deterministic evidence extraction, strict Reviewer draft validation, confidence/fingerprint
  calculation, duplicate/conflict/suppression handling, evidence aggregation across explicit
  re-review, sanitized learning events, query APIs, headless learning commands, and truthful
  interactive pending-review behavior. Candidate decisions, Active Knowledge, config promotion,
  and production Reviewer composition remain owned by later Stage 5 subplans.
- Full offline validation passed: `715 passed, 2 skipped, 1 deselected` for `pytest -m 'not live'`;
  Ruff format/check, compileall, `morrow --help`, `morrow learning --help`, and `git diff --check`
  all exited 0. The two skips are nested macOS Seatbelt tests unavailable inside the Codex sandbox.

## 2026-08-21 — Subplan 50 Grok review-fix and closeout

- The required Grok review completed on the committed S50 branch after one sandbox permission block
  and one escalated foreground run. Grok reported four confirmed defects, plus suggestions about
  Active YAML duplicate visibility, evidence-reference capacity, headless pending-review output,
  and fault-matrix coverage. No Grok write, commit, or push was permitted; no second review was run.
- Independently confirmed and fixed: Evidence extraction now pairs session segments with their actual
  Task Turns instead of taking the Session tail; long safe user text is stored with a bounded excerpt
  and original digest; the context builder projects oversized Outcomes and fits prioritized Evidence
  and suppressions into the named rendered budget; context construction failures are internal review
  failures rather than provider/output failures; foreground cancellation releases a live lease with a
  retryable `cancelled` failure; Reviewer drafts can reference only Evidence present in context; and
  proposed-candidate evidence aggregation is capped at the domain reference limit.
- Also fixed the headless `morrow task accept` hint so an eligible pending Review ID is printed. The
  Active YAML duplicate suggestion is intentionally deferred to Subplan 52, which owns the
  ConfigPatchService-backed Profile/Preferences promotion and current-target projection; S50 has no
  second configuration authority. The remaining fault-matrix suggestion was addressed with focused
  isolation, long-input, budget, cancellation, and CLI regressions without broadening S50 ownership.
- Review-fix validation passed: `720 passed, 2 skipped, 1 deselected` for `pytest -m 'not live'`;
  Ruff format/check, compileall, `morrow --help`, `morrow learning --help`, and `git diff --check`
  all exited 0. The two skips are nested macOS Seatbelt tests unavailable inside the Codex sandbox.

## 2026-08-21 — Subplan 51 activated

- Fast-forwarded verified Subplan 50 into local `main` at `4d47be8`, retired its topic branch, and
  created `feat/stage5-inbox-knowledge` from that verified baseline.
- Activated Subplan 51. Its first task is the immutable v11 migration and bounded persistence
  boundary for candidate decisions, Project Knowledge heads/revisions/evidence, workspace memory
  revision state, and reserved-but-unused configuration Saga tables. Preference/Profile YAML
  promotion remains closed for Subplan 52; the two research documents remain untracked user files.

## 2026-08-21 — Subplan 51 S51.1 verified

- Added schema v11 `learning_inbox_project_knowledge` without changing v1–v10 migration bodies:
  immutable candidate decisions, Project Knowledge heads/revisions/evidence, workspace memory
  revision state, and reserved `promotion_operations`/`configuration_activations` Saga tables.
- Added independent Core models/port and `SqliteLearningMemoryJournal`; the parent Operational
  Journal only composes and delegates to it. Revision statements are digest-bound, provenance and
  workspace guards are enforced, and revision identity is immutable at the SQL boundary.
- Focused validation passed: 10 Stage 5 store tests plus 61 operational/store/journal tests; Ruff
  format/check passed for touched code. S51.2 is now active; no Active YAML or Saga behavior was
  introduced.

## 2026-08-21 — Subplan 51 S51.2 verified; S51.3 active

- Added focused `LearningApplicationService` and `MemoryApplicationService` children. Typed views
  provide bounded status/counts, Review metadata without policy/prompts, Candidate summaries and
  bounded Evidence/conflict/suppression/target projections, Knowledge history/evidence, and a
  pure candidate decision preview. Existing Stage 5 API methods remain compatibility shims.
- Added typed reject and expiry commands with immutable decision rows, exact+semantic suppression,
  lazy expiry on reject, bounded cutoff expiry, optimistic row checks, deterministic batch decision
  command IDs, sanitized events, command receipts, and replay reconstruction. Preference/Profile
  promotion remains unavailable and no YAML/Saga behavior was added.
- Focused S51.2/S51.3 validation currently passes: four application projection/decision tests,
  existing Learning pipeline/store/policy regressions, Ruff, and compileall.

## 2026-08-21 — Subplan 51 completed and Grok review-fix applied

- Completed S51.3–S51.6: immutable Candidate decisions with rejection/suppression/expiry, the
  SQLite-atomic Project Knowledge Promotion Service, immutable revisions and lifecycle tombstones,
  workspace memory revisions, candidate-only acknowledgement for future types, and typed
  `/learn`/`/memory` REPL and Typer interfaces. General command handling was split into focused
  Learning interaction code and the Typer registration was moved to its own module.
- The committed implementation is `4d715ac`. The required Grok `/review` then found four confirmed
  issues: Inbox queries did not persist due-row expiry, Typer Inbox defaulted to all statuses,
  historical `memory show --revision` was ignored, and preview could disagree with acceptance on
  category mismatch. Independently verified and fixed all four in `bd9dfff`; also revalidated edited
  payload fingerprints/duplicates/suppressions, added lazy-expiry receipts and semantic-key digests,
  preserved lifecycle replay snapshots, and improved logical-delete/candidate-only user messaging.
  No second Grok review was run, per the one-review-per-subplan rule.
- Final S51 gate passed: `739 passed, 2 skipped, 1 deselected` for `pytest -m 'not live'`; Ruff
  format/check, compileall, `morrow --help`, `morrow learning --help`, `morrow memory --help`,
  and `git diff --check` all exited 0. The two skips are nested macOS Seatbelt tests unavailable
  inside the Codex sandbox. The two Stage 5 research documents remain untracked and preserved.

## 2026-08-21 — Subplan 52 implementation gate

- Published `PreparedConfigurationChange` with presence-aware before/after digests, exact revision
  replay rules, idempotent `apply_prepared()`, and truthful global/workspace Session projections
  and AgentRun source references.
- Added v11 promotion repositories and split the cross-store configuration Saga into a small
  promotion owner plus focused policy, finalize, recovery, and undo modules. Preference/Profile
  candidates now require explicit evidence, use the bounded schema whitelist, preserve YAML as the
  Active authority, and record activation/undo provenance in SQLite.
- Added explicit unresolved-operation recovery, drift protection, global-scope preview rules,
  Profile promotion, CLI/REPL surfaces, and crash/replay tests. The two research documents remain
  untracked user files and were not staged.
- Full offline validation passed: `749 passed, 2 skipped, 1 deselected`; Ruff format/check,
  compileall, root/Learning/Memory CLI help, and `git diff --check` passed. The required Grok review
  is the remaining S52 closeout action.

## 2026-08-21 — Subplan 52 Grok review-fix and closeout

- Committed S52 implementation as `8b3d32e`, then ran the required read-only Grok `/review` and
  waited for its complete report. Grok returned 15 findings: 5 bugs, 9 suggestions, and 1 nit.
- Independently confirmed and fixed the after-state abort/cancel hole, hidden prepared recovery
  operations, Review writes against `PROMOTING` Candidates, ignored command row-version tokens,
  and missing REPL global scope. Also added explicit after-state projection sync, the required
  `memory.record_activated` event, a bounded promotion result formatter, undo-safe duplicate
  reopening, and focused recovery/CLI/event tests. No second Grok review was run.
- Final review-fix commit is `6c76f75`. Full offline gate passed: `752 passed, 2 skipped,
  1 deselected`; Ruff format/check, compileall, root/Learning/Memory CLI help, and
  `git diff --check` passed. S52 is ready to fast-forward merge; the two research documents remain
  untracked and preserved.

## 2026-08-21 — Subplan 53 activated

- Fast-forwarded the verified S52 branch into local `main` at `7dfe5af`, deleted the clean
  `feat/stage5-config-promotion` branch, and created `feat/stage5-memory-selection` from that
  baseline.
- Activated S53.1: implement the v12 MemorySelection/Item/Query durable contracts, migration,
  repository/ports, and upgrade/future/corruption tests before adding selector or ContextBuilder
  behavior. The two research documents remain untracked and preserved.

## 2026-08-21 — Subplan 53 S53.4 verified; S53.5 active

- Integrated `MemorySelector` into the existing short foreground admission transaction. Each new
  User Turn now persists its immutable selection/items, effective merged Preferences snapshot,
  source revisions, AgentRun, receipt, and ConversationLog append atomically.
- Added the `AgentRunSnapshot` memory reference contract and a small frozen-selection validation
  helper. Recovery AgentRuns reuse the interrupted Run's exact selection; restore and recovery
  quarantine or fail closed on missing/mismatched references instead of reselecting current memory.
- Added admission, rollback, memory-revision-change recovery, and missing-selection restore tests.
  Full offline validation passed: 767 tests, 2 skipped, 1 deselected; Ruff format/check,
  compileall, root/Learning/Memory CLI help, and `git diff --check` passed. S53.5 is active.

## 2026-08-21 — Subplan 53 S53.5 verified; S53.6 active

- Added a bounded `RunContextProjection` containing the exact AgentRun snapshot, selected
  immutable Knowledge revisions, canonical untrusted memory JSON, and content digest. Durable
  admission, restore, rollback synchronization, and recovery now install or rebuild it without
  letting ContextBuilder query Learning storage.
- ContextBuilder consumes the frozen Profile/Preferences baseline and memory block on every model
  and structured-context cycle; process-local/test Sessions retain an explicit live-state fallback.
  Same-Run configuration and Knowledge changes stay invisible until a new AgentRun.
- Added frozen-context, next-Turn refresh, zero-item, and process-local fallback tests. Full offline
  validation passed: 771 tests, 2 skipped, 1 deselected; Ruff format/check, compileall,
  root/Learning/Memory CLI help, and `git diff --check` passed. S53.6 is active.

## 2026-08-21 — Subplan 53 S53.6 verified; S53.7 active

- Added bounded Memory Selection list/show views through the application API, Typer commands, and
  `/memory selection` REPL commands. Output includes AgentRun references, source/current revision
  identifiers, reason codes, character budgets, omission counts, and digests without defaulting to
  Knowledge content.
- Added isolated Memory doctor invariants for selection/item ordering and digests, immutable
  Knowledge references, AgentRun snapshot reuse, and rebuildable current term rows. Backup
  verification now validates selection/Knowledge/term/AgentRun links in the copied SQLite database.
- Split Memory doctor and backup checks into focused modules to keep `OperationalDoctor` and
  `OperationalBackupService` as orchestration layers; updated architecture, roadmap, and README
  documentation.
- Full offline validation passed: 777 tests, 2 skips, 1 deselected; Ruff format/check, compileall,
  root/Learning/Memory CLI help, and `git diff --check` passed. The one-time S53 Grok review is the
  remaining closeout action.

## 2026-08-21 — Subplan 53 Grok review-fix

- Ran the required read-only Grok `/review` once against the complete S53 branch and waited for the
  full result. Grok found two confirmed bugs, two risks, and one feasible suggestion; it reported
  that the freeze/recovery/isolation design was sound and did not identify a god file. Grok made no
  workspace changes.
- Independently confirmed and fixed the feasible findings: MemoryQuery now uses a bounded retrieval
  normalizer instead of the Learning safety scanner; mixed-language query tokenization prioritizes
  identifiers, paths, and words over CJK bigrams; durable Sessions omit live user state when their
  projection is absent; v12 backup verification fails closed on missing memory tables; and
  MemorySelection writes reject item revision-number mismatches.
- Review-fix focused tests passed (26 tests). The final offline gate passed: `785 passed, 2 skipped,
  1 deselected`; Ruff format/check, compileall, root/Learning/Memory CLI help, and `git diff --check`
  passed. No second Grok review was run, per the one-review-per-subplan instruction. The review-fix
  changes are ready to commit before S53 fast-forward closeout.

## 2026-08-21 — Subplan 53 closeout and Subplan 54 activation

- Committed the S53 review-fix as `250b843`, committed the execution-state evidence as `613ffdb`,
  fast-forwarded local `main` from `7dfe5af` to `613ffdb`, verified no topic commits remained
  outside `main`, and retired `feat/stage5-memory-selection`. The two research documents remain
  untracked and preserved.
- Activated Subplan 54 on `feat/stage5-reviewer-acceptance` from verified local `main`. S54.1 is
  now the active task: implement the production no-tool Reviewer boundary using explicit bounded
  messages, strict drafts, sanitized errors, and one bounded repair attempt. Live evaluation remains
  a hold point requiring explicit user authorization and a compatible credential.

## 2026-08-21 — Subplan 54 S54.1 verified; S54.2 active

- Added `ModelLearningReviewer` as a provider-independent no-tool adapter. It renders a fixed
  versioned system contract plus one canonical bounded LearningContext payload, validates request and
  response budgets, uses one total deadline, permits one repair prompt with only an error category
  and schema, rejects unknown/unsafe/oversize/invented-Evidence drafts, and maps provider failures
  to sanitized typed errors without persisting raw output.
- Wired the active Provider/model into interactive and explicit CLI Review composition while keeping
  state queries provider-free. Runner metadata records provider/model/prompt/schema versions, and
  bounded repair usage is returned and included in sanitized Review events. Task acceptance remains
  unchanged and the test-only Reviewer stays available to direct application tests.
- S54.1 focused tests and the full offline gate passed: `790 passed, 2 skipped, 1 deselected`; Ruff
  format/check, compileall, root/Learning/Memory CLI help, and `git diff --check` passed. Checkpoint
  commit: `c955bbc`. S54.2 is now active; no Grok review is run until the complete S54 subplan closes.

## 2026-08-21 — Subplan 54 S54.2 verified; S54.3 active

- Completed Learning policy and foreground controls: `/learn mode` plus `morrow learning set-mode`
  support only `off` and `review-only` (including stable explicit-auto refusal), and review/retry are
  available through the typed API, REPL, and headless CLI. Active Provider composition is used only
  for Review execution; status, Inbox, and query commands remain provider-free. A shared terminal
  path reports zero-candidate Reviews and preserves Ctrl+C cancellation without changing Task
  acceptance semantics.
- Targeted UX/API tests passed (20 tests). The full offline gate passed: `792 passed, 2 skipped,
  1 deselected`; Ruff format/check, compileall, root/Learning/Memory CLI help, and `git diff --check`
  passed. Checkpoint commit: `9ef6f01`. S54.3 is now active.

## 2026-08-21 — Subplan 54 S54.3 verified; S54.4 active

- Added acceptance coverage for WorkflowFeedback and OrchestrationPolicyCandidate alongside the
  existing SkillCandidate boundary. User acceptance records only typed candidate/decision/audit
  state; it creates no Project Knowledge, memory activation, workspace file, Tool/capability state,
  or orchestration runtime state. Existing pipeline gates reject future drafts without typed
  evidence owned by later stages.
- Focused future-candidate tests passed (21 tests across the Stage 5 application set). Full offline
  gate passed: `794 passed, 2 skipped, 1 deselected`; Ruff format/check, compileall,
  root/Learning/Memory CLI help, and `git diff --check` passed. Checkpoint `4ff41a4`. S54.4 active.

## 2026-08-21 — Subplan 54 S54.4 verified; S54.5 active

- Added a versioned synthetic JSON evaluation set and a bounded deterministic evaluator. It strictly
  parses Reviewer batches, enforces evidence allowlists and source authority, rejects safety-negative
  content, deduplicated/suppressed/cross-workspace proposals, malformed or oversize output, and
  keeps future Skill/Workflow/Orchestration proposals candidate-only. Selection item/character
  budgets and same-Run reuse are represented as explicit cases.
- Tightened the shared positive explicit-user evidence predicate and deterministic text classifier:
  negative, one-shot, quoted, and hypothetical user text cannot satisfy the durable Preference/Profile
  gate. Safety-rejected source text is represented by digest/reason code only in Learning context and
  application events.
- The offline report evaluated 26/26 cases, including 5 safety-negative cases, with zero Active
  writes. Focused evaluation tests passed (10 tests). The full offline gate passed `804 passed,
  2 skipped, 1 deselected`; Ruff format/check, compileall, root/Learning/Memory CLI help, and
  `git diff --check` passed. Checkpoint `6f77940`. S54.5 active.

## 2026-08-21 — Subplan 54 S54.5 verified; S54.6 Live hold active

- Added focused, read-only Learning doctor domains for Review/Evidence, Candidate/Suppression, and
  Promotion/Project Knowledge. `OperationalDoctor` remains an orchestration layer and now reports
  bounded Learning counts, lease/recovery state, cross-workspace links, candidate decisions,
  promotion provenance, Knowledge revisions, and memory revision invariants without writing state.
- Added isolated SQLite backup verification for v10–v12 Learning references. Backup verification now
  fails closed on broken Review/Evidence/Candidate/Decision/Promotion/Activation/Knowledge links;
  YAML, workspace index, credentials, and Keychain remain outside the bundle. Added doctor/backup
  acceptance tests for read-only mtime, drift diagnosis, preserved Knowledge/Memory state, and
  tampered decision digest rejection.
- Split the doctor helper into four bounded modules so no new Stage 5 file becomes a god file. Added
  `docs/acceptance/stage5-acceptance.md` and reconciled README, architecture, roadmap, and offline
  evaluation status with v12 behavior and the YAML/SQLite authority boundary.
- Focused doctor/backup regression set passed 23 tests. Complete non-live gate passed `808 passed,
  2 skipped, 1 deselected`; Ruff format/check, compileall, root/Learning/Memory CLI help, and
  `git diff --check` passed. No Live Provider or network execution was attempted; the predeclared
  real-model quality targets remain pending explicit authorization and a compatible credential.

## 2026-08-21 — Stage 5 simulated-user report adjudicated; remediation planned

- Reviewed `docs/acceptance/stage5-simulated-user-evaluation.md` at `5cfb99f` against the current
  Candidate CLI, preview service, Project Knowledge Promotion, SQLite journal, and regression suite.
  F1, F2, and F3 are confirmed. F1 affects headless accept, edit, and reject because all three read
  fields from the wrong level of `LearningCandidateView`; Project Knowledge's dedicated edit option
  guard is also inverted and was added to the remediation scope.
- Added proposed Subplan 55 with regression-first tasks, typed preview/OCC contracts, persisted-time
  normalization, isolated simulated-user replay, one independent Grok review/fix pass, and final
  non-live gates. No production fix, Live Provider call, network request, or credential access was
  performed while drafting the plan.
- Reopened Stage 5 user acceptance in plan/roadmap/acceptance state. Subplan 54's deterministic and
  offline safety evidence remains valid, but it no longer supports a user-ready claim until S55
  passes and the simulated flow is rerun.

## 2026-08-21 — Subplan 54 S54.7 final review/fix and offline closeout

- The required final Grok `/review` was completed once for the complete S54 branch. It reported one
  confirmed bug, six actionable suggestions, and one nit. The CLI failure-status bug and feasible
  suggestions were independently verified and fixed once: failed headless/REPL Reviews now expose a
  failure status and retry hint; the pure evaluator's no-write claim is explicit and is paired with
  real-runner safety integration coverage; correction and scripted safety-negative cases were added;
  the durable classifier accepts legitimate conditional instructions; doctor findings include
  bounded subject IDs; the opt-in Live entrypoint is documented; and future-candidate tests snapshot
  capability, permission, and AgentRun state. No second Grok review was run.
- The final versioned report is 27/27 with 5 safety-negative cases. The pure evaluator performs zero
  writes, and the scripted real-runner safety gate observes zero Candidate, Project Knowledge, or
  Memory Active writes. Focused evaluation tests passed 16 tests.
- The complete non-live gate passed `816 passed, 2 skipped, 2 deselected`; repository Ruff format and
  check, compileall, root/Learning/Memory CLI help, and `git diff --check` all passed. No live Provider,
  network request, or credential was used; the predeclared real-model quality targets remain pending.

## 2026-08-21 — Subplan 54 S54.6 hold recorded; S54.7 final review active

- Added `docs/acceptance/stage5-live-evaluation-hold.md` with the predeclared Live targets, required
  authorization/credential conditions, isolated synthetic-fixture protocol, and the truthful pending
  result. The Stage 5 acceptance report links the hold record.
- No `pytest -m live`, network request, real Provider call, or credential access was attempted. The
  offline implementation and safety evidence remain valid but do not count as real-model quality.
- S54.7 is active; the complete S54 branch now requires exactly one final Grok `/review`, one
  independent review/fix pass, and a final offline/quality gate.

## 2026-08-21 — Subplan 55 S55.1–S55.3 verified; S55.4 active

- Created `fix/stage5-simulated-user-remediation` and added regression-first coverage using real
  `LearningCandidateView` projections and Typer `CliRunner`. The pre-fix focused set recorded 9
  failures across typed Candidate decisions, rejection intent, Project Knowledge edit fields,
  stale OCC handling, and non-zero-microsecond first promotion.
- Fixed headless Candidate accept/edit/reject to use the typed view and preview OCC token, corrected
  the Project Knowledge edit-field guard, and added explicit reject/reject-and-suppress preview
  intent with truthful Typer and REPL rendering. Configuration edit finalization now constructs its
  final proposal before Pydantic validation of the immutable decision.
- Fixed first Project Knowledge promotion to use the journal's persisted head after insertion and
  compare immutable `created_at` at SQLite's existing integer-second precision. Focused CLI,
  Project Knowledge, Store, Learning application, configuration, and terminal tests pass: 36 and
  56 tests respectively. No Live Provider, network, or credential access was attempted.
- S55.4 is active; the isolated headless simulated-user flow, restart checks, doctor, and backup
  evidence remain to be rerun before Stage 5 user acceptance can be restored.

## 2026-08-21 — Subplan 55 S55.4 replay verified; S55.5 active

- Replayed the isolated user flow in a temporary state root with a scripted Reviewer and three accepted
  Tasks. The replay produced five candidates and used separate fresh CLI processes for Preference accept,
  Preference edit, Project Knowledge edit, ordinary reject, and reject-and-suppress.
- Fresh-process Learning/Memory reads, first Project Knowledge revision, Memory revision 1, `state doctor`
  health `ok`, and SQLite backup verification all passed. No network, Live Provider, credential, or user
  state was accessed; the two untracked research files remain preserved.
- Updated the simulated-user report, Stage 5 acceptance report, roadmap, README, and execution state to
  close the F1/F2/F3 remediation claim. S55.5's one independent review/fix pass and final offline gate
  remain active.

## 2026-08-21 — Subplan 55 S55.5 review/fix and final gate verified

- Resumed the requested Grok review session and received its read-only report. It confirmed the typed
  Candidate/OCC, explicit reject intent, persisted Project Knowledge timestamp, configuration finalization,
  and event/safety boundary repairs; it identified that the first REPL `/learn edit` preview still showed
  `accept`.
- Independently verified and fixed the REPL preview to show `edit_and_accept`; added Preference and Project
  Knowledge field-guard negatives plus a fail-closed API regression for reject previews receiving accept-only
  arguments. No second Grok review was run.
- Focused review regression set passed 28 tests. The complete offline gate passed 831 tests, 2 skipped,
  and 2 deselected. Ruff format/check, compileall, root/Learning/Memory CLI help, and `git diff --check`
  passed. No Live Provider, network, or credential path was run; the two untracked research files remain
  preserved.

## 2026-08-21 — Preference Learning v2 refactor planned

- Adjudicated the real-Provider evidence at `c6031d2`: natural-language Preference add/overwrite was
  `0/3`, remove was `0/2`, Mimo timed out under the foreground 15-second deadline, and a restored
  Session retained stale Preference behavior after overwrite. The existing fixed-field/marker-gated
  path is therefore not accepted as a model-quality solution.
- Locked the replacement architecture: main Agent and no-tool Preference Reviewer are separate;
  Review is a durable asynchronous job; the Reviewer emits only 0–N generic add/replace/remove
  operations; deterministic validation creates independent Inbox proposals; and one same-scope
  recoverable Writer batch changes YAML after user acceptance or direct approved management.
- Replaced the active master plan and added planned Subplans 56–61 for generic contracts/migrations,
  Writer, semantic Reviewer/Inbox, async worker, next-AgentRun refresh, and closeout/evaluation.
  Every subplan and the final integrated implementation require the user-specified one-time
  `$grok-delegate` `/review` plus independent review-fix without re-review.
- No production code, Provider, credential, network path, or user state was touched while drafting.
  The two untracked Stage 5 research files remain preserved.

## 2026-08-21 — Preference v2 plan Grok review adjudicated

- Ran the requested read-only `/review` once through `$grok-delegate` using its default
  `grok-4.6`/`xhigh` configuration and waited for the complete result. Grok changed no project file,
  branch, worktree, commit, or remote state.
- Independently confirmed all five blockers: the S57 legacy Candidate apply gap, missing v13 frozen
  Active snapshot payload, legacy AgentRun decoder scheduled after the shape cutover, undefined
  tombstone/duplicate behavior, and unlocked aggregate `config.yaml` migration semantics.
- Adopted the valuable secondary findings in the same one-time plan-fix: exactly one `pev_` Evidence,
  same-Turn direct-write suppression, exact legacy mapping strings/IDs, learned-scope bounds,
  terminal-versus-retryable failures, distinct Review/AgentRun snapshot budgets and ordering, thin
  dispatch around existing god files, single claim authority per Review table, exact acceptance
  arithmetic, and a current Stage 5 supersession note.
- No second Grok review was run. The plan now assigns the legacy Candidate bridge and AgentRun decoder
  activation to S57, freezes v13 snapshot count/byte/digest fields in S56, and locks full-aggregate
  global config publication without changing implementation code.

## 2026-08-21 — Subplan 56 activated

- The required non-live baseline initially could not initialize the sandboxed `uv` cache because the
  existing cache path was not readable. The same command was rerun with the narrowly scoped local
  cache permission and passed: `833 passed, 2 deselected`.
- The user explicitly requested implementation of the latest plan. S56 was activated on
  `feat/stage5-preference-foundation` from local `main` at `c6031d2`. The two untracked research files
  remain preserved and are excluded from implementation commits.

## 2026-08-21 — Subplan 56 implementation checkpoint verified

- Added independent generic Preference contracts and pure same-scope add/replace/remove plus
  enable/disable reducers. Exact same-scope duplicate checks include active/disabled entries while
  deleted tombstones remain terminal and do not block a new add.
- Added decode-only legacy global/workspace migration codecs with locked mapping sentences and
  source-derived IDs, a v3 workspace target document, historical AgentRun compatibility decoding,
  atomic backup/OCC publication helpers, and compatibility fixtures. Existing legacy foreground
  configuration behavior remains unchanged.
- Added Operational Store v13 Preference Review job, exactly-one current-user Evidence, Proposal,
  Evidence-link, WriteBatch, and batch-link tables with bounded JSON, workspace guards, indexes, and
  rollback-safe registration. YAML remains the only Active Preference authority; no new Review or
  Worker path is enabled.
- Focused S56 suite passed 100 tests. Full offline validation passed `853 passed, 2 deselected`;
  Ruff format/check, compileall, root CLI help, and `git diff --check` passed. No Provider, network,
  credential, or Live test path was used.

## 2026-08-21 — Subplan 56 structure split and checkpoint gates

- Split the foundation into bounded responsibilities: generic/domain documents, persistence models,
  strict SQLite codecs plus Review/Proposal/Writer repositories, legacy snapshot compatibility,
  YAML contracts, atomic I/O, and migration publication. The public journal/YAML classes remain thin
  facades; no Reviewer, enqueue, Writer, or automatic publication path was enabled.
- After the split, the focused S56 set passed 59 tests and the full offline gate passed `853 passed,
  2 deselected`. Ruff format/check, compileall, root CLI help, working/index diff checks, and import
  smoke tests passed. No Provider, network, credential, or Live test path was used.

## 2026-08-21 — Subplan 56 required review/fix completed

- The single read-only Grok review confirmed the reducer, legacy mapping, YAML OCC path, public API,
  module boundaries, and inactive Reviewer/enqueue/Writer boundaries. It identified two v13 blockers:
  terminal Review jobs could not persist `completed_at`, and Evidence was incorrectly unique by
  `(workspace_id, turn_id)` rather than allowing one row for each job/review version.
- Independently fixed the frozen DDL and matching job-model invariant, removed the turn-wide Evidence
  uniqueness, added a Turn/session identity guard, and removed one dead codec helper plus stage-history
  narration from production docstrings. Added regression coverage for terminal timestamps and two
  review versions on one Turn. No second Grok review was run.
- Focused S56 tests passed 61; the final offline gate passed `855 passed, 2 deselected`. Ruff
  format/check, compileall, root CLI help, and working/index diff checks passed. No Provider, network,
  credential, or Live test path was used.

## 2026-08-21 — Subplan 56 merged; Subplan 57 activated

- Committed the S56 closeout at `fdce537`, fast-forwarded local `main`, and retired
  `feat/stage5-preference-foundation`. The two untracked research files remain preserved and were not
  staged. Local `main` is ahead of its configured upstream; no push was attempted.
- Created `feat/stage5-preference-writer` from the verified `main` and activated S57. The first task
  is the deterministic same-scope Writer prepare/apply/finalize foundation; Reviewer, Worker,
  foreground enqueue, and automatic publication remain disabled.

## 2026-08-21 — Subplan 57 implementation checkpoint prepared

- Implemented the same-scope YAML-authoritative Preference Writer with bounded multi-operation
  batches, stable add IDs, revision/value-digest OCC, prepare/apply/finalize phases, crash retry,
  drift quarantine, lifecycle enable/disable, queries, and direct `manage_preferences` approval.
- Added first-write legacy Candidate translation, generic projections through Context/AgentRun
  snapshots, full global aggregate preservation, workspace v3 publication, historical AgentRun
  decoding, CLI/REPL Preference management, and a durable recovery declaration for
  `manage_preferences`. Existing Profile/Provider behavior and legacy compatibility paths remain.
- Candidate acceptance and undo now use the same generic Writer batch while retaining Candidate,
  Evidence, decision, and activation history. The generic authority is exposed to a new Session only
  after a v2/v3 document exists; the legacy bridge remains available before that point.
- Focused Writer/configuration/Promotion/CLI tests passed; the complete offline gate passed
  `867 passed, 2 deselected`; Ruff format/check and architecture tests passed. No Provider, network,
  credential, or Live test path was used. The two untracked research files remain untouched.

## 2026-08-21 — Subplan 57 independent review and fix pass

- The single global `grok-delegate` invocation was started on the current branch, but the user
  interrupted the turn before Grok returned its reviewer report. No second invocation was made and
  no Grok finding was treated as accepted.
- Independent local review found two valuable Writer boundary fixes: a concurrent same-command
  insert now re-reads the authoritative batch and returns replay/conflict semantics, and an
  unreadable YAML authority is persisted as `needs_resolution` instead of remaining retryable
  without a visible state. Direct request validation and tool error classification were tightened.
- Added a regression test for the unreadable-authority state. The focused Writer suite passed `12`
  tests after the fix; final offline, Ruff, compile, CLI, and diff gates remain to be run.

## 2026-08-21 — Subplan 57 closeout and Subplan 58 activation

- Final S57 validation passed: `869 passed, 2 deselected` for the complete offline suite, `60 passed`
  for the affected Writer/configuration/Promotion set, Ruff format/check, compileall, CLI help, and
  `git diff --check`. No Live, Provider, network, or credential path was used.
- Committed the independent fix pass as `4ad7bbf` (`fix(preferences): harden writer recovery
  boundaries`). The two untracked research files remain untouched and excluded.
- S57 is complete and S58 is activated in the execution state. The next branch must start from the
  verified local `main`; do not implement S59 work early.
- Local `main` is ahead of configured `origin/main` by 46 commits after the fast-forward merge. No
  push was attempted because the user did not authorize the external remote mutation.

## 2026-08-22 — Subplan 58 implementation, review, and independent fix pass

- Implemented the frozen Preference Review context, one-call no-tool semantic Reviewer, deterministic
  proposal pipeline, Writer-backed Preference Inbox, dedicated `preferences inbox` CLI surface, and
  the explicit Preference v2 boundary for the legacy Learning Reviewer. The implementation checkpoint
  is `2cc6fd5`; the two untracked research files remain untouched and excluded.
- The single planned Grok review returned a report with no repository changes. It confirmed the main
  S58 contracts and identified three confirmed boundary issues: Writer-linked proposals could remain
  proposed after a finalized YAML batch, replay/suppression depended on the oldest 500 rows, and the
  production/API/CLI composition did not construct `ModelPreferenceReviewer`. It also noted test gaps
  and optional schema/CLI diagnostics improvements.
- Independently accepted and fixed those three issues: Writer finalization now updates all linked
  proposals in one SQLite transaction and replays finalized batches to recover the decision rows;
  suppression uses an indexed fingerprint existence query and same-job replay is bounded by job scope;
  bootstrap reuses the active Provider/model for `ModelPreferenceReviewer`, and `preferences inbox
  review` invokes the manual runner without printing raw context. Added the minimal global/workspace
  wire schema and preserved CLI error codes. Added runner paraphrase/zero-op, suppression, and
  finalize-window recovery tests. Rejected only the optional stage-history docstring cleanup.
- Final independent validation passed `889 passed, 2 deselected`; Ruff format/check, compileall,
  `morrow learning --help`, `morrow preferences inbox --help`, and `git diff --check` passed. No
  Provider, credential, Live, or real-network test was run, and no second Grok review was invoked.

## 2026-08-22 — Subplan 59.1 atomic terminal Review enqueue completed

- Added `PreferenceReviewJobEnqueuer` and connected it to the existing terminal Turn commit
  transaction. The hook uses the v13 `(workspace_id, turn_id, review_version)` replay key, writes
  one pending `PreferenceReviewJob` plus one current-user `PreferenceEvidence` row atomically,
  persists the complete bounded Active global/workspace snapshot, and performs no model, YAML, or
  notification work.
- Eligibility excludes slash/control input, LearningPolicy `off`, safety-rejected user content,
  and a successful `manage_preferences` execution in the same Turn. Existing job/evidence rows are
  returned on terminal replay; missing or changed replay evidence is treated as store repair.
- Added focused integration coverage for snapshot metadata, context reconstruction, replay
  idempotency, and rollback of terminal append plus enqueue. The offline suite passed `891 passed,
  2 deselected`; Ruff format/check, compileall, `morrow learning --help`, and `git diff --check`
  passed. No Provider, network, credential, or Live path was used.

## 2026-08-22 — Subplan 59.2 Review Worker lifecycle completed

- Added claimable v13 Preference Review job listing, row-version OCC lease claims, immutable job
  saves, and a process-local async `ReviewWorker` with explicit `start`, `wake`, `drain_once`, and
  `stop` boundaries. Reviewer calls remain outside SQLite transactions; proposal persistence and
  successful Job finalization are idempotent, while cancellation leaves a lease recoverable by
  expiry.
- Added worker tests for successful proposal completion, start/stop plus expired-lease recovery,
  and same-workspace serialization. S59.2 validation passed: `894 passed, 2 deselected`, Ruff
  format/check, compileall, and `git diff --check`. No Provider, network, credential, or Live path
  was used.

## 2026-08-22 — Subplan 59.3 retry and failure boundaries completed

- Preference Review now validates a finite bounded timeout with a 60-second default. Retryable
  provider, timeout, malformed-output, lease-loss, cancellation, and persistence failures use
  deterministic 5/15-second lease backoff and at most three attempts; terminal context/request
  budget, safety, and frozen-snapshot failures close as sanitized `failed` rows.
- A third retryable failure closes as `exhausted` with only the v13 allowlisted failure code;
  provider messages and tracebacks never enter the Job. Added regression coverage for retry,
  exhaustion, terminal context budget, and timeout validation. S59.3 validation passed `901
  passed, 2 deselected`, Ruff format/check, compileall, CLI help, and `git diff --check`.

## 2026-08-22 — Subplan 59.4/59.5 Review Worker implementation checkpoint

- Moved accepted-Task legacy Learning Review execution off the interactive foreground path. The
  accepted Task transaction still owns creation of the pending `LearningReview`; after commit the
  API only wakes the shared process-local `ReviewWorker`. The worker routes Preference jobs through
  the leased Preference runner and pending legacy reviews through `LearningReviewRunner` without
  pre-claiming the legacy row. Explicit `/learn review` and `/learn retry` remain foreground paths.
- Added sanitized Preference Review job query projections and bounded list/show/status/retry
  surfaces. Retry resets only failed/exhausted jobs with an allowlisted retryable failure code to a
  fresh pending attempt budget; the immutable frozen snapshot and idempotent proposal pipeline are
  reused, so retry does not duplicate proposals.
- Added a bounded one-shot `preferences inbox run-pending` command that reports post-run pending and
  running counts and explicitly emits `daemon: false`. The REPL drains process-local notices only
  for new proposals or exhausted retries; zero-operation Review completion is quiet. Reviewer
  context, raw output, provider details, and exceptions remain outside public AgentEvents and query
  projections.
- Corrected focused validation passed: `66 passed`; full offline validation passed: `910 passed,
  2 deselected`; Ruff format/check, compileall, `morrow learning --help`, and `git diff --check`
  passed. The S59 plan refers to `tests/test_turn_lifecycle.py` and `tests/test_cli.py`, neither of
  which exists in this repository; the actual corresponding test files were used.
- The required single Grok `/review` could not be invoked because no Grok tool or skill is exposed
  in the current thread (`ALL_TOOLS` contains no Grok capability). No review result is being claimed;
  the implementation checkpoint remains ready for review if that capability is restored.

## 2026-08-23 — Subplan 59 Grok review and independent fix pass

- The user exposed the global `grok-delegate` skill. The one planned read-only review completed with
  `grok-4.6` at `xhigh` and made no project changes. Independent adjudication confirmed missing
  post-Turn worker wakeup, drain termination after one non-completed result, and manual Preference
  Review bypassing worker claim/finalization. No second Grok review was invoked.
- Fixed ordinary post-dispatch wakeup, continued bounded drains after deferred/failed/exhausted
  outcomes, routed explicit job execution through the worker, and added injected lease-backoff wake
  scheduling. Also finalized expired third-attempt leases, skipped over-budget supplemental Review
  snapshots without rolling back foreground Turns, and exposed only the bounded Reviewer/job fields
  required by the plan's status contract.
- The independent Preference model override and active-model fallback already exist at composition;
  a persistent override would require an out-of-scope config schema change. The suggested worker
  module split is deferred as an optional structural refactor.
- Affected S59 validation passed `77` tests. Final offline validation passed `917 passed, 2
  deselected`; Ruff format/check, compileall, `morrow learning --help`, and `git diff --check`
  passed. No Provider, credential, Live, or real-network path ran. The two untracked research files
  remain untouched and excluded.

## 2026-08-23 — Subplan 59 closeout and Subplan 60 activation

- Committed the S59 independent fix pass as `f855c64` (`fix(preferences): close asynchronous review
  lifecycle gaps`) and fast-forward merged `feat/stage5-review-worker` into local `main`.
- Activated S60 from the verified `f855c64` baseline. Its scope is next-AgentRun Preference reload,
  exact same-Run freeze/recovery, deterministic bounded rendering, and typed observability.
- Local `main` remains ahead of configured `origin/main`; no push was attempted because the user did
  not authorize an external remote mutation. The two untracked research files remain untouched.

## 2026-08-23 — Subplan 60 implementation checkpoint

- Reloaded global/workspace Preference YAML before each new AgentRun transaction and froze a
  deterministic 64-entry/8-KiB generic projection with digest, omission count, source scopes, and
  sanitized refresh state. Invalid YAML now contributes an observable degraded empty layer rather
  than stale Session-start values or a blocked ordinary chat.
- Recovery and same-Run context use only the exact frozen projection. Prompt rendering places typed
  `[scope:id]` rules in a dedicated lower-authority block after safety/capability boundaries;
  disabled/deleted entries are absent and cross-scope duplicate/budget ordering is deterministic.
- Added typed `preferences status` fields that keep live YAML, injected Preferences, and Project
  Knowledge MemorySelection separate. Doctor loads YAML without creating state paths, reports
  lifecycle counts/revisions, and detects a tampered frozen projection digest.
- Focused S60 validation passed `112` tests before the final digest-tamper regression. Full offline
  validation passed `924 passed, 2 deselected`; Ruff format/check, compileall, `morrow --help`, and
  `git diff --check` passed. No Live, Provider, credential, or real-network path ran; the two
  untracked research documents remain untouched.

## 2026-08-23 — Subplan 60 Grok review and independent fix pass

- The single planned `$grok-delegate` review ran read-only with `grok-4.6` at `xhigh` and changed no
  project files. Its report confirmed the AgentRun admission reload, frozen recovery, deterministic
  rendering, degraded YAML, authority, and cross-store race boundaries.
- Independent adjudication confirmed one correctness bug: Memory doctor called the combined
  Preference+Memory run projection, so a tampered Preference digest could also be reported as
  `memory_agent_run_projection`. Memory doctor now validates only its frozen MemorySelection link;
  the existing selection checks continue to own Memory content integrity.
- Added a mixed valid-Memory/tampered-Preference doctor regression and strengthened tests for the
  next Turn on the restored Session object, exact non-equal cross-scope render ordering, and an
  adversarial Preference that cannot add tools to `ContextPack`. Clarified that degraded reads use
  empty pre-transaction Preference layers.
- Final focused S60 validation passed `98` tests. Full offline validation passed `926 passed, 2
  deselected`; Ruff format/check, compileall, `morrow --help`, and `git diff --check` passed. No
  Live, Provider, credential, or real-network path ran. The two untracked research documents remain
  untouched and excluded.

## 2026-08-23 — Subplan 60 closeout and Subplan 61 activation

- Committed the S60 independent fix pass as `1a8e38b` (`fix(preferences): separate doctor projection
  diagnostics`) and fast-forward merged `fix/stage5-preference-context-refresh` into local `main`.
  The merged branch had no commits absent from `main` and was deleted cleanly.
- Activated S61 from the verified `1a8e38b` baseline on
  `refactor/stage5-preference-v2-closeout`. Its scope is legacy active-path retirement, v13/YAML
  doctor and backup coverage, the versioned semantic evaluation corpus, product documentation, and
  integrated quality gates.
- Local `main` remains ahead of `origin/main`; no push was attempted because no external remote
  mutation was authorized. The two untracked research files remain untouched and excluded.

## 2026-08-23 — Subplan 61 implementation checkpoint

- Retired fixed-field Preferences from the public configuration tool and `/config edit`, isolated
  historical command validation, removed the fixed effective Preference from newly created
  AgentRun snapshots, and left only explicitly named migration/history compatibility paths.
- Extended doctor and backup verification across Preference v13 links, snapshot integrity,
  lease/retry lifecycle, proposal/write-batch cardinality, and the separate YAML/credential restore
  boundary. Added isolated migration and restore acceptance.
- Added the versioned `preference-v2-natural-language-v1` corpus and scripted no-write evaluator
  with frozen scoring arithmetic. The existing v12 corpus is preserved under an explicit legacy
  resource name; no real Provider or credential path was executed.
- Reconciled architecture, roadmap, README, Stage 5 acceptance/hold documentation, and CLI help,
  while keeping post-implementation simulated-user/live acceptance pending.
- Focused S61 acceptance passed `37` tests and full offline validation passed `932 passed, 2
  deselected`. Ruff format/check, compileall, `morrow --help`, Preferences/Learning/Memory help, and
  `git diff --check` passed. The two untracked research documents remain untouched and excluded.

## 2026-08-23 — Subplan 61 Grok review and independent fix pass

- The one planned `$grok-delegate` review completed read-only with `grok-4.6` at `xhigh`; it made no
  project changes. It confirmed a production compatibility break in historical AgentRun snapshots,
  a second incompatible backup parse, missing doctor integrity coverage, an absent v2 live harness,
  and an absolute target threshold that allowed `6/8` to pass. No second S61 review was run.
- Added one shared read-only AgentRun snapshot decoder for classic fixed projections, S60 frozen
  projections, and current snapshots. Journal restore and Memory backup verification both use it;
  stored JSON is never rewritten. Added production-shape and backup regressions.
- Doctor now runs the complete v13 Preference verifier in diagnose mode and reports only bounded
  reason codes. Added snapshot, lease, and exact Evidence-cardinality tamper coverage.
- Replaced the old opt-in Learning smoke with the planned Preference v2 corpus scorer and ten actual
  frozen-ContextBuilder adherence probes. Reports contain aggregate counters only. The live test was
  not executed and no Provider, credential, or network path was used.
- Changed target passing to accuracy `>= 6/7`, retired public `/config reset` alongside `/config
  edit`, and corrected stale worker/report documentation. Focused validation passed `33 passed, 1
  deselected`; final offline validation passed `938 passed, 2 deselected`. Ruff format/check,
  compileall, all required CLI help, and `git diff --check` passed.

## 2026-08-23 — Subplan 61 closeout and integrated review activation

- Committed the S61 review fix as `8833910` (`fix(preferences): close s61 review findings`) and
  fast-forward merged `refactor/stage5-preference-v2-closeout` into local `main`. The merged branch
  had no commits absent from `main` and was deleted cleanly.
- Activated `refactor/stage5-preference-v2-integrated-closeout` from the verified `8833910`
  baseline for the one separate S56–S61 integrated Grok review, one independent fix pass, and final
  complete offline gate.
- Local `main` is ahead of `origin/main`; no push was attempted. The two untracked research files
  remain untouched and excluded.

## 2026-08-23 — S56–S61 integrated Grok review and independent fix pass

- The one separate integrated `$grok-delegate` review completed read-only with `grok-4.6` at
  `xhigh` over the complete S56–S61 implementation. It changed no files and found two cross-subplan
  bugs plus two valuable closeout suggestions. No second integrated review was run.
- Excluded deleted tombstones from Reviewer snapshots while retaining active/disabled entries, so
  accumulated logical removes cannot permanently exhaust the 256-entry/192-KiB enqueue budget.
- Isolated excerpt/Evidence validation from the terminal transaction outcome: invalid supplemental
  Review evidence now skips Review while preserving the completed foreground Turn. Added a control-
  character regression proving the Assistant completion remains durable.
- Refused legacy fixed-field whole-document writes once workspace Preferences are v3 generic,
  preventing ID churn and loss of generic/disabled/deleted entries. Corrected the remaining stale
  acceptance matrix row to the SQLite queue + in-process worker/no-daemon contract.
- Focused integrated validation passed `70` tests. Final offline validation passed `941 passed, 2
  deselected`; Ruff format/check, compileall, all required CLI help, and `git diff --check` passed.
  No Live, Provider, credential, or network path ran.

## 2026-08-23 — Preference v2 implementation closeout

- Committed the integrated review fix as `720df62` (`fix(preferences): close integrated v2 review
  findings`) and fast-forward merged the integrated branch into local `main`. The branch had no
  commits absent from `main` and was deleted cleanly.
- Preference v2 implementation is complete through S61 and the required separate integrated review.
  Post-implementation simulated-user and real-Provider acceptance remain explicit pending hold
  points; neither was run during this closeout.
- Local `main` remains ahead of `origin/main`; no push was attempted. The two untracked research
  files remain untouched and excluded.

## 2026-08-23 — Preference v2 post-implementation acceptance activation

- The user explicitly authorized continuing beyond the implementation hold point. Activated
  `test/stage5-preference-v2-acceptance` from verified local `main` at `4e48b1c`.
- The acceptance order is locked: isolated simulated-user protocol first, then the opt-in live
  corpus only if its explicit environment credential is present. Credential values will not be
  read, printed, persisted, or copied.
- The two untracked research files remain untouched and excluded.

## 2026-08-23 — Preference v2 simulated-user acceptance

- Ran the frozen simulated-user groups across Preference CLI/Inbox/Writer/Reviewer/worker,
  migration/restart/recovery, next-AgentRun context, MemorySelection isolation, doctor, backup, and
  crash/OCC boundaries. Result: `158 passed in 5.22s`.
- All executions used pytest-isolated state roots. No real Provider, credential, network, user YAML,
  or user Operational Store was accessed.
- Checked only whether `MORROW_OPENCODE_GO_API_KEY` was non-empty; it was absent. The value was not
  read or printed, and the live corpus was not run. Simulated-user acceptance is complete while
  live acceptance remains pending.
- Re-ran the complete non-live suite after the simulated protocol: `941 passed, 2 deselected in
  22.52s`. `ruff format --check`, `ruff check`, `compileall`, and `git diff --check` also passed.
- Committed the bounded evidence as `9e4403c`, fast-forwarded it with activation commit `2fd568a`
  into local `main`, and deleted the clean acceptance branch. No push was attempted; live
  acceptance remains the only open Stage 5 acceptance item.

## 2026-08-23 — Preference v2 live corpus and remediation activation

- The user persisted the compatible OpenCode Go credential in macOS Keychain. The product Provider
  connection test succeeded without exposing the credential.
- The first corpus attempt exposed a stale `Session(persisted=...)` test fixture before scoring.
  Removed the obsolete argument, added a non-live adherence-probe regression, and passed the full
  offline gate with `942 passed, 2 deselected`; committed and merged as `3493c88`.
- The repaired `deepseek-v4-flash` corpus completed in an isolated report root: positive operations
  `8/12`, proposal precision `10/16`, targets `7/8`, safety-negative Active writes `0`, adherence
  `10/10`, attempts `32`, latency `173908 ms`. No raw output, text, statements, reasoning, or
  credential was persisted.
- The frozen live thresholds did not pass. Activated Subplan 62 on
  `fix/stage5-live-reviewer-quality`; Stage 5 acceptance remains open.
- Static diagnosis found the v2 semantic instruction only asked whether a durable Preference change
  existed; it did not define operation cardinality, add/replace/remove selection, global scope,
  target ambiguity, or negative categories. Added an explicit bounded v3 semantic contract while
  retaining one no-tool call and the v2 output schema. Added case-ID/operation-signature-only live
  diagnostics. Focused tests passed `10 passed, 1 deselected`; the complete non-live gate passed
  `942 passed, 2 deselected`, with Ruff, format, compileall, and diff checks green.
- The one Reviewer v3 live replay produced positive operations `11/12`, precision `13/16`, targets
  `8/8`, safety-negative Active writes `0`, adherence `10/10`, attempts `32`, and `170882 ms` total
  latency. Sanitized signatures isolated global cancellation-as-replace plus extra adds for explicit
  repository-content denial and hidden-control input. No immediate second replay was run. Reviewer
  v4 now tightens these three general semantic boundaries before the planned review and final replay.
- Reviewer v4 focused tests passed `10 passed, 1 deselected`; the complete non-live gate passed
  `942 passed, 2 deselected`. Ruff format/check, compileall, and `git diff --check` passed. The branch
  is ready for its one planned Grok review before any final live replay.
- Ran the one planned Subplan 62 `$grok-delegate` review read-only with `grok-4.6` at `xhigh` over
  `main...HEAD`. Grok found no correctness or leakage bug and judged v4 justified/non-overfit. It
  suggested unifying the live-report contract and adding version/model IDs, adding explicit report
  sanitization and prompt anti-overfit tests, and containing stable per-case Reviewer errors so a
  scored report still exists. Independently accepted all four as valuable; no second review will
  run. Grok changed no project file and did not access Keychain, Provider, credentials, or live tests.
- Independently implemented the four accepted suggestions: the harness now emits one documented
  failed-case-only contract with prompt/schema/provider/model IDs; non-live tests enforce report
  allowlists and secret/source/statement exclusion; prompt tests prohibit complete corpus text,
  distinctive case IDs, and gold target IDs; stable per-case `PreferenceReviewerError` becomes a
  sanitized miss rather than aborting the report. Focused validation passed `12 passed, 1
  deselected`; the full gate passed `944 passed, 2 deselected`, with Ruff, format, compileall, and
  diff checks green.
- Ran the single reviewed Reviewer v4 final live replay with the Keychain credential injected only
  into the subprocess. Result: `1 passed, 3 deselected in 139.21s`; positive operations `12/12`,
  precision `14/14`, targets `8/8`, safety-negative Active writes `0`, adherence `10/10`, attempts
  `32`, latency `139066 ms`, and no failed cases. The sanitized report contains bounded version/model
  IDs and aggregates only. Stage 5 acceptance targets are satisfied pending final gates and merge.
- Final closeout gates passed: `944 passed, 2 deselected in 20.90s`; Ruff format/check, compileall,
  and `git diff --check` also passed.
- Committed the final live evidence as `0f0058c`, fast-forwarded Subplan 62 into local `main`, and
  deleted the clean branch. No push was attempted. Stage 5 is complete and accepted; Stage 6 remains
  unopened.

## 2026-08-24 — Stage 6 final design and implementation-plan activation

- The user explicitly opened Stage 6 planning and supplied a review-agent assessment of the prior
 方案. Reconciled it against local `main` at `479270b`, current code and the Stage 6 roadmap.
- Wrote the final design at `docs/reviews/stage-6-skills-and-extensions-final-proposal.md` and updated
  the roadmap contracts for SkillBinding, local Trust authority, dedicated Skill script execution,
  compound MCP launch/tool policy, no MCP auto retry, complete results and Backup v2.
- Kept `docs/ARCHITECTURE.md` unchanged because the future modules are not implemented. The final
  closeout subplan will synchronize it from actual code.
- Activated the Stage 6 master plan with sequential Subplans 63–75. Subplan 63 is ready but not in
  progress. It may evaluate dependencies without modifying the lock; any exact dependency addition
  still requires explicit user approval before Subplan 72.
- No production code, dependency, branch, Provider, MCP, credential, network or user state changed.

## 2026-08-24 — Subplan 63 dependency and contract spike completed

- Reconfirmed all Stage 6 seams with exact source refs and recorded them in the ADR:
  AgentRun assembly/replay (`turn_lifecycle.py:240-349`, `memory_run_projection.py`), ToolExecutor/
  `TOOL_NAME_PATTERN` (`models.py:26`), PermissionSnapshot/CapabilityPolicy verdict order,
  ProcessExecutionService minimal env, AdapterRegistry/ModelProvider protocol, v13 migration
  registry, backup v1 manifest and doctor pattern.
- Evaluated official MCP Python SDK `mcp 2.0.0` in `/tmp/mcp-eval-venv` (Python 3.13, matching the
  project interpreter): MIT, requires-python >=3.10, wheel 342 016 B, direct deps include
  `jsonschema>=4.20` and `mcp-types==2.0.0`; per-call `read_timeout_seconds` raises
  `MCPError("Request 'tools/call' timed out")`; no public per-request cancel API; OTel dependency
  is default-inert; no auto-retry.
- Selected `jsonschema` with one controlled dialect (draft 2020-12; absent `$schema` defaults to
  2020-12; all others fail-closed) after measuring that `validator_for` silently falls back to the
  latest draft on unknown `$schema` — that fallback is rejected for Morrow.
- Prototyped the narrow stdio connect/list/call/close path against a pure-stdlib Fake server in
  `tests/spikes/` (offline, ~3.4 s): handshake, typed results, is_error results, timeout evidence,
  session-usable-after-timeout, and a call log proving exactly one handler entry for the timed-out
  call. Runs under `uv run --with mcp`; skips cleanly in the default dev env.
- Measured locked budgets: AgentRunSnapshot 3 192 B base → 4 822 B with 8 Skill refs + 1 MCP ref
  (64 KiB cap); Skill context entry 250 B (<= 2 KiB each, <= 16 KiB per run); MCP tool snapshot
  1/16/64 tools = 196/3 786/21 474 B (<= 4 KiB per tool, <= 64 KiB per server); result chunk
  <= 128 KiB.
- Locked Skill package canonicalization (skv_ envelope, canonical tree digest over sorted
  regular-file entries, reject symlink/hardlink/device/socket/FIFO and escapes, Unicode/case
  collision rejection, TOCTOU-safe reads) and confirmed v14/v15/v16 migration ownership plus
  backup bundle v2 manifest-versioning seams.
- Published `docs/research/stage6-mcp-dependency-spike.md`: recommendation `mcp >=2.0.0,<3` and
  `jsonschema >=4.20,<5`, rejected alternatives (hand-rolled client, HTTP transport, internal JSON
  Schema validator, pydantic interpretation), risks, and the explicit user-approval gate before
  Subplan 72.
- Validation: `ruff format --check .`, `ruff check .`, `compileall`, `git diff --check` passed;
  full offline suite `947 passed, 1 skipped (mcp spike), 2 deselected in 22.94s`;
  `pyproject.toml`/`uv.lock` byte-identical. Committed, fast-forwarded Subplan 63 into local `main`,
  deleted the clean branch; no push (remote publication not in scope; `main` is one commit ahead of
  origin). Subplan 64 is ready to activate.

## 2026-08-24 — Subplan 64 per-AgentRun runtime preparation completed

- Added core/agent_runs.py contracts (ProviderCapabilities/ModelCapabilities/ExactModelCapabilities,
  ProviderRuntimeSnapshot with sanitized endpoint and CredentialRef name/version only,
  PreparedAgentRunSpec) and moved RunPolicy/ProviderToolSupport into core/models.py with re-exports.
- Added application/agent_runs/preparation.py: prepare_new() reads current global config per run;
  rehydrate() rebuilds strictly from stored AgentRun evidence; a frozen unresolvable CredentialRef
  raises ProviderUnavailableError with no fallback; pre-Stage-6 snapshots use the boot legacy runtime;
  PreparedAgentRunRuntime.close() is bounded and idempotent.
- TurnSubmissionCoordinator.probe() is read-only (no IDs/YAML/writes); submit_user retains the
  in-txn receipt recheck so concurrent duplicates persist exactly one AgentRun and the loser's
  unused runtime is closed by run_task.
- AgentRunSnapshot gained optional provider_runtime + run_policy evidence; redaction splits a strict
  dedicated scan for the provider subtree away from the generic "credential" needle scan.
- AgentLoop.run_task() constructs runner/tool-cycle per invocation from a prepared run; run_turn()
  stays a thin delegate; orchestrator probes first (admission errors surface as ordered error
  events), prepares only for new runs, and rehydrates on recovery resume.
- One regression found and fixed: orchestrator-level probe must not raise health errors as
  exceptions — test_stage4_task_outcome expects them as error events from the loop.
- Validation: 14 new preparation tests; full offline suite `961 passed, 1 skipped (mcp spike),
  2 deselected in 23.73s`; ruff format/check, compileall, git diff --check passed; pyproject.toml
  and uv.lock unchanged; snapshot budget re-measured at 4 367 B base / 5 997 B with refs.
- Committed and fast-forwarded Subplan 64 into local `main`; branch deleted; no push (remote
  publication not in scope). Subplan 65 is ready to activate.

## 2026-08-24 — Subplan 65 Skill package and catalog foundation completed

- Added core/skills/ contracts (identity safe rules, manifest contracts, TrustLevel +
  effective_trust from local provenance, SkillDefinition/Version/Binding/Selection views).
- Added adapters/skills/: strict manifest parser (frontmatter + morrow.yaml; unknown keys rejected;
  requested Trust/permissions non-authoritative), canonical tree builder with one-safe-open
  fstat+bytes hashing, collision/reserved/oversize/non-UTF-8 rejection, Morrow-written
  managed-version.json envelopes with digest verification and drift detection, and bounded root
  discovery with scope isolation.
- Added application/skills/catalog.py: deterministic truthful projection; same id/digest folds,
  same id/different digest -> identity_conflict, different id/same normalized name ->
  name_conflict; scope/source never overrides; requested vs effective Trust exposed.
- Operational Store v14: skill_definitions, skill_versions, skill_catalog_operations and the
  reserved agent_run_skill_selections/agent_run_skill_contexts tables (scope + scope_id composite),
  SqliteSkillJournal, thin aggregate delegation; SUPPORTED_SCHEMA_VERSION 14.
- Bumped legacy expectations in migration/version tests (hardcoded 13 -> 14) and the future-schema
  tamper test (15). Full offline suite `983 passed, 1 skipped, 2 deselected in 22.62s`; ruff,
  compileall, git diff --check green; dependency files unchanged.
- Committed as c3038e0 on codex/feat/stage6-skill-catalog and fast-forwarded into local `main`;
  branch deleted; no push (remote publication not in scope; main is 3 commits ahead of origin).
  Subplan 66 is ready to activate.

## 2026-08-24 — Subplan 65 review repairs

- Re-read the complete 13-item review for Subplans 63–65 and verified every finding against the
  implementation. Repaired v14 global scope identity with a non-null SQLite sentinel and composite
  FK enforcement, made Skill versions insert-only, and restored effective Trust in definition rows.
- Hardened Skill package discovery and validation: lstat directory roots, reject hardlinks, consume
  manifest bytes captured by the canonical tree scan, validate malformed envelopes/manifests within
  bounded failures, recompute Trust from local source provenance, and require version directory IDs
  to match envelopes. Removed the unused identity helper.
- Routed preparation and rehydration failures through AgentLoop's ordered error lifecycle, made the
  production ToolExecutor factory follow the prepared run policy, and made Model capability
  overrides narrowing-only. Updated stale v14 scope documentation and added regression coverage.
- Focused validation passed 47 tests; final full offline validation passed 992 passed, 3 skipped, 2
  deselected; Ruff format/check, compileall and git diff --check passed. Branch creation and commit
  were unavailable because the workspace exposes .git as read-only.

## 2026-08-24 — Subplan 66 activated

- The user explicitly resumed Stage 6 implementation. Activated Subplan 66 for Skill Lifecycle and
  Binding Control after confirming Subplan 65 and its review repairs are locally complete.
- Creating `codex/feat/stage6-skill-lifecycle` failed because the workspace cannot create `.git` refs;
  implementation continues in the existing `main` worktree without discarding the review repairs.

## 2026-08-24 — Subplan 66 Skill lifecycle and Binding control completed

- Added independent Extension YAML authorities, immutable managed package installation, Binding
  lifecycle commands, bounded status queries, CLI projections and cross-store recovery records.
- Kept installation disabled by default, preserved source/provenance boundaries, rejected
  ambiguous/conflicted/unavailable dependencies, protected referenced versions, and did not add
  Agent-facing lifecycle tools or AgentRun selection/context injection.
- Added recovery coverage for package publication, YAML publication with revision advancement and
  package-removal boundaries; durable global/workspace command replay is covered by the journal
  and bounded application receipts.
- Validation: focused suite `41 passed`; full non-live suite `1003 passed, 3 skipped, 2 deselected`;
  Ruff format/check, compileall, root/Skill CLI help and `git diff --check` passed.
- Could not create or commit a topic branch because `.git` refs remain read-only; all existing
  review-repair and Subplan 66 changes remain in the shared working tree.

## 2026-08-24 — Subplan 67 activated

- The user authorized continuation of Stage 6 after Subplan 66 completion. Activated Subplan 67 for
  bounded Skill selection, per-AgentRun context/resource evidence and historical rehydration.
- Scope is limited to explicit/workspace-default/conservative description selection, frozen package
  reads, v14 selection/context persistence and low-authority context rendering; Draft, scripts, MCP,
  learned routing and unrestricted injection remain excluded.
- Topic branch creation remains unavailable because the workspace `.git` refs are read-only; work
  continues in the shared `main` worktree while preserving the existing dirty changes.

## 2026-08-24 — Subplan 67 Skill selection, context and resources completed

- Added deterministic explicit/default/conservative description selection with scope, source, pin,
  availability, dependency, platform, capability and budget checks; omitted candidates retain
  bounded reasons in the selection plan.
- Persisted exact per-AgentRun selection and context evidence in the admission transaction while
  keeping the main snapshot reference-only; historical rehydration reads journal evidence only.
- Added low-authority bounded Skill context rendering and frozen relative resource reads with
  traversal, symlink, MIME, size, Artifact and package-drift protections.
- Validation: full non-live suite `1011 passed, 3 skipped, 2 deselected`; Ruff format/check,
  compileall, CLI help and `git diff --check` passed. No live Provider, MCP, network or credential
  path was exercised.

## 2026-08-24 — Subplan 68 activated

- Activated Generated Skill Drafts and Usage after Subplan 67 passed its exit gates. Scope is limited
  to accepted Stage 5 SkillCandidates, bounded Draft/validation/Usage evidence and v15 storage;
  Draft approval must not enable or repin a Binding.
- Topic branch creation remains unavailable because the workspace `.git` refs are read-only; work
  continues in the shared `main` worktree while preserving all prior changes.

## 2026-08-24 — Subplan 68 Generated Skill Drafts and Usage completed

- Added bounded Draft, validation-report and observational Usage contracts with digest-checked
  SQLite mappings and the v15 `skill_drafts`, `skill_draft_validations` and `skill_usage` tables.
- Added accepted same-workspace SkillCandidate→Draft generation, replay-safe package roots,
  deterministic validation, revision/diff/edit/revalidate/reject transitions and immutable lifecycle
  publication. Draft acceptance records approval evidence without enabling or repinning a Binding.
- Added bounded Usage recording keyed to exact AgentRun/Task/selection/version facts and descriptive
  comparisons that explicitly return insufficient-data results instead of routing decisions.
- Fixed migration expectations for schema 15 and version-reference scanning for
  `skill_drafts.accepted_version_id`; added CLI Draft/Usage commands and focused regression tests.
- Validation passed: focused suite `6 passed`; full non-live suite `1017 passed, 3 skipped,
  2 deselected`; Ruff format/check, compileall, CLI help and `git diff --check`.
- The topic branch remains unavailable because `.git` refs are read-only. The verified local
  checkpoint was committed on `main` as `8afbcd2`; remote publication is not in scope.

## 2026-08-24 — Subplan 69 activated

- Activated Skill Script execution after the verified Subplan 68 checkpoint `8afbcd2` and state
  record `10e6826`.
- Scope is limited to bounded script requests, frozen managed-package verification, dedicated
  constrained execution, existing permission/approval/audit/recovery seams and Artifact outputs;
  no general shell, ambient host access or capability widening is allowed.

## 2026-08-24 — Subplan 69 Skill Script execution completed

- Added strict `SkillScriptRequest`/`SkillScriptResult` contracts, immutable `FrozenSkillPackage`
  capture, and `SkillScriptExecutionService` over the existing process adapter seam. The service
  revalidates the managed envelope/tree/script digest immediately before launch and never falls
  back to the workspace-rooted `ProcessExecutionService` or an unconfined production adapter.
- Added isolated temporary package/input/output roots, fixed safe environment construction, root /
  input / output symlink and mutation checks, declared output validation, bounded redacted stdout /
  stderr and session-scoped Artifact publication. Manifest permission declarations become risk
  flags and cannot grant capability; missing native sandbox is rejected.
- Registered `run_skill_script` in the standard ToolExecutor path and added the durable recovery
  declaration `PROCESS_EFFECT_NON_DURABLE / OUTCOME_UNKNOWN` with frozen-confinement evidence.
- Focused coverage and existing Process/Permission/Recovery regressions passed; final non-live gate
  passed `1023 passed, 3 skipped, 2 deselected`. Ruff format/check, compileall and `git diff --check`
  passed. Committed as `73f99db`; no remote push.

## 2026-08-24 — Subplan 70 activated

- The user requested continuation of the current Stage 6 plan after Subplan 69 completion. Activated
  Provider/Model Control Plane from the clean local `main` checkpoint `9b352b0`.
- Scope is limited to exact Adapter/Model capability snapshots, versioned Provider/Model control,
  sanitized test/sync projections, second-Adapter isolation and per-new-AgentRun resolution. No
  AgentLoop/Session/Task branching, silent fallback, dependency addition or live Provider calls.

## 2026-08-24 — Subplan 70 Provider/Model control plane completed

- Added typed Adapter defaults and narrowing-only exact Model capability overrides covering streaming,
  tool protocol, multi-tool calls, structured output, request/context limits, input modalities and
  cost metadata source/time. Exact capabilities now flow from ProviderModelConfig into new-run
  frozen ProviderRuntimeSnapshot evidence; rehydration remains evidence-only.
- Added explicit registry discovery, OpenAI-compatible model discovery, Provider/Model control
  services with CredentialRef-only YAML, URL/Adapter validation, revision OCC, backup-preserving
  writes, active-model removal protection and sanitized failure projections.
- Added plan-compatible provider/model CLI forms and a Fake second Adapter contract. No AgentLoop,
  Session, Task or fallback branches changed.
- Validation passed: focused provider/preparation/CLI suite `77 passed, 1 skipped`; full non-live
  suite `1030 passed, 3 skipped, 2 deselected`; Ruff format/check, compileall, CLI help and
  `git diff --check` passed. Implementation committed as `89e70e9`.

## 2026-08-24 — Subplan 71 activated

- Fast-forwarded the verified Subplan70 implementation and state commits into local `main`, removed
  the clean topic branch, and created `feat/stage6-dynamic-tools` from that checkpoint.
- Scope is limited to one bounded tool-argument validator interface, selected JSON Schema dialect
  validation, declaration-owned recovery metadata and local tool migration. No MCP dependency,
  MCP-specific branch, live server or policy-default change is authorized.

## 2026-08-24 — Subplan 71 Dynamic Tool Contracts completed

- Added one bounded `ToolArgumentsValidator` seam with a Pydantic compatibility adapter and a
  dependency-free explicit Draft 2020-12 JSON Schema subset. Schema and argument budgets cover
  bytes, depth, properties, arrays, strings and numbers; unsupported or remote references fail
  closed without exposing raw values.
- Changed `RegisteredTool` to use one validator authority and one declaration-owned recovery
  contract. Local, configuration, preference, Git, Skill-script and fixture factories pass
  explicit declarations; `arguments_model` remains only as a constructor adapter for compatibility.
- Prepared intents persist the declaration before handler entry and recovery prefers the frozen
  evidence over the current registry or static tool-name inventory. Dynamic unknown-at-start tests
  cover validation, approval, execution and recovery stability without name-prefix branches.
- Focused compatibility/dynamic/recovery suites passed `76`; full non-live validation passed
  `1036 passed, 3 skipped, 2 deselected`. Ruff format/check, compileall and `git diff --check`
  passed. No dependency files changed; exact MCP/JSON Schema dependency approval remains gated
  before Subplan 72.

## 2026-08-24 — Subplan 72 activated

- The user explicitly approved the exact dependency change `mcp >= 2.0.0, < 3` and
  `jsonschema >= 4.20, < 5`, with the Subplan 63 ADR as rationale and compatibility evidence.
- Created `feat/stage6-mcp-catalog` from verified `main@11f7f6d`. Scope is limited to MCP stdio
  Server definitions, Extension YAML control, catalog normalization/namespacing and v16 evidence;
  MCP tools remain unavailable to AgentRun execution until Subplan 73.

## 2026-08-24 — Subplan 72 dependencies locked

- Added the approved direct ranges `mcp>=2.0.0,<3` and `jsonschema>=4.20,<5`; `uv lock` resolved
  the official SDK at `mcp==2.0.0` and `jsonschema==4.26.0` plus its recorded transitive packages.
- `uv sync` completed successfully. The offline Fake stdio Spike passed `4 tests`; no networked or
  credentialed MCP path was run.

## 2026-08-24 — Subplan 72 MCP Control Plane and Catalog completed

- Added strict stdio MCP Server definitions and YAML-authoritative add/show/list/enable/disable/
  remove operations with revision/digest OCC. Servers are disabled on add; enable requires a
  refreshed Catalog, explicit local allowlist and local risk mappings. Remote annotations remain
  observational evidence only.
- Added the official SDK handshake/list/close adapter with cancellation-aware timeouts, bounded
  stderr counting and no remote tool execution. Catalog refresh normalizes Draft 2020-12 schemas,
  isolates invalid tools, degrades only unusable Server catalogs and assigns deterministic bounded
  `mcp__<server>__<tool>` names.
- Added Operational Store v16 Server/Catalog/run snapshot/Artifact-link persistence, safe query
  projections and MCP CLI commands. MCP is still not registered with AgentRun execution; that is
  reserved for Subplan 73.
- Validation passed: focused MCP/SDK suite `8 passed`; affected migration/binding suite `97 passed`;
  full non-live suite `1044 passed, 2 skipped, 2 deselected`; CLI help, Ruff format/check,
  compileall and `git diff --check`. No live/networked MCP path or remote push was run.
- Committed as `c1a7132`, fast-forwarded into local `main`, and retained no MCP-specific branch in
  ToolExecutor or recovery. Remote publication remains out of scope.

## 2026-08-24 — Subplan 73 activated

- The user requested continuation of the Stage 6 plan after the verified Subplan 72 merge. Created
  `codex/feat/stage6-mcp-runtime` from local `main@97d4a82`.
- Scope is limited to frozen MCP launch/tool evidence, the existing ToolExecutor/permission/
  approval/recovery seams, lazy per-AgentRun lifecycle, bounded result normalization and no-retry
  behavior. Backup v2, doctor closeout, new dependencies and live/network/credential tests remain
  out of scope.

## 2026-08-24 — Subplan 73 MCP Runtime and Security Adapter completed locally

- Added run-bound launch/tool snapshots with config, executable, Catalog, toolset, allowlist and
  recovery-declaration evidence; AgentRun creation persists the rows atomically and rehydration
  reads the historical Catalog revision while rejecting current config/executable/schema drift.
- Added exact local-interface MCP review evidence, deny-first launch/tool policy composition and
  ordinary ToolExecutor registration with JSON Schema validation, approval, budget and generic
  outcome-unknown recovery semantics. No MCP-specific recovery or permission engine was added.
- Added lazy per-AgentRun stdio pools with reuse, per-Server degradation, cancellation/timeout/
  crash isolation, no retry, allowlist enforcement, bounded text/structured/link normalization and
  ArtifactStore import references for binary/embedded content. Doctor reports bounded MCP state.
- Validation passed: focused MCP/runtime/recovery suite `67 passed`; full offline suite `1051 passed,
  2 skipped, 2 deselected`; Ruff format/check, compileall and `git diff --check` passed. No live,
  networked, credentialed MCP path or remote push was run.
- Committed as `514a9a6`, fast-forwarded into local `main`, verified the topic branch had no commits
  absent from `main`, and deleted the clean topic branch. Remote publication remains out of scope.

## 2026-08-24 — Subplan 74 Backup v2 and Doctor activated

- Created `codex/feat/stage6-backup-doctor` from verified local `main@514a9a6`.
- Scope is limited to preserving Backup v1 compatibility, adding isolated Backup v2 for referenced
  Stage 6 state, and extending bounded Doctor integrity checks. Integrated acceptance closeout,
  live/network/credential paths and Stage 7 behavior remain out of scope.

## 2026-08-24 — Subplan 74 Backup v2 and Doctor completed locally

- Added explicit v1/v2 backup contracts and preserved v1 verification while adding v2 online SQLite
  backup, current sanitized YAML, Artifact bytes, referenced managed Skill packages and bounded
  references. Verification rejects path traversal, symlinks, drift, unsupported schemas, digest or
  cross-store mismatches; restore publishes only after verification into an isolated atomic target.
- Added bounded Skill and MCP backup verifiers plus schema-gated Doctor checks for catalog, Binding,
  selection/context, Draft/Usage and managed package integrity. Credential values, raw results and
  diagnostics remain excluded.
- Validation passed: Stage 6 backup suite `5 passed`; affected backup/Doctor/MCP/Skill suite `32
  passed`; full offline suite `1056 passed, 2 skipped, 2 deselected`; Ruff format/check, compileall,
  required CLI help and `git diff --check` passed. No live, networked or credentialed path was run.

## 2026-08-24 — Subplan 75 Integrated Acceptance and Closeout activated

- Fast-forwarded Subplan 74 commit `0dc4e54` into local `main`, verified the topic had no commits
  absent from `main`, retired the clean `codex/feat/stage6-backup-doctor` branch, and created
  `codex/feat/stage6-closeout` from `main@0dc4e54`.
- Scope is limited to isolated offline acceptance fixtures, confirmed integration repairs, exact final
  gates and documentation reconciliation. No live/network/credential path, new dependency, policy
  default, public event lifecycle or Stage 7 behavior is in scope.

## 2026-08-24 — Subplan 75 Integrated Acceptance and Closeout completed locally

- Added inert Stage 6 fixtures for a handwritten Skill, generated candidate and second Provider
  shape, plus isolated integrated tests for workspace Skill selection/resource/Usage/Doctor/Backup
  and Candidate→Draft→edit→accept→explicit enable. Existing offline Fake stdio MCP and script
  suites cover the remaining runtime seams without a second authority.
- Acceptance exposed one real defect: accepted generated Draft packages wrote `effective_trust=generated`
  but omitted the controlled approval reference from `managed-version.json`; discovery therefore
  recomputed `unknown` and could not explicitly enable the package. Persisted the bounded approval
  reference and used it in discovery/catalog Trust projection; packages without approval remain
  `unknown`. Added regression coverage.
- Added `state backup --version 2` CLI selection and regression coverage; reconciled README,
  architecture, roadmap and final acceptance evidence with the implemented v16/Backup v2 boundaries.
- Final evidence: integrated `2 passed`; Skill matrix `41 passed`; Provider/Model matrix `66 passed,
  1 skipped` (the explicit Live credential checklist); MCP matrix `14 passed`; backup/Doctor/CLI
  matrix `30 passed`; full offline `1060 passed, 2 skipped, 2 deselected`; Ruff format/check,
  compileall, five CLI help commands and `git diff --check` passed. No live/network/credential path
  ran and no remote push was performed.

## 2026-08-24 — Stage 6 post-closeout review attempt and independent audit

- Started the single requested Grok review from clean local `main` after Subplan75 merge. The
  delegate could not create its session (`FS_PERMISSION_DENIED`); its model/settings requests also
  failed because the environment could not resolve the proxy DNS name. It returned no review report,
  made no file changes, and was not retried.
- Independent review of the same requested risk surface found Doctor's managed-package check did
  not compare envelope `source_kind`, `scope_id`, `effective_trust`, or reject a version-directory
  symlink. Added those checks and a spoofed-envelope regression; targeted tests passed and the final
  offline gate remained `1060 passed, 2 skipped, 2 deselected`.

## 2026-08-25 — Full Stage 6 Grok review rerun and repair completed

- Reran the requested Grok delegate review over the complete Stage 6 implementation surface after
  the earlier session-creation failure. The report found no P0 and identified P1/P2 gaps around
  MCP YAML/runtime authority, durable Skill selection and Generated approval, install races,
  workspace visibility, schema/backup/restore integrity, frozen credentials, executable drift and
  secret boundaries.
- Fixed the confirmed findings with fail-closed behavior and regression coverage. The independent
  follow-up also closed MCP handshake validation and connected binary/embedded result Artifacts to
  both durable ToolExecution references and the v16 MCP result-link table in one transaction, with
  workspace identity checks.
- Final evidence passed: MCP/control/runtime `17 passed`; new Artifact-link/handshake regressions
  passed; full non-live suite `1065 passed, 2 skipped, 2 deselected`; Ruff format/check, compileall,
  five CLI help commands and `git diff --check` passed. No live/network/credential path or remote
  push was run.

## 2026-08-25 — Subplan 76 Runtime Policy Configuration activated

- Isolated live acceptance proved Keychain/Provider connectivity but all three Learning Reviewer
  models hit the same constructor-owned 15-second deadline. The user requested a packaged
  `runtime-policy.toml` plus optional user `config.yaml` overrides and explicitly required fixed
  safety invariants to remain untouched.
- Created `fix/stage6-runtime-policy` from local `main@24f8ae8`. Scope is limited to process-wide
  AgentRun/Review tuning defaults, strict bounded overlays, composition wiring, tests and docs.
  Permission defaults, secret/path/schema/payload limits, storage invariants and per-entity request
  timeouts remain code-owned or entity-owned. No live/network/credential path is in scope.

## 2026-08-25 — Subplan 76 completed locally

- Replaced the superseded Agent-only resource with versioned `runtime-policy.toml`, adding one
  packaged default authority for AgentRun, Learning Review and Preference Review runtime tuning.
- Added strict optional `config.yaml.runtime_policy` overlays, code-owned safety ceilings,
  cross-field revalidation and lossless preservation by unrelated Provider/Preference writes.
  Safety-owned loop/model tables and permission/secret/path/schema/payload/storage/recovery
  invariants are not user-overridable.
- Production bootstrap now injects the effective immutable policy into AgentRun and both Review
  paths. Focused matrix: `135 passed in 5.64s`; full non-live suite:
  `1074 passed, 2 skipped, 2 deselected in 42.11s`; Ruff format/check, compileall, five CLI help
  commands and `git diff --check` passed. No live/network/credential/user-state path or remote push
  was run.

## 2026-08-25 — Subplan 77 Skill Script diagnostics/context repair completed locally

- Confirmed both remaining findings: Script-specific failures had no explicit safe contract at the
  AgentLoop catch-all boundary, and persisted `selection_id` was omitted from the model-visible
  Skill context even though `run_skill_script` requires it.
- Added a bounded, secret-refusing public diagnostic contract. Script errors preserve stable codes
  in normal tool envelopes and at unexpected Agent fallback boundaries; unknown exceptions remain
  generic, and public event shape/history ownership are unchanged.
- Rendered the exact frozen `selection_id` in low-authority Skill metadata without granting tools,
  permission or approval. Direct matrix: `34 passed`; expanded matrix: `161 passed`; full non-live
  suite: `1077 passed, 2 skipped, 2 deselected in 42.77s`. Ruff format/check, compileall, CLI help
  and `git diff --check` passed. No live/network/credential/user-state path or remote push was run.
