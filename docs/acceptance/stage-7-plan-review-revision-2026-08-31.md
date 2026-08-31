# Stage 7 计划评审决议与修订记录（2026-08-31）

> 性质：计划评审（plan review）的核实结论与修订映射，不是运行验收。评审对象为当时 main 上的
> Stage 7 总计划与九个子计划；本文件记录每条评审意见是否真实存在、采纳/部分采纳/拒绝的决定及
> 理由，以及修订落在哪些文件。评审原文由 2026-08-31 会话输入提供（结论：有条件通过，综合
> 7.2/10）。

## 一、核实方法

逐条对照 `.agent/PLAN.md`、`.agent/subplans/1-9`（修订前版本，已归档至
`.agent/archive/subplans/stage7-workflow-runtime-v1/`）、`docs/roadmap/stage-7-workflow-runtime.md`
与相关代码事实（`src/morrow/core/domain.py` 的 TaskRun/TaskOutcome 模型、
`src/morrow/runtime/structured.py` 的 `complete_structured`、
`src/morrow/core/skills/catalog.py` 的 SkillVersion、LEGAL_TASK_TRANSITIONS）。

## 二、P0 阻塞项核实与修订

### P0-1 `validate` 写入矛盾 —— 确认存在，已修复

证据：修订前 Subplan 8 任务 4 规定 "`validate` is pure/read-only"，任务 5 与 PLAN.md §4.1 却规定
"首次对内置定义执行 `validate`/`compile`/`run` 都会编译并发布 Revision/head"。两者直接矛盾，CI
中的 validate 会产生数据库写入。

修订：`validate` 永远零写入（不创建 Version/Revision、不推进 Head、不 lazy publish）；只有显式
`publish` 写入；`run` 默认要求 exact 已发布 Revision，唯一例外是显式 `--ensure-published`（回显
写入事实）。CLI 命令由 `workflow compile` 更名为 `workflow publish`。落在 PLAN.md §4.1/§4.7、
子计划 1/3/8、roadmap §4.1/§11.2。

### P0-2 required/optional 工具无声明方 —— 确认存在，已修复

证据：修订前 PLAN.md §4.6、子计划 3 任务 9、子计划 7 任务 2 使用 "optional bash 移除 / required
bash 失败"，但 AgentDefinition 只有 "tool allow/deny names"（子计划 1 任务 2、roadmap §4.1），
没有任何 required/optional 的声明处。

修订：新增 `tool_requirements[]`（name + `required|optional|forbidden`）作为唯一声明模型；
AgentDefinition 声明基础集合，WorkflowNode 只能叠加 restriction-only 覆盖，Compiler 按固定优先级
合并（forbidden > required/optional；required 被拒/静态缺失为编译错误；optional 移除并诊断；
required 的运行时 backend 不可用只失败目标节点）。评审建议的 "SkillVersion 声明附加能力需求" 未
采纳：代码核实 SkillVersion/SkillDefinition 无任何工具声明字段，Skill 带入的工具继续受
Definition 声明集合与 task policy 约束，不为无消费者的字段扩建合同。落在 PLAN.md §4.4、子计划
1/2/3/6、roadmap §4.1/§4.5/§7.4。

### P0-3 冻结 Run 与可变 Head 停用冲突 —— 确认存在，已修复

证据：修订前 PLAN.md §4.1 明确写着 "Disabling an Agent head rejects new leaf admission, including a
not-yet-started leaf of an existing WorkflowRun"，与同一计划的冻结恢复语义矛盾——已准入 Run 的
可执行性会被运行中的外部可变状态改变。

修订：普通 disable 只门禁新准入（新 standalone AgentRun 与新 WorkflowRun Start；Start 校验所有引用
Head enabled），已准入 Run 的未启动节点与恢复不受影响。紧急制动由独立的 additive、带审计、单向
revocation 记录承担（目标为 exact immutable Version/Revision），在 Start、未启动节点准入与 resume
处检查；受影响 Run 以 `cancelled(reason=policy_revoked)` 收口并保留审计证据，不再混入普通执行失败。
落在 PLAN.md §4.1/§4.7、子计划 1/2/4/5/8、roadmap §4.1/§8.0/§8.2/§8.5。

### P0-4 结构化结果权威提交协议缺失 —— 确认存在，已修复

证据：修订前子计划 6 任务 3 与 roadmap §6.3 依赖"解析同一条 final Assistant 消息"构建
EvidenceBundle/ReviewReport 等结构化合同；代码中现成的 `complete_structured` 是"另一次 Provider
请求 + repair"模式，恰是计划明文禁止的，评审指出的 fence/附加文本/缺字段/流中断/崩溃模糊等失败
模式成立。

