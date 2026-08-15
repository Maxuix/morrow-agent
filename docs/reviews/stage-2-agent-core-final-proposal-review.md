# Stage 2 Agent Core 审批稿 Review

> 对象：[Stage 2 Agent Core 完整方案（审批稿）](stage-2-agent-core-final-proposal.md)
> 对照：现行权威路线 [`docs/roadmap/stage-2-agent-core.md`](../roadmap/stage-2-agent-core.md)、[`docs/ROADMAP.md`](../ROADMAP.md)、[`docs/ARCHITECTURE.md`](../ARCHITECTURE.md)、Stage 1 已落地代码，以及阶段 3–5 边界草案
> 日期：2026-08-14
> 结论：**Stage 2 本身值得做，这份审批稿不值得按原文成为实施合同。** 方向对，范围对，协议主线大体对；但方案把“无副作用工具循环”写成了准生产 Agent Kernel，并用 9 个模块子计划把可运行闭环推到最后。

---

## 0. 一句话判断

Stage 2 真正要证明的只有一件事：

> 在 Stage 1 连续对话之上，模型能选工具、运行时能执行、结果能合法回写、循环能停、取消能恢复，且不破坏已有交接与事件契约。

审批稿把这件事讲清楚了，也正确拒绝了文件/Shell、MCP、Skills、持久历史和 LLM 摘要。问题不在漏写，而在**为了一次写完后续阶段不会后悔的内核，提前建设了当前 Stage 用不到的策略框架、循环检测、体积会计和模块体系。**

现有权威路线已经偏细，但仍大致贴着工具循环。审批稿相对那份基线，新增的几乎都是“以后可能需要”的完整性，不是当前 Stage 的正确性。

**建议：有条件退回。保留协议、ToolCycle 不变量、取消/失败闭合和 Adapter 组装这几条硬约束；删掉或延后策略栈、循环检测、Cycle 剩余额度分配、Anthropic fixture 和 9 模块实施合同。先做一个可运行的垂直切片，再按真实压力加护栏。**

---

## 1. 总体结论

| 问题 | 判断 |
|---|---|
| Stage 2 要不要做 | 要。路线顺序正确：先受控工具循环，再开放真实本地能力。 |
| 审批稿能不能原文批准并拆 9 个子计划 | 不能。 |
| 核心协议方向对不对 | 对。内部使用 OpenAI-compatible function-calling 子集，差异放 Adapter，是当前代码的自然延伸。 |
| 当前方案是否用尽可能简单的方式解决问题 | 否。正确性约束被一圈生产级框架包住了。 |
| 是否存在必须先改设计再开工的阻塞 | 有。不是“做不出”，而是按原文做会把 2–3 周花在当前 Stage 验证不了的基础设施上，并增加与 Stage 1 运行时的双轨维护成本。 |
| 是否应该改用 LangGraph / OpenAI Agents SDK / Pydantic AI 替换自研循环 | 否。循环本身应该自研且更薄，不是换成另一个框架。 |

这份文档质量很高：边界清楚、失败语义认真、和 Hermes / 后续阶段的隔离写得很自觉。审查结论不是“写得不够完整”，而是**完整过头，并且把完整性误当成了 Stage 2 的准入条件。**

审批稿第二十八节要求在“批准 / 有条件批准 / 不批准”中选一个。对应到实施决策：

- **不批准将本文全文升格为 Stage 2 权威设计。**
- **批准一个收敛后的 Stage 2**：做工具循环，不做人造的 Agent 操作系统。
- 现行 [`stage-2-agent-core.md`](../roadmap/stage-2-agent-core.md) 比审批稿更接近可执行范围；收敛时应往那份基线收，而不是在审批稿上继续加条款。

---

## 2. 最重要的问题与风险

按对技术决策的影响排序。

### P0. 方案服务的是“未来内核”，不是当前 Stage

Roadmap 给 Stage 2 的阶段结果是：

> 一个可以通过工具调用完成简单任务的 Agent

工具被明确限制为无本地副作用。验收任务是查价格、查税率、做一次计算。这是一个很小的闭环。

审批稿同时要求一次建成：

- 9 个模块（M1–M9）和一张接近最终形态的文件树
- 三层策略对象 + 版本化 TOML + ModelRef override
- ProviderCapabilities
- SHA-256 Cycle fingerprint 与 `A B A B A B` 模式检测
- ToolCycle 总体积预算和剩余额度的确定性分配
- 13 个公开 `AgentStopCode`
- `RequestSizer` Protocol
- `ConversationSnapshot` / `TurnTerminalRecord` / 派生 `ClosedToolCycle`
- 独立的 `ModelCallRunner` 模块，并保留 Stage 1 `AgentRuntime.run_turn()`
- 9 个必须先完成、最后才做 E2E 的实施子计划

