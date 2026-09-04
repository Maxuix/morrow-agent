# Stage 8 运行时合同（Runtime Contracts）

> 状态：已接受（2026-09-03）
> 范围：Stage 8 Subplans 1–3（Runtime Kernel 与 Core API）
> 来源：第二轮外部评审 findings 经逐条代码核实后冻结；是 `.agent/PLAN.md` 的一部分权威，
> 不是第二份实施规格——实现细节仍以各子计划为准。

## C1. Lineage 数据模型

三张新表（均在单次 v26 迁移中随 `workflow_runs` 重建一并落地，见 C5）：

```text
workflow_run_execution_nodes        -- 不可变执行集
- workflow_run_id, node_id, topology_ordinal, inclusion_reason

workflow_run_artifact_imports       -- 继承产物（引用，非复制字节）
- workflow_run_id, source_workflow_run_id, source_node_run_id,
  source_node_id, output_slot, artifact_id, contract_json, inherited_at_unix
```

不变量：

- initial run 的执行集等于完整 Revision；continuation 只含 Compiler 闭包后的 Future。
- 每个 execution node 恰好预创建一个 attempt-1 NodeRun；inherited Past 在 child 中不物化
  NodeRun，只通过 imports 表和 parent 引用出现。
- Scheduler 只遍历执行集；child 的完成判定只要求执行集完成，不要求 Revision 全节点有行。
- 唯一的 lineage-aware 读取路径是 EffectiveOutputResolver（ Scheduler readiness、input
  binding、finalizer result 计算、Query/GUI、Backup/Doctor/Cleanup 都经它解析 effective
  output）；禁止各模块自行做 lineage fallback，也禁止把 parent node_run_id 写进 child 的
  `workflow_artifact_bindings`（现有 `bind_artifact` 的 run 内归属校验正确地拒绝这种写法）。
- Query 对 Revision 的有效输出做统一投影，并显式标记 imported output；不能因 child 未物化
  inherited Past 的 NodeRun 而从状态视图中丢失这些输出。

## C2. 单次 admission 事务

Node admission 是不可分割的一个权威事务，其间不得出现 `await`：

```text
校验 run.status == running 且 pause_requested == false
校验 lineage/head 事实（child 未 superseded、root ownership 仍属本 run）
校验无未消费 ReplanSignal（Subplan 8 接入同一断言语句）
校验 node 属于执行集且为 queued
校验用户显式配置的 deadline 与 lineage 剩余 cap（C3；未配置则只记录 accounting）
解析并绑定有效输入（含 inherited imports）
创建/绑定 leaf AgentRun 证据
node queued -> running
写 event / receipt
commit
```

Provider 调用只能发生在 commit 之后。现有"先 `_bind_node_inputs` 后 `_drive_node`"的两段式
结构必须合并，否则 Pause 可在两段之间提交。

Pause 控制写入也遵循同一事务边界：idle RUNNING（无 RUNNING/BLOCKED NodeRun）直接形成
`pause_requested=true,status=paused`，只有仍有 Active 工作才保持 `draining`。当前产品选择是
PAUSED 只允许 resume 或 continuation supersede；若要取消，须先显式 resume，再由前台 owner
执行既有 cancel 流程。

## C3. 可选 Lineage 限制在既有 admission seam 执行

不新建账本表。`admit_model_request` 的 durable `purpose=agent` 行在请求准入时原子写入，本身
就是崩溃安全的 usage/accounting 事实（无法证明 Provider 未收到请求即视为已消耗）。Workflow 或
Node 只有在用户显式设置正数 cap 时才执行预算判定；缺省 `None` 不因请求次数停止。Stage 8 的改动
是把已配置的 Workflow 级判定从"Scheduler 按单 run 预检"移入该既有 seam，并按
`lineage_budget_root_run_id` 统计 continuation 链已消费量（不越过最近的 rerun/new-root 边界）。
request 行需可关联到 budget root（经 run 的 lineage 字段推导，或冗余列——实现时取最简）。
显式 cap/deadline 的放宽或移除只能是用户 exact edit 或已批准 proposal，与 parent 事实一起做 OCC；
未配置 timeout 时 `admission_deadline_at=None`。

