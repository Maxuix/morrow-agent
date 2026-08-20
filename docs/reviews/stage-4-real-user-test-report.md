# Stage 4 真实用户场景复测报告

> 历史记录：本文保留 `015f407` 上的原始测试结果，不随后续修复回写。
> RUT-001～RUT-008 的根因、修复合同和回归证据见
> [Stage 4 真实用户测试修复记录](stage-4-real-user-test-remediation.md)。
> 当前 RUT-001～RUT-008 及独立终审补充发现的 Recovery health 守卫已完成修复与
> 重验；原独立 reviewer 最终复审无 P0/P1，以修复记录为当前状态。

> 测试日期：2026-08-20
> 被测分支：`refactor/stage4-boundaries`
> 被测提交：`015f407 docs(agent): close boundary refactor`
> 结论：**不建议在修复 RUT-001～RUT-003 前把当前 Stage 4 视为可完整日用**

## 1. 执行摘要

本轮不是只运行已有 pytest，而是在一次性状态目录中实际执行 Morrow 的 CLI、生产 bootstrap、
SessionOrchestrator、真实本地只读工具、SQLite Operational Store、Filesystem Artifact Store、
Recovery、Backup、Doctor、Cleanup 和 CapabilityGrant 边界。共覆盖正常操作、重复提交、错误参数、
跨工作空间访问、进程重启、模拟崩溃、备份篡改、写锁竞争和潜在危险清理等场景。

Stage 4 的多数正向流程可用：

- Session、TaskRun、多 Turn 修正、显式接受和 TaskOutcome 可以持久化并在重启后恢复；
- `read_file` 真实读取工作区文件，ToolExecution、ToolMessage 和最终回答顺序正确；
- checkpoint 可以生成，Fork 元数据和父历史隔离正确；
- Recovery 的 REPL 命令服务和独立 CLI 均能完成 acknowledge + resume；
- Artifact 的发布、读取、pin/release、敏感内容拒绝和容量限制有效；
- Backup 可创建并验证，篡改 Artifact 后 `verify-backup` 以 exit 2 拒绝；
- Full Access Manual Grant 会展示 unconfined 风险，支持 stale 拒绝和撤销；
- SQLite 写锁竞争在约 2.76 秒内有界失败，释放后同一命令可安全重试成功；
- 命令回执的 replay、conflict、跨 workspace 隔离和多数错误退出码符合预期。

但实际组合路径发现 3 个影响完整性的高优先级问题：

1. **RUT-001 / Blocker：某 workspace 的 cleanup dry-run 会把另一个 workspace 的合法 Artifact
   列为可删除；执行 `--apply` 存在跨 workspace 数据丢失风险。**
2. **RUT-002 / Blocker：Fork Session 可以创建和读取继承历史，但永远不能创建自己的 TaskRun，
   因而无法继续对话。**
3. **RUT-003 / High：Session 可以在 TaskRun 仍 open 时归档，已归档 Session 也可以继续创建新
   TaskRun，形成 `archived + current open task` 的矛盾持久状态。**

另发现 Session `updated_at` 不随对话、建任务和 Recovery 更新，Doctor orphan 永久误报、CLI 分页
不输出 next cursor 等问题。完整离线测试仍为绿色，说明这些属于真实用户组合路径的测试缺口。

## 2. 测试环境与边界

- 临时根目录：`/private/tmp/morrow-stage4-real-user.6fUL5p`
- 状态目录：`/private/tmp/morrow-stage4-real-user.6fUL5p/state`
- 测试工作区：包含 `app.py`、`test_app.py` 和 `README.md` 的最小 Python 项目
- 数据库最终大小：356,352 bytes，schema v9
- Provider：`ScriptedModelProvider`
- CredentialStore：`MemoryCredentialStore`
- 外部 CLI：真实 `uv run morrow ...` 进程
- 生产组合：真实 `build_application()`、`build_session_application()` 和
  `SessionOrchestrator`
- 网络与凭据：未使用真实网络、真实 API Key 或 live Provider
- Sandbox：嵌套 Codex 环境中的两个真实 Seatbelt 测试按项目约定跳过

使用 Scripted Provider 是为了准确控制 ToolCall 和最终回答，不会伪造存储、工具或应用服务行为。
因此本报告能评估执行协议和持久状态准确性，但不能把脚本化回答当作真实 LLM 推理质量证据。

## 3. 实际操作结果

