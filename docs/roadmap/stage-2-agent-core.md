# 阶段 2：Agent 核心能力

> 历史说明：本文记录阶段 2 当时的实现与边界；其中 Handoff 相关能力已在阶段完成后移除。

> 状态：已完成；Subplans 17–20 与最终树验收通过（2026-08-17）
> 阶段结果：一个可以通过无本地副作用工具自主执行若干步骤、完成简单任务的 Agent
> 上级文档：[开发路线总览](../ROADMAP.md)
> 上一阶段：[阶段 1：方向确定与可运行原型](stage-1-direction-and-prototype.md)
> 下一阶段：[阶段 3：本地工具与安全](stage-3-local-tools-and-safety.md)
> 历史执行计划：保留在 Git commit `831c4ea` 的 `.agent/PLAN.md`

## 一、阶段目标

在阶段 1 的连续对话之上加入最小但完整的工具调用循环，让模型能够选择工具、接收结果并继续推理，直到给出最终答案或触发停止条件。

这一阶段只建立 Agent Loop、统一工具 wire、合法消息历史、预算、中断和确定性上下文裁剪。它把聊天程序变成能够采取受控行动的 Agent，但不授予文件、Shell、Git、网络或其他真实项目操作能力。

## 二、进入条件与当前状态

- 阶段 1 的最终树离线、Live 和人工验收已经通过。
- `AgentRuntime.run_turn()` 的单模型回合、公开事件生命周期、ContextBuilder 权威和 Handoff 边界已经稳定。
- 阶段 2 的四个垂直实施子计划已完成。最终代码审查修复后，最终树 300 个离线测试通过、严格收集 301 个测试；包安装、真实终端产品流、能力/副作用边界与哨兵扫描通过。可选 Live 因未提供明确凭据而未运行，未被计为通过或失败。完整证据见 [Stage 2 Acceptance Evidence](../acceptance/stage-2-evidence.md)。

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
- 模型尝试、工具轮次、总调用、单 Cycle 调用、总运行时间、单工具超时、上下文、单结果和单 Cycle 体积预算。
- 重复 ToolCycle 的提前止损，以及独立的硬轮次兜底。
- 取消、参数错误、工具不存在、超时、执行失败、预算耗尽和内部错误处理。
- 旧 Tool Result 的确定性占位替换和合法历史硬裁。
- Handoff、StructuredCompletion 和确定性 fallback 的安全历史投影。
- 开发者 TOML 策略、精确 ModelRef 安全请求上限和 Provider 工具能力声明。
- `tool.status`、精确停止原因和 mixed-content 终端分段。
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
- 把 Agent 策略暴露为用户 Preferences、Profile、Handoff、CLI 或自然语言配置。

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
└── tool_calls: tuple[FunctionToolCall, ...]

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
├── sequence: int
├── turn_id: str
└── message: Message

TurnTerminalRecord
├── sequence: int
├── turn_id: str
├── terminal_state: completed | cancelled | failed
└── interrupted_call_ids: tuple[str, ...]
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
├── finish_turn(turn_id, state, interrupted_call_ids)
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
├── results: tuple[ToolMessage, ...]
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
- 消费 Adapter 已归一化的 Provider stream，转发可见文本；
- 接收 Adapter 完整组装的 AssistantMessage 和 ModelFinishReason，不解析 SDK fragments；
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

所有普通聊天从第一个实施切片起都进入 `AgentLoop.run_task()`。现有 `AgentRuntime.run_turn()` 如果为了兼容保留，只能薄委托 `run_task(..., tools=empty)`；不得保留独立的 Session 写入、重试、取消或公开生命周期。StructuredCompletion、Handoff 和 Provider 探测继续使用无 tools 的 `complete()`，不属于聊天状态机。

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

这一不变量从 Slice 1 首个工具 E2E 起成立：用户取消时为所有 unresolved calls 按原顺序写 `cancelled`，接纳 batch 后发生未预期 Runtime/Executor 异常时写有界 `internal`；保留已完成结果，随后才允许 terminal 或下一 User。Slice 3 只扩展完整 commit-point、deadline、`budget_exhausted` 与公开状态语义，不能把最小闭合推迟到最终护栏阶段。