这些东西在 Stage 3 真实文件/终端、Stage 4 长会话、Stage 6 长任务里可能分别有价值。它们不是让 `lookup_record` + `calculate` 变正确的前提。

一个清楚的错位：演示工具返回几十字节，默认策略却按现代编码 Agent 来配——`max_tool_rounds=30`、`max_run_seconds=1800`、`requested_chars=800000`、`max_tool_calls_per_cycle=32`。方案在用计算器任务给一个尚未存在的编码运行时定规格。

Stage 1 评审已经指出过同一种偏差：文档质量高，但把后面阶段的产品内核提前写成了当前阶段的实现合同。审批稿正在重复这件事。

### P0. 实施顺序把可运行闭环放到最后

批准后的子计划是：

1. Core Protocol
2. Agent Policy
3. Provider Adapter
4. Conversation
5. Context
6. Tool Execution
7. Model Call
8. Agent Loop
9. Integration / E2E

前 7 项都可以在没有一次真实“模型 → 工具 → 模型 → 最终答案”的情况下被标记完成。方案还继承了旧基线里“不得在第一项同时实现完整 Agent Loop”的精神，只是把工作流式顺序改成了更碎的模块合同。

这和 Stage 2 的验证目标相反。Stage 2 的未知数不是 JSON Schema Draft 版本，而是：

- 现有 `AgentRuntime` / `ContextBuilder` / `Session.messages` 能否在不大改 Stage 1 契约的前提下变成多步循环
- 流式 tool-call 碎片能否稳定组装
- 取消和失败会不会留下非法历史
- Handoff / 结构化完成在出现 tool 消息后是否仍然可用

这些只有垂直切片能暴露。按 9 模块推进，最大的风险不是写不出代码，而是**很晚才发现模块边界和现有运行时打架**，然后为了保住已经写完的 M1–M7 继续加抽象。

### P1. 与 Stage 1 运行时的接缝被低估了

审批稿把 ConversationLog 说成“唯一事实源”，`Session.messages` 删除或降为只读视图。这个方向对，但迁移面比方案承认的大。

当前代码里，消息历史不是一个可随手替换的列表：

- `Session.accept_user()` / `accept_assistant()` 是公开可变 API
- `AgentRuntime.run_turn()` 自己写 Session
- `ContextBuilder.build(session, current_user=...)` 仍可能再追加一次当前 user
- `HandoffService._fallback()` 直接扫 `session.messages`，取最后一条 `role=user` / `role=assistant` 的 `content`
- `complete_structured()` 用 `type(context.messages[0])(role="user", content=...)` 构造新消息；审批稿已经禁止这种猜测，但没有给出结构化完成路径的替换方案
- Context 裁剪按 user/assistant 成对单元工作，不认识 `tool` 消息
- `ModelProvider.stream(model, messages)` 还没有 `tools`；`ModelEvent.completed` 也不携带组装后的 AssistantMessage
- 现有 Adapter 对消息做 `model_dump()`，一旦 Message 长出 `tool_calls` / `sequence` 之类字段，就会泄漏到 Provider

审批稿花了大量篇幅定义未来模块的不可变 DTO，却没有把这条**已经存在的调用链**当成第一设计对象。真正危险的不是少一个 `LoopDetector`，而是 Stage 2 上线后：

1. 聊天走 `AgentLoop`，Handoff / 配置提取仍走旧 `run_turn` / `complete()`；
2. 两套消息模型并存；
3. 取消后的 tool 历史让 Handoff fallback 读到 `content=None` 的中间 Assistant，或把工具 envelope 喂给结构化完成。

这是当前仓库里已经能指出来的回归面，不是假想需求。

### P1. 默认上下文预算会先把模型打挂，而不是先保护用户

Stage 1 的 `ContextBuilder.max_chars` 是 24000，偏保守，但和“进程内短会话、字符近似”一致。

审批稿把 `context.requested_chars` 提到 800000，同时：

- 明确 Stage 2 不用真实 token 计数
- `ProviderCapabilities.context_window_tokens` 预留了但没有换算路径
- 未知模型只靠 `unknown_model_fallback_chars=160000`