| 编号 | 实际用户操作 | 结果 | 主要观察 |
|---|---|---|---|
| U01 | CLI 创建 Session | PASS | exit 0；schema 自动初始化；首启约 0.42 秒 |
| U02 | 相同 command ID 重放相同创建请求 | PASS | 返回原 Session，未重复创建 |
| U03 | 相同 command ID 改变 Session ID | PASS | exit 2；`conflict`，未写入新 Session |
| U04 | `--limit 0`、不存在 Session、跨 workspace 查询 | PASS | 均安全拒绝，exit 2 |
| U05 | 真实 Agent 调用 `read_file` 读取 `app.py` | PASS | 返回内容含 `def greet`；ToolExecution/ToolMessage 闭合 |
| U06 | 同一任务追加修正 Turn | PASS | 沿用同一 TaskRun，重新到 `ready_for_acceptance` |
| U07 | `/accept` 后重启恢复 Session | PASS | TaskRun 为 `accepted`；Outcome v1 保留；6 条消息恢复 |
| U08 | 接受后发送新目标 | PASS | 创建新的 TaskRun，不复用已接受任务 |
| U09 | 创建 checkpoint | PASS | `chk_manual`，边界 position 9，retained records 3 |
| U10 | 从 checkpoint Fork | PASS | child lineage、cut position、checkpoint ID 正确；父历史未复制 |
| U11 | 在 Fork 子 Session 继续对话 | **FAIL / Blocker** | generic internal error；无 child TaskRun、无自有记录 |
| U12 | CLI 对 Fork child 执行 `task new` | **FAIL / Blocker** | exit 2：`invalid: application input is invalid` |
| U13 | 发布、读取、pin、release Artifact | PASS | hash/size/权限正确，retention 正确变化 |
| U14 | 发布 secret-like / 超限 Artifact | PASS | 分别拒绝为 not redacted / budget exceeded |
| U15 | 创建并验证 Backup | PASS | SQLite、FK、manifest、Artifact、credential exclusion 全部通过 |
| U16 | 修改备份内 Artifact 后验证 | PASS | `artifacts_ok=False`，issues 为 changed/restore，exit 2 |
| U17 | Recovery REPL 命令服务 ack + resume | PASS | 旧报告问题已修复；health 最终为 `ok`，模型回合继续 |
| U18 | Recovery CLI 未对账直接 resume | PASS | exit 2，阻止未解决 blocking item |
| U19 | Recovery CLI 自动 command ID ack + resume | PASS | report resolved，Session health 为 `ok` |
| U20 | Full Access Manual Grant 创建、list、stale revoke、正确 revoke | PASS | 风险提示清晰；stale 不写入；撤销后 row_version=2 |
| U21 | 给非 Full Access AgentRun 授权 | PASS | 安全拒绝：requires a Full Access Manual AgentRun |
| U22 | open TaskRun 直接 accept | PASS | 非法状态迁移被拒绝 |
| U23 | stale TaskRun cancel 后正确 cancel，再 replay | PASS | stale exit 2；正确请求和 replay 均一致 |
| U24 | cancelled TaskRun resume | PASS | 终态恢复被拒绝 |
| U25 | open TaskRun 未关闭时 archive Session | **FAIL / High** | archive 成功，留下 archived + open current task |
| U26 | archived Session 创建新 TaskRun | **FAIL / High** | 创建成功，再次形成矛盾状态 |
| U27 | cleanup 默认 dry-run，再 apply 人为 orphan | PASS | 仅删除隔离目录中的未托管测试文件；合法 Artifact 保留 |
| U28 | 从另一 workspace dry-run cleanup | **FAIL / Blocker** | `ws_real` 把 `ws_1/art_1` 列为 eligible=1 |
| U29 | Doctor 检查矛盾 archived 状态 | **FAIL / High** | 未识别 archived + current open task |
| U30 | Doctor 检查 Artifact | PARTIAL | 可验 hash，但把受管 `tmp/` 永久计为 orphan candidate |
| U31 | 3 个 Session、limit=2 的分页 | PARTIAL | 第二页可用手工 cursor=2 获取，但第一页不输出 next cursor |
| U32 | SQLite 被另一连接持有 `BEGIN IMMEDIATE` | PASS | 第二个写命令约 2.76 秒后 `busy`，未无限等待 |
| U33 | 释放锁后复用同一 command ID | PASS | 约 0.35 秒成功，证明失败前未留下假 receipt |
| U34 | 未知 REPL 命令、错误 Task 子命令、无报告 recovery、普通 `/grant` | PASS | 有界中文说明，不修改状态 |
| U35 | missing store 的 `state doctor` | PARTIAL | 正确输出 needs_repair，但进程 exit code 为 0 |

