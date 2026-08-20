# Stage 4 真实用户测试修复记录

> 日期：2026-08-20
> 输入：[Stage 4 真实用户场景复测报告](stage-4-real-user-test-report.md)
> 状态：RUT-001～RUT-008 及独立终审发现的 Recovery health 守卫已修复并重验；
> 独立终审最终确认无 P0/P1
> 范围：只修复 Stage 4 正确性、数据安全、诊断与 CLI 可操作性；Stage 5 仍未激活

## 1. 修复结论

本次修复将原报告的八个问题收敛为四组不可破坏的合同：

1. Artifact cleanup 以整个 data root 的 SQLite metadata 与 reference 为权威，不再以当前
   workspace 的局部视图判定 orphan。
2. Fork child 创建时不继承父 TaskRun，但创建后是正常 Session，可拥有自己的
   TaskRun、Turn 和 child-local conversation records。
3. 普通前台 Turn 或 TaskRun 只能在 `lifecycle=active` 且 `health=ok` 时开始或恢复；
   archived、needs-recovery、quarantined 和 read-only 状态都不能绕过应用边界。
4. Session `updated_at` 是真实的乐观并发令牌：每个外层写事务只分配一个时间戳，
   且以整秒精度严格单调递增。

## 2. RUT 修复矩阵

| 问题 | 根因核实 | 修复与当前合同 | 主要回归证据 | 状态 |
|---|---|---|---|---|
| RUT-001 | Artifact 字节目录是 data-root 全局资源，旧 cleanup 却只加载当前 workspace metadata，并最终按路径 `unlink` | 全局枚举 workspace，合并 Artifact metadata、普通 reference 和 checkpoint reference；每个候选在不可重放的 `BEGIN IMMEDIATE` 事务内再查一次全局权威，有任何权威即拒绝 | `test_orphan_cleanup_preserves_artifacts_from_every_workspace`；cleanup 事务/竞态/硬链接回归 | 已关闭 |
| RUT-002 | `DurableSession` 的永久校验把“fork 创建时不继承 task”错误扩大为“fork child 永久不能拥有 task” | 不继承约束由 fork 创建边界保证；持久化 child 可通过新 TaskRun 与普通 Turn 继续，父历史不被复制或修改 | `test_fork_restored_through_production_bootstrap_can_complete_own_turn` | 已关闭 |
| RUT-003 | archive、task new/resume、Turn submit/recovery resume 各自使用不完整的 lifecycle 判定，Store 最终守卫也只拒绝 deleted | archive 必须没有 current task；非 active Session 不能持有/创建/恢复 current task 或创建 Turn；普通工作同时要求 health OK；Doctor 将 `archived_active_task` 报为 error/needs-repair | `test_archive_requires_no_current_task_and_archived_session_cannot_start_one`；`test_archived_session_rejects_new_turn_at_application_and_orchestrator_boundaries`；`test_orchestrator_refreshes_stale_durable_session_health_before_submit`；`test_doctor_reports_archived_session_with_active_task_as_needs_repair` | 已关闭 |
| RUT-004 | 任务指针、对话位置和 health 写回旧 `updated_at`，让旧快照仍能通过 stale 校验 | Store/journal 使用注入 clock；Session 可观察变更每个外层事务共享一个严格单调整秒 token；Task、Turn、conversation、health/recovery 都推进它 | `test_session_updated_at_is_monotonic_and_invalidates_old_archive_tokens`；production/CLI store-clock 回归 | 已关闭 |
| RUT-005 | 旧 orphan 扫描未排除受管 `artifacts/tmp/`，Doctor 也没有区分受管未引用、非托管可隔离与不安全拒绝 | 正常受管 `tmp/` 不是 candidate；异常碰撞仍可诊断；Doctor 输出 `artifact_managed_unreferenced`、`artifact_unmanaged_removable`、`artifact_unsafe_refused`，并在遍历前验证受信目录链 | `test_managed_artifact_tmp_directory_is_not_an_orphan_candidate`；Doctor 全局分类、symlink/不安全布局回归 | 已关闭 |
| RUT-006 | API 已返回 page metadata，CLI 却只遍历 `.items` | Session/Task/Artifact list 的文本输出在有下页时显示 `next_cursor`；三者均支持 `--json`，JSON 保留 `items` 与 `next_cursor`；Artifact list 补齐 `--cursor/--limit` | `test_cli_session_list_exposes_next_cursor_and_json_page`；`test_cli_task_and_artifact_lists_preserve_page_metadata` | 已关闭 |
| RUT-007 | Doctor 把“命令已生成报告”与“Store 健康”混成同一 exit 0 语义 | Doctor 仍输出完整有界报告，但只有 `health=ok` 返回 0；warning/error 导致的非 OK health 返回 2 | `test_cli_doctor_fails_when_missing_store_needs_repair` | 已关闭 |
| RUT-008 | 预启动领域/持久化异常落入 Provider 泛化错误；Grant revoke 在 stale 检查前先走了 unavailable 分支 | AgentLoop 在预启动失败时仍发出一对完整事件，但 ApplicationError 保留稳定应用消息，未知异常不再误报 Provider；Session health 使用 `needs_recovery`/`quarantined`/`read_only`；Grant stale 统一返回 `stale` | `test_pre_start_application_error_keeps_stable_non_provider_message`；Grant stale revoke 与 Session health 应用边界回归 | 已关闭 |

