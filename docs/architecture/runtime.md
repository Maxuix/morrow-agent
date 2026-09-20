# 运行时与编排

[架构总览](../ARCHITECTURE.md) · [状态与恢复](state.md) · [接口](interfaces.md)

## 普通输入与单 Agent 循环

CLI/REPL 经 `SessionOrchestrator` 调度；Chat 先由 `InteractionService` 校验、绑定模型与设置、
保存接纳回执，再由 Session runtime 消费输入。排队未消费的输入不会伪造 Turn 或 user record。
普通聊天不要求先经过 GraphPlanner，也不额外推断输出合同。

```text
输入校验与接纳 → 获取执行权 → 准备运行并冻结配置/权限/工具
→ 提交 Turn/User → turn.started
→ ContextBuilder 投影 → 请求 admission → Provider 流 → 请求 settlement
→ Assistant（含 tool calls 时先持久化 intent）
→ ToolExecutor 预检/审批/执行 → 有序 ToolMessage → 下一次模型请求
→ 合法 STOP、可恢复中断或确定性失败/取消 → Turn 终态与 turn.completed
```

[AgentLoop](../../src/morrow/runtime/agent.py) 独占模型重试、工具轮次、上下文压缩和聊天追加时机。
`AgentRuntime.run_turn()` 保持薄委托。`ToolCycleExecutor` 负责 handler 入口、审批、超时、取消和
执行状态，不构造聊天历史或公开生命周期。Assistant tool calls 与有序 ToolMessage 构成完整 ToolCycle；
ContextBuilder 不能拆分它，也不修改原始日志。

`SessionPersistence` 是运行时持久化门面；Turn 提交/恢复、权限证据、工具执行和聊天事务分别委托
聚焦 coordinator。`DurableRunCoordinator` 是 loop 看到的显式合同，外部协作者不修改其私有投影。
每次模型请求有独立的 admission/settlement，terminal metrics 从已提交事实聚合，不保存 prompt 或 SDK 对象。
合法无工具 STOP 决定普通回合结束；验证事实用于遥测，不是最终回答的隐藏门禁。

## 上下文、重试与控制输入

[ContextBuilder](../../src/morrow/application/context.py) 从不可变历史和冻结配置生成模型投影。
long-horizon compaction 通过 `pi_compaction` checkpoint 保存摘要与来源范围，只改变模型输入，
不删除、复制或重放 durable 对话。摘要作为明确标记的历史数据进入 User-role；无有效内容或未正常
STOP 的摘要不能推进压缩边界。活动运行的摘要调用使用同一请求账本并标记 `purpose=compaction`；
空闲手动压缩没有新 AgentRun，其用量由 CompactionEntry 保存。

有精确 context-window capability 时按窗口和输出 reserve 计算；否则使用不含图像 Base64
的保守文本字符预算，不伪造 token 窗口。图像 token 按 Provider 与 exact model 的公开计量
（尺寸与模型分档）估算，未知模型使用标明的像素回退；HTTP 传输体积另计全部 Base64，
与模型预算分开。当前输入本身超限、传输体积超限和历史需要压缩使用不同错误提示。
重试只归 AgentLoop，Scheduler 不重复请求。瞬态 Provider 错误可按 RunPolicy
重试，明确余额、配额或计费失败不重试；工具超时和 Artifact 保留上限是独立的每操作边界。
默认值和可覆盖字段以 [runtime-policy.toml](../../src/morrow/resources/runtime-policy.toml) 为准。

模型请求在有限瞬态重试耗尽后， durable Chat/Workflow runtime 会把可恢复的 Provider、内部或
无效流终止写成 `FinishReason.INTERRUPTED`，记录 stop code、请求和已闭合工具调用的安全点，并
保留原 TaskRun/WorkflowRun。普通聊天继续时追加新的 Turn/AgentRun，沿用原模型/权限快照和
累计请求预算；Workflow 继续时在原 NodeRun/叶子 Session 上追加 successor segment，已完成节点
不会重跑。并行只读 frontier 由同一暂停事实唤醒所有活动叶子，各叶子分别闭合自己的 segment。
进程崩溃导致未闭合 Turn 时仍走既有 Recovery 对账边界，未知工具副作用不会被“继续”自动重放。

普通 `runtime_control_queue` 的 steering/follow-up 由
[SessionOrchestrator](../../src/morrow/application/orchestrator.py) 消费：steering 在安全请求边界
检查，follow-up 在正常 STOP 后逐项 drain。消费与下一次 Turn admission 同事务；取消、错误和
Host 关闭不自动消费 follow-up。不能把这一队列与定向节点纠偏或暂停协议混为一谈。