## 4. 已确认问题

### RUT-001：Cleanup 会把其他 workspace 的合法 Artifact 当作可删除 orphan

- 严重度：**Blocker / P0（潜在数据丢失）**
- 复现稳定性：100%
- 影响入口：`morrow state cleanup --workspace-id ... --apply`

实测状态：

- `ws_1` 发布了合法 `art_1`，文件为 `state/artifacts/art_1.artifact`；
- `ws_real` 没有任何 Artifact metadata；
- 在 `ws_real` 执行 dry-run：

```text
dry_run: True
inspected: 2
eligible: 1
removed: 0
refused: 1
```

被列为 eligible 的唯一常规文件就是属于 `ws_1` 的 `art_1.artifact`。本轮为避免数据破坏，没有对
该跨 workspace 场景执行 `--apply`。

根因：Artifact 文件目录由整个 data root 共享，但
[`cleanup.py`](../../src/morrow/application/cleanup.py) 只加载当前 workspace 的 metadata 作为
`known`；[`artifacts.py`](../../src/morrow/adapters/state/artifacts.py) 随后扫描全局 Artifact 目录。
其他 workspace 的合法文件在当前 workspace 的 `known` 中不存在，于是进入 eligible，最终
`path.unlink()`。

影响：

- 一个 workspace 的维护操作可能删除另一个 workspace 的合法 Artifact 字节；
- 被删 workspace 的 metadata 仍存在，之后读取、Doctor 和 Backup 会报告 missing/corrupt；
- CLI 文案称其只删除“已验证的非托管孤儿文件”，实际验证范围不足。

建议：

1. 修复前暂时禁用 `cleanup --apply`，或至少在多 workspace data root 中 fail closed；
2. orphan 判定必须使用 data root 全部 workspace 的 Artifact metadata，或把 Artifact 物理目录按
   workspace 分区；
3. 删除目标必须同时证明“全局无 metadata、全局无 reference、目标仍为同一 inode/private regular
   file”；
4. 增加两个 workspace、各有 Artifact 的真实 cleanup 回归测试。

### RUT-002：Fork Session 无法创建自己的 TaskRun，子分支不可继续

- 严重度：**Blocker / P1**
- 复现稳定性：100%
- 影响入口：Fork 后的 REPL 普通输入、`task new`

Fork 创建本身成功，child 能恢复 6 条父历史消息，父 Session 也没有被修改。但在 child 中发送普通
输入得到：

```text
error: 模型服务发生未预期错误
turn.completed: finish_reason=error, stop_code=internal
```

随后核对：child 自有 conversation records 为 0，current task 为 `None`。直接执行：

```text
morrow task new ses_manual_child ...
invalid: application input is invalid
exit 2
```

根因位于 [`core/domain.py`](../../src/morrow/core/domain.py)：`DurableSession` 的模型校验只要发现
`parent_session_id` 和非空 `current_task_run_id` 同时存在，就报“forked Session cannot inherit a
current TaskRun”。这个模型无法区分“创建 Fork 时错误继承父任务”和“Fork 创建完成后合法创建新任务”，
因此永久禁止 child 拥有任何 current TaskRun。

建议把“不继承父 TaskRun”约束放到 Fork 创建命令中，只校验新 child 初始指针为空；持久化后的 child
必须允许创建自己的 TaskRun。回归测试应从 production bootstrap 恢复 child，再通过
`SessionOrchestrator.dispatch()` 完成一个真实新 Turn，而不能只用 `DurableConversationWriter` 手工追加记录。

### RUT-003：Archived Session 仍允许活跃任务和新任务

- 严重度：**High / P1**
- 复现稳定性：100%

实际得到两种矛盾状态：

1. Session 有 `open` current TaskRun 时，`session archive` 仍 exit 0；
2. Session 已为 `archived` 且 current task 已清空时，`task new` 仍 exit 0。

最终持久化投影示例：

```text
lifecycle: archived
health: ok
current_task_run_id: task_7fR1mT-bsokr3CNC
task status: open
```

[`application/api.py`](../../src/morrow/application/api.py) 的 archive 只拒绝 `deleted`，没有处理 current
open/ready task；[`journal.py`](../../src/morrow/adapters/state/journal.py) 的 task 创建同样只拒绝
`deleted`，没有要求 `active`。Doctor 只检查“Session 指向 terminal task”，没有检查 archived Session
指向活跃任务，所以该矛盾状态不会被诊断出来。