800k 字符大约对应 200k token 量级。很多 OpenAI-compatible 端点仍是 128k 窗口。如果 `safe_request_chars` 为空，默认策略会把过大请求送出去，然后把 Provider 拒绝包装成 `invalid_response` / `context_budget`。这不是策略完备，是**用一个编码 Agent 的窗口假设覆盖了当前唯一 Adapter 的真实约束**。

Stage 2 会话不持久化。用户极少在单进程 REPL 里把计算器对话堆到需要两阶段压缩的长度。先把字符预算保持在 Stage 1 同量级，或最多放到数万字符，比先建 `RequestSizer` + 旧结果占位清理更重要。

### P1. 循环检测解决的是 `max_tool_rounds` 已经能停的问题

`LoopDetector` 设计完整：规范化 arguments、成功结果做 SHA-256、失败忽略具体 message、排除 call ID、检测长度 1..4 的重复后缀。

对当前 Stage：

- 工具没有外部副作用，重复调用的代价是时间和一点钱
- `max_tool_rounds` / `max_run_seconds` 已经能硬停
- 误判成本高于漏判：一次“查价格失败 → 换 key 再查 → 再算”很容易长得像短模式
- 真正可怕的循环出现在 Stage 3：反复写同一文件、反复跑同一条失败命令

把 SHA-256 fingerprint 和可配置 `repeat_limit` 写进 Stage 2 权威设计，是在为还没出现的失败模式锁定算法。这属于 technically interesting, practically low-value。

### P2. “没有任何待定参数”本身是风险

第二十八节宣布：审批稿不存在实现时再决定的 Stage 2 设计参数。

这把不该锁的东西锁死了：

- 默认预算数字
- fingerprint 算法
- TOML 策略 schema
- Draft 2020-12 与远程 `$ref` 拒绝规则
- Cycle 额度在剩余 calls 之间如何分配
- 9 个模块的文件边界

Stage 1 的教训不是“参数不够早锁”。教训是：**该锁的是不变量（历史合法、取消闭合、密钥不泄漏、Handoff 不自动加载），不该锁的是还没有使用数据的产品与策略选择。** 审批稿把后一类也写成了协议。

---

## 3. 过度设计 / 可以简化的地方

下面这些不是“写错了”，而是**当前 Stage 可以不做，做了会增加理解成本和后续改动成本。**

### 3.1 策略栈可以整段去掉

`DeveloperAgentPolicy` + `EffectiveAgentPolicy` + `ProviderCapabilities` + `adapters/policy/toml.py` + `resources/agent-policy.toml` 是一套小型配置系统。

Stage 2 需要的是几个冻结常量，例如：

```text
max_tool_rounds
max_run_seconds
tool_timeout_seconds
max_tool_result_chars
model_retry_limit
```

测试注入一个 dataclass 即可。生产缺 TOML 就拒绝启动，属于 Stage 7 的发布纪律，不是 Agent 核心正确性。精确 ModelRef override、glob 被明确禁止、自然语言不能改策略——这些讨论的前提是已经有一份用户可编辑的策略文件。现在没有这个需求。

### 3.2 ToolCycle 体积会计对演示工具没有收益

“单结果上限不能保证一次多 call 响应整体可重放，因此要按剩余额度分配 Cycle wire”是编码 Agent 读大文件时的真问题。Stage 2 的三个工具返回短字符串。

一个 `max_tool_result_chars`，外加接纳 Assistant 前拒绝明显过大的 tool-call 消息，已经够用。剩余额度滚动分配是可以等第一次被真实输出打爆再写的算法。

### 3.3 ConversationLog 的记录分类过重

需要保留的不变量只有：

- 消息不能由 CLI / ContextBuilder / Executor 随便改
- assistant.tool_calls 与 tool results 必须成对、顺序一致
- 任意退出都要闭合已接纳的 calls
- 发送 Provider 前再检查一次

不需要为此引入：

- `MessageRecord | TurnTerminalRecord` 联合类型
- 与公开事件完全独立的第二套 sequence
- `ConversationSnapshot.through_sequence`
- 作为日志记录存在、但永不发给模型的 terminal 行

Turn 边界已经可以由“下一条真实 UserMessage”识别。取消或失败且没有最终 Assistant，Stage 1 已经把“孤独 user”当成合法历史单元。TerminalRecord 主要在给 ContextBuilder 提供机械裁剪边界；用 turn 元数据或“从最近 user 起算”就能得到同样结果，不必把内部记账写进事实源。