修订：结构化合同一律经内部机制工具 `submit_node_result(schema_version, outputs, summary,
evidence_refs)` 提交——按冻结 Revision 合同校验、每个 NodeRun 恰好一次有效提交（重放 no-op、冲突
拒绝）、确定性 `(node_run_id, output_slot)` Artifact identity、durable ToolExecution 作为提交事实、
崩溃后由 committer 从 durable 事实完成而不重跑节点、缺失时关闭为
`failed(reason=output_contract_unsatisfied)`。free-text `TextResult` 保留消息包装形态（整条消息即
负载，天然免疫解析类失败）。评审建议签名中的 `terminal_disposition` 未采纳：终态权属既有固定映射
表，提交工具不复制第二份终态权威。落在 PLAN.md §4.5、子计划 4/6/7、roadmap §6.3。

## 三、高优先级问题核实与修订

### 1. `needs_revision` 映射为 root FAILED —— 确认存在，已修复

证据：修订前 PLAN.md §4.6、子计划 6 任务 6、roadmap §8.2 均将 blocking verdict 的 root TaskRun 置为
`FAILED`，仅靠 Outcome 引用与 `result_status` 区分业务否定结论与执行失败，监控/SLO/UI 会误判。

修订（比评审建议更贴合现有模型）：代码核实 `READY_FOR_ACCEPTANCE -> OPEN` 是既有合法迁移，无需新增
TaskRun 状态或 TaskOutcome 字段。`needs_revision` 现在与 succeeded 一样把 root 收口为
`READY_FOR_ACCEPTANCE`，结果快照引用 ReviewReport 并带固定 `completion_basis` 事实
`workflow_result=needs_revision`；用户接受或 resume 后显式开新 Run。原"invoking-session 图不得导出
result-driving ReviewReport"的门禁因其存在理由（无法把同一终态重解释为 FAILED）消失而一并移除，
由子计划 7 直接证明该形态。落在 PLAN.md §4.3/§4.6/§4.7、子计划 2/5/6/7、roadmap §4.7/§8.2/§10.2。

### 2. `required=false` 语义重载 —— 确认存在，且存在计划内自相矛盾，已修复

证据：除评审指出的语义重载外，修订前子计划 7 任务 7 自身矛盾——"fan-out 叶子以 `required=false`
观察输出发布（或作为 Synthesizer 消费的输入）"，但子计划 2/3 规定 binding 只能引用
`required=true` slot。

修订：输出必要性拆为两个独立事实——slot 级 `required_for_node_completion`（完成门禁）与 Revision
级 `required_outputs[]`（导出清单）；binding 与导出都只能引用完成必需 slot，绑定但不导出是正常
fan-in 形态。评审建议的 `InputBinding.on_missing` 未采纳：Stage 7 没有 optional-input 消费者，
生产者完成门禁已是唯一保障机制，不为无消费者语义扩 schema。落在 PLAN.md §4.4、子计划 2/3/8、
roadmap §4.5/§7.2/§10.3。

### 3. disconnected node 仅 warning —— 确认存在，已修复

修订：多节点 Revision 必须是单一弱连通分量；disconnected component 是 compile error 并指名节点与
修法（显式 control edge 或删除）。连通但无人消费的节点保留 warning 且仍执行。评审建议的
`execution_role: observation` 逃逸口未采纳：显式 control edge 已能表达，且四个内置模板都不需要
disconnected 节点，不为无消费者字段扩 schema。落在 PLAN.md §4.4、子计划 3/5、roadmap §7.1/§15。

### 4. 两套状态机风险 —— 部分确认（结构性问题成立，命名不准确），已修复

核实：计划中并不存在名为 DirectWorkflowRunner 的独立运行器；原子计划 4 也明文禁止分叉的
Direct 调度器。但评审的核心判断成立：首个执行闭环（原子计划 4）在 Scheduler 尚不存在时就改造
TurnLifecycle/Turn 准入/acceptance 装配，root 终态从一开始就有两条路径，跨域改动过大。

修订：首个执行切片改为单节点 isolated 图，从第一天起就只有一套
WorkflowScheduler/WorkflowTransitionService/NodeResultCommitter/finalizer；`invoking_session` 作为
Session 绑定策略推迟到串行与 Multi-Agent 稳定之后（新子计划 7 的 Direct adapter）。落在 PLAN.md
§4.3、子计划 4/5/7、roadmap §2.4/§14。

### 5. `invoking_active` 解析时点含糊 —— 确认存在，已修复

修订：新增唯一解析时点契约——`invoking_active` 在消费方制品自己的冻结边界解析一次并永久冻结：
Workflow 为 publish-time freeze（切换 active model 需显式重新发布，旧 Revision 不受影响），
standalone AgentRun 为自己的 admission boundary。同一 Revision 不做 per-run 重新解析（否则同一
Revision 的多次运行不可重放，该策略被明确拒绝）。落在 PLAN.md §4.1、子计划 1/3、roadmap §4.1。

### 6. TextSafetyProfile 改造面过大 —— 评审基于旧版本，当前计划已满足

核实：评审提到 `legacy_marker_aware` profile，但当前计划已是
`legacy_strict | workflow_value_sensitive` 两档，且已要求：合入既有 redaction/refusal 单一 owner 的
共享入口按 profile 分发（不写第四条独立规则）、profile 只持久化在 Artifact/TaskOutcome 两个耐用
envelope 用于 rehydration 兼容、子计划 2 集中审计所有 Workflow 可达调用点并持有一份校准测试集。
评审的诉求（统一 scanner、边界调用、golden tests、独立小步交付）与当前计划一致，无需再改。

