# S7P-04 Workspace Change Lifecycle 验收报告

## 1. 结论

在本 topic branch 的离线确定性测试中，S7P-04 覆盖的 Direct 工作空间变更目标为 `PASS`：
普通文件 delete/move/rename、严格 SHA 冲突、no-clobber、dirfd/symlink confinement、审批、
durable evidence/recovery、sandbox promotion 和 bounded partial failure 均有可重复的测试证据。

计数：`PASS=23`、`FAIL=0`、`BLOCKED=0`、`NOT RUN=0`、`INCONCLUSIVE=0`（本报告列出的 S7P-04
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
| S7P04-07 | 用户推广多个沙箱修改但后项失败 | 两个 created changes；第二个注入失败 | 一次选择两项 | 全部先 preflight；前项保留；整体失败且 bounded partial | `test_promotion_preflights_all_then_returns_bounded_partial_failure` 检查失败码、前项 ChangeSet/fact、未回滚 | PASS |
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

## 6. 发现

本次专用场景没有可确认的产品缺陷（`P0/P1/P2/P3=0`）。测试 harness 的真实 Seatbelt 用例在
嵌套 Codex sandbox 中按既有测试规则跳过，不作为 S7P-04 产品缺陷；本报告也没有把 skipped native
isolation 当作 PASS。

## 7. 覆盖与缺口

- S7P-04 专用场景：8 个计划用户目标，8 个执行，8/8 `PASS`；专用 pytest 当前包含 23 个通过测试。
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

## 9. 后续动作

1. 完成同一 activation baseline 到当前 HEAD 的只读 Luna Max 代码审查，并逐条本地验证其 findings。
2. 重新运行受影响 focused gates 与完整 `-m 'not live'`、Ruff、compileall、两个 CLI help 和 diff check。
3. 在 host-level native sandbox runner 可用且获得授权后，再补 Seatbelt isolation evidence；不把该环境
   工作转化为 S7P-05 或 live Provider 测量。