## 3. Cleanup 安全语义

`state cleanup --apply` 现在是“隔离”，不是“销毁”。它的成功路径是：

```text
加载 data-root 全局 Artifact metadata/reference 快照
→ 通过 O_NOFOLLOW 目录句柄锁定并验证 data_root/artifacts/tmp 目录链
→ 只接受 0600、单链接、合法 ID/后缀的普通文件
→ 在不可重放的 BEGIN IMMEDIATE 事务内终态复查全局权威
→ 原子 rename 到同一受信父目录下的随机 0700 私有 quarantine 目录
→ fsync 目录，保留 quarantine payload
```

正常 apply 的真实报告语义是 `removed=0` 且 `quarantined=1`，原受管路径消失，
字节仍保留在 quarantine。该路径不调用 `unlink`、`truncate` 或 `ftruncate`，因此不会销毁
原 inode 的字节，也不会通过后来出现的外部硬链接销毁字节。事务结果不确定时，
恢复只能在再次确认全局无权威后用 exclusive hard link 尝试，绝不覆盖或删除后来的路径；
无法证明时保留 quarantine 并 fail closed。

回归额外覆盖：Artifact/symlink 父目录替换、候选路径替换、提交前/后故障、
提交后新 metadata/reference 发布、外部硬链接、恢复失败以及 quarantine payload 丢失/
改名时的文件描述符释放。

## 4. Session、Task 与 Fork 语义

- Fork 创建事务仍强制 child 的 `current_task_run_id=None`；该事实不再是
  `DurableSession` 的永久模型禁令。child 被持久化后可从 production bootstrap 恢复，
  创建自己的 TaskRun，完成 Turn，且只追加 child-local records。
- archive 对 current task 采用 fail-closed 策略：不自动 cancel/abandon，不改写任务历史。
  用户必须先以明确任务命令关闭 current task，再归档 Session。
- 新 Turn、新 TaskRun 和 TaskRun resume 都同时要求 `ACTIVE + health OK`。
  `needs_recovery` 保留既有 recovery disposition；`quarantined` 和 `read_only` 通过稳定类型错误拒绝。
  Journal/Store 端仍有最终守卫，避免上层快照陈旧时写入非法状态。
- Recovery resolve 的同 command receipt 只返回 replay；已非 `OPEN` 的 RecoveryReport
  使用新 command 会稳定拒绝，且写事务内会再读 durable report status。因此旧 report
  不能清除后来设置的 `quarantined`/`read_only` health，也不能重复创建 AgentRun。
  解决后的 `resume_recovery()` 只在 Session 仍为 `ACTIVE + health OK` 时启动模型回合。
- Session 的每个外层写事务只取一次注入 clock，事务内所有嵌套写共享同一
  `updated_at`；下一事务至少比旧值增加一秒，即使物理时钟未前进也不例外。

## 5. 诊断与 CLI 语义

- Doctor 在遍历 candidate 之前验证 data root、`artifacts/` 和 `artifacts/tmp/` 的类型、
  权限、symlink 与打开后 inode；不安全布局直接记录 error/needs-repair。
- 健康的受管 `artifacts/tmp/` 不进入 orphan 统计；异常的 symlink 或同名碰撞仍可见。
- `morrow state doctor` 只在 health OK 时 exit 0，其他状态 exit 2。
- Session/Task/Artifact list 的文本模式显示 `next_cursor`，`--json` 返回可程序化的
  `{items, next_cursor}` page。
- cleanup CLI 文案明确说明 `--apply` 将候选移入私有隔离区，不销毁原字节。

## 6. 验证状态

下列结果来自 Recovery health 最终防线修复后的重跑。完整 offline suite 在宿主环境执行，
所有 Seatbelt 用例都实际运行，`0 skipped`；一项 deselected 是显式排除的 live 测试。

| 门禁 | 当前记录 |
|---|---|
| RUT/Stage 4 聚焦回归（14 files） | `199 passed in 5.83s` |
| `uv run pytest -m 'not live'` | `663 passed, 1 deselected in 12.26s`；`0 skipped` |
| `uv run ruff format --check .` | `164 files already formatted` |
| `uv run ruff check .` | exit 0 |
| `uv run python -m compileall -q src tests` | exit 0 |
| `uv run morrow --help` | exit 0 |
| `uv run morrow state cleanup --help` | exit 0；文案为“移入私有隔离区；不销毁原字节” |
| `git diff --check` | exit 0 |

最终修复后已由原独立 reviewer 复审，结论为无剩余 P0/P1。

本次没有运行 live Provider、真实网络或真实凭据测试；它们也不属于本次 RUT 修复范围。