### 7. ChangeArtifactCapture 应前置 —— 确认（打包方式），已修复；其 workspace-diff 建议被拒绝

修订：capture 提为子计划 6 的第一个独立门禁任务组，先验收再组装 pipeline。评审建议的"节点提交时
对 workspace 做权威 before/after diff、bash 走边界 diff 兜底"被拒绝：Stage 7 不持有
workspace-global lease，事后整 workspace diff 会把其他进程/Session 的改动错误归因到本节点；bash 的
权威捕获边界是现有 native sandbox 的 `promote_sandbox_changes`，Host-mode bash 对 complete-patch
合同在 handler 前拒绝。落在子计划 6 任务 1、roadmap §6.3。

### 8. 并行整节点最大额度预留过度保守 —— 确认存在，随并行整体移至 Stage 8 一并修复

修订：Stage 7 只串行准入，不存在预留账本。Stage 8 的只读并行条目（8H）采用按请求原子 claim：
frozen node-local cap + Workflow remaining 下按幂等键申领、响应后结算；进入条件为串行 DAG 经过
crash/restart 考验、ToolEffect 分类稳定、rate-limit ownership 明确、按请求 claim 已实现、隔离经过
压力测试、可见性屏障已验证；优先级排在 child-run continuation 之后。落在 PLAN.md §3/§4.6、
roadmap stage-7 §7.5/§19、stage-8 §三/§十五(8H)。

## 四、结构调整（采纳评审第八、十节）

- Stage 7 保留串行 Workflow 与 Multi-Agent；只读并行移出 Stage 7（roadmap stage-7 §十七/§十八
  与 stage-8 8H 同步更新）。
- 子计划重排为 7A 合同（1–3）、7B 可靠串行（4–5）、7C Multi-Agent（6）、7D 产品化（7–8）、
  收尾（9），每阶段有自己的门禁，最终验收不再是单一大爆炸。
- Direct invoking-session adapter 移到 Multi-Agent 之后（7D），不再承担首个闭环的跨域改造。
- doctor/repair UX 打磨后移至管理子计划；Subplan 1/2 保留权威行的最小 backup/doctor 覆盖
  （防止新增权威行处于不可恢复状态），deferred 的只是修复类 UX。
- 总计划 §5 新增唯一写入者矩阵（含 cross-owner 规则）。
- Parallel Research 模板保留在 Stage 7，以串行 fan-in 形态执行；Synthesizer/Planner 随其真实
  模板消费者进入子计划 8。

## 五、未采纳项汇总

| 评审建议 | 决定 | 理由 |
|---|---|---|
| SkillVersion 声明工具需求 | 不采纳 | 代码中 Skill 模型无工具字段，无消费者 |
| `InputBinding.on_missing` | 不采纳 | Stage 7 无 optional-input 消费者；生产者完成门禁已覆盖 |
| `execution_role: observation` 逃逸口 | 不采纳 | 显式 control edge 已可表达；无模板需要 disconnected 节点 |
| 提交工具携带 `terminal_disposition` | 不采纳 | 终态权属固定映射表，不复制第二份权威 |
| 事后 whole-workspace diff 兜底捕获 | 拒绝 | 无 workspace-global lease，会错误归因外部改动 |
| 新增 TaskRun `NEEDS_REVISION` 一等状态 | 不采纳 | 既有 `READY_FOR_ACCEPTANCE -> OPEN` 迁移足够，改动面更小 |

## 六、修订落点清单

- `.agent/PLAN.md`：头部修订记录；§1/§2/§3/§4.1/§4.3/§4.4/§4.5/§4.6/§4.7/§5/§6/§7/§10/§11/§12。
- `.agent/subplans/`：旧九版归档至 `.agent/archive/subplans/stage7-workflow-runtime-v1/`；新九版
  1–9（4 改为 isolated 垂直闭环、7 改为 Direct adapter、并取消原只读并行子计划）。
- `docs/roadmap/stage-7-workflow-runtime.md`：状态头、§2.4、§4.1、§4.5、§4.7、§6.3、§7.1、
  §7.2、§7.4、§7.5、§7.6、§8.0、§8.1、§8.2、§8.3、§8.5、§10.2、§10.3、§11.1、§11.2、
  §十四、§十五、§十六、§十七、§十八、§十九。
- `docs/roadmap/stage-8-adaptive-orchestration-and-gui.md`：§三进入条件、§4.1 模板注记、新增 8H
  有界只读并行切片。
- `docs/ROADMAP.md`：状态头修订注记。
- `.agent/TRACKER.md`、`.agent/LOG.md`、`.agent/subplans/README.md`：执行状态同步。

修订后的计划仍处"Subplan 1 ready、生产未授权开始"状态；本文件不宣称任何实现已完成。