更简单的形状：`Session` 持有一份只允许 `AgentLoop` 追加的消息元组，外加一个 `assert_history_legal(messages)`。这就是 ConversationLog 的有效载荷，不必先长成存储引擎。

### 3.4 两阶段上下文压缩可以延后

“先把旧 Tool Result 换成占位符，再按 turn / Cycle 硬裁”是长会话技术。Stage 2 不持久化历史，也不调用摘要模型。现有 `ContextBuilder` 已经按原子单元从新到旧裁切，Stage 1 补丁还修过孤儿 assistant。

Stage 2 真正要加的只有：**裁剪单元从 user/assistant 对扩展为 user + 完整 ToolCycle，禁止拆开。** 占位替换、成功/失败 Cycle 的清理优先级、`cleared_cycle_count` 统计，都可以等第一次真正撞上预算再做。

### 3.5 ModelCallRunner 不必先独立成模块

Stage 1 的 `AgentRuntime.run_turn()` 已经会：建 turn、发事件、有限重试、取消、拒绝非 `stop`、拒绝空文本。Stage 2 需要的是把它扩成“一次模型调用可以返回 tool_calls，一次用户目标可以多次调用”。

保留旧 `run_turn()` 再包一层 `ModelCallRunner`，再让 `AgentLoop` 成为唯一写日志的人，会在相当长一段时间里维持两条入口。聊天、取消、重试、可见文本一致性会在两条路径上重复出现。

更简单：用户聊天统一走 `run_task()`；无工具时它就是一次模型调用。`complete()` / 结构化完成 / Provider 探测继续走无 tools 的文本路径。不要长期保留“兼容单轮入口”。

### 3.6 文件树和 Core 拆分过早

`core/messages.py`、`tooling.py`、`modeling.py`、`policy.py`，再加 `application/loop_detection.py`、`application/agent_loop.py`，看起来像终态架构，不像 Stage 2 增量。

当前 `core/models.py`、`runtime/agent.py`、`application/context.py` 已经是可工作的边界。Stage 2 更自然的落点是：

- 扩展现有 Message / ModelEvent / ModelProvider
- 在 `runtime/` 增加 tool registry、executor、loop
- 扩展现有 ContextBuilder

为尚未存在的行为预建模块，直接违反现行架构基线里“不为未来能力预建空模块”。

### 3.7 公开停止码过细

`FinishReason = stop | cancelled | error` 应保留。任务失败时带一个稳定 `error_code` 也合理。但把 13 个 `AgentStopCode` 全部锁进 Stage 2 公开契约，会让终端、测试和后续客户端为还没出现的失败模式提前分支。

Stage 2 够用的公开码大概是：

```text
provider_* / invalid_response / context_budget / budget / internal
```

`loop_detected`、`model_output_limit`、`content_filtered`、把 model/tool limit 拆成两个公开码，都可以先作为内部原因，等终端真的要区分文案再提升。

---

## 4. 可以用成熟方案替代自研的部分

先说不该买的。

### 4.1 不要引入 Agent 框架

LangGraph、OpenAI Agents SDK、Pydantic AI、Claude Agent SDK 都能提供“模型 ↔ 工具”循环。它们不适合现在替换 Morrow 核心，原因很具体：

- Stage 1 已经有自己的 Session、事件信封、ContextBuilder、Handoff、编排层。换框架等于重写，而不是少写。
- OpenAI Agents SDK 默认走向 Responses API、handoff、sandbox、MCP、Skills；这些恰好是本阶段明确排除、后续阶段才讨论的能力。审批稿自己也拒绝了 Responses API。
- LangGraph 的价值在可检查点的多 Actor 工作流。Stage 2 是单用户、单进程、单循环。
- Pydantic AI 和 Morrow 的类型风格接近，但它会夺走 Provider / 消息 / 运行时所有权。Morrow 的产品主张是“核心稳定、边界可替换”，不是再绑一个应用框架。

工具循环在这个代码库里应该是一两百行状态机，不该是新的运行时依赖。

### 4.2 应该直接用现有 Pydantic，而不是再引入 jsonschema 栈

审批稿锁定：

```text
jsonschema>=4.26,<5
Draft202012Validator
check_schema()
拒绝远程 $ref
不启用 format assertions
注册时编译一次
校验错误按 JSON path 稳定排序
```

项目已经依赖 Pydantic。三个演示工具的参数完全可以用普通模型表达，`model_json_schema()` 生成发给 Provider 的 schema，`model_validate()` 校验 arguments。这和现有 Handoff / ConfigPatch 路径一致。