### 9.4 预算

Stage 2 不使用一个含糊的 `max_steps`。所有数值来自随包 `resources/agent-policy.toml`，由标准库 `tomllib` 和现有 Pydantic 解析为严格、冻结的 `AgentPolicy` / `RunPolicy`。测试可以注入更小策略；这些字段不进入任何用户设置面。

```text
AgentPolicy
├── max_tool_rounds
├── max_model_attempts
├── max_tool_calls
├── max_tool_calls_per_cycle
├── max_run_seconds
├── tool_timeout_seconds
├── model_retry_limit
├── requested_context_chars
├── unknown_model_fallback_chars
├── max_tool_result_chars
├── max_tool_result_request_ratio
├── max_tool_cycle_chars
├── max_tool_cycle_request_ratio
├── max_validation_errors
├── loop_detection_enabled
├── loop_repeat_limit
└── loop_max_pattern_cycles
```

初始默认值锚定现代 Agent 的多步能力，属于可由实现/Live 证据调整的开发者策略：30 个工具轮次、40 次模型尝试、128 个工具调用、每 Cycle 32 个调用、1800 秒总时间、120 秒单工具上限、1 次模型重试、800000 请求字符目标、160000 未知模型 fallback、64000 单结果字符、0.10 单结果比例、256000 单 Cycle 字符、0.35 Cycle 比例、3 条验证错误、重复检测开启、重复阈值 3、最大模式长度 4。

每个真实 Provider 请求（包括重试）计一次模型尝试；每个独立 FunctionToolCall 计一次 tool call；每个已闭合 batch 计一个 tool round。每个 call 开始前重新计算剩余总时间，有效 timeout 是单工具上限与剩余时间的较小值。

Provider/Model 能力只声明：

```text
ProviderToolSupport
├── tool_protocol: none | openai_function
├── multiple_tool_calls: bool
└── safe_request_chars: int | None
```

`tool_protocol` 与 `multiple_tool_calls` 来自 Adapter 注册信息；`safe_request_chars` 只按随包 TOML 中精确 `provider_id/model_id` 查找。未命中时为 `None`，不按名称或 token window 猜测。首版生产模型表为空，全部真实模型先使用 160000 unknown-model fallback；只有获得精确 ModelRef 的 Live 证据后才增加生产条目，单元测试的 exact-hit 数据只存在于注入策略。

```text
effective_request_chars =
    min(requested_context_chars,
        safe_request_chars if present else unknown_model_fallback_chars)

effective_result_limit =
    min(max_tool_result_chars,
        floor(effective_request_chars × max_tool_result_request_ratio))

effective_cycle_limit =
    min(max_tool_cycle_chars,
        floor(effective_request_chars × max_tool_cycle_request_ratio))
```

如果已收到合法 tool calls，但剩余预算不足以执行：

1. 接纳并写入 AssistantMessage；
2. 为未执行调用写 `budget_exhausted` ToolMessage；
3. 写 `terminal(failed)`；
4. 发布公开 error 和唯一的 `turn.completed(error)`。

单响应 call 数超过 `max_tool_calls_per_cycle` 时不接纳 Assistant。合法 batch 超过剩余总调用额度时接纳 Assistant，但不执行任何 call，为整组写 `budget_exhausted` 并闭合。若 Assistant tool-call wire 已大到无法为每个 call 容纳最小 envelope，则不接纳 Assistant，以 `model_output_limit` 结束，并在内部记录 `stop_detail=tool_cycle_too_large`。

各预算彼此独立。PreparingRequest 的固定顺序是取消 → run deadline → model attempts → tool rounds → context；总 tool calls 与 per-cycle calls 在新 batch 接纳时检查。先命中的项目决定 stop code，因此有重试时 40 次模型尝试可以有意先于 30 个工具轮次耗尽。策略必须满足 `loop_repeat_limit × loop_max_pattern_cycles <= max_tool_rounds`，保证最长配置模式仍能在硬轮次上限前被检测。验收必须覆盖这两种组合行为，不能只逐项测试单一上限。

