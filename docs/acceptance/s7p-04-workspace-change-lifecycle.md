# S7P-04 Workspace Change Lifecycle 验收报告

## 1. 结论

在本 topic branch 的离线确定性测试中，S7P-04 覆盖的 Direct 工作空间变更目标为 `PASS`：
普通文件 delete/move/rename、严格 SHA 冲突、no-clobber、dirfd/symlink confinement、审批、
durable evidence/recovery、sandbox promotion 和 bounded partial failure 均有可重复的测试证据。

计数：`PASS=30`、`FAIL=0`、`BLOCKED=0`、`NOT RUN=0`、`INCONCLUSIVE=0`（本报告列出的 S7P-04
专用场景）。完整仓库门禁另行以最终命令输出为准。

## 2. 测试依据

| 项目 | 记录 |
|---|---|
| Revision | `codex/feat/s7p-04-workspace-change-lifecycle`；审查基线 `e80c3157d3286116b04566cf76bf01fd192c6b8b` |
| 日期/平台 | 2026-08-27；macOS topic worktree（本地实现也对 Linux `renameat2` 分支 fail closed） |
| 启动方式 | pytest 直接调用生产 bootstrap、ToolRegistry、ToolExecutor、AgentLoop/SessionOrchestrator |
| 状态隔离 | pytest `tmp_path`；每个场景独立 workspace/state/temp root |
| 公共接口 | `delete_file`、`move_file`、`rename_file`、`promote_sandbox_changes`、`show_changes`，以及既有 `write_file`/`apply_patch` 合同审计 |
| Provider | 未运行真实 Provider/model/Pi/MCP/network/credential；仅使用确定性 `ScriptedModelProvider` 驱动工具调用 |
| 安全边界 | 未读取或修改三个用户自有 research 文档；未改变公开事件生命周期、runtime-policy 默认值或依赖 |
| Formal review | Averroes `01a03f96-8c8b-7ad3-9f44-c767bf550a46`；`gpt-5.6-luna` / reasoning `max`；只读审查；正式结论 `REQUEST CHANGES` |

## 3. 用户表面清单

| 表面/能力 | 实现证据 | 可达状态/模式 | 场景 | 覆盖 |
|---|---|---|---|---|
| `delete_file` | production bootstrap + `WorkspaceMutationService` | 成功、SHA 冲突、缺失、symlink/目录/special、拒批/取消、恢复 | S7P04-01/02/04/05 | PASS |
| `move_file` | production bootstrap + atomic no-replace adapter | 成功、目标存在/竞态、dirty source、父目录/symlink、跨设备 fail closed | S7P04-01/02/03/04 | PASS |
| `rename_file` | production bootstrap + same-parent preflight | 成功、跨父目录拒绝、目标冲突、dirty source、两路径 evidence | S7P04-01/02/04 | PASS |
| Sandbox promotion | `SandboxSnapshotService` + same mutation service | delete、无歧义 rename/move、重复内容歧义、稳定 preflight、partial failure | S7P04-06/07 | PASS |
| ChangeSet/ToolFact | `ChangeSetService` + `ChangeToolFact` | source/destination pair、已生效项保留、错误不宣称成功 | S7P04-01/06/07/08 | PASS |
| Durable recovery | prepared intent + `observe_file`/classifier | completed、safe-to-retry、mixed/reconciliation、outcome-unknown | S7P04-04/08 | PASS |

发现的旧 Stage-3 清单冲突已在 `docs/roadmap/stage-3-local-tools-and-safety.md` 修正；copy、
目录/递归 delete、undo、chmod/link、Git write 和真实 Provider 质量测量不属于本验收表面。

## 4. 场景结果

