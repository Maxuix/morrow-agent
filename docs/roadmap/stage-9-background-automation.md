# Stage 9：后台任务与可靠自动化

> 状态：未开始
> 阶段结果：经过验证的 Task/Workflow 可以被一次性或周期性调度，在进程重启、失败和等待审批后可靠恢复
> 上级文档：[开发路线总览](../ROADMAP.md)
> 上一阶段：[Stage 8：自适应编排与 GUI 控制面](stage-8-adaptive-orchestration-and-gui.md)
> 下一阶段：[Stage 10：产品化与 Morrow 1.0](stage-10-productization-and-1.0.md)

## 一、阶段目标

Stage 9 让 Morrow 从“用户保持前台交互时执行任务”演进为“可以可靠托管长任务和周期任务的个人 Agent”。

核心闭环：

```text
用户创建 ScheduleDefinition 或后台任务
→ 校验 Workflow、权限、预算、环境与审批策略
→ 持久化定义和下一次触发时间
→ Local Scheduler 产生 ScheduledRun
→ Worker 创建/恢复 TaskRun 与 WorkflowRun
→ 按检查点执行
→ 需要用户判断时暂停并通知
→ 失败按显式策略重试或进入人工处理
→ 完成后保存 TaskOutcome、Artifact 和通知
→ 重启后从权威状态恢复
```

本阶段不追求无人监督的无限自治，而是：

> **把已经可靠的 Workflow 放进一个可恢复、可暂停、可审计的本地执行环境。**

## 二、进入条件

- Stage 4 的持久化、恢复、Artifact 和副作用对账已经稳定。
- Stage 7 的 WorkflowRun/NodeRun 可以恢复，不会重复 completed 节点。
- Stage 8 的 Command/Query/Event/Approval API 与 GUI 可以观察和控制运行。
- Tool、Skill、MCP 和 Provider 具有明确版本与权限来源。
- 已知哪些操作可以安全重试、哪些需要 reconciliation。
- 用户能够理解 TaskOutcome、预算和实际副作用。

## 三、核心领域模型

### 3.1 ScheduleDefinition

```text
ScheduleDefinition
- schedule_id
- name
- description
- workspace_id
- workflow_definition_id
- workflow_revision_policy
- trigger
- timezone
- input_template
- capability_policy_ref
- approval_policy_ref
- budget
- retry_policy
- concurrency_policy
- enabled
- source
- created_at / updated_at
- next_run_at
```

### 3.2 Trigger

第一版支持：

```text
once(at)
interval(every, anchor)
cron(expression, timezone)
manual-background
```

时间语义必须明确：

- 保存时区，而不只保存 UTC 字符串。
- 夏令时重复/缺失时间有确定策略。
- 机器睡眠或离线期间错过的运行按 `misfire_policy` 处理。

### 3.3 ScheduledRun

```text
ScheduledRun
- scheduled_run_id
- schedule_id
- scheduled_for
- triggered_at
- task_run_id
- workflow_run_id
- status
- attempt
- lease_owner
- lease_expires_at
- result
- next_retry_at
```

### 3.4 Worker

Worker 只负责领取、执行和更新 Run，不拥有业务定义：

- Scheduler 决定何时产生 Run。
- WorkflowRuntime 决定节点如何执行。
- ToolExecutor 决定工具权限和审批。
- Learning Plane 决定是否提出学习候选。

### 3.5 Notification

```text
Notification
- notification_id
- event_type
- severity
- task_run_id / workflow_run_id
- channel
- status
- sanitized_payload
- created_at / delivered_at
```

通知是事件消费者，不进入 Workflow 领域核心。

## 四、进程模型

### 4.1 初始方向：本地单机 daemon + worker

建议：

```text
Morrow Core Daemon
├── API Server
├── Scheduler
├── Worker Pool（受限并发）
├── WorkflowRuntime
└── Operational Store

GUI / CLI
└── 通过本地 API 连接
```

也可以拆分 Scheduler/Worker 进程，但第一版优先减少部署复杂度。具体方案在阶段激活时通过崩溃隔离和平台测试决定。

