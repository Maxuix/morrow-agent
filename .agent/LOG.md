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
