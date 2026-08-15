# 阶段 2：Agent 核心能力

> 状态：设计基线已锁定，尚未开始实现
> 阶段结果：一个可以通过无本地副作用工具自主执行若干步骤、完成简单任务的 Agent
> 上级文档：[开发路线总览](../ROADMAP.md)
> 上一阶段：[阶段 1：方向确定与可运行原型](stage-1-direction-and-prototype.md)
> 下一阶段：[阶段 3：本地工具与安全](stage-3-local-tools-and-safety.md)

## 一、阶段目标

在阶段 1 的连续对话之上加入最小但完整的工具调用循环，让模型能够选择工具、接收结果并继续推理，直到给出最终答案或触发停止条件。

这一阶段只建立 Agent Loop、统一工具 wire、合法消息历史、预算、中断和确定性上下文裁剪。它把聊天程序变成能够采取受控行动的 Agent，但不授予文件、Shell、Git、网络或其他真实项目操作能力。

## 二、进入条件与当前状态

- 阶段 1 的最终树离线、Live 和人工验收已经通过。
- `AgentRuntime.run_turn()` 的单模型回合、公开事件生命周期、ContextBuilder 权威和 Handoff 边界已经稳定。
- 阶段 2 已解除阻塞；本文的范围和核心契约已经锁定，但生产代码、实施子计划和验收证据尚未开始。

## 三、锁定原则

1. **使用统一的 OpenAI-compatible 工具 wire。** Core 不为每个 Provider 发明一套工具协议；仅在不兼容 Provider 的 Adapter 边界做双向转换。
2. **请求与响应两侧都属于协议。** 除 `tool_calls` 和 `role=tool` 历史外，Core 必须定义请求侧 `tools`。
3. **ConversationLog 是进程内事实源。** 它保存完整但已在执行出口做过体积限制的 tool use/result；ContextBuilder 只构造派生 View。
4. **预算只决定何时缩减以及保留多少。** 不允许在字符或 token 中间切割消息；实际刀口必须落在合法历史边界。
5. **ToolCycle 是硬原子单元。** 一条带 `tool_calls` 的 assistant 消息及其全部对应 ToolMessage 不可拆分；一次响应中的多个 calls 同属一个 Cycle。
6. **tail 预算是选择算法，用户 turn 只是对齐偏好。** 当前用户目标和未闭合 Cycle 受保护；优先按完整 public turn 裁切，必要时退到完整 ToolCycle 边界。
7. **先确定性清理旧工具结果，再硬裁历史。** Stage 2 不调用模型做摘要。
8. **超大工具输出在 Executor 出口截断。** ContextBuilder 不通过拆开 ToolCycle 解决单个结果过大问题。
9. **每个公开 turn 只有一次开始和一次完成。** `tool_calls` 是模型调用结束原因，不是公开 turn 的完成原因。
10. **ContextBuilder 同步、确定性、无副作用。** 它不调用 Provider、不修改 Session、不发布 checkpoint。
11. **自动上下文处理不得修改 Handoff。** ConversationLog、Handoff 和未来的 ContextSummary 是不同对象与权威。

## 四、Stage 2 范围

### 4.1 包含

- OpenAI Chat Completions function-calling 子集的领域模型和显式序列化。
- 不兼容 Provider 的双向 Adapter 映射边界。
- 请求侧工具定义、最小 Tool Registry 和 Tool Executor。
- 进程内 ConversationLog、ToolCycle 配对校验和确定性 terminal 记录。
- 单用户目标、多模型调用的 Agent Loop。
- 多个 tool calls 的协议支持与按调用顺序串行执行。
- 模型调用数、工具调用数、总运行时间、单工具超时和工具输出体积预算。
- 取消、参数错误、工具不存在、超时、执行失败、预算耗尽和内部错误处理。
- 旧 Tool Result 的确定性占位替换和合法历史硬裁。
- 无文件、Shell、网络等副作用的演示工具。
- 覆盖消息协议、循环、裁剪、取消和阶段 1 回归的离线测试。