### 4.2 单实例与租约

- 同一个 Operational Store 同时只有一个活动 Scheduler。
- Worker 领取 Run 使用租约/原子状态转换。
- 进程崩溃后租约过期，Run 进入 recovery，而不是立即重复执行。
- 不使用 last-write-wins。

### 4.3 多工作空间

- 每个 Run 固定 workspace_id 和根路径。
- 工作空间不存在、移动或未挂载时暂停。
- 不因后台模式放宽路径边界。
- 同一工作空间 Writer 并发仍受锁/策略约束。

## 五、任务可靠性

### 5.1 检查点

检查点建立在 Stage 4/7 的 Run 状态上：

- WorkflowRevision。
- completed NodeRun。
- Artifact binding。
- pending/ready 节点。
- 已消费预算。
- Approval 状态。
- 未完成副作用分类。

不保存模型隐藏状态；恢复依赖可重建的 Prompt/Artifact/ConversationLog。

### 5.2 幂等

每个可后台运行的操作应声明：

```text
idempotent
idempotent_with_key
reconcilable
non_idempotent
unknown
```

- Scheduler 创建 Run 使用唯一 trigger key。
- Node attempt 使用唯一 attempt ID。
- Tool call 使用 call ID/idempotency key（若后端支持）。
- 非幂等外部操作默认不能无人值守自动重试。

### 5.3 重试

RetryPolicy 至少包含：

```text
max_attempts
retryable_error_codes
backoff
max_backoff
jitter
budget_cap
requires_reapproval
```

禁止：

- 对认证错误无限重试。
- 对结果未知的写操作直接重跑。
- 每次重试重置总预算。
- 重试覆盖原失败记录。

### 5.4 恢复分类

```text
safe_resume
safe_retry
requires_reconciliation
requires_user
terminal_failure
```

恢复报告必须说明分类依据。

## 六、Approval 与无人值守边界

### 6.1 ApprovalPolicy

后台任务创建时明确：

```text
interactive_only
preapproved_capabilities
approval_lease
always_pause
never_allow
```

### 6.2 预授权不是无限授权

预授权必须限定：

- 工作空间。
- Workflow Revision 或 Definition 范围。
- 工具效果等级。
- 路径/命令/外部目标。
- 金额/调用/Token/时间预算。
- 有效时间。

模型不能在运行中扩大预授权。

### 6.3 Approval Lease

对于重复低风险操作，可选短期 lease：

```text
approval_id
policy_scope
allowed_effects
constraints
valid_from / expires_at
created_by
revoked_at
```

高风险外部操作、删除、发布、支付和权限变更默认不使用持久 lease。

### 6.4 等待用户

后台 Run 进入 `waiting_approval` 或 `waiting_user`：

- 释放不必要资源。
- 保留 checkpoint。
- 发送通知。
- 在 GUI/CLI 显示明确问题。
- 超过期限按策略取消或继续等待，不自行猜测。

## 七、调度语义

### 7.1 Misfire

机器离线时错过触发：

```text
skip
run_once_now
catch_up_limited
require_user
```

默认避免一次性补跑大量周期任务。

### 7.2 Overlap

同一 Schedule 上一次尚未完成：

```text
forbid
queue_one
queue_all_limited
replace（只允许安全可取消任务）
```

默认 `forbid` 或 `queue_one`。

### 7.3 Definition 更新

Schedule 可选择：

- `pinned_revision`：始终使用固定 Revision。
- `latest_validated`：每次运行使用最新已验证 Revision。

默认关键任务使用 pinned。更新不能改变已经创建的 ScheduledRun 快照。

### 7.4 时区与系统时间

- 内部记录 UTC 时间与原始时区。
- 处理系统时钟回拨/前跳。
- 测试使用可控 Clock。
- UI 显示用户时区和下一次准确触发时间。

## 八、后台适用范围

首批适合：

- 定期运行项目测试/静态检查并生成报告。
- 定期汇总工作空间变更。
- 只读依赖/文档检查。
- 长时间只读研究或受控构建。
- 用户显式排队的前台任务转后台。