## C4. Core Host 单写线程模型

SQLite connection 绑定 owner thread（`operational.py`），不能交给 ASGI worker 直接并发调用。
冻结模型：**单 Core runtime 线程/事件循环 + 有界串行 command bus**。

- HTTP handler 不直接操作共享 journal；mutation command 进入有界单写队列，durable 事务全部
  同步执行；queue 满返回明确 backpressure。
- read projection 在 Core 线程或独立 read-only connection 上执行。
- WebSocket 只推送 `latest_cursor` 通知，客户端从 durable `/events?after=` 拉取；事件流不是
  权威状态。复用现有 workspace monotonic cursor + append-only event + command receipt 机制，
  不新建第二套事件真相。
- 一个 WorkflowRun 至多一个 in-process driver（显式 `RunSupervisor` 持有）；server shutdown
  不得被记录为 user cancellation。

## C5. 迁移框架通用化 + 单次 v26 迁移

现有 runner 只对 version 5 特判 `legacy_alter_table`/`foreign_keys`（`operational.py:628`）。
先给 `SchemaMigration` 增加通用 metadata（`requires_foreign_keys_off`、
`requires_legacy_alter_table`、rebuild 后 `PRAGMA foreign_key_check` + integrity 检查 +
失败恢复），再做**一次** v26 迁移覆盖 Stage 8 runtime 全部 schema：`draining/paused/superseded`
状态、`pause_requested`、`run_relation`、`lineage_budget_root_run_id`、`parent_run_id`、执行集表、
imports 表、`workflow_active_root` 索引重建，以及 C6 的 Revision 编号命名空间分离。这是对
"不提前实现后续子计划 schema"规则的有意
例外：对同一核心表连续重建的风险大于 schema 先行（代码路径仍按子计划逐个落地）。

## C6. Run-local Revision 与 Definition Head 分离

现有 publication 路径会移动 WorkflowDefinition head（`publication.py`）。运行中 Patch 和自动
Replan 产生的 Revision 必须是 **detached**：不可变、有 parent 谱系、仅供该 continuation child
引用，不移动 Definition head、不进入模板列表。只有显式的 "save/publish as definition" 用户命令
才更新 desired source 和 head。

Published Revision 使用正整数 head 序列；detached Revision 使用同一 definition 下单独递减的
负整数序列，0 禁用。两者保留数据库唯一约束但永不争用槽位；detached 的谱系由
`parent_workflow_revision_id` 表达，不以数值相邻推断。

## C7. Retry / Rerun / Continuation 的语义推导

不新增正交字段。两个语义轴从既有事实确定性推导：

| | artifact 继承 | budget root |
|---|---|---|
| `initial` | 无（全新执行集） | 自己 |
| `continuation` | 有（执行集不含 inherited Past） | 继承 parent accounting root |
| `rerun`（failed-node retry） | 有（执行集 = 失败节点 + retained Future） | 新 accounting root |
| `rerun`（full） | 无（执行集 = 全图） | 新 accounting root |

继承与否由执行集是否映射 inherited Past 决定，不需独立列；UI/CLI 对任何新 budget root 明示。

## C8. 风险分类维度

Patch 风险分类不止看权限/预算/角色扩张。以下任一为真即非低风险（默认 approval-required）：
删除用户显式声明的 Reviewer/审批门禁、放宽 output contract、删除用户显式声明的测试/报告依赖、
删除 control edge、改变 Writer 节点顺序、改 required outputs 指向、改 `conversation_scope`、改变
Provider/Model 数据边界、删除用户显式指定的节点、放宽或移除显式 cap/deadline，以及任何无法确定
归类的变化（unknown 默认升级）。角色名本身、通用模板顺序或不存在的默认预算不是风险权限来源。