### 4.2 明确不包含

- 文件读取、搜索、编辑、补丁应用、Shell、Git 或测试执行工具。
- 浏览器、网络搜索、外部系统、MCP、Skills 或插件。
- 工具的真实并行执行；Stage 2 只支持一次模型响应包含多个 calls，并按原始顺序串行执行。
- 模糊工具名修复、call ID 自动改写、Runtime 自动重试工具。
- OpenAI `Responses API`、旧式 `function_call`、`role=function` 或复杂 `tool_choice` 策略。
- ContextSummary、SummaryCheckpoint、LLM 摘要、`/compact`、周期刷新或 artifact 回注。
- 持久化 ConversationLog、完整会话恢复、长期记忆、向量检索或后台任务。
- 不确定副作用跟踪；Stage 2 工具没有外部副作用。
- 为当前不存在的原生 Provider 引入 SDK 或空 Adapter 模块。

## 五、统一工具协议

### 5.1 请求侧 ToolDefinition

```text
ToolDefinition
├── type: Literal["function"]
└── function
    ├── name: str
    ├── description: str
    └── parameters: dict        # JSON Schema object
```

规则：

- Stage 2 只支持 `type="function"`。
- 工具名精确匹配，满足 `[A-Za-z0-9_-]{1,64}`，且 Registry 内唯一。
- `description` 非空。
- `parameters` 是合法 JSON Schema object；注册时先验证定义。
- `ModelProvider.stream(model, messages, *, tools=None)` 接收可选工具集合。
- Agent Loop 的模型调用传入 `tools`；`complete()`、结构化完成、Handoff、配置提取和 Provider 探测保持纯文本，不传工具。
- Stage 2 固定使用 Adapter 侧的 `tool_choice="auto"`，不把选择策略扩展进 Core。

### 5.2 FunctionToolCall

```text
FunctionToolCall
├── id: str
├── type: Literal["function"]
└── function
    ├── name: str
    └── arguments: str
```

- `id` 和 `name` 必须非空。
- 同一 AssistantMessage 中的 call ID 必须唯一。
- `arguments` 必须保持 Provider 返回的原始字符串；消息层不解析、不修复、不重新序列化。
- JSON 解析和参数 Schema 校验只属于 Tool Executor。

### 5.3 Message 联合类型

`Message` 是以 `role` 为 discriminator 的联合类型：

```text
SystemMessage
├── role: Literal["system"]
└── content: non-empty str

UserMessage
├── role: Literal["user"]
└── content: non-empty str

AssistantMessage
├── role: Literal["assistant"]
├── content: str | None
└── tool_calls: list[FunctionToolCall]

ToolMessage
├── role: Literal["tool"]
├── tool_call_id: str
└── content: str
```

AssistantMessage 必须至少包含以下之一：

- 非空 `content`；
- 非空 `tool_calls`。

所有消息变体和嵌套协议对象都拒绝未知字段。Core 提供统一构造入口，不允许调用方使用 `type(messages[0])(...)` 猜测消息类型。

### 5.4 显式 Provider 序列化

领域对象不能直接 `model_dump()` 给 SDK。每个 Adapter 必须通过显式 serializer 只发送目标协议允许的字段：

- 不发送 ConversationLog 的 `sequence`、`turn_id` 或 terminal 元数据。
- 不发送 SDK 原始对象、reasoning 或 Provider 私有字段。
- 纯工具调用 AssistantMessage 必须发送 `"content": null`，不能因为 `exclude_none` 而省略。
- `ToolMessage.content` 始终是字符串，关联字段使用 `tool_call_id`。

### 5.5 Provider Adapter 双向转换

OpenAI-compatible Adapter 直接使用上述子集。不兼容 Provider 在 Adapter 内双向映射，Core 和 Agent Loop 不出现 Provider 名称分支。