## 十、Tool Registry 与 Tool Executor

### 10.1 注册结构

```text
RegisteredTool
├── name
├── description
├── arguments_model: type[BaseModel]
└── handler: async (validated_arguments: dict) -> str
```

- Registry 按精确名称查找，不模糊修复或自动改名。
- 参数模型使用 Pydantic `extra="forbid"` 与 strict validation；`model_json_schema()` 生成 ToolDefinition，`model_validate_json(..., strict=True)` 验证原始 arguments。
- 不新增 jsonschema 依赖，也不应用会改变用户输入语义的隐式强制/default。
- Handler 只接收已解析、已通过参数模型校验的字典。
- Handler 返回字符串，并支持协作式取消。
- Stage 2 不在线程中运行不可中断的同步函数。

### 10.2 执行顺序

```text
FunctionToolCall
→ 精确查找工具
→ Pydantic 严格解析/验证原始 arguments
→ 检查剩余 run deadline
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
| `not_found` | true | 工具领域内的目标对象不存在 |
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

### 10.6 单 Cycle 结果上限

一次响应允许多个 calls，因此除单结果上限外还要约束整组。Stage 2 使用简单等额分配：

```text
available_result_chars =
    effective_cycle_limit - serialized_assistant_tool_call_chars

per_call_result_limit =
    min(effective_result_limit, floor(available_result_chars / call_count))