| ID | 用户与任务 | 前置条件 | 用户动作 | 预期 | 实际与证据 | 状态 |
|---|---|---|---|---|---|---|
| S7P04-01 | 开发者删除、移动、重命名普通文件 | 文件存在且有 SHA；目标不存在 | 通过三个显式工具执行 | 内容/模式保持；源消失；ChangeSet 有正确状态 | `test_delete_move_and_rename_publish_only_regular_files_without_overwrite` 检查最终树、mode、status、source/destination | PASS |
| S7P04-02 | 开发者误选目录、special、symlink 或越界路径 | workspace 含这些对象 | 提交破坏性路径 | 无副作用且返回稳定拒绝 | 专用 preflight 矩阵检查 `invalid_target`/`symlink_not_allowed`/`invalid_path`，外部文件保持不变 | PASS |
| S7P04-03 | 开发者移动到已被用户占用的目标 | source 已通过预检；目标随后出现 | 执行 move | no-clobber；源和原目标都保持 | destination race 与 unsupported primitive fixture 检查源/目标字节和错误码 | PASS |
| S7P04-04 | 重启后的操作者判断 delete/rename 是否完成 | durable intent 含 absence/two-path evidence | 观察源/目标并分类 | 仅两项 expected 才 completed；before 可安全重试；mixed/missing/third-party 不成功 | prepared persistence 与 recovery observation/classifier 测试检查有序 evidence、hash/size、无正文 | PASS |
| S7P04-05 | 用户拒绝或在 handler 前取消破坏性请求 | 工具已预检，审批尚未放行 | reject/cancel approval | handler 不进入，文件不变 | 三个 destructive tool 的拒批参数化测试及 awaiting-approval cancellation 测试 | PASS |
| S7P04-06 | 用户审核沙箱删除和 rename | snapshot 中有 baseline 文件 | 在沙箱删除/改名，再选择 promotion | preview 与真实结果一致；一个逻辑 rename 携带 source+destination | sandbox promotion 测试检查审批、最终树、两个 ChangeSet entries 和 pair identity | PASS |
| S7P04-07 | 用户推广多个沙箱修改但后项失败 | 两个 created changes；第二项已产生 effect 后注入 fsync/结果不确定 | 一次选择两项 | 全部先 preflight；前项与不确定项保留；整体失败且 bounded partial/unknown | `test_promotion_preflights_all_then_returns_bounded_partial_failure` 检查两项 ChangeSet/fact、`UNKNOWN` disposition、未回滚 | PASS |
| S7P04-08 | Direct 用户让 Agent 删除和重命名项目文件 | production session 已确认 workspace | scripted Agent 发送两个 tool calls，再 `show_changes` | 最终树和 ChangeSet 是事实源 | `test_scripted_direct_production_acceptance_verifies_tree_and_changeset` 通过 production registry/stream 检查最终树与 pair fields | PASS |

### 场景复现命令

```bash
PYTHONPATH=src /Users/ruirui/Documents/Project/Agent/developing/.venv/bin/python \
  -m pytest -q tests/test_s7p04_workspace_change_lifecycle.py
```

该命令只使用临时目录和 fake/scripted fixtures；它不测量真实模型的工具选择质量，也不执行
网络或 live Provider。

## 5. 复杂旅程

1. **生产 Direct delete + rename + ChangeSet 回读（S7P04-08）**：scripted Assistant 首轮发出
   两个破坏性 tool calls，正常经过生产 registry、能力策略、审批、durable tool cycle 和
   `SessionOrchestrator.stream`；第二轮调用 `show_changes`。最终 verifier 同时检查旧路径消失、
   新路径内容和 ChangeSet 的 `source_path`/`destination_path`，因此不是只根据 Assistant 文本判断。

2. **沙箱 identity promotion（S7P04-06）**：状态先在 snapshot 中改变，再由 collector 按
   hash/size/mode 配对 deleted+created；同父目录成为 rename，删除保持独立逻辑 change。选择、
   approval preview、真实 mutation 和 ChangeSet 共享路径/哈希约束；重复内容时保留 delete/create，
   不猜身份。

3. **多项推广的 partial failure（S7P04-07）**：两个选项先全部 preflight，第一项真实发布并记入
   ChangeSet，第二项失败后返回 `publish_failed` 和 bounded `applied/remaining` details。源数据不
   被回滚或覆盖，run-level 结果不是 success；该旅程验证了跨多个 effect 的真实失败语义。

## 6. 审查、发现与修复

同一实施任务内的只读 reviewer Averroes（`01a03f96-8c8b-7ad3-9f44-c767bf550a46`，模型
`gpt-5.6-luna`，reasoning `max`）审查了完整
`e80c3157d3286116b04566cf76bf01fd192c6b8b...751bd02` diff；未修改、创建、删除、暂存或提交文件。
正式 verdict 为 `REQUEST CHANGES`。审查时记录的完整离线数字为 `1213 passed, 4 failed,
2 skipped, 2 deselected`，4 个失败均为新增生产工具后的旧 inventory 断言；其 Ruff format/check、
`morrow run --help` 与 `git diff --check` 通过，审查 worktree clean。此前卡住的 Euclid
（`01a03f7f-efef-7b50-b794-697f03af07bb`）已按任务指令中断，不替代正式报告。

确认 findings 与本地修复如下：