Anthropic 返回示例：

```json
{
  "type": "tool_use",
  "id": "toolu_123",
  "name": "calculator",
  "input": {"expression": "12 * 8"}
}
```

Adapter 转为 Core：

```json
{
  "id": "toolu_123",
  "type": "function",
  "function": {
    "name": "calculator",
    "arguments": "{\"expression\":\"12 * 8\"}"
  }
}
```

工具执行后的 Core `role=tool` 消息由 Anthropic Adapter 反向转换为 `tool_result` content block。连续 user 内容按 Anthropic 原生规则合并。

Stage 2 锁定映射契约；只有在加入实际原生 Provider 时才实现对应 Adapter，不提前引入未使用的 SDK 或空模块。

## 六、模型完成语义与流式组装

模型调用结束原因与公开 Agent turn 结束原因分离：

```text
ModelFinishReason =
    stop | tool_calls | length | content_filter | unknown

FinishReason =
    stop | cancelled | error
```

Adapter 私下累计文本和 tool-call argument fragments，对外只把可见文本映射为 `text_delta`。收到真实结束信号后，`completed` 必须携带：

- 完整组装后的 AssistantMessage；
- ModelFinishReason。

接纳规则：

- `stop`：只接纳存在非空 content 且没有 tool calls 的 AssistantMessage。
- `tool_calls`：只接纳 call 结构合法、ID 唯一、名称非空、arguments 为字符串的 AssistantMessage。
- `stop` 但存在非空 tool calls 的兼容响应按 `tool_calls` 处理。
- `length`、`content_filter`、`unknown`、缺失结束信号、空 `stop`、空 calls、重复或缺失 ID、非 function 类型、非字符串 arguments 均不写入 assistant history。
- `completed.message.content` 必须与已发布 `text_delta` 的拼接一致；只有 tool calls 时允许为 `None`。
- 已产生任意文本或 tool-call fragment 即视为已有进展，模型调用不得自动重试。

合法 call 的 arguments 即使不是有效 JSON，AssistantMessage 仍然接纳；Executor 随后生成规范的 `invalid_arguments` ToolMessage。非法 call 结构则是模型无效响应，不接纳 AssistantMessage。

## 七、进程内 ConversationLog

### 7.1 权威与记录类型

ConversationLog 取代可自由修改的 `Session.messages`，成为本次进程内会话的唯一消息事实源。`Session.messages` 应删除或降为只读兼容视图，不能形成双写。

```text
ConversationRecord =
    MessageRecord | TurnTerminalRecord

MessageRecord
├── kind: Literal["message"]
├── sequence: int
├── turn_id: str
└── message: Message

TurnTerminalRecord
├── kind: Literal["turn_terminal"]
├── sequence: int
├── turn_id: str
├── terminal_state: completed | cancelled | failed
└── interrupted_call_ids: list[str]
```

本阶段不加入 `record_id`、`uncertain_outcome_call_ids`、`abandoned` 或持久化字段。

### 7.2 ConversationLog API

```text
ConversationLog
├── records
├── next_sequence
├── active_turn_id
├── begin_turn(turn_id, user_message)
├── append_assistant(turn_id, message)
├── append_tool_result(turn_id, message)
├── close_unresolved_calls(...)
├── finish_turn(turn_id, state)
├── messages_view()
└── reset()
```

规则：

- `sequence` 在 session 内严格单调，与每个公开 turn 内重新计数的 `AgentEvent.sequence` 完全独立。
- 固定 system、Profile、Preferences 和 Handoff 不进入 ConversationLog。
- 调用方不能直接修改 `records`。
- 同一时间只能存在一个 active public turn。
- 新 user、下一条 assistant 或 terminal 写入前，当前 ToolCycle 必须闭合。
- `finish_turn()` 只能在没有未解决 tool call 时成功。
- 取消或失败时先为未完成调用写确定性 ToolMessage，再写 terminal。
- `interrupted_call_ids` 记录因取消或失败而被合成关闭的调用；正常完成时为空。
- `/new` 和 Session reset 清空 ConversationLog；Stage 2 不跨进程加载它。
- Provider 和 ContextBuilder 只能通过明确的消息 View 读取；terminal 记录绝不进入 Provider payload。

