# 子计划 2 — Core Chat 执行与实时流

> 主计划：[Chat 工作台补全](../PLAN.md)
> 状态：[ ] 待开始。
> 分支：feat/chat-core-runtime
> 依赖：子计划 1 的接纳、历史、流与所有者合同已验证并集成。
> 对应要求：C01、C05–C07；单工作区服务基础。
> 主要旅程：A02–A05、A10。

## 目标

通过 Core API 完成真实的单工作区多轮对话、查询历史、运行中输入、停止和恢复。
即使尚未配置 Provider，也能启动管理/查询界面。模型调用复用原 AgentLoop，
API 接纳和运行驱动不会重复追加用户消息或阻塞审批/停止。

## 修改归属

- application：新增共享 InteractionService，改造 orchestrator、turns/turn_lifecycle、
  runtime_control 与 AgentRunPreparation 接缝。
- bootstrap：分离 Provider-independent 管理启动与按需 Session 执行组装。
- server：protocol、app/commands、context/composition、host、实时回复投影。
- runtime：仅适配统一接纳 ID、取消来源和受控事件 sink；不复制模型/工具循环。
- tests：建议新增 test_stage8_chat_submission.py、test_stage8_chat_stream.py、
  test_stage8_chat_runtime_control.py；扩展已有 Session/Recovery/Core API 回归。

## 顺序任务

- [ ] 2.1 实现无 Provider/active_model 的管理启动；现有默认工作区与旧 /v1 查询保持可用，
  调用执行时才返回可修复的模型配置错误。
- [ ] 2.2 引入结构化交互提交和回执，将首条 send 的 client_message_id、设置版本与
  run 标识传到实际 turn admission；CLI/GUI 共享应用入口。
- [ ] 2.3 实现幂等接纳与正文/意图摘要；同键重放返回原事实，同键不同载荷返回冲突。
  明确 pending → admitted → running 与 Log append 边界，不能预先双写。
- [ ] 2.4 实现 Session driver 生命周期和停止来源；HTTP 接纳后快速返回，
  不在 mutation bus 里等待模型生成或 ToolApproval。
- [ ] 2.5 接通 steering/follow-up、回执查询、队列显示和未消费撤回。
  每个被消费输入仅经原 Log writer 形成一个 Turn；停止后不意外执行被撤回项。
- [ ] 2.6 从既有 log/turn/task/工具事实构建 Timeline 查询，稳定 ID、来源与分页；
  回答、任务结论、失败和需恢复状态不能混淆。
- [ ] 2.7 提供可见回复 WebSocket 投影和当前草稿快照；实现 epoch/sequence、
  一致水位、提交替换、去重、慢消费者背压和 resync。
- [ ] 2.8 对接原 ApprovalWaiters/ServerApprovalPort，确保模型等待和审批期间仍能处理
  用户停止；关页仅注销订阅，Core 退出使用非用户取消收束。
- [ ] 2.9 接入 SessionRestore/Recovery；对未确认副作用展示恢复入口，
  不用重新发原 prompt 代替恢复。
- [ ] 2.10 运行定向与完整离线检查；保留 scripted client 的持久化/请求次数证据。

## 冻结语义

- 单工作区是本切片范围，路由/ID 必须已能表达工作区，不能依赖浏览器当前选中项。
- 未来附件/非默认生成参数只有在能力实现后才接纳；本切片不得接受后静默忽略。
- 队列是待消费输入，ConversationLog 是聊天权威；消费后的引用和清理按合同完成。
- ApplicationEvent 不承载 text delta/正文；隐藏 reasoning 和原始工具参数不得进入回复流。
- 模型/工具 await 不跨 SQLite 事务；driver 不拿着长事务等待网络。
- 显式用户 Stop 记录真实取消；客户端断线、Core 停机不伪造用户意图。
- Core 崩溃恢复已提交事实，未提交尾部不恢复为成功消息。

## 确定性验证

| 场景 | 必须断言 |
|---|---|
| 两轮发送 | 上一轮上下文进入下一轮，用户/助手记录顺序和数量正确 |
| 同键重试/并发接纳 | 一个 Turn、一个 driver、一次对应的模型执行；异载荷冲突 |
| 掉线发生在提交/首 token/最终提交 | 回执与历史正确，重连无重复拼接或自动重发 |
| steering 与 follow-up 竞争 | 安全点优先级沿用 CLI，消费和撤回唯一 |
| 模型等待时停止/审批 | 命令仍可处理，取消与审批唤醒不重复执行工具 |
| Core 退出/恢复 | 用户取消与进程退出不同，未知副作用由 Recovery 接管 |
| 慢客户端/缺口 | 明确 resync、有限缓冲，不阻塞另一个客户端或 Agent |
| 敏感数据注入 | stream/query/events 均不泄露禁止内容 |

用 asyncio Event/屏障与 fake chunks 控制时序，不用 wall-clock sleep 作断言。

## 验证与退出

运行新测试与现有 session/conversation、recovery、runtime_control、core_api/security、
审批和工作流 host 回归；完成完整离线 pytest、Ruff format/check、compileall、CLI help、
git diff --check。本切片不以浏览器 UI 作为 API 可用的前置。

退出证据必须含真实 Core Host 的脚本化用户旅程及日志/请求计数；不是只测 mock handler。
按主计划记录实际测试文件、命令、结果、限制并关闭；下一子计划为 3。