`jsonschema` 作为库并不重，真正重的是把它升级成 Stage 2 的校验平台。动态 JSON Schema 是 Stage 5 MCP / 外部工具才出现的问题。现在为三个内置工具预建 Draft 2020-12 子集，是典型的“为假想注册中心设计”。

### 4.3 流式 tool-call 组装没有可直接替换的库，自研是合理的

OpenAI Python SDK 的 Chat Completions 流仍是按 index 到达的 delta。社区和 SDK 文档里的 accumulator 都是约 40–60 行的手工拼接。没有一个小库能同时满足审批稿里这些不变量：

- 单 choice
- `content: null` 必须显式发送
- id 冲突拒绝
- 原始 arguments 字符串保真
- reasoning 隔离
- 结束后 `completed.message.content` 等于已发 `text.delta`

这块自研成立。它应该是 Adapter 里的一个函数，不是单独的子计划，更不必为尚未接入的 Anthropic 先写双向 fixture。

### 4.4 不要为了字符估算引入新 Protocol

一个与 Adapter 白名单序列化共用的 `len(serialize(messages, tools))` 就够了。`RequestSizer` Protocol 只有在第二个原生 Adapter 出现、且两边序列化确实不同时才有意义。现在只有 OpenAI-compatible。

tiktoken / 官方 token 计数也不该现在加。既然不加，默认预算就必须保守，不能假装已经有 model-aware sizing。

---

## 5. 方向错误或收益不足的部分

### 5.1 循环检测：延后，不要优化

删除 Stage 2 范围中的 `LoopDetector`。不要改 fingerprint，不要先做成“可关的策略项”。`max_tool_rounds` 就是循环检测。

如果实现时发现脚本化 Provider 或 Live 模型会在 8 轮内原地打转，再加一个 20 行的“相同 name + 规范化 arguments 连续 N 次则停”。不要从模式检测起步。

### 5.2 Anthropic 映射契约：现在锁只会腐烂

审批稿反复强调 Stage 2 不引入原生 SDK，却要求用 fixture 锁定 Anthropic `tool_use` / `tool_result` / `is_error` / 连续 user 合并。这是在为不存在的 Adapter 冻结协议。

等真正加原生 Provider 时，对方的块结构和错误字段都以当时 SDK 为准。今天锁的 fixture 大概率要改，却会在心理上变成“已批准契约”。

### 5.3 演示工具集略重，但不构成方向错误

`calculate` + `lookup_record` 已经能证明多步依赖。`current_time` 主要证明注入 Clock，Stage 1 测试基础设施已经有 Clock。保留前两个即可；第三个不是问题，只是可以不做。

把验收故事写成“套餐价格 × 税率 × 三个月”没有错，但不要让这个故事倒逼出 dataset / not_found / 数字格式策略的小型产品面。工具要丑、要确定性、要小。

### 5.4 模块化实施合同是错误的项目管理方向

“先写完所有模块再集成”看起来像工程纪律，实际是在复制 Stage 1 后半段的重量，而不是复制 Stage 1 真正有用的东西（先有可运行对话，再补不变量）。

Stage 2 如果再走 9 个门禁式子计划，很大概率会再次出现：测试很多、协议很完整、第一次 Live 多步任务才暴露编排接缝。

---

## 6. 当前方案真正遗漏的重要问题

审批稿在“未来内核”上过度完整，在“如何改现有程序”上反而有空洞。下面这些问题比 LoopDetector 更影响 Stage 2 能不能安全做完。

### 6.1 Handoff 和结构化完成还当历史是纯文本

`HandoffService._fallback()` 取最后一条 assistant 的 `content`。Stage 2 里最后一条 assistant 经常是：

- 纯 tool call，`content is None`
- 带一句中间话的 tool-call assistant，那句话不是最终答案
- 用户取消后的半截可见文本，最终答案不存在

`complete_structured()` 会把当前 context（审批稿下将包含 tool 消息和 JSON envelope）再加一条 user 指令，要求模型只输出 Handoff JSON。工具结果里的 JSON 会污染抽取；审批稿禁止 `type(messages[0])(...)` 后，这条路径甚至不能按现在的方式构造 prompt。

方案写了“Handoff 可以读 ConversationLog，但仍用独立 Schema”，没有定义：

- 生成 Handoff 时看哪些消息
- 是否剥离 tool envelope
- 是否只保留 user + 最终 assistant 文本
- fallback 在没有最终文本时用什么

这是 Stage 2 一接上现有产品面就会坏的路径，不是 Stage 4 的事。