### 7.3 TurnTerminalRecord 的边界

TurnTerminalRecord 是内部确定性边界，不是模型消息，也不替代公开 `turn.completed`：

- 它帮助验证 turn 是否闭合，并给 ContextBuilder 提供机械的裁剪边界。
- 取消和失败的具体 call 仍通过合成 ToolMessage 保持 Provider 历史合法。
- 终止状态不写入 Handoff 或任何持久化会话文件。

## 八、ToolCycle 原子性与校验

ToolCycle 是从 ConversationLog 确定性派生的只读结构，不另建第二份存储：

```text
ToolCycle
├── turn_id
├── assistant_sequence
├── assistant: AssistantMessage
├── results: list[ToolMessage]
├── unresolved_call_ids
└── closed
```

`results` 始终按 AssistantMessage 中 `tool_calls` 的原始顺序排列。

一个 public turn 的合法结构是：

```text
UserMessage
→ 0..N 个完整 ToolCycle
→ 可选 Final AssistantMessage
→ TurnTerminalRecord
```

- `completed` 必须有最终的非工具 assistant 文本。
- `cancelled` 或 `failed` 可以没有最终 assistant。
- Stage 2 一次响应可以包含多个 tool calls，但按原始顺序串行执行和写入。
- 未闭合 Cycle 期间不能开始下一模型调用、下一 user 或 terminal。

以下位置都必须运行 ToolCycle 校验：

1. ConversationLog 写入时；
2. ContextBuilder 裁剪后的 View；
3. 实际发送 Provider 前的最终 payload。

写入时使用增量校验：允许刚写入 assistant 后存在一个 open Cycle，也允许按原始 call 顺序追加部分结果。View 完成和 Provider 发送前使用闭合校验，任何 missing result 都必须拒绝。

必须拒绝：

- unknown tool result；
- duplicate tool result；
- 在要求闭合的 View 或 Provider payload 中存在 missing tool result；
- orphan ToolMessage；
- call ID 重复；
- Stage 2 顺序执行路径中的越序 result；
- open Cycle 期间出现新的 user、assistant 或 terminal。

预算、失败和取消路径也必须闭合 Cycle。任何已接纳的合法 AssistantMessage.tool_calls 都必须最终获得一个真实或合成的 ToolMessage。

## 九、运行时职责与 Agent Loop

### 9.1 分层

```text
SessionOrchestrator
└── AgentLoop.run_task()
    ├── ConversationLog
    ├── ModelCallRunner
    ├── ToolRegistry / ToolExecutor
    └── ContextBuilder
```

#### ModelCallRunner

只负责一次模型调用：

- 接收 `messages + tools`；
- 消费 Provider stream；
- 组装文本和 tool-call fragments；
- 返回完整 AssistantMessage 和 ModelFinishReason；
- 不修改 Session；
- 不执行工具；
- 不生成公开 `turn.started` 或 `turn.completed`；
- 只在尚无任何文本或 tool-call fragment 时允许重试。

#### AgentLoop.run_task()

负责一个完整用户目标：

- 一个用户目标只生成一组公开 `turn.started` 和 `turn.completed`；
- 写入 user、assistant、tool 和 terminal 记录；
- 调用工具并管理全部预算；
- 保证所有已接纳 ToolCycle 最终闭合；
- 工具错误默认回传模型，由模型决定是否改参、换工具或结束；
- 达到预算或取消时确定性结束，不进行无上限循环。

#### SessionOrchestrator

