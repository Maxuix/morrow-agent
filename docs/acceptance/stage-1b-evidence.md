# Stage 1B 修复验收证据

> 状态：2026-08-14 最终修复树离线、Live 与人工复验全部通过；Stage 1B 完成，Stage 2 已解除阻塞但尚未开始。

## 门禁映射

| 门禁 | 最终树证据 |
|---|---|
| S1B-01 | 三层 Preferences、unset、原序 instructions、快照刷新及损坏 workspace 层隔离测试。 |
| S1B-02 | must-trigger/must-not-trigger、敏感词普通聊天、混合任务、禁止字段、判别式提取、一次修复、精确预览与零副作用测试。 |
| S1B-03 | dirty `/new`/`/continue`、独立 save/discard/cancel、EOF code 2、接力保存、取消/失败保留会话测试。 |
| S1B-04 | 真多进程 claim/write/clear/锁、revision conflict、corrupt/future schema、v2 tombstone、目录 fsync 与故障注入测试。 |
| S1B-05 | Provider add/configure/test/show、环境优先凭据、base-URL-only、masked rotation、类型化错误与非零 CLI 退出测试。 |
| S1B-06 | canonical relink 冲突/保留 ID、Profile/Handoff/Preferences 独立 clear/reset 与 cleared revision 测试。 |

## 已确认 P1/P2 → 回归

| 发现 | 精确回归（测试函数简称） |
|---|---|
| 重复/并发 workspace ID、relink Git 根碰撞 | `repeated_confirmation...`、`concurrent_confirmation...`、`relink_rejects_effective_git_root...` |
| clear revision 回退/非原子 clear/目录耐久 | `clear_persists_revision...`、`workspace_publication_failures...` |
| event/session/workspace ID | `runtime_default_ids...`、`one_application_id_source...` |
| Provider 异常/非正常 finish | `provider_exception_always_completes...`、全部 `adapter_rejects...` |
| EOF 忙循环/切换 prompt EOF | `dirty_independent_eof...`、`closed_switch_prompt...` |
| workspace 降级仍可继续 | `profile_or_handoff_failure_enforces...`、`corrupt_workspace_preferences...` |
| 配置误拦截/形状 fallthrough/预览后写 | `sensitive_vocabulary...`、`config_extraction_result...`、`command_service_routes_deterministic...` |
| 环境凭据/base URL/rotation/show | `environment_credential_has_precedence...`、`credential_rotation...` |
| structured 双倍超时/repair 丢任务 Schema | `structured_completion_uses_one_total_deadline...`、`structured_repair_retains...` |
| Provider error code/CLI 零退出 | `provider_test_persists_typed_failure_code`、`failed_provider_test_returns_non_zero_exit` |
| orphan assistant | `context_never_keeps_assistant...`、`context_does_not_skip...` |
| 空 goal/弱 persistence invariant | `handoff_requires_non_empty...`、`invalid_persisted_handoff_invariants...` |
| 证据高估（十轮/真多进程/终端） | `ten_turns...`、multiprocess state tests、`tests/test_terminal.py` |

## 最终树离线结果

`149 passed, 1 expected Live deselection`; strict collection `150`; Ruff format/check、compileall、CLI help、import smoke、Stage 2 boundary与 55 项 boundary/multiprocess/terminal/CLI 子集均通过。

## 最终树 Live／人工结果

显式 Live 测试通过（`1 passed, 149 deselected`），真实流边界为 5 个可见正文分片、1 个 `completed(stop)`、0 个错误、0 个公开 reasoning 字段。临时空状态 Provider 引导、十轮真实终端、长回答取消与取消后对话、`/handoff update`、`Ctrl+D`、两个普通项目、两个真实 Git worktree、移动/relink 保留 ID/state，以及断网退出 fallback 后重启展示并显式继续均实际通过。精确密钥哨兵、项目内容/状态和 worktree cleanliness 扫描通过。

因此所有已确认 P1/P2 均有回归覆盖，S1A-01–08、S1B-01–06、最终树离线门禁和完整 Live／人工清单均有实际证据。Stage 1 修复验收完成，Stage 2 解除阻塞；Stage 2 尚未开始，需另行定义范围与计划。