首批不适合默认无人值守：

- 自动 push/merge/release/deploy。
- 删除或重写大量文件。
- 修改工作空间外个人数据。
- 需要频繁开放式用户判断的任务。
- 无法定义完成和预算的“持续改进项目”。

## 九、预算与资源

### 9.1 Budget

后台 Run 至少限制：

- 总模型调用。
- Token/费用（若可用）。
- 总 wall-clock deadline。
- 节点/工具调用次数。
- 并行 Agent 和进程数量。
- Artifact/日志磁盘使用。
- 网络调用（若允许）。

### 9.2 全局资源治理

- Worker 并发上限。
- 每工作空间 Writer 上限。
- 每 Provider 并发/速率上限。
- 每 MCP Server 并发上限。
- 系统负载或电池策略（可在 Stage 10 产品化）。

### 9.3 预算耗尽

进入：

```text
paused_budget
```

并生成当前进度、已发生副作用、剩余节点和继续所需预算。不得悄悄换便宜模型或重置预算。

## 十、通知

### 10.1 首期渠道

- GUI 内通知中心。
- 桌面系统通知可在 Stage 10 打包后接入。
- CLI 查询。

邮件、消息平台等外部渠道属于后续扩展，不应阻塞 Stage 9。

### 10.2 通知事件

- scheduled。
- started。
- waiting_user。
- waiting_approval。
- budget_exhausted。
- failed。
- recovered。
- completed。

通知只包含脱敏摘要；敏感 Artifact 需回到本地 GUI 查看。

### 10.3 去重与噪声

- 同一状态变化不重复通知。
- Retry 可聚合。
- 周期任务可配置只在失败/变化时通知。
- 通知发送失败不影响 Task 权威状态。

## 十一、后台任务与 Learning

- 每个 ScheduledRun 仍产生 TaskOutcome。
- LearningReview 遵循同样用户策略。
- 周期任务不得每次重复提出同一 Preference/SkillCandidate。
- 无用户接受信号的后台结果不自动成为强正向偏好证据。
- 自动 Skill 更新仍只生成 Draft。
- Schedule 自身修改需要用户明确指令或候选审查。

## 十二、CLI 与 GUI

### 12.1 CLI

```text
morrow daemon start/status/stop
morrow schedule create/list/show/edit/enable/disable/delete
morrow schedule run <schedule-id>
morrow run list/show/pause/resume/cancel/retry
morrow approvals list/resolve
morrow notifications list
```

### 12.2 GUI

新增：

- Schedule 列表和时间线。
- 下一次运行时间和时区。
- Run history。
- Waiting approval/user 队列。
- Retry/Resume/Cancel。
- Budget 和资源使用。
- Notification Center。

GUI 仍通过 Core API，不直接控制系统进程细节。

## 十三、安全与系统集成

### 13.1 Daemon 权限

- 以当前用户权限运行。
- 不默认请求管理员/root。
- 不把 Provider Key 写入服务配置。
- OS 自启动必须显式开启。
- 工作空间访问与交互模式相同。

### 13.2 休眠、关机和网络变化

- 休眠前无需假装完成。
- 唤醒后按 misfire/recovery 处理。
- 网络不可用进入可重试或等待状态。
- 关机中断依赖事务和 checkpoint 恢复。

### 13.3 外部内容

定时读取的外部内容仍是不可信数据，不能通过 Prompt Injection 修改 Schedule、Approval 或长期记忆。

## 十四、实施切片

### 9A：Schedule 与 Scheduler

交付：

- ScheduleDefinition/Trigger/ScheduledRun。
- 可控 Clock、时区、misfire 和 overlap。
- CLI 手动/一次性/间隔触发。

门禁：重启后下一次触发时间一致，不会重复创建同一 Run。

### 9B：Daemon、Worker 与租约

交付：

- Local daemon 进程模型。
- Worker 领取和 lease。
- Core API 连接。
- 崩溃后 Run recovery。