- 把普通 chat dispatch 到 Agent Loop；
- 继续拥有命令、配置、Handoff 和 session 转换；
- Handoff、配置提取和 Provider 管理继续使用不带 tools 的结构化或文本完成路径；
- 不直接操作消息列表。

现有 `AgentRuntime.run_turn()` 保留为无工具兼容入口，内部可以复用 ModelCallRunner。不能把它改成隐式多步循环而破坏阶段 1 的单模型回合契约。

### 9.2 状态机

```text
START
  → begin_turn + UserMessage
  → MODEL_CALL
      ├─ stop
      │   → 校验并写入最终 AssistantMessage
      │   → terminal(completed)
      │   → DONE
      │
      ├─ tool_calls
      │   → 校验并写入 AssistantMessage
      │   → EXECUTE_TOOLS
      │       → 按原始顺序执行并写入全部 ToolMessage
      │       → MODEL_CALL
      │
      └─ abnormal/error
          → terminal(failed)
          → DONE
```

### 9.3 取消与失败

```text
模型流式阶段取消
→ 丢弃未完成 AssistantMessage
→ terminal(cancelled)

工具执行阶段取消
→ 保留已完成结果
→ 为其余 call 写 cancelled ToolMessage
→ terminal(cancelled)

ToolCycle 完成后取消
→ terminal(cancelled)
```

模型异常结束不接纳部分 AssistantMessage。工具调用已经接纳后发生的任何退出，都必须先生成合成结果关闭 Cycle。

### 9.4 预算

Stage 2 不使用一个含糊的 `max_steps`，而是分别配置：

- `max_model_calls`；
- `max_tool_calls`；
- `max_run_seconds`；
- `tool_timeout_seconds`；
- `max_tool_result_chars`。

每个 Provider 完成计一次 model call；每个独立 FunctionToolCall 计一次 tool call。具体默认数值在实施计划中确定并由测试锁定。

如果已收到合法 tool calls，但剩余预算不足以执行：

1. 接纳并写入 AssistantMessage；
2. 为未执行调用写 `budget_exhausted` ToolMessage；
3. 写 `terminal(failed)`；
4. 发布公开 error 和唯一的 `turn.completed(error)`。

## 十、Tool Registry 与 Tool Executor

### 10.1 注册结构

```text
RegisteredTool
├── definition: ToolDefinition
└── handler: async (arguments: dict) -> str
```

- Registry 按精确名称查找，不模糊修复或自动改名。
- Handler 只接收已解析、已通过参数 Schema 校验的字典。
- Handler 返回字符串，并支持协作式取消。
- Stage 2 不在线程中运行不可中断的同步函数。

### 10.2 执行顺序

```text
FunctionToolCall
→ 精确查找工具
→ JSON 解析原始 arguments
→ 验证顶层是 object
→ 按 ToolDefinition.parameters 校验
→ 用单工具 timeout 调用 handler
→ 限制输出体积
→ 生成 ToolMessage
```

### 10.3 ToolMessage 结果 envelope

成功结果：

```json
{
  "success": true,
  "content": "96",
  "truncated": false
}
```

截断结果：

```json
{
  "success": true,
  "content": "bounded prefix...",
  "truncated": true,
  "original_chars": 125430
}
```

失败结果：

```json
{
  "success": false,
  "error": {
    "code": "invalid_arguments",
    "message": "expression must be a string",
    "retryable": true
  }
}
```

Envelope 是 ToolMessage 字符串内容的应用约定，不是第二套模型 wire。规则：

- `content` 固定为字符串。
- 截断发生在写入 ConversationLog 之前。
- Log 保存完整的有界权威结果，不保留被截掉的超大原文。
- `original_chars` 只在 `truncated=true` 时存在。
- 错误消息同样受长度限制，并移除 traceback、密钥和敏感内部细节。

### 10.4 错误码