```

接纳 Assistant 前必须确认每个 call 至少能容纳最小成功/失败 envelope。具体执行不滚动转让前一个 call 未使用的配额，避免引入第二套复杂会计。

## 十一、ContextBuilder 与确定性压缩

### 11.1 职责

ContextBuilder 仍是所有用户状态和会话消息进入模型的唯一入口。它只读取 Session 快照和 ConversationLog，不再接收一份可能造成重复的 `current_user`，也不修改事实源。

Stage 2 的“压缩”只包含确定性占位替换和合法历史硬裁，不包含摘要。

### 11.2 预算估算

Stage 2 使用 RunPolicy 的 `effective_request_chars` 作为确定性请求上限。估算对象是 Adapter canonical serializer 产生的实际请求 wire，而不是只计算 `message.content`：

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

清理顺序严格按时间：从最老的 ClosedToolCycle 开始，不再按成功、retryable 或失败分类。当前未闭合 Cycle 永不清理。

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
- Profile、Preferences 和显式加载的 Handoff；
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

Tool Result 的结构边界同样是硬约束：它只能作为 `role=tool` 进入 Chat View，不得进入 Structured/Fallback View、ConfigIntentGate、CommandService、权限判断或任何直接状态写入路径。ToolExecutor 只返回数据，只有 AgentLoop 可以决定是否继续模型循环；envelope 中即使包含“执行命令/修改系统”等文本也没有产品控制效果。

同一 ConversationSnapshot 必须生成三种明确投影：

- **Chat View**：固定 System、动态用户状态、合法 user/assistant/tool 历史，以及单独 request tools；
- **Structured View**：真实 User 和 completed turn 的最终无工具 Assistant；排除 ToolMessage、带 tool_calls 的 Assistant、cancelled/failed partial text，并且不发送 tools；
- **Handoff Fallback View**：最近真实 User 与最近 completed final Assistant，不读取 `content=None`、中间工具说明或 envelope。

必须保持：

- ConversationLog 不写入 Handoff YAML。
- 自动清理或硬裁不修改 Handoff。
- Handoff 生成只能读取 Structured View，仍沿用独立 Schema 和显式保存流程。
- StructuredCompletion 显式构造 `UserMessage`，不得通过 `type(context.messages[0])` 猜测消息类型。
- `/continue` 加载 Handoff 不恢复旧 ConversationLog。
- Stage 2 不引入 ContextSummary，也不让后台状态改写 Handoff.current_goal。

## 十三、公开事件与终端体验

- 一个 `run_task()` 必须恰好发布一个 `turn.started` 和一个 `turn.completed`。
- `turn.completed.finish_reason` 继续使用 `stop | cancelled | error`。
- 中间模型调用结束不发布 public turn completion。
- 可见文本继续使用 `text.delta`，不得暴露 reasoning 或原始 SDK fragments。
- 工具步骤使用 `tool.status`：

  ```text
  call_id
  name
  status: running | succeeded | failed | cancelled | skipped
  ordinal
  total
  error_code?
  truncated?
  ```

- `skipped` 只表示从未启动：取消跳过写 `cancelled` envelope，预算/deadline 跳过写 `budget_exhausted` envelope；已开始后取消使用公开 `cancelled`。
- 不把完整参数、超大结果、异常堆栈或秘密写入公开事件，终端默认不显示 call_id。
- 工具错误通常作为 ToolMessage 回传模型，不自动变成 public error；只有循环无法继续、预算耗尽或模型响应无效时才以 public error 结束。
- 消费者继续忽略未知事件类型和字段。

fatal `error` 事件负载固定为有界 `message` 和 `stop_code: AgentStopCode`。Stage 1 的公开 `code: ModelErrorCode` 字段被替换而不是并存；ModelErrorCode 只保留为 Adapter/ModelCallRunner 内部分类。紧随其后的 `turn.completed.stop_code` 必须与 error 相同。

`turn.completed` 负载固定包含 `finish_reason`、`text`、`text_length`，error 必须包含 `stop_code`，stop/cancelled 不带。公开 AgentStopCode 为：

```text
provider_auth
provider_network
provider_rate_limit
provider_timeout
invalid_response
model_output_limit
content_filtered
context_budget
model_call_limit
tool_call_limit
run_timeout
loop_detected
internal
```

`stop_detail` 只用于内部诊断和测试；Provider length 使用 `provider_length`，Cycle 最小闭合空间不足使用 `tool_cycle_too_large`，两者共享公开 `model_output_limit`。

终端按 `text.delta` 只渲染一次，不重复渲染 completion.text。mixed content 后进入工具阶段时先换行并显示有界步骤标记；工具阶段后最终 Assistant 从新行开始。该规则必须有 `Terminal.show_event` 离线事件序列测试，不能只靠人工观察。

### 13.1 重复循环提前止损

- 只比较当前真实 User turn 内的 ClosedToolCycle；
- 有效 arguments 使用 canonical JSON，非法 arguments 使用去首尾空白原文；
- call ID 和 Assistant 中间文本不参与；
- 成功结果比较状态、截断 metadata 和有界内容，失败比较 error code/retryable；
- 检测最近后缀中长度 1..`loop_max_pattern_cycles` 的重复模式；
- 达到 `loop_repeat_limit` 后以 `loop_detected` 结束，不再调用模型解释；
- 下一真实 User turn 重置；`max_tool_rounds` 仍是独立硬上限。

## 十四、垂直切片实施顺序

模块职责保持解耦，但实施以可运行闭环为单位；完整任务见 [执行计划](../../.agent/PLAN.md)。

1. **Slice 1 — Walking Skeleton**
   - 第一项先把目录名守卫改为能力/副作用边界守卫；
   - Message/Tool wire、OpenAI-compatible serializer/fragment accumulator；
   - Session 从第一天持有 ConversationLog，`run_task()` 是唯一写入者，`Session.messages` 只读，`run_turn()` 只可薄委托；
   - 最小 Registry/Executor、`lookup_record`、`calculate`；
   - 同一切片跑通两工具步骤 E2E、普通无工具聊天，以及取消/内部异常后 unresolved calls 的最小合成闭合。
2. **Slice 2 — History, Context, Product Projections**
   - 完整 ToolCycle/Snapshot 校验；
   - Chat/Structured/Fallback View；
   - canonical request sizing、旧结果清理和合法硬裁；RunPolicy 落地前只允许 composition root 显式注入现有 24000 兼容上限，ContextBuilder 自身无默认值；
   - 清理剩余历史读者/夹具，迁移 StructuredCompletion 与 Handoff；
   - `/handoff update`、`/exit`、`/new`、取消后再聊回归。
3. **Slice 3 — Guardrails, Policy, Observability**
   - 开发者 TOML、冻结 RunPolicy 与 Provider 工具能力；
   - 全部模型/工具/时间/context/result/Cycle 预算和逐 call deadline；
   - 在 Slice 1 最小闭合上补全全部 commit-point/deadline/budget 状态、progress-aware retry 与循环提前止损；
   - 移除 retry=1/context=24000 两个兼容数值源，最终所有上限只来自 RunPolicy；
   - AgentStopCode、`tool.status`、mixed-content Terminal 测试；
   - 护栏整体通过后才在 production bootstrap 启用默认工具。
4. **Slice 4 — Acceptance and Delivery**
   - 全部离线单元/集成/E2E、Stage 1 全量回归、包资源与终端验收；
   - 可选真实 Provider function-calling smoke；
   - 能力边界复验、验收证据与架构/路线/README 状态同步。

一个切片未通过自身完整质量门禁时不得激活下一个切片；后续切片不能以补丁方式掩盖前一切片的双写、非法历史或协议错误。

## 十五、验收测试矩阵

### 15.1 协议与 Adapter

- 请求侧 tools 序列化，且纯文本 `complete()` 不携带 tools。
- 纯文本、纯 tool calls、混合文本与 tool calls 的流式 fragment 组装。
- 多 call arguments 交错 fragment 的正确归并。
- `content: null`、原始 arguments 保真、显式字段白名单和 reasoning 隔离。
- 合法 finish reason 与 abnormal/missing finish 的接纳差异。
- OpenAI-compatible 组装后再显式序列化的 round trip。
- 不为未实现的原生 Provider 建 SDK fixture。

### 15.2 ConversationLog 与 ToolCycle

- 单 call 和单响应多 call 的完整 Cycle。
- unknown、duplicate、missing、orphan 和越序 result 拒绝。
- 新 user/assistant/terminal 不能越过 open Cycle。
- 取消、失败和预算耗尽先合成结果，再写 terminal。
- terminal 不进入 Provider payload。
- Snapshot 深度只读。
- `/new` 和 reset 清空 Log；不跨进程恢复。

### 15.3 ContextBuilder

- 不修改 ConversationLog 或 Session。
- 工具定义、arguments 和结果都计入预算。
- 先清理最老完整 Cycle，再硬裁最老合法前缀。
- 不拆 ToolCycle，不产生 orphan assistant/result。
- 长 active turn 可裁旧 closed Cycle，但保留当前 UserMessage。
- 保护集超预算时在 Provider 调用前失败。
- 裁剪后的实际 wire 再验证且不超预算。
- Structured View 不含 ToolMessage/中间 Assistant，Fallback 不读取 envelope。
- Config/Handoff 路径不能把 Tool Result 当用户指令。

### 15.4 Tool Executor

- 正常结果、截断结果和 original_chars。
- 非 JSON、非 object、Schema 不匹配和未知工具。
- timeout、执行异常、内部异常、取消和输出上限。
- 单结果截断和单 Cycle 等额上限。
- `retryable` 不触发 Runtime 自动重试。
- 一个合法 call 始终产生一个 ToolMessage。

### 15.5 Agent Loop 与回归

- 一个用户目标经过至少两个工具步骤后给出最终答案。
- 一次响应包含多个 calls，按原始顺序执行并整体闭合。
- 模型尝试、工具轮次、工具调用、单 Cycle 调用、总时间、单工具超时、上下文、单结果和 Cycle 体积预算。
- 每个 call 前检查总 deadline，有效 timeout 不超过剩余运行时间。
- 模型无进展临时错误可有限重试；文本/tool fragment 进展后不重试。
- 模型流中取消、首个结果前取消、部分结果后取消、Cycle 闭合后取消。
- 取消/预算对未启动 call 的 envelope 与 `tool.status=skipped` 映射正确。
- Slice 1 起工具取消/内部异常就先关闭 unresolved calls，且无需 reset 即可开始健康下一 turn。
- A A A 与 A B A B A B 重复提前停止，参数/结果变化不误判。
- fatal `error.stop_code` 与随后 `turn.completed.stop_code` 相同，公开 payload 不再保留 Stage 1 `code` 字段。
- 组合预算顺序可预测：带重试时 model_call_limit 可以先于 tool round 上限；最长循环模式在硬轮次上限前触发。
- 一个任务只产生一组 public start/completion。
- 工具失败可被模型消费并继续，不崩溃或无限重试。
- 工具任务结束后仍可正常对话。
- 阶段 1 的十轮对话、空响应、异常 finish、重试、取消、配置、结构化完成、Handoff、工作空间隔离和退出行为继续通过。
- `Terminal.show_event` 输入 `text.delta → tool.status → text.delta → turn.completed` 时分段正确且最终文本只显示一次。
- `tests/test_stage_boundary.py` 从 Slice 1 起检查能力/副作用边界，不按合理的 `tools`/`loop` 目录名失败。

## 十六、阶段交付物

- OpenAI-compatible 工具消息和请求协议。
- Provider Adapter 的显式序列化与流式 tool-call 组装。
- 进程内 ConversationLog 和 ToolCycle 校验。
- 独立于具体 Provider 的 Agent Loop。
- 最小 Tool Registry、Tool Executor 和无副作用演示工具。
- 随包开发者策略、Provider 工具能力，以及模型、工具、时间、上下文、单结果和单 Cycle 预算。
- 确定性的旧结果清理和合法历史裁剪。
- 中断/失败/循环闭环、精确停止码和终端步骤状态。
- 完整离线测试与阶段 1 回归证据。

## 十七、阶段完成标准

- Agent 能从一个用户目标出发，自主完成至少两个工具步骤并给出最终答案。
- 每个已接纳 tool call 都有且只有一个对应 ToolMessage，任何退出路径都不留下未闭合 Cycle。
- OpenAI-compatible Provider 收到的请求只包含合法 wire 字段；Adapter 内部元数据不泄漏。
- 参数错误、未知工具、超时、执行失败和输出截断都形成规范结果并可回传模型。
- 达到模型、工具、时间、上下文或输出预算时能够安全停止并说明原因。
- 重复调用在配置阈值内提前停止，硬轮次上限独立兜底。
- 每个串行 call 都受剩余总 deadline 约束。
- 用户可以在模型或工具阶段中断任务，之后继续正常对话。
- ContextBuilder 在预算内保留最近的合法完整轨迹，不修改原始日志，不调用摘要模型。
- ConversationLog 仅存在于当前进程，不自动修改或替代 Handoff。
- Handoff/StructuredCompletion 不消费 ToolMessage 或中间 tool-call Assistant。
- mixed content、工具步骤和最终答案在终端不重复且可区分。
- 阶段 1 的配置隔离、结构化完成、显式交接、事件生命周期和安全边界没有回归。
- 默认测试离线通过，且没有加入 Stage 3/4/5 的能力。

## 十八、实施计划状态

Stage 2 不再保留阻塞实施的设计参数：

- 初始开发者策略及其可调整边界见 9.4；
- 演示工具锁定为 `lookup_record` 和 `calculate`；
- 参数 Schema/验证使用现有 Pydantic v2；
- 工具公开事件锁定为 `tool.status`，终端非语义文案可在不改变分段合同的前提下调整；
- 四个垂直切片及完整任务、门禁、风险与交付物见 [Stage 2 Agent Core Implementation Plan](../../.agent/PLAN.md)。

数值默认值和内部算法可以在实现证据支持下调整，但不得修改 OpenAI-compatible wire、Adapter/AgentLoop/Conversation/Context/ToolExecutor 所有权、ToolCycle 原子性、单一历史写入、Handoff 边界或 Stage 2 范围。

阶段 2 通过后，才向 Agent 开放真实的本地项目操作能力。