门禁：强杀 Worker 后不会同时有两个执行者运行同一 Run。

### 9C：Checkpoint、幂等与 Retry

交付：

- Workflow checkpoint。
- Tool 恢复分类。
- RetryPolicy/backoff。
- reconciliation 流程。

门禁：completed Node 不重跑，结果未知副作用暂停而不是自动重复。

### 9D：后台 Approval、Budget 与等待用户

交付：

- ApprovalPolicy/Lease。
- waiting_user/approval。
- 预算耗尽暂停。
- GUI/CLI 解决入口。

门禁：无人值守权限不超过创建时预授权，过期授权不会继续使用。

### 9E：通知与周期任务产品面

交付：

- Notification model/center。
- 周期任务历史。
- 失败/变化通知策略。
- 只读项目检查示例。

门禁：用户可创建、观察、暂停、恢复、禁用和删除一个周期任务。

### 9F：可靠性与长期运行验收

交付：

- 进程、机器休眠、网络变化、磁盘错误故障注入。
- 多日模拟 Clock 测试。
- 资源泄漏和磁盘增长测试。
- 运维诊断数据。

门禁：在反复重启和故障下不出现无界重复执行、Run 丢失或状态漂移。

## 十五、测试矩阵

- 同一 trigger 的重复调度。
- 时区和夏令时边界。
- 系统时间回拨/前跳。
- 机器离线后的 misfire。
- 上一 Run 未完成时 overlap。
- Scheduler/Worker/Core 任意点崩溃。
- lease 过期与双 Worker 竞争。
- Tool 结果未知。
- Retry 预算和最大次数。
- Approval 过期、撤销和 Core 重启。
- 工作空间移动/删除。
- Provider/MCP 离线。
- Artifact 磁盘上限。
- 通知失败和重复。
- Disable/Delete Schedule 时已有 Run 的语义。

## 十六、阶段交付物

- ScheduleDefinition、Trigger 和 ScheduledRun。
- Local Scheduler、daemon、Worker 和 lease。
- Workflow checkpoint、恢复、幂等和 Retry。
- 后台 Approval、Budget 和等待用户机制。
- Notification Center 与周期任务 UI/CLI。
- 可靠性故障注入、长期运行和资源测试。
- 示例后台 Workflow 与用户文档。

## 十七、完成标准

1. 用户能创建、查看、编辑、启用、暂停和删除一次性/周期任务。
2. Schedule 在重启后保留，触发不会丢失或重复。
3. Worker 崩溃后不会并发重复执行同一 Run。
4. Workflow 从 checkpoint 恢复，completed Node 不重跑。
5. 结果未知或非幂等副作用不会被自动重试。
6. Retry 有明确错误白名单、次数、退避和总预算。
7. 后台权限不超过创建时的受限预授权。
8. 需要用户时任务可靠暂停并可从 GUI/CLI 继续。
9. 达到预算时暂停并报告进度，不静默超支或换模型。
10. 通知失败不影响 Task 状态，敏感内容不进入通知摘要。
11. 工作空间和 Writer 并发边界在后台仍然有效。
12. 周期任务不会产生无界重复 Learning Candidate。
13. 故障注入和长期模拟中无 Run 丢失、无界重复或状态漂移。

## 十八、明确不包含

- 分布式任务集群和远程 Worker。
- 多租户队列和团队调度。
- 默认无人值守发布、部署、支付或高风险删除。
- 无限持续运行且无完成条件的 Agent。
- 跨设备 Schedule 同步。
- 外部消息渠道全覆盖。
- 用第三方重型编排基础设施替代尚未验证的本地需求。

## 十九、进入 Stage 10 前必须确认

- Daemon、GUI 和 Python Runtime 的安装/升级形态。
- SQLite、Artifact 和 Skill 数据如何备份与迁移。
- 自动更新失败时如何回滚二进制和 schema。
- 各平台的自启动、通知和权限差异。
- 1.0 的性能、稳定性、隐私和兼容性目标。
- 哪些高级能力应默认关闭，哪些适合作为首次体验。