| Code | 默认 retryable | 语义 |
|---|---:|---|
| `invalid_arguments` | true | arguments 不是合法 JSON object 或不满足参数 Schema |
| `tool_not_found` | true | 工具不存在，模型可以选择其他已注册工具 |
| `timeout` | true | 单次工具执行超时 |
| `execution_failed` | false | 工具自身执行失败 |
| `output_limit` | false | Executor 无法形成合法的有界结果 |
| `cancelled` | false | 用户取消 |
| `budget_exhausted` | false | Agent Loop 的调用数或总时间预算耗尽 |
| `internal` | false | Executor 内部错误 |

`retryable` 只是发给模型的提示，Runtime 不根据它自动重试工具。

### 10.5 异常边界

- JSON 解析或 Schema 校验失败时不调用 handler。
- `asyncio.CancelledError` 重新抛给 Agent Loop，不转成 `internal`。
- 单工具超时转换为 `timeout`。
- 已知工具异常转换为 `execution_failed`。
- 未知异常转换为经过清理的 `internal`。
- 无论成功还是失败，每个合法 call 最终都生成一个 ToolMessage。

## 十一、ContextBuilder 与确定性压缩

### 11.1 职责

ContextBuilder 仍是所有用户状态和会话消息进入模型的唯一入口。它只读取 Session 快照和 ConversationLog，不再接收一份可能造成重复的 `current_user`，也不修改事实源。

Stage 2 的“压缩”只包含确定性占位替换和合法历史硬裁，不包含摘要。

### 11.2 预算估算

Stage 2 继续使用确定性的 `max_chars` 近似。估算对象是实际请求 wire，而不是只计算 `message.content`：

```text
request_size =
    serialized system messages
  + serialized history messages
  + serialized ToolDefinition list
```

必须计算 tool call ID、名称、原始 arguments、ToolMessage content、nullable assistant content 和参数 Schema。未来使用上一次 API 返回的 prompt token 作为权威触发属于后续上下文阶段。

### 11.3 构建管线

```text
ConversationLog
→ 提取 MessageRecord
→ 加入固定 System Boundary 与 Profile / Preferences / Handoff 状态
→ 校验原始 ToolCycle
→ 是否超过预算？
    ├─ 否：直接返回
    └─ 是：
        → 从最老的已闭合 ToolCycle 开始清理结果
        → 仍超预算则裁掉最老的合法历史前缀
        → 校验最终 Provider payload
```

### 11.4 旧 Tool Result 清理

清理单位是完整的已闭合 ToolCycle，而不是单条 ToolMessage。一个 Cycle 被清理时，其中所有 ToolMessage.content 同时替换为确定性占位符：

```text
[tool result omitted from active context: budget]
```

保留 assistant.tool_calls、每个 tool_call_id、ToolMessage 数量与原始顺序，因此协议仍合法。替换仅存在于本次 View，ConversationLog 原始结果不变。

清理顺序：

1. 最老的成功 Cycle；
2. 最老的失败 Cycle；
3. 当前未闭合 Cycle 永不清理。

### 11.5 硬裁规则

清理后仍超预算，才执行硬裁：

- 从最老的合法历史前缀开始，不在字符中间切割。
- 优先落在完整 public turn 边界；必要时退到完整 ToolCycle 边界。
- 只要保留某个 turn 的 assistant 或 ToolCycle，就必须同时保留该 turn 的原始 UserMessage。
- ToolCycle 内部绝不裁切。
- 不留下孤立 assistant 或 ToolMessage。
- 不跳过较新的历史去保留更老的历史。
- 当前 turn 中较老且已闭合的 Cycle 可以整体裁掉，但当前 UserMessage 必须保留。

保护集：

- 固定 system messages；
- 当前 active turn 的原始 UserMessage；
- 当前未闭合 ToolCycle；
- 当前 Provider 请求所需的 ToolDefinition。

如果保护集本身超过预算，抛出明确的 `ContextBudgetExceeded`；不能拆 Cycle、丢当前目标或回退到更老历史。

### 11.6 最终校验

实际发送 Provider 前重新校验：

