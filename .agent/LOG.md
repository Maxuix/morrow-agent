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