建议明确归档合同：归档前要求无 current task，或者在同一事务中将 current task明确终止/abandon；所有
新 Turn、task new、recovery resume 和普通 resume 都必须要求 lifecycle=active。Doctor 应增加对应一致性
检查。

### RUT-004：Session `updated_at` 不反映对话、任务与 Recovery 更新

- 严重度：**Medium / P2（并发令牌可靠性）**
- 复现稳定性：100%

`ses_1` 在完成多 Turn、接受任务、创建新任务后 conversation position 已到 11，但：

```text
created_at: 2026-08-20T02:17:57Z
updated_at: 2026-08-20T02:17:57Z
```

另一 Session 在 14 秒后完成 Recovery resume，`updated_at` 仍保持创建时间。代码中的任务指针更新和
conversation position 更新写回的是旧 `session.updated_at`；Recovery 保存 health 时也没有提供新时间。

这不仅影响排序/UX：`archive_session(expected_updated_at=...)` 把该字段当作 optimistic stale token，
但中间发生 Turn、Task 或 Recovery 更新时 token 不变，客户端可能用过期快照通过 stale 校验。

建议所有 Session 可观察状态变化使用同一个可注入 clock 更新 `updated_at`，并增加“旧 timestamp 在任意
中间 mutation 后必须 stale”的应用边界测试。

### RUT-005：Doctor/Cleanup 永久把受管 `artifacts/tmp/` 当作 orphan

- 严重度：**Medium / P2**

即使只有一个正常 Artifact，Doctor 也报告 `orphan_candidates=2`：一个是未引用的受管 Artifact，另一个是
Artifact 根目录下必需的 `tmp/` 子目录。扫描 `artifacts_dir.iterdir()` 时，`tmp/` 不匹配
`.artifact` 后缀，被加入 `artifact_id=None` candidate；Cleanup 随后以 `unsafe_target_refused` 拒绝。

结果是健康 Store 长期带有 `artifact_orphan` warning，用户无法区分真实 orphan、可清理 candidate 和受管
目录。应在扫描最终 Artifact 目录时显式排除受管 `tmp/`，并在 Doctor 输出中区分 managed-unreferenced、
unmanaged-removable 和 unsafe-refused。

### RUT-006：Session/Task/Artifact CLI 分页不输出 next cursor

- 严重度：**Low / P2 UX**

创建 3 个 Session 后以 `--limit 2` 列表只显示前两项，没有提示还有下一页，也没有输出 next cursor。
手工猜测 `--cursor 2` 可以取得第三项，但普通用户无法从第一页结果得到该值。Application API 已返回
`next_cursor`，CLI 在迭代 `.items` 时丢弃了它。

建议在有下一页时输出稳定的 `next_cursor`，并为脚本增加 `--json`。Events 使用可见的单调 cursor，受影响
较小。

### RUT-007：Doctor 报 needs_repair 时仍 exit 0

- 严重度：**Low / P2 运维体验**

对不存在的 Store 执行 Doctor：

```text
health: needs_repair
error: store_missing (1) operational store is missing
exit 0
```

输出内容正确，但 CI、备份脚本或健康探针只看 exit code 时会误判成功。若这是刻意的“命令执行成功”语义，
建议提供 `--fail-on warning|error`；否则 health 非 ok 时应使用非零退出码。

### RUT-008：部分错误被过度泛化

- 严重度：Low / P3

- Fork child 的领域校验在 REPL 中显示成“模型服务发生未预期错误”，会误导用户排查 Provider；
- stale Grant revoke 输出 `unavailable`，而 Task stale 输出 `stale`，同类乐观锁错误不一致；
- `task new` 对 Fork child 只显示 `application input is invalid`，没有说明实际 lifecycle/lineage 约束。

建议应用层保留安全、稳定的领域错误 code，同时继续隐藏 traceback 和宿主路径。

## 5. 效率与准确率观察

### 5.1 实测耗时