- 每个 tool call ID 唯一；
- 每个 assistant tool call 有且仅有一个对应结果；
- ToolMessage 顺序与原始 calls 一致；
- 没有 orphan result 或未闭合 Cycle；
- 没有 terminal 或内部日志字段；
- 当前 UserMessage 只出现一次；
- 请求字符数不超过预算。

## 十二、System、Handoff 与状态边界

固定 System Boundary 需要在 Stage 2 实现时更新：继续声明权限和用户状态边界，但不能再声称 Agent 完全不能采取行动；它只能使用本阶段注册的无副作用工具。

Profile、Preferences 和已显式加载的 Handoff 继续作为用户状态数据进入固定上下文，不构成工具授权。

必须保持：

- ConversationLog 不写入 Handoff YAML。
- 自动清理或硬裁不修改 Handoff。
- Handoff 生成可以读取 ConversationLog，但仍沿用独立 Schema 和显式保存流程。
- `/continue` 加载 Handoff 不恢复旧 ConversationLog。
- Stage 2 不引入 ContextSummary，也不让后台状态改写 Handoff.current_goal。

## 十三、公开事件与终端体验

- 一个 `run_task()` 必须恰好发布一个 `turn.started` 和一个 `turn.completed`。
- `turn.completed.finish_reason` 继续使用 `stop | cancelled | error`。
- 中间模型调用结束不发布 public turn completion。
- 可见文本继续使用 `text.delta`，不得暴露 reasoning 或原始 SDK fragments。
- 工具步骤至少通过现有可扩展事件机制发布工具名称、call ID 和开始/结束状态；不把完整参数、超大结果、异常堆栈或秘密写入公开事件。
- 工具错误通常作为 ToolMessage 回传模型，不自动变成 public error；只有循环无法继续、预算耗尽或模型响应无效时才以 public error 结束。
- 消费者继续忽略未知事件类型和字段。

具体工具事件名称与终端文案在实现子计划中锁定，但不得改变上述生命周期和数据边界。

## 十四、建议实施顺序

1. **消息与 Provider wire**：Message 联合类型、ToolDefinition、FunctionToolCall、ModelFinishReason、显式 serializer 和 Adapter fragment 组装。
2. **ConversationLog 与 ToolCycle**：唯一事实源、受控写入、terminal、配对校验和 Session 兼容迁移。
3. **确定性 ContextBuilder**：完整请求估算、旧结果清理、合法硬裁和最终 payload 校验。
4. **Tool Registry / Executor**：参数验证、结果 envelope、输出限制、超时、取消和演示工具。
5. **Agent Loop**：多模型调用状态机、独立预算、公开事件和失败闭环。
6. **端到端与回归验收**：正常多步、全部失败路径、取消恢复和阶段 1 行为。

实施前应把本顺序拆成独立子计划；不得在第一项同时实现完整 Agent Loop。

## 十五、验收测试矩阵

### 15.1 协议与 Adapter

- 请求侧 tools 序列化，且纯文本 `complete()` 不携带 tools。
- 纯文本、纯 tool calls、混合文本与 tool calls 的流式 fragment 组装。
- 多 call arguments 交错 fragment 的正确归并。
- `content: null`、原始 arguments 保真、显式字段白名单和 reasoning 隔离。
- 合法 finish reason 与 abnormal/missing finish 的接纳差异。
- OpenAI-compatible round trip；Anthropic 映射契约使用 fixture 验证，不要求当前引入原生 SDK。

### 15.2 ConversationLog 与 ToolCycle

- 单 call 和单响应多 call 的完整 Cycle。
- unknown、duplicate、missing、orphan 和越序 result 拒绝。
- 新 user/assistant/terminal 不能越过 open Cycle。
- 取消、失败和预算耗尽先合成结果，再写 terminal。
- terminal 不进入 Provider payload。
- `/new` 和 reset 清空 Log；不跨进程恢复。