| 控制事实 | Owner | 边界 |
| --- | --- | --- |
| Chat 控制输入 | [ControlReceiptService](../../src/morrow/application/control_receipts.py) | 先持久输入和目标，再分类执行；同 command ID 回放或冲突，不写 ConversationLog |
| 节点纠偏 | [NodeSteerService](../../src/morrow/application/node_steer.py) | 根 Session 授权、图/节点身份绑定、请求边界至多一次消费、过期可见 |
| 执行暂停 | [ExecutionPauseService](../../src/morrow/application/execution_pause.py) | 保存安全点和控制代次；暂停、恢复和取消为不同事实 |
| 可执行控制动作 | [TaskPlanAdmissionService](../../src/morrow/application/workflows/plan_admission.py) | `control_projection()` 提供 durable 状态和 allowed intents，UI 不猜测 |

## Workflow 定义、规划与接纳

Agent desired source 与 Workflow desired source 由 YAML 持有；发布服务生成不可变版本，
Compiler 持有纯校验/编译规则，Repository 不自行生成 Revision 或内容哈希。
`AgentFactory` 绑定编译冻结的 Agent、Model、Skill 和权限要求；普通 Direct 默认路径不经过 Factory。
内置定义是可复制起点，不是额外的角色专用执行协议，默认结果链为 `TextResult@1/result`。

模板编辑与 Chat 当前任务规划是两条入口：

- [WorkflowDraftService](../../src/morrow/application/workflows/drafts.py) 管理可恢复的模板草稿；
  编辑/校验不启动执行，freeze 经 publication 写入 desired source 并发布不可变版本。
- [GraphPlanner](../../src/morrow/application/workflows/graph_planner.py) 基于特征和已授权 Catalog
  生成可编辑模板 Draft；[task_planning.py](../../src/morrow/application/workflows/task_planning.py)
  与 [planning_model.py](../../src/morrow/application/workflows/planning_model.py) 承担 Chat 任务规划。
  任务专用计划经 `plan_admission.py` 校验后才进入执行；生成、发布与开始不能等同。

规划等待 Provider 不占用串行 mutation bus；提交时重读策略/Catalog/版本与约束，拒绝迟到结果。
规划不能发布新 Agent、扩大工具权限或自动伪造用户的开始命令。
[OrchestrationPolicyService](../../src/morrow/application/workflows/orchestration_policy.py) 解析用户
保存的 global/workspace 策略。`auto_run_mode=auto` 的资格由用户策略决定，不再要求 paired-benefit
证据；默认 `approval_only`。模板发布本身仍不启动 Workflow，Chat 任务计划仍走自己的开始准入。

## Scheduler、输出与并行

[WorkflowScheduler](../../src/morrow/application/workflows/scheduler.py) 是 Workflow 唯一图执行器，
在 loop 外组织叶子，叶子仍运行 `run_task()`。`isolated` 节点使用独立 Session 和 `workflow_node`
Task；合法单节点 `invoking_session` 使用根 Session/user Task，根终态由 TurnLifecycle 持有。
`EffectiveOutputResolver` 统一 readiness、输入绑定、查询和结果收口的继承输出语义。

串行是默认行为；显式提高并发只允许已证明的独立只读 frontier。准备和准入屏障由
[parallel.py](../../src/morrow/application/workflows/parallel.py) 协调，逐调用 guard 复核冻结工具
契约。Writer 或无法证明只读的节点不与其他活动节点重叠。叶子即时保存请求、日志和候选产物，
join 后按稳定节点顺序发布 output binding 与节点完成事实；崩溃恢复不重跑已提交结果。
请求 cap/deadline 仅在显式配置时限制接纳，不按任务大小猜测终止条件。

## 暂停、修补与继续

暂停保存 durable intent、安全点和执行 segment，而不保存 coroutine 或 SDK 对象。模型/Provider
故障与用户暂停共用可恢复的 Turn/segment 终态，但保留 `provider_failure` 或
`process_interrupt` 原因，供 UI 区分“等待继续”与普通取消。
Workflow 节点 segment 为追加事实；继续从已提交历史建立后继 segment，不能用清空聊天来恢复。
规划暂停与模型请求 outcome 单独记录，避免把传输完成、内容有效和用户暂停混成一个状态。

图级修改通过 [PatchApplicationService](../../src/morrow/application/workflows/patching.py)：
保留 immutable Past，校验 Future、execution set、依赖、Artifact 来源和 OCC，再创建 detached
Revision 与 continuation child。ReplanCoordinator 只在合适的暂停窗口提案或 handoff；
用户 `allow_low_risk` 策略只允许当前分类仍为 low 的自动应用。扩大权限、角色、模型数据边界或
显式约束的修改需要升级处理；Compiler/OCC 始终执行。

continuation 继承有效输出与 lineage accounting root；rerun 创建新的 accounting root。
失败节点 rerun 包含仍需执行的声明节点，不能静默省略断开分支；full rerun 重新执行完整图。
恢复未知副作用先经原 Recovery 边界，不把 UI 的“继续”解释成自动重放工具。

运行时的持久执行、Workflow 和恢复合同以当前代码与测试为准。