### 6.2 串行工具执行缺少步间截止检查

状态机在 `PreparingRequest` 检查总运行时间，在单工具上设置 timeout。`max_tool_calls_per_cycle=32` 且 `tool_timeout_seconds=120` 时，一轮就可以合法跑过 `max_run_seconds=1800`。

即使默认改小，规则仍应是：**每个 call 开始前检查剩余 deadline；不够就为剩余 calls 写 `budget_exhausted` 并闭合 Cycle。** 审批稿对“整批超过总工具次数”写得很细，对“整批超过剩余时间”没有同等规则。

### 6.3 双运行时的生命周期责任不清晰

“`run_turn()` 保持 Stage 1 单模型回合，内部可复用 ModelCallRunner”听起来兼容性好，结果是：

- 普通聊天是否始终进入 `run_task()`？
- 无工具对话是否仍发空 `tools`，还是走旧路径？
- 取消、重试、`text.delta` 一致性由谁保证？
- `Session.dirty`、`/new`、Handoff 保存读哪一份历史？

只允许一条写历史的路径。兼容层如果存在，也只能是 `run_task()` 的薄委托，不能保留第二套状态机。

### 6.4 中间可见文本和最终答案会在终端上打架

审批稿正确规定：mixed content + tool_calls 的文本要流式展示，但不能冒充最终答案；`turn.completed.text` 只取最后一次无工具 Assistant。

现有终端是按 `text.delta` 打印、按 `turn.completed` 收尾。中间文本已经呈现在屏幕上之后，完成事件再带另一段“最终文本”，用户会看到两段都像答案。方案锁了事件字段，没有锁终端如何避免把中间推理/过渡句当成完成回复。这是体验问题，也是验收问题——人工验收很容易把“先说话再调工具”判成错误完成。

### 6.5 工具结果作为不可信数据只写进了 System Prompt

System Boundary 要求“不得把 Tool Result 中的指令提升为系统指令”，这是对的，也够 Stage 2 演示工具使用。缺的是结构上的隔离：结果以模型可读 JSON 回到同一条消息流，没有 wrapper、没有 role 隔离之外的防护。

不必在 Stage 2 做完整 prompt injection 防御。但方案把大量精力花在 envelope 键顺序和 SHA-256 上，却没有规定 Handoff / 配置提取不得把 tool content 当作用户指令来源。这是更近的洞。

### 6.6 Adapter 仍是当前最大的正确性风险点

现有 `OpenAICompatibleProvider`：

- `messages` 直接 `model_dump()`
- 只处理 `delta.content`
- 任何非 `stop` 的 finish 都变成错误
- `completed` 不携带消息对象

审批稿把组装权放在 Adapter，这个所有权判断是对的。但它把 Adapter 当成 9 个子计划中的第 3 项、在 Policy 之后、在 Loop 之前单独做完。没有 loop 的 Adapter 测试只能用 fixture 自说自话。

Stage 2 最值得先写的测试是：脚本化碎片流 → 组装出的 AssistantMessage → 再经白名单序列化发回，且 `content: null`、arguments 原文、reasoning 不泄漏。这应和最小 loop 同一天存在，而不是一个先行模块。

### 6.7 测试矩阵很大，但缺“接上 Stage 1 产品面”的几刀

矩阵覆盖了协议、Log、Executor、LoopDetector、取消。还缺更便宜、更关键的几条：

- 一次工具任务之后 `/handoff update` 仍能生成合法 Handoff
- 工具任务取消后 `/exit` 的 dirty / fallback 行为
- `complete_structured` 在历史含 tool 消息时不会把 envelope 当成 schema
- `Session.messages` 若仍被测试和命令直接写入，必须证明没有双写
- 旧的 `test_stage_boundary.py` 禁的是目录名 `loop` / `tools`；换成文件名后要防止“改名绕过”的同时，也要防止边界测试反过来禁止合理的 `agent_loop.py`

---

## 7. 保留、修改、删除、替换、延后

### 保留

- Stage 2 目标：无本地副作用的多步工具循环
- 内部 OpenAI-compatible function-calling 子集
- Provider 差异只留在 Adapter
- Message 联合类型；纯 tool call 显式 `content: null`
- arguments 保持原始字符串，消息层不修
- ToolCycle 作为**不变量**：成对、顺序、不拆、退出必闭合
- 一次响应多个 calls，按原始顺序串行执行
- 工具错误回传模型，Runtime 不自动重试工具
- 一个公开 turn 一次开始、一次完成
- 取消：未完成 Assistant 丢弃；已接纳 Cycle 必须合成闭合
- ContextBuilder 同步、无副作用、不改 Handoff、不做 LLM 摘要
- Conversation 不持久化，`/continue` 不恢复 Log
- `tool.status` 这种最小公开工具事件
- 无副作用演示工具 + 脚本化 Fake Provider 的离线 E2E
- Stage 1 回归