| 级别/编号 | 复核结论 | 修复与回归 |
|---|---|---|
| P1-1 recovery evidence 越界读取 | confirmed；`..` 路径探针曾读取 workspace 外文件 | execution/capability validator 与 `observe_file` 统一拒绝空、`.`、`..`、反斜杠和绝对路径，并对 forged evidence fail closed |
| P1-2 source SHA/目录项 TOCTOU | confirmed；原实现 hash 后按名称 effect | destructive adapter 持有 no-follow regular fd，通过有界稳定双读及 dev/ino/type/mode/size/mtime/ctime 目录项身份重验后才 effect；源替换回归验证不误删/误移用户文件 |
| P1-3 FIFO/special 阻塞 | confirmed；leaf open 缺少 non-blocking | mutation/read/recovery leaf open 增加 `O_NONBLOCK`，special/FIFO 只 bounded fail closed |
| P1-4 effect 后 fsync/验证丢失事实 | confirmed；effect 后错误曾闭合为 success | 增加 `OUTCOME_UNKNOWN`、ChangeSet/ChangeToolFact、durable body-free file evidence 与恢复观察；post-effect failure 不再 success |
| P2-1 move/rename mode/size 未核验 | confirmed | destination hash、size、mode 均与源 evidence 比较；mode drift 回归为 unknown |
| P2-2 4 个生产 inventory 断言失败 | confirmed | 同步 configuration、Stage-2、Stage-4、stage-boundary 工具集合并跑全量 suite |
| P2-3 竞态/effect 后/partial coverage 不足 | confirmed | 增加 source replacement、FIFO、mode drift、fsync unknown、closed unknown recovery 与第二项已 effect 的 partial-failure tests |
| P3-1 执行状态/验收文档过时 | confirmed | `.agent`、本验收文档、架构说明和日志同步为完成/修复后证据 |

修复后没有开放 confirmed finding（`P0/P1/P2/P3=0`）。以下仍是非 finding：Darwin
`renameatx_np(RENAME_EXCL)`/Linux `renameat2(RENAME_NOREPLACE)` 的 no-clobber primitive 及 fail-closed
分支已通过静态检查；普通 symlink、目录、special、越界、protected、审批拒绝、handler 前取消、
sandbox 一对一配对/歧义不猜测和基础 partial failure 未发现额外缺陷；ancestor directory 在 fd 打开
后被第三方移动的极端竞态未动态确认；嵌套 Codex sandbox 中的两个 native Seatbelt 用例跳过不是产品
finding，也未被计为 PASS。

## 7. 覆盖与缺口

- S7P-04 专用场景：8 个计划用户目标，8 个执行，8/8 `PASS`；专用 pytest 当前包含 30 个通过测试。
- 高风险组合直接覆盖：三种 destructive tool × 拒批、source dirty；move/rename × destination
  conflict/race；delete/rename × durable evidence；sandbox × ambiguity/partial failure。
- 状态迁移覆盖：preflight → approval → effect、source before/expected absence、two-path
  before/expected、mixed/third-party/outcome-unknown、partial effect → failed result。
- 未覆盖真实 Provider/model 的行为和延迟；按 S7P-04 明确范围与离线门禁要求不运行。
- native Seatbelt host-level acceptance 仍需在产品允许的 host runner 单独执行；当前嵌套环境不能
  证明该平台隔离性质。Windows 与未声明支持的平台也不在本机执行。
- 未做 copy、目录/递归、undo、chmod/link、Git write；这些是明确 non-goal，不是遗漏。

## 8. Provider 证据

未使用真实 Provider。`ScriptedModelProvider` 仅提供固定 Assistant tool-call 序列，用于验证生产
工具 inventory、ToolExecutor/SessionOrchestrator composition 和最终文件事实；不能推导模型规划、
纠错或延迟指标。

## 9. 最终门禁与交接状态

修复提交为 `ea19067`（`fix(workspace): close S7P-04 review findings`）；此前实现/验收提交为
`aecd4ec` 与 `751bd02`。修复后实际结果：

- affected focused suite：`205 passed, 2 skipped in 16.20s`；跳过项为既有嵌套 Codex sandbox
  的两项 host-level Seatbelt 测试。
- full offline gate（fallback interpreter）：`1225 passed, 2 skipped, 2 deselected in 50.45s`，
  `0 failed`。
- `uv run pytest -m 'not live'`：未能启动，uv cache `/Users/ruirui/.cache/uv` 在受限沙箱中不可写；
  按仓库规则使用当前 worktree 的 `PYTHONPATH=src` 环境完成同等 gate。
- `ruff format --check .`：通过，`467 files already formatted`；`ruff check .`：通过，`All checks passed!`。
- `python -m compileall -q src tests`：通过；`python -m morrow --help` 与 `python -m morrow run --help`：均通过；
  `git diff --check`：通过；当前 worktree import proof 指向
  `/Users/ruirui/.codex/worktrees/8ab3/developing/src/morrow/__init__.py`。
- 未运行 live Provider/model/Pi/MCP/network/credential 测试。topic branch 保持独立、待 root task
  fast-forward 集成；不 merge/push/delete branch/worktree，也不开始 S7P-05。