### 15.3 ContextBuilder

- 不修改 ConversationLog 或 Session。
- 工具定义、arguments 和结果都计入预算。
- 先清理最老完整 Cycle，再硬裁最老合法前缀。
- 不拆 ToolCycle，不产生 orphan assistant/result。
- 长 active turn 可裁旧 closed Cycle，但保留当前 UserMessage。
- 保护集超预算时在 Provider 调用前失败。
- 裁剪后的实际 wire 再验证且不超预算。

### 15.4 Tool Executor

- 正常结果、截断结果和 original_chars。
- 非 JSON、非 object、Schema 不匹配和未知工具。
- timeout、执行异常、内部异常、取消和输出上限。
- `retryable` 不触发 Runtime 自动重试。
- 一个合法 call 始终产生一个 ToolMessage。

### 15.5 Agent Loop 与回归

- 一个用户目标经过至少两个工具步骤后给出最终答案。
- 一次响应包含多个 calls，按原始顺序执行并整体闭合。
- 模型调用数、工具调用数、总时间、单工具超时和输出体积预算。
- 模型流中取消、首个结果前取消、部分结果后取消、Cycle 闭合后取消。
- 一个任务只产生一组 public start/completion。
- 工具失败可被模型消费并继续，不崩溃或无限重试。
- 工具任务结束后仍可正常对话。
- 阶段 1 的十轮对话、空响应、异常 finish、重试、取消、配置、结构化完成、Handoff、工作空间隔离和退出行为继续通过。
- `tests/test_stage_boundary.py` 中阻止预建 Stage 2 模块的旧守卫必须被更精确的阶段边界测试替代，不能通过改名绕过。

## 十六、阶段交付物

- OpenAI-compatible 工具消息和请求协议。
- Provider Adapter 的显式序列化与流式 tool-call 组装。
- 进程内 ConversationLog 和 ToolCycle 校验。
- 独立于具体 Provider 的 Agent Loop。
- 最小 Tool Registry、Tool Executor 和无副作用演示工具。
- 模型、工具、时间、超时和输出体积预算。
- 确定性的旧结果清理和合法历史裁剪。
- 中断、失败闭环和终端步骤状态。
- 完整离线测试与阶段 1 回归证据。

## 十七、阶段完成标准

- Agent 能从一个用户目标出发，自主完成至少两个工具步骤并给出最终答案。
- 每个已接纳 tool call 都有且只有一个对应 ToolMessage，任何退出路径都不留下未闭合 Cycle。
- OpenAI-compatible Provider 收到的请求只包含合法 wire 字段；Adapter 内部元数据不泄漏。
- 参数错误、未知工具、超时、执行失败和输出截断都形成规范结果并可回传模型。
- 达到模型、工具、时间或输出预算时能够安全停止并说明原因。
- 用户可以在模型或工具阶段中断任务，之后继续正常对话。
- ContextBuilder 在预算内保留最近的合法完整轨迹，不修改原始日志，不调用摘要模型。
- ConversationLog 仅存在于当前进程，不自动修改或替代 Handoff。
- 阶段 1 的配置隔离、结构化完成、显式交接、事件生命周期和安全边界没有回归。
- 默认测试离线通过，且没有加入 Stage 3/4/5 的能力。

## 十八、实施计划仍需确定的参数

以下内容不改变本文架构，可以在 Stage 2 实施子计划中确定：

- `max_model_calls`、`max_tool_calls`、`max_run_seconds`、`tool_timeout_seconds` 和 `max_tool_result_chars` 的默认值。
- 演示工具的最小集合及其参数 Schema。
- ToolDefinition 参数 Schema 使用的具体校验库。
- 工具步骤公开事件的最终名称和终端文案。

上述参数确定前可以拆分实施子计划，但不能绕过本文已经锁定的协议、原子性、职责和范围边界。

阶段 2 通过后，才向 Agent 开放真实的本地项目操作能力。