### 修改

- ConversationLog → 追加受限的消息列表 + `assert_history_legal()`，不要记录分类学
- 预算 → 四个数：轮次、墙钟、单工具超时、单结果长度；外加现有模型重试次数
- Context 裁剪 → 只扩展现有原子单元，禁止拆 Cycle
- 运行时 → 聊天只走一条 `run_task()` 路径
- 默认值 → 按演示工具和 Stage 1 会话长度取保守值，不要 30 分钟 / 80 万字符
- 系统提示 → 更新“可以调用本轮提供的无副作用工具”即可，不要顺手写进循环政策
- 实施顺序 → 先垂直切片，再补不变量，最后才考虑策略对象
- Handoff / structured completion → 规定只看见 user 文本和最终 assistant 文本

### 删除或延后到真正出现需求时

| 项 | 去向 |
|---|---|
| `LoopDetector` 与 fingerprint | 删除出 Stage 2 |
| TOML 策略、三层 Policy、ModelRef override | 延后；需要可调预算时用代码内 dataclass |
| `ProviderCapabilities` | 延后到第二个真实 Adapter 或真实 token 限制 |
| ToolCycle 剩余额度分配 | 延后到 Stage 3 大输出工具 |
| 旧 Tool Result 占位清理 | 延后到第一次真实撞预算 |
| Anthropic 双向 fixture | 延后到真正做该 Adapter |
| `RequestSizer` Protocol | 延后 |
| `TurnTerminalRecord` / Snapshot 类型 | 删除；需要时再加 |
| 13 个公开 `AgentStopCode` | 收成很少的 error_code |
| 9 模块文件树作为实施合同 | 删除 |
| `jsonschema` Draft 2020-12 平台 | 见下，替换 |
| `current_time` | 可删，非必须 |

### 替换

| 方案中的选择 | 替换为 |
|---|---|
| `jsonschema` + Draft 2020-12 子集 | 现有 Pydantic 模型：`model_json_schema()` 上线，`model_validate()` 入参 |
| 独立 Policy 模块 | `runtime` 或 `core` 里一个 frozen dataclass |
| 先模块、后 E2E | 先 E2E 切片，模块边界随实现收敛 |
| 保留 `run_turn()` + 新增 `run_task()` | 单一任务入口 |

---

## 8. 若重新收敛这个 Stage，更简单的方案

目标不变：模型能自主走完至少两步无副作用工具并给出最终答案；任意退出不留未闭合 Cycle；Stage 1 不回归。

### 8.1 范围

做：

1. Message 联合类型与显式序列化
2. OpenAI-compatible Adapter：`tools`、碎片组装、finish reason 分型
3. 最小 Registry / Executor：精确匹配、Pydantic 校验、超时、截断、规范错误字符串
4. 一个 `AgentLoop.run_task()`，复用并扩展现有 runtime，而不是旁边再放一套
5. 追加受限的会话消息 + ToolCycle 合法性检查
6. ContextBuilder：裁剪单元包含完整 Cycle；超预算则在调用前失败
7. 预算：`max_tool_rounds=8`，`max_run_seconds=120`，`tool_timeout_seconds=30`，`max_tool_result_chars=8000`
8. 两个演示工具：`calculate`、`lookup_record`
9. `tool.status` 事件；终端只显示名字和状态
10. 离线 E2E 与 Stage 1 回归

不做：策略文件、循环检测、Cycle 额度会计、Anthropic、RequestSizer、TerminalRecord、第二运行时、第三套公开错误分类学。

### 8.2 运行时形状

```text
SessionOrchestrator
  └── AgentLoop.run_task()
        ├── Session.messages   # 只读对外，仅 loop 追加
        ├── ContextBuilder
        ├── Provider.stream(..., tools=)
        └── ToolExecutor
```

状态机保持审批稿里那张小图，但实现就是一个 `while`：

```text
append user
publish turn.started
loop:
    if cancelled or over budget: close and finish
    build context (legal cycles only)
    stream model
    if invalid / provider error: finish error
    if stop and final text: append assistant, finish stop
    if tool_calls:
        append assistant
        for each call:
            if over deadline: synthetic budget result; break
            execute; append result; emit tool.status
        continue
finish exactly once
```