| 操作 | 实测耗时 | 说明 |
|---|---:|---|
| 首次 CLI Session create | 0.42 s | 包含 `uv run` 与 Python 启动 |
| CLI Session list（5 项） | 0.41 s | 小型 356 KB Store |
| CLI Doctor | 0.24 s | 完整 integrity/FK/领域检查 |
| CLI Backup | 0.25 s | 356 KB DB + 17-byte Artifact |
| Scripted Agent：1 次 read_file + 最终回答 | 6.98 ms | 不含网络模型延迟 |
| Scripted Agent：普通修正 Turn | 2.57 ms | 不含网络模型延迟 |
| 200 次 in-process Session query | 5.39 ms | 约 0.027 ms/次，小型本地数据 |
| 写锁竞争失败 | 2.76 s | 8 次有界重试后 `busy` |
| 锁释放后重试 | 0.35 s | 同 command ID 成功 |

对当前小型 Store，SQLite、Doctor 和 Backup 没有性能阻塞。CLI 冷启动约 0.24～0.42 秒，可接受；写锁
竞争 2.76 秒虽有界，但交互上已有明显等待，建议终端显示“正在等待 Store 写锁/第 n 次重试”。本轮数据量很
小，不能外推到长期数万 Turn/Artifact 的表现；后续应增加 10k/100k records 的 list、restore、doctor 和
backup 基准。

### 5.2 准确率

- 工具准确性：`read_file` 返回的实际 `app.py` 内容、截断字段和 ToolMessage 均正确；
- 状态准确性：正向 Session/Task/Outcome/Recovery/Grant/Backup 断言全部符合预期；
- 安全准确性：secret-like Artifact、跨 workspace 查询、backup path traversal、篡改备份和无授权 Grant
  都 fail closed；
- 模型回答准确率：**未评估**。Scripted Provider 只能验证协议，不能代表真实 Provider 的推理、工具选择、
  幻觉率或长上下文表现；
- 功能完整率：不能用简单百分比表达。35 个场景中多数通过，但 RUT-001 数据风险和 RUT-002 Fork 阻塞使
  Stage 4 的关键声明不能视为完整满足。

## 6. 自动化回归与覆盖缺口

本轮实际运行：

```text
uv run pytest -m 'not live'
611 passed, 2 skipped, 1 deselected in 12.31s
```

两个 skip 是需要在 nested Codex sandbox 外运行的真实 macOS Seatbelt 测试；未运行 live/real-network。

现有自动化全部通过但未发现本报告问题，主要缺口是：

- cleanup 只测试单 workspace，不测试共享 Artifact 目录的跨 workspace 所有权；
- Fork 测试用低层 Writer 向 child 追加历史，没有通过生产 TaskService/AgentLoop 创建 child 的第一个任务；
- archive 测试只断言 lifecycle 变为 archived，没有覆盖 open task、后续 task new 或普通 Turn；
- Session 测试断言 conversation position/current pointer，但未断言 `updated_at` 单调变化及 stale token；
- Doctor 未包含 archived + current task、受管 tmp 目录排除和跨 workspace Artifact 扫描；
- CLI list 测试没有覆盖超过一页时 next cursor 的可发现性；
- missing Store Doctor 的 exit 0 是被现有测试明确接受的行为，需要产品层重新确认语义。

## 7. 建议修复顺序与复验门禁

1. **立即处理 RUT-001。** 修复前不要运行 `state cleanup --apply`；增加跨 workspace 删除保护测试。
2. **修复 RUT-002。** Fork child 必须能通过生产 AgentLoop 完成自己的第一个 Turn，同时父历史不变。
3. **修复 RUT-003。** 原子定义 archive/current task/普通输入的合法状态，并让 Doctor 可检测矛盾状态。
4. **修复 RUT-004。** 统一所有 Session mutation 的 clock/`updated_at`，恢复 stale-token 语义。
5. 修复 Doctor tmp false positive、CLI next cursor 和错误分类/退出码。

建议新增的最小验收门禁：

```text
workspace A publish Artifact
→ workspace B cleanup dry-run/apply 都不能删除或修改 A 的文件

parent Session completed Turn
→ fork child
→ resume child through production bootstrap
→ child dispatch normal input and close Turn
→ child owns new TaskRun/records; parent records unchanged

Session with open/ready TaskRun
→ archive must fail or atomically close according to documented policy
→ archived Session rejects task new / turn submit / recovery resume

capture Session.updated_at
→ append conversation / create task / change health
→ updated_at must increase; old expected_updated_at must return stale
```

修复后应重跑本报告全部操作、Stage 4 聚焦测试、完整非 live suite、Ruff、compileall 和 CLI help。只有
RUT-001～RUT-003 关闭后，才建议重新声明 Stage 4 的 Artifact cleanup、Fork 和 Session lifecycle 已完成。