ToolCycle 是校验函数，不是存储类型。

### 8.3 和现有代码的接法

- `Message` 从单类扩成 discriminated union；提供唯一构造入口，修掉 `complete_structured` 的 `type(messages[0])(...)`
- `ModelEvent.completed` 带上组装后的 AssistantMessage 和 `ModelFinishReason`
- `ModelProvider.stream` 增加 `tools=None`
- Adapter 停止 `model_dump()`，改为白名单序列化
- `Session.messages` 不再暴露 `accept_*` 给 loop 以外的人；测试改走 loop 或测试夹具
- Handoff 生成与 fallback 只读取 user 文本和**最终** assistant 文本
- `ContextBuilder` 不再接收可能重复的 `current_user`
- 删除或改写 `test_stage_boundary.py`，禁止的是能力（文件/Shell/MCP/持久对话），不是 `agent_loop.py` 这个文件名

### 8.4 实施顺序

1. **同一条垂直切片**：假 Provider 连续返回两次 tool_calls，再返回最终文本；真的执行 `lookup_record` + `calculate`；历史合法；一组公开事件。
2. **补失败面**：非法 JSON、未知工具、超时、取消、预算耗尽，全部闭合 Cycle。
3. **接 Stage 1 产品面**：之后仍能普通对话、`/handoff update`、取消后再聊、结构化完成不带 tools。
4. **只有切片稳定后**，才考虑要不要把常量提成策略对象、要不要占位清理、要不要更细的 stop code。

不要先建 M1–M7。

### 8.5 仍然值得从审批稿里留下的硬约束

这些写得好，收敛后也应保留：

- 已接纳的 tool call 必须有且只有一个 result
- 半截 stream 不得入历史
- 有可见进展后不得静默重放模型请求
- Runtime 不按 `retryable` 自动重试工具
- 公开事件不带完整参数、完整结果、密钥、traceback、reasoning
- 自动上下文处理不得改 Handoff
- 不为尚未使用的 Provider 预建空 Adapter

---

## 9. 和现行 Stage 2 基线的关系

现行 [`docs/roadmap/stage-2-agent-core.md`](../roadmap/stage-2-agent-core.md) 已经锁定了协议、ToolCycle、ConversationLog、确定性裁剪和 Agent Loop。它偏细，但还没有策略框架、循环检测、Cycle 额度分配和 9 模块合同。

审批稿第二十七节列出的 11 条“批准后对正式路线的替换”，大部分是在把基线往更重的方向推。独立审查的建议相反：

1. Adapter 拥有碎片组装 —— **接受**
2. 工具事件锁定为 `tool.status` —— **接受**
3. 完整 `AgentStopCode` 表 —— **不接受**，只保留很小的 error_code
4. 把 `max_model_calls` 拆成尝试 / 轮次 / 调用三类预算 —— **不接受为 Stage 2 合同**；一轮工具 + 一次模型重试上限足够
5. Policy / TOML / Capabilities —— **不接受**
6. ToolCycle 总体积预算 —— **不接受**
7. 循环检测 —— **不接受**
8. 锁定 jsonschema 与演示工具全集 —— **部分接受**：锁定“无副作用、可脚本化验证”，不要锁校验库和三个工具的产品规格
9. System / Handoff 渲染边界 —— **接受方向**，但必须补上 Handoff 看见什么历史
10. 模块化子计划 —— **不接受**
11. 摘要 / 持久历史 / 真实工具 / MCP / Skills 继续排除 —— **接受**

---

## 10. 最终建议

Stage 2 应该继续，但**不要批准这份审批稿成为权威实现合同。**

下一步不是按 M1–M9 拆子计划，而是用更短的文本重写 Stage 2 范围：一份能在现有 `src/morrow` 上直接开工的垂直切片说明，外加上面那组不变量。现行路线基线可以作为底稿；审批稿里关于 Adapter 组装、公开 turn 生命周期、ToolCycle 闭合和显式序列化的段落可以并回去。其余作为备忘，等 Stage 3/4 的真实压力出现再打开。

如果必须用审批稿自己的选项说话：

> **有条件批准 Stage 2 开工，不批准本文全文。** 条件就是第 7、8 节的收敛。不收敛就开工会重复 Stage 1 的过重规格，只是这次超重的部分更像 Hermes 内核，更不像 Morrow 当前需要证明的东西。
