# Stage 2 Agent Core 完整方案（修订审批稿）

> 状态：已批准并合入正式 Stage 2 路线；本文作为审批裁决与详细设计记录，不是独立实施权威
> 修订日期：2026-08-15
> 修订依据：[Stage 2 Agent Core 审批稿 Review](stage-2-agent-core-final-proposal-review.md)、[修订审批稿可执行性 Review](stage-2-agent-core-revised-proposal-review.md)
> 当前正式路线：[阶段 2：Agent 核心能力](../roadmap/stage-2-agent-core.md)
> 当前执行计划：[Stage 2 Agent Core Implementation Plan](../../.agent/PLAN.md)
> 阶段结果：一个能够通过无本地副作用工具自主执行多个步骤、完成简单任务的 Agent

## 一、Review 裁决摘要

Review 指出的实施顺序、Stage 1 接缝、Handoff/StructuredCompletion、步间 deadline、单一聊天入口和终端分段问题属实，本稿全部修正。

Review 建议删除模块边界、开发者配置、循环检测、最小 terminal 记录和旧 Tool Result 清理，不全部成立。本稿保留这些能力，但将其从“先建完整框架”改为“垂直切片稳定后添加的薄护栏”，并移除不必要的文件合同和算法锁定。

| Review 结论 | 裁决 | 本稿处理 |
|---|---|---|
| 原实施顺序把首次闭环放到最后 | 接受 | 改为四个垂直切片，第一个切片即完成模型→工具→模型 E2E |
| Stage 1 运行时接缝被低估 | 接受 | 增加单一聊天入口、Session 迁移、Handoff 和 StructuredCompletion 投影 |
| 串行工具执行缺少步间 deadline | 接受 | 每个 call 前检查剩余时间，timeout 取单工具上限和剩余时间较小值 |
| mixed content 与最终答案终端表现不清 | 接受体验问题 | 增加终端分段规则；`turn.completed.text` 不重复渲染 |
| “没有待定参数”过度锁死 | 接受 | 只锁不变量；数值是开发者初始默认，可按证据调整 |
| Draft 2020-12/jsonschema 对内置工具过重 | 接受 | Stage 2 使用已有 Pydantic 严格模型生成 Schema 和验证参数 |
| Anthropic fixture 会在 Adapter 不存在时腐烂 | 接受 | 保留 Adapter 双向转换原则和示例，删除 Stage 2 fixture 要求 |
| RequestSizer 独立 Protocol 过早 | 接受 | 使用当前 Adapter 共享的纯序列化/字符估算 callable，不建独立模块 |
| 三个演示工具偏重 | 接受 | 只保留 `lookup_record` 和 `calculate` |
| 9 个模块子计划会造成晚集成 | 接受 | 保留模块职责，删除九段门禁式实施合同 |
| 应删除模块解耦 | 不接受 | 模块职责是此前明确要求；问题在实施顺序，不在边界本身 |
| 应删除开发者配置并改成代码常量 | 不接受 | 与“数值不写死、但不全部暴露给用户”的已确认要求冲突 |
| 800k 会在没有模型上限时直接发送 | 结论不属实，但需澄清 | 缺少 `safe_request_chars` 时强制使用 160k fallback，800k 只在显式安全上限允许时生效 |
| `max_tool_rounds` 足以替代循环检测 | 不接受删除，接受后置 | 硬上限保证最终停止，重复检测用于提前止损；算法不再锁成 SHA-256 合同 |
| ToolCycle 总体积只服务 Stage 3 | 不完全成立 | 一次响应允许多个 calls，单结果限制不能约束整组；保留简化的等额上限 |
| TerminalRecord/Snapshot 属于存储引擎过度设计 | 不接受 | 最小 terminal 是取消/失败的确定性 turn 边界，不持久化、不承担存储职责 |
| 旧 Tool Result 清理可延后 | 不接受 | “完整 Log + 派生 View 清旧结果”是此前明确锁定的 Stage 2 上下文契约 |
| 13 个停止码过细 | 不接受 | 这些是已存在或 Stage 2 可直接触发的不同恢复原因，精确分类比模糊 `budget` 更可诊断 |

2026-08-15 的可执行性 Review 给出“有条件批准”：R1（阶段边界守卫时序）和 R2（Session/ConversationLog 单一事实源时序）成立并已合入 Slice 1；C1–C3 已补为明确协议，C4 原本已在验收矩阵中，本次进一步锁定为 `Terminal.show_event` 离线单测。

## 二、阶段目标

Stage 2 在 Stage 1 连续对话之上建立一个最小但完整的工具调用闭环：

```text
用户目标
→ 模型选择工具
→ Runtime 执行工具
→ 结果合法写回
→ 模型继续调用工具或给出最终答案
```

Stage 2 必须证明：

- 模型能完成至少两个相互依赖的工具步骤；
- 已接纳的 tool calls 在任何退出路径上都完整闭合；
- Provider 流式 fragments 能稳定组装；
- 取消、失败和预算不会污染历史；
- Handoff、StructuredCompletion、终端和 Stage 1 行为不回归。

本阶段工具没有文件、Shell、Git、网络或其他真实项目副作用。

## 三、范围

### 3.1 包含

- OpenAI-compatible function-calling Core wire；
- 请求侧工具定义和 tool message 历史；
- Provider Adapter 显式序列化和流式 tool-call 组装；
- 不兼容 Provider 的双向转换边界；
- 进程内 ConversationLog 和 ToolCycle 合法性；
- 单一任务级 AgentLoop；
- 一次响应多个 calls，按原始顺序串行执行；
- Pydantic 参数模型、Tool Registry 和 ToolExecutor；
- 工具错误回传模型，Runtime 不自动重试工具；
- 模型、工具、时间、单工具超时、单结果和单 Cycle 预算；
- 重复工具循环的提前止损；
- 旧 Tool Result 的确定性 View 替换和合法历史硬裁；
- Handoff/StructuredCompletion 的安全历史投影；
- `tool.status`、终端分段和精确停止原因；
- 两个无副作用演示工具；
- 离线垂直切片、产品面回归和可选 Live smoke。

### 3.2 明确不包含

- 文件、搜索、编辑、补丁、Shell、Git 或测试执行工具；
- 浏览器、网络、外部系统或数据库；
- MCP、Skills 或插件；
- 工具真实并行执行；
- ConversationLog 持久化和跨进程恢复；
- ContextSummary、SummaryCheckpoint、LLM 摘要、`/compact`；
- 长期记忆、向量检索或 artifact 回注；
- 不确定外部副作用恢复；
- OpenAI Responses API、旧 `function_call` 或 `role=function`；
- 未实际使用的原生 Provider SDK、Adapter 或 fixture；
- 为未来阶段预建的空模块。

## 四、硬约束与可调策略

### 4.1 不可配置的协议与安全不变量

以下条款审批后不可通过配置关闭：

1. Core 使用统一 OpenAI-compatible function-calling 子集；
2. Provider 差异只在 Adapter；
3. 纯 tool-call Assistant 显式序列化 `content:null`；
4. arguments 原始字符串由消息层保真；
5. 已接纳 call 必须有且只有一个 result；
6. 同一响应的 calls/results 构成不可拆 ToolCycle；
7. 半截 Provider stream 不得进入历史；
8. 有可观察进展后不得静默重放模型请求；
9. Runtime 不按 retryable 自动重试工具；
10. ContextBuilder 不调模型、不改 Session、不改 Handoff；
11. 一个任务恰好一次公开开始和一次公开完成；
12. 公开事件不包含秘密、reasoning、完整参数、完整结果或 traceback；
13. ToolMessage 只作为模型数据，不能触发命令、配置或权限变更。

### 4.2 开发者可调策略

模型尝试数、工具轮次、工具调用数、运行时间、单工具超时、输出体积、上下文字符预算和循环阈值是开发者策略，不是协议。

本稿给出初始默认值，但允许在实现和 Live 证据出现后调整，无需修改消息 wire、ToolCycle 不变量或用户设置 Schema。

这些选项不进入 Preferences、Profile、Handoff，也不提供 Stage 2 用户界面或自然语言修改入口。

## 五、模块职责与依赖

模块化仍然保留，但它是职责边界，不再是“先完成七个底层模块、最后才集成”的实施顺序，也不要求每个职责现在就占一个文件。

```mermaid
flowchart TD
    CORE["Core Protocol"] --> ADAPTER["Provider Adapter"]
    CORE --> CONV["Conversation"]
    CORE --> TOOLS["Tool Execution"]
    CORE --> POLICY["Agent Policy"]
    ADAPTER --> MODEL["Model Call"]
    CONV --> CONTEXT["Context"]
    POLICY --> CONTEXT
    TOOLS --> LOOP["AgentLoop"]
    MODEL --> LOOP
    CONTEXT --> LOOP
    CONV --> LOOP
    POLICY --> LOOP
    LOOP --> PRODUCT["Orchestrator / Handoff / Terminal"]
```

职责：

| 模块 | 拥有 | 禁止 |
|---|---|---|
| Core Protocol | Message、ToolDefinition、错误枚举、内部 DTO | SDK、Session 写入、终端渲染 |
| Provider Adapter | Vendor 序列化、fragment 组装、finish/error 映射 | 工具执行、任务循环、历史写入 |
| Conversation | 受控追加、turn 边界、ToolCycle 校验、只读 Snapshot | Provider 调用、裁剪 View、公开事件 |
| Tool Execution | Registry、参数校验、handler、结果 envelope | 历史写入、UI 事件、任务重试 |
| Context | 用户状态渲染、历史投影、预算、View 缩减 | 模型调用、Session 修改、Handoff 写入 |
| Model Call | 一次逻辑请求和有限 Provider 重试 | 工具执行、任务完成判断、历史写入 |
| AgentLoop | 唯一聊天状态机、预算、写 Log、公开事件 | Provider 私有解析、持久状态写入 |
| Product Integration | 命令分流、Handoff、终端展示、Bootstrap | 直接修改消息或解析 Provider wire |

允许小模块在第一垂直切片中暂时同文件实现；只有职责已经独立且测试边界明确时才拆文件。不得创建没有行为的占位模块。

## 六、Core 工具协议

### 6.1 ToolDefinition

```text
ToolDefinition
├── type: Literal["function"]
└── function
    ├── name: str
    ├── description: str
    └── parameters: JSON Schema object
```

规则：

- Stage 2 只支持 `type="function"`；
- 工具名满足 `[A-Za-z0-9_-]{1,64}`；
- Registry 内精确匹配且唯一；
- description 非空；
- parameters 由注册工具的 Pydantic 参数模型生成；
- 工具定义按名称排序后发送；
- AgentLoop 固定使用 Adapter 侧 `tool_choice="auto"`；
- StructuredCompletion、Handoff、配置提取和 Provider 探测不发送 tools。

### 6.2 FunctionToolCall

```text
FunctionToolCall
├── id: non-empty str
├── type: Literal["function"]
└── function
    ├── name: non-empty str
    └── arguments: str
```

- 同一 AssistantMessage 内 call ID 唯一；
- arguments 保存 Provider 返回的原始字符串；
- 消息模型不解析、不修复、不重新序列化 arguments；
- 参数解析和验证只属于 ToolExecutor。

### 6.3 Message 联合类型

```text
Message =
    SystemMessage
  | UserMessage
  | AssistantMessage
  | ToolMessage
```

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
├── tool_call_id: non-empty str
└── content: str
```

AssistantMessage 必须至少含非空 content 或一个 tool call。

协议模型使用严格、不可变边界：

- `extra="forbid"`；
- 集合转换为 tuple；
- 不允许 SDK 对象、reasoning、日志 metadata 混入；
- 公开 AgentEvent 仍保持消费者忽略未知字段的前向兼容规则。

### 6.4 显式 Provider serializer

领域消息不能直接 `model_dump()` 给 SDK。Adapter 通过字段白名单构造请求：

- System/User 只发送 role/content；
- Assistant 只发送 role/content/tool_calls；
- 纯工具调用显式发送 `"content":null`；
- Tool 只发送 role/tool_call_id/content；
- 不发送 sequence、turn_id、terminal、reasoning 或内部错误对象；
- 纯文本调用省略 tools，而不是发送空或假工具集合。

## 七、Provider Adapter 和一次模型调用

### 7.1 Adapter 所有权

Adapter 独占：

- Core request 到 Vendor wire 的显式序列化；
- Vendor stream fragments 的完整组装；
- finish reason 归一化；
- Provider 异常分类；
- 原生 tool use/result 的双向转换。

ModelCallRunner 不解析 SDK fragments。

### 7.2 ModelFinishReason

```text
ModelFinishReason =
    stop
  | tool_calls
  | length
  | content_filter
  | unknown
```

接纳规则：

- stop：存在非空最终文本且没有 tool calls；
- tool_calls：存在完整合法 calls；
- stop 携带 calls：兼容归一化为 tool_calls；
- 混合文本和 calls：合法，文本保存为中间 Assistant 内容；
- length/content_filter/unknown：不接纳 AssistantMessage；
- 空 stop、空 calls、重复或缺失 ID、空名称、非法 type、非字符串 arguments：invalid response；
- arguments 不是合法 JSON 不影响 Assistant 接纳，Executor 随后生成 invalid_arguments。

### 7.3 OpenAI-compatible fragment accumulator

- 一个请求只允许一个逻辑 choice；
- usage-only chunk 可以忽略；
- tool calls 以 Vendor 私有 index 累计；
- name 和 arguments 按到达顺序拼接；
- id 使用首个非空值，相同重复值允许，冲突值拒绝；
- type 必须为 function；
- 完成后按 index 排序；
- index 和 SDK 对象不进入 Core；
- reasoning 不发布、不存储；
- 缺失或冲突结束信号均拒绝；
- completed.message.content 等于已发布文本 fragments 的拼接，纯 calls 时允许 None。

### 7.4 非兼容 Provider 边界

Core wire 不为 Anthropic 等 Provider 改形。真正增加原生 Provider 时，Adapter 负责：

```text
native tool use → Core FunctionToolCall
Core ToolMessage → native tool result
```

Stage 2 不实现、不测试尚不存在的 Anthropic Adapter，也不冻结其 SDK fixture。本文只锁定“转换发生在 Adapter，Core 不出现 Provider 分支”。

### 7.5 ModelCallRunner

ModelCallRunner 是内部职责，不是第二条聊天运行时。它可以先和 AgentLoop 位于同一 runtime 文件，待边界稳定后再拆出。

内部事件：

```text
ModelCallEvent =
    text_delta
  | retrying
  | completed(AssistantMessage, ModelFinishReason)
  | error(ModelErrorCode, made_progress)
```

规则：

- 每个真实 Provider 请求，包括重试，都计入 model_attempt_count；
- 只有临时错误、made_progress=false 且预算允许时才重试；
- 任意文本或 tool-call fragment 都令 made_progress=true；
- 有进展后的失败不重放；
- 半截 Assistant 不写入 ConversationLog；
- 不执行工具、不写历史、不发布任务级完成事件。

## 八、单一聊天入口和 Stage 1 迁移

### 8.1 唯一状态机

所有普通用户聊天最终只进入：

```python
AgentLoop.run_task(session, user_input, tools) -> AsyncIterator[AgentEvent]
```

SessionOrchestrator 不再在“普通聊天”和“工具聊天”之间选择两套写历史的状态机。

现有 `AgentRuntime.run_turn()` 如果为了兼容测试或外部调用暂时保留，只能是薄委托：

```text
run_turn(...)
→ run_task(..., tools=empty)
```

它不能保留自己的 Session 写入、重试、取消或事件生命周期。Stage 2 完成前，代码库中只能存在一条聊天历史写入路径。

StructuredCompletion、Handoff 生成和 Provider 探测继续使用无 tools 的 `complete()` 路径，它们不是聊天状态机，也不写 ConversationLog。

### 8.2 Session 迁移

- Session 持有 ConversationLog，不能同时维护第二份可变 messages；
- `Session.messages` 如为兼容保留，只能返回只读派生 tuple；
- `accept_user()` / `accept_assistant()` 不再作为任意调用者可用的写入口；
- 测试需要历史时使用 ConversationLog fixture 或 AgentLoop，不直接绕过不变量；
- begin_turn 接纳真实 UserMessage 时设置 Session dirty；
- completed、cancelled、failed 的真实用户输入都属于未保存会话内容；
- Handoff 成功发布、Session reset 或用户显式丢弃后才清除 dirty。

### 8.3 StructuredCompletion 构造

禁止使用：

```python
type(context.messages[0])(role="user", content=instruction)
```

统一使用显式 `UserMessage(content=instruction)` 构造。结构化完成不依赖上下文第一条消息的具体类型。

## 九、ConversationLog 和 ToolCycle

### 9.1 为什么保留最小 Log

ConversationLog 不是持久化存储引擎。它只是一个进程内、受控追加的事实源，用来同时解决：

- 单一历史写入权；
- tool calls/results 配对；
- cancelled/failed turn 的确定性结束边界；
- ContextBuilder 和 Handoff 的只读投影；
- `/new` 的完整 reset。

### 9.2 记录类型

```text
ConversationRecord = MessageRecord | TurnTerminalRecord

MessageRecord
├── sequence
├── turn_id
└── message: UserMessage | AssistantMessage | ToolMessage

TurnTerminalRecord
├── sequence
├── turn_id
├── terminal_state: completed | cancelled | failed
└── interrupted_call_ids: tuple[str, ...]
```

不加入 record_id、abandoned、uncertain outcome、持久化 revision 或编辑历史字段。

sequence 由 Log 分配，在 Session 内严格递增；它和每个公开 turn 重新计数的 AgentEvent sequence 完全独立。

### 9.3 API

```text
begin_turn(turn_id, user_message)
append_assistant(turn_id, message)
append_tool_result(turn_id, message)
finish_turn(turn_id, state, interrupted_call_ids)
snapshot()
reset()
```

AgentLoop 生成合成 ToolMessage，ConversationLog 只校验和追加。

### 9.4 Snapshot

```text
ConversationSnapshot
├── through_sequence
└── records: tuple[ConversationRecord, ...]
```

Snapshot 深度只读，创建后不受后续追加影响。ContextBuilder、Handoff projection 和循环检测只读取 Snapshot，不访问可变 Log。

### 9.5 ToolCycle

ToolCycle 从记录确定性派生，不另建第二份存储：

```text
Assistant(tool_calls A, B)
→ Tool(result A)
→ Tool(result B)
```

规则：

- 同一 Assistant 中 call ID 唯一；
- results 按 calls 原始顺序追加；
- 每个 call 恰好一个 result；
- unknown、duplicate、missing、orphan 和越序 result 均拒绝；
- 一个响应中的 calls/results 同属一个不可拆 Cycle；
- Assistant 同时含文本不影响 Cycle 判定；
- call ID 只要求 Cycle 内唯一；
- open Cycle 期间禁止 User、Assistant 或 Terminal；
- 任意退出路径先闭合已接纳 Cycle，再写 terminal。

校验发生在：

1. ConversationLog 增量追加；
2. Context View 完成；
3. Provider 请求发送前。

## 十、Tool Registry 和 ToolExecutor

### 10.1 使用现有 Pydantic

Stage 2 不新增 jsonschema 依赖。每个内置工具注册：

```text
RegisteredTool
├── name
├── description
├── arguments_model: type[BaseModel]
└── async handler(validated_arguments: dict) -> str
```

参数模型要求：

- Pydantic `extra="forbid"`；
- strict validation，不做数字/字符串隐式强制；
- `model_json_schema()` 生成 ToolDefinition.parameters；
- `model_validate_json(raw_arguments, strict=True)` 验证调用；
- 不自动应用会改变用户输入语义的 default；
- 验证错误按字段路径稳定呈现，并限制返回数量；
- Registry 在任务开始时冻结，工具定义按名称排序。

未来 Stage 5 接入外部动态 JSON Schema 时，可以增加独立 validator 实现，不改变 OpenAI-compatible Core wire。

### 10.2 执行顺序

```text
FunctionToolCall
→ 精确查找工具
→ Pydantic 严格解析和验证 arguments
→ 检查剩余 run deadline
→ 在有效 timeout 内调用 async handler
→ 限制输出
→ 返回 ToolExecutionOutcome
→ AgentLoop 追加 ToolMessage
```

ToolExecutor 不写日志、不发布公开事件、不自动重试。

### 10.3 Tool Result envelope

成功：

```json
{"success":true,"content":"96","truncated":false}
```

截断：

```json
{"success":true,"content":"bounded prefix…","truncated":true,"original_chars":125430}
```

失败：

```json
{
  "success": false,
  "error": {
    "code": "invalid_arguments",
    "message": "values must contain at least two numbers",
    "retryable": true
  }
}
```

规则：

- content 固定为字符串；
- envelope 使用稳定紧凑 JSON；
- 截断发生在写 ConversationLog 之前；
- Log 不保存被截掉的原始大结果；
- original_chars 只在 truncated=true 时存在；
- 错误消息同样有界并清除 traceback、秘密和内部细节；
- retryable 只提示模型。

### 10.4 ToolErrorCode

| Code | 默认 retryable | 语义 |
|---|---:|---|
| `invalid_arguments` | true | 非法 JSON、参数类型/约束或语义无效 |
| `tool_not_found` | true | Registry 中不存在指定工具 |
| `not_found` | true | 请求的领域对象不存在 |
| `timeout` | true | 单工具执行超时 |
| `execution_failed` | false | Handler 普通失败 |
| `output_limit` | false | 无法形成合法有界结果 |
| `cancelled` | false | 任务取消 |
| `budget_exhausted` | false | 任务预算耗尽 |
| `internal` | false | Runtime/Executor 不变量失败 |

`asyncio.CancelledError` 必须重新抛给 AgentLoop。

`skipped` 只属于公开 `tool.status`，不是 ToolErrorCode。未启动 call 的 ToolMessage 使用实际停止原因：

- 因用户取消而跳过：envelope `error.code="cancelled"`，公开事件 `status="skipped"`、`error_code="cancelled"`；
- 因工具调用额度或 run deadline 而跳过：envelope `error.code="budget_exhausted"`，公开事件 `status="skipped"`、`error_code="budget_exhausted"`；
- 已经开始后被取消的 call：envelope `cancelled`，公开事件 `status="cancelled"`。

### 10.5 单 Cycle 体积

一次响应允许多个 calls，因此单结果限制不能单独保证整个 Cycle 可在下一次请求中重放。

本阶段采用简单、确定性的等额上限，不实现滚动剩余额度算法：

```text
available_result_chars =
    max_cycle_chars - serialized_assistant_tool_call_chars

per_call_result_limit =
    min(max_tool_result_chars, floor(available_result_chars / call_count))
```

接纳 Assistant 前确认每个 call 至少能容纳最小成功/失败 envelope；否则不接纳该 Assistant，并以 model_output_limit 结束，内部 `stop_detail="tool_cycle_too_large"`。

该规则既约束整组，又避免复杂的“前一个结果未用完多少转给后一个”会计。具体字符常量属于开发者策略。

## 十一、AgentLoop 状态机

```mermaid
stateDiagram-v2
    [*] --> Starting
    Starting --> PreparingRequest
    PreparingRequest --> CallingModel
    CallingModel --> HandlingResponse
    HandlingResponse --> Finalizing: 最终文本
    HandlingResponse --> ExecutingTools: tool_calls
    ExecutingTools --> ClosingCycle: calls 全部闭合
    ClosingCycle --> PreparingRequest: 继续
    ClosingCycle --> Finalizing: 取消、预算或循环
    PreparingRequest --> Finalizing: 上下文/时间失败
    CallingModel --> Finalizing: Provider 失败
    HandlingResponse --> Finalizing: 响应无效
    Finalizing --> [*]
```

### 11.1 Starting

- 生成 turn_id；
- 冻结当前 RunPolicy 和 ToolRegistry；
- begin_turn 写真实 UserMessage，并设置 Session dirty；
- 发布唯一 turn.started；
- 记录 monotonic deadline 和计数器。

### 11.2 PreparingRequest

顺序固定：

1. 检查取消；
2. 检查总运行 deadline；
3. 检查模型尝试、工具轮次和工具调用预算；
4. 获取 ConversationSnapshot；
5. 构造 chat Context View；
6. 校验 ToolCycle 和 request size；
7. 调用模型。

### 11.3 HandlingResponse

最终文本：

- 追加无 tools Assistant；
- 该写入是 completed 的提交点；
- 进入 Finalizing(completed)。

tool calls：

- 先检查 per-cycle call 数、剩余总调用额度和 Cycle 最小闭合空间；
- 追加完整 Assistant，打开 ToolCycle；
- mixed content 保存为中间 Assistant 文本；
- 进入 ExecutingTools。

如果 call 数超过 max_tool_calls_per_cycle，不接纳 Assistant，直接 tool_call_limit。

如果合法批次超过剩余总工具额度：

- 接纳 Assistant；
- 不执行任何 call；
- 为整组写 budget_exhausted；
- 闭合 Cycle；
- tool_call_limit 结束。

### 11.4 串行执行和步间 deadline

同一响应中的 calls 按原始顺序执行：

```text
call 1 → result 1
call 2 → result 2
call 3 → result 3
```

每个 call 开始前：

1. 检查取消；
2. 计算 `remaining_run_seconds = deadline - monotonic_now`；
3. 若剩余时间小于等于 0，为当前及剩余 calls 写入 `budget_exhausted` envelope，并发布 `tool.status=skipped`；
4. 否则有效工具 timeout 为 `min(tool_timeout_seconds, remaining_run_seconds)`；
5. 执行、追加结果并发布 tool.status；
6. 执行完成后再次检查 deadline，再决定是否启动下一个 call。

因此一组串行 calls 不可能因为每个 call 都有独立 timeout 而合法越过总 run deadline。

### 11.5 ClosingCycle

- 每个 call 恰好一个 result；
- 顺序正确；
- Cycle 完整进入 Log；
- tool round 计数增加；
- 执行当前 turn 的重复检测；
- 未停止时回到 PreparingRequest。

### 11.6 取消和提交点

- 模型流中取消：丢弃未完成 Assistant，terminal(cancelled)；
- tool-call Assistant 已接纳后取消：正在执行的 call 写 `cancelled` envelope 并发布 cancelled；未开始 calls 写 `cancelled` envelope 并发布 skipped；全部闭合后 terminal(cancelled)；
- 最终 Assistant 写入前取消：cancelled；
- 最终 Assistant 写入后到达的迟到取消：忽略，仍 completed；
- 所有路径只发布一次 turn.completed。

## 十二、循环提前止损

Review 正确指出 max_tool_rounds 已经保证最终停止，但它不能避免在明显重复时继续消耗完整轮次。Stage 2 保留一个薄的重复检测，安排在首个垂直切片稳定之后实现。

语义要求：

- 只比较当前 turn 的 ClosedToolCycle；
- call ID 和 Assistant 中间文本不参与；
- 有效 arguments 使用 canonical JSON；非法 arguments 使用去首尾空白原文；
- 成功结果比较状态、截断 metadata 和有界内容摘要；
- 失败结果比较 error code 和 retryable，不比较可变文案；
- 检测最近后缀中长度 1..max_pattern_cycles 的连续重复模式；
- 达到 repeat_limit 后直接 loop_detected，不再调用模型解释；
- 下一真实用户 turn 重置检测。

本文不锁定 SHA-256 为外部合同。实现可以比较 canonical tuple 或使用稳定 digest，只要上述语义和测试成立。

结果内容或参数发生变化时不是相同 Cycle，因此“换 key 再查”不会被视为重复。默认连续三次完全相同模式才提前停止；max_tool_rounds 仍是最终硬上限。

## 十三、ContextBuilder 和历史投影

### 13.1 三种明确 View

同一 ConversationLog 派生不同用途的只读 View，不能把 chat tool 历史原样复用于所有模型调用。

#### Chat View

- 固定 System Boundary；
- Profile、Preferences、显式加载的 Handoff；
- 合法 user/assistant/tool 消息历史；
- 当前工具定义通过 request tools 字段发送。

#### Structured View

用于 Handoff、配置提取和其他 StructuredCompletion：

- 固定 System Boundary 和用户状态；
- 只保留真实 UserMessage；
- 只保留每个 completed turn 的最终无工具 Assistant 文本；
- 排除 ToolMessage；
- 排除带 tool_calls 的中间 Assistant；
- 排除 cancelled/failed turn 的半截可见文本；
- 不发送 tools。

#### Handoff Fallback View

确定性 fallback 使用：

- 最近真实 UserMessage 作为 current_goal 候选；
- 最近 completed turn 的最终 Assistant 文本作为 recovery note 候选；
- 如果没有最终 Assistant，最近回复为空，不读取 `content=None` 或中间工具说明；
- 不把 ToolMessage envelope 拼入 recovery note。

### 13.2 Context 接口

```text
ContextRequest
├── purpose: chat | structured
├── user_state
├── conversation_snapshot
├── tool_definitions
└── run_policy

ContextPack
├── messages
├── tools
├── estimated_request_chars
├── cleared_cycle_count
└── dropped_record_count
```

ContextBuilder 不接收 `current_user`。当前 user 已由 begin_turn 写入 ConversationLog，避免重复。

### 13.3 字符估算

Stage 2 只有 OpenAI-compatible Adapter，因此不建立独立 RequestSizer 模块或 Protocol。

Adapter 提供一个纯、无 SDK 调用的 canonical request serializer。Composition root 将共享的 `estimate_request_chars(messages, tools)` callable 注入 ContextBuilder；该 callable 对 serializer 的紧凑 JSON 结果取长度。

Provider 发送前使用同一 callable 再校验一次。加入第二个真实原生 Adapter 后，再决定是否提升为正式 Protocol。

### 13.4 旧 Tool Result 清理

保留“完整 Log、派生 View 清理”的已锁定设计，但简化优先级：

```text
从最老的 ClosedToolCycle 开始，严格按时间顺序清理
```

不再区分成功、retryable 失败和其他失败三套队列。

清理一个 Cycle 时同时替换其全部 ToolMessage.content：

```text
[tool result omitted from active context: budget]
```

该占位符只存在于 Chat View，不写入 ConversationLog，不参与循环检测，也不进入 Structured View。

### 13.5 硬裁

清理后仍超预算才执行硬裁：

- 不在字符串或消息内部切割；
- 不拆 ToolCycle；
- 不留下 orphan Assistant 或 ToolMessage；
- 优先丢最老完整 public turn；
- 必要时在长 turn 内丢最老完整 ClosedToolCycle；
- 不跳过较新历史去保留更老历史；
- 当前真实 UserMessage始终保留；
- 当前 open Cycle 始终保留；
- 保留某 turn 的 Assistant/Cycle 时同时保留该 turn 的 UserMessage。

保护集：

- 固定 System；
- 当前 UserMessage；
- 当前 open Cycle；
- Profile/Preferences/显式 Handoff；
- 当前 ToolDefinition。

保护集自身超预算时在 Provider 调用前 context_budget 失败。Stage 2 不调用摘要模型。

## 十四、Tool Result 的不可信数据边界

System Prompt 是第一层，不是唯一层。Stage 2 同时锁定结构边界：

1. Tool Result 只能进入 role=tool 的 Chat View；
2. Tool Result 不进入 Structured View 或 Handoff fallback；
3. Tool Result 不能进入 ConfigIntentGate、CommandService 或权限判断；
4. Tool Result 不能直接触发配置补丁、Handoff 写入或用户状态写入；
5. ToolExecutor 只返回数据，唯一编排者 AgentLoop 决定下一步；
6. 工具 envelope 中出现的“执行命令”“修改系统”等文本没有权限效果；
7. Profile、Preferences 和 Handoff 同样是状态数据，不是工具授权。

Stage 2 不声称解决所有 prompt injection；它保证工具数据不会绕过模型直接进入产品控制面。

## 十五、开发者策略

### 15.1 简化后的配置形状

保留“数值不写死、但不暴露给普通用户”的要求，同时删除不必要的独立 Policy Adapter 和过早能力字段。

- 使用一个随应用发布的 `resources/agent-policy.toml`；
- 用标准库 tomllib 加现有 Pydantic 解析为 `AgentPolicy`；
- Bootstrap 解析当前 ModelRef 的安全请求上限，生成冻结 `RunPolicy`；
- 测试直接注入 AgentPolicy/RunPolicy；
- 不提供用户 CLI、Preferences 字段或自然语言修改；
- 缺失或非法的随包策略在 Bootstrap 明确失败，不回退到散落在 Runtime 的魔法数字。

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

Adapter/Model 注册信息只需提供 Stage 2 直接使用的能力：

```text
ProviderToolSupport
├── tool_protocol: none | openai_function
├── multiple_tool_calls: bool
└── safe_request_chars: int | None
```

不在 Stage 2 提前加入未使用的 `context_window_tokens` 或 `max_output_tokens` 转换逻辑。

能力来源固定为：

- `tool_protocol` 和 `multiple_tool_calls` 由实际 Adapter 注册信息声明；
- `safe_request_chars` 只从随包 `agent-policy.toml` 的精确 ModelRef 表读取；
- 未命中的模型得到 `safe_request_chars=None`，随后使用 unknown-model fallback；
- 不从 Provider 名称、模型名称前缀或 context token 数进行猜测。

首版 TOML 形状：

```toml
[models."provider_id/model_id"]
safe_request_chars = 160000
```

### 15.2 初始默认值

| 参数 | 初始默认值 |
|---|---:|
| max_tool_rounds | 30 |
| max_model_attempts | 40 |
| max_tool_calls | 128 |
| max_tool_calls_per_cycle | 32 |
| max_run_seconds | 1800 |
| tool_timeout_seconds | 120 |
| model_retry_limit | 1 |
| requested_context_chars | 800000 |
| unknown_model_fallback_chars | 160000 |
| max_tool_result_chars | 64000 |
| max_tool_result_request_ratio | 0.10 |
| max_tool_cycle_chars | 256000 |
| max_tool_cycle_request_ratio | 0.35 |
| max_validation_errors | 3 |
| loop_detection_enabled | true |
| loop_repeat_limit | 3 |
| loop_max_pattern_cycles | 4 |

这些默认值锚定现代 Agent 的多步能力，不是演示任务的最小数字，也不是用户可见产品承诺。

测试可以注入更小预算以快速覆盖停止行为。实现和 Live 证据如果表明某个默认值不合适，可以只修改开发者策略资源和相应测试，不需要重新批准架构。

### 15.3 请求预算澄清

有效请求预算始终是：

```text
effective_request_chars = min(
    requested_context_chars,
    safe_request_chars if safe_request_chars is not None
    else unknown_model_fallback_chars,
)
```

因此：

- 800000 只在当前模型显式声明至少该字符安全上限时生效；
- `safe_request_chars=None` 时最多使用 160000；
- 不把 `context_window_tokens` 猜测换算成字符；
- 不会因为模型“已注册但没有安全上限”而直接使用 800000；
- Provider 仍拒绝上下文时分类为明确的 context/provider error，不静默重试更大请求。

有效工具限制：

```text
effective_result_limit = min(
    max_tool_result_chars,
    effective_request_chars × result_ratio,
)

effective_cycle_limit = min(
    max_tool_cycle_chars,
    effective_request_chars × cycle_ratio,
)
```

## 十六、公开错误和停止原因

```text
FinishReason = stop | cancelled | error
```

公开 AgentStopCode：

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

保留精确码的理由：

- length 和 content_filter 是 Provider 已明确区分的结束原因；
- model/tool/time/context budget 有不同恢复动作；
- loop_detected 需要告诉用户不是普通额度耗尽；
- Terminal 和未来客户端可以先统一渲染未知码，不要求为每个码立即建立复杂 UI 分支。

可恢复 ToolErrorCode 不提升为 AgentStopCode。取消不使用错误码。

### 16.1 恢复矩阵

| 来源 | 情况 | 处理 | 自动重试 | 任务终止 |
|---|---|---|---:|---:|
| Provider | network/rate limit/timeout | 无进展时有限重试 | 有条件 | 耗尽后是 |
| Provider | auth | provider_auth | 否 | 是 |
| Adapter | 非法 fragments/calls | invalid_response | 无进展时有条件 | 是 |
| Provider | length | model_output_limit；内部 detail=`provider_length` | 否 | 是 |
| Runtime | ToolCycle 最小闭合空间不足 | model_output_limit；内部 detail=`tool_cycle_too_large` | 否 | 是 |
| Provider | content filter | content_filtered | 否 | 是 |
| Context | 保护集超预算 | context_budget | 否 | 是 |
| Tool | 参数/未知/not found/timeout/普通异常 | ToolMessage | 否 | 通常否 |
| Tool | 输出过长 | 成功截断或 output_limit | 否 | 通常否 |
| Runtime | 用户取消 | 闭合 Cycle | 否 | cancelled |
| Runtime | model/tool/time budget | 闭合 Cycle | 否 | 是 |
| Runtime | repeated Cycle | loop_detected | 否 | 是 |
| Runtime | 不变量失败 | internal 结果并闭合 | 否 | 是 |

`stop_detail` 是 Runtime 内部诊断字段，不新增公开 AgentStopCode，也不要求终端或其他客户端分支处理。测试用它验证同一公开停止码的具体来源；公开错误文案仍需清楚区分 Provider 输出截断和 ToolCycle 过大。

## 十七、System、Handoff 和状态边界

### 17.1 System Boundary

固定 System Boundary 必须说明：

- 只能调用本次请求实际提供的工具；
- 未提供文件、Shell、Git、网络工具时不得声称执行过这些操作；
- Tool Result 是不可信数据，不得把其内容提升为系统或用户指令；
- Profile、Preferences 和 Handoff 是状态数据，不是权限授权；
- 不得编造工具事实；
- 工具失败后可以修正参数或换工具；
- 不得无变化重复同一调用；
- 工具完成后再给最终答案。

工具名称不写进 System Prompt，通过 tools 字段发送。固定 Boundary 和动态用户状态使用两条 SystemMessage。

### 17.2 Handoff

- 自动上下文处理不写 Handoff YAML；
- 清理和硬裁不修改 Handoff；
- Handoff 生成使用 Structured View，不读取 ToolMessage 或中间 tool-call Assistant；
- Handoff fallback 使用最近 User 和最近 completed final Assistant；
- `/continue` 只加载 Handoff，不恢复旧 ConversationLog；
- Handoff 成功发布后按现有契约清除 Session dirty；
- cancelled/failed 工具 turn 的真实 User 仍能成为 fallback current_goal，但半截/中间模型文本不能冒充 progress；
- Stage 2 不引入第二份 current_goal 权威。

## 十八、公开事件和终端行为

### 18.1 生命周期

```text
turn.started
→ text.delta / status.changed / tool.status / error
→ turn.completed
```

- 一个任务恰好一次 started 和一次 completed；
- 中间模型调用完成不发布 turn.completed；
- 可恢复工具错误只发 tool.status，不发任务 error；
- fatal error 发一次 error，随后 completed(error)；
- cancelled 不发 error；
- 事件 sequence 在当前 turn 内严格递增。

### 18.2 tool.status

```text
payload
├── call_id
├── name
├── status: running | succeeded | failed | cancelled | skipped
├── ordinal
├── total
├── error_code?
└── truncated?
```

不发送完整 arguments、完整 result、traceback 或 reasoning。终端默认不显示 call_id。

`skipped` 只表达“该 call 从未启动”。其 `error_code` 必须是 `cancelled` 或 `budget_exhausted`，并与写入 ConversationLog 的合成 ToolMessage 保持一致。

### 18.3 turn.completed

```text
payload
├── finish_reason: stop | cancelled | error
├── text: str
├── text_length: int
└── stop_code?: AgentStopCode
```

- 正常完成 text 只含最后一个无工具 Assistant 最终文本；
- cancelled/error text 是已经向用户展示的可见文本；
- error 必须有 stop_code；
- stop/cancelled 不带错误码。

### 18.4 mixed content 终端分段

现有 Terminal 按 text.delta 打印，turn.completed 只收尾。因此不会把 completed.text 再打印一次。Review 所说的“完成事件再次显示答案”不是当前实现行为，但 mixed content 的视觉边界问题属实。

Stage 2 锁定：

- text.delta 始终只渲染一次；
- turn.completed.text 是结构化结果和客户端恢复数据，Terminal 不重复渲染；
- mixed content 后若模型进入 tool_calls，首个 tool.status 前强制换行；
- 终端显示明确分隔，例如 `↳ 工具步骤 1/2：lookup_record`；
- 工具阶段结束后，最终 Assistant 文本从新行开始；
- 中间文本、工具状态和最终文本在人工验收中可明显区分；
- error/cancel 后也不重放已经显示的 partial text。

## 十九、演示工具和 E2E

### 19.1 lookup_record

```text
dataset: plans | regions
key: non-empty string
```

- 查询注入的静态内存字典；
- 不访问文件、数据库或网络；
- 不存在时返回 retryable not_found。

### 19.2 calculate

```text
operation: add | subtract | multiply | divide
values: number[2..32]
```

- 顺序计算，不使用 eval；
- 拒绝 NaN/Infinity；
- 除零返回 retryable invalid_arguments；
- 输出确定性数字字符串。

不实现 current_time；Stage 1 已有 Clock 注入能力，它不能增加 Stage 2 工具循环的验证覆盖。

### 19.3 离线故事

```text
查询套餐价格
→ 查询地区税率
→ calculate 计算三个月税后总价
→ 最终回答
```

脚本化 Fake Provider 必须返回两轮或以上 tool_calls，再返回最终文本。第一次实施切片就必须跑通该链路，不能等所有模块完成后才集成。

## 二十、垂直切片实施计划

模块职责保持解耦，但实施以可运行闭环为单位。

### Slice 1：Walking Skeleton

同一个子计划完成：

- 第一批提交即把 Stage 1 的目录名守卫改为 Stage 2 能力边界守卫：允许工具循环实现，继续禁止文件、Shell、MCP、持久会话和其他后续阶段能力；
- Message 联合类型和显式构造；
- OpenAI-compatible tools 请求和最小 fragment accumulator；
- Session 持有最小 ConversationLog，`run_task()` 从第一天起成为唯一历史写入者；
- `Session.messages` 同步降为只读派生 tuple，`run_turn()` 如保留只能薄委托 `run_task(tools=empty)`；
- 最小 ToolCycle 追加约束；
- `lookup_record`、`calculate` 的最小 Registry/Executor；
- 单一 AgentLoop.run_task；
- Fake Provider 的模型→工具→模型→最终文本 E2E；
- 一组 started/completed 事件。

完成标准：首次合并前就能走完两工具步骤，历史合法，Session 不双写，并且普通无工具聊天仍通过同一入口工作；能力边界守卫从 Slice 1 起保持绿色。

### Slice 2：历史、上下文和 Stage 1 产品面

- 完整 ToolCycle 校验和 Snapshot；
- ContextBuilder chat/structured/fallback 三种投影；
- 旧 Tool Result View 清理和合法硬裁；
- 清理 ConversationLog 的剩余旧读者和测试夹具，不再改变 Slice 1 已建立的单一写入权；
- `complete_structured` 显式 UserMessage；
- Handoff 生成和 fallback 只读安全投影；
- `/handoff update`、`/exit`、`/new`、取消后再聊回归。

完成标准：工具历史存在时，Handoff/配置提取不读取 ToolMessage envelope，Session 不双写。

### Slice 3：护栏和可观测性

- 开发者 AgentPolicy 和冻结 RunPolicy；
- 模型/工具/时间/输出/Cycle/context 预算；
- 每 call deadline；
- Provider retry made_progress；
- 全部取消和合成闭合路径；
- 重复循环提前止损；
- AgentStopCode、tool.status 和 mixed-content 终端分段；
- 增加 `Terminal.show_event` 离线单测：输入 `text.delta → tool.status → text.delta → turn.completed`，断言换行、分隔符和最终文本只渲染一次。

完成标准：每个失败入口都闭合 Cycle、只完成一次，并能在下一用户 turn 正常继续。

### Slice 4：最终集成与验收

- 全部离线单元/集成/E2E；
- Stage 1 全量回归；
- 复验 Slice 1 已建立的能力边界守卫，确认其禁止的是 Stage 3/4/5 能力而不是 `tools`/`loop` 等目录名；
- 默认离线环境验证；
- 可选 Live tool-call smoke；
- 人工终端验收 mixed content、取消、Handoff 和继续对话。

完成标准：所有阶段完成标准和证据同步，且没有 Stage 3/4/5 能力进入生产代码。

## 二十一、验收矩阵

### 21.1 协议与 Adapter

- tools request 和纯文本请求；
- 纯文本、纯 calls、mixed content + calls；
- 多 call fragments 交错；
- id 冲突、重复 ID、缺 finish；
- content:null；
- arguments 原始字符串保真；
- reasoning/SDK metadata 隔离；
- Adapter 组装出的 Assistant 再经白名单 serializer 回发；
- 不为未实现 Provider 建 fixture。

### 21.2 Conversation 和 ToolCycle

- 单 call、多 call；
- unknown、duplicate、missing、orphan、越序拒绝；
- open Cycle 期间禁止新 User/Assistant/Terminal；
- cancel/failure/budget 先合成结果再 terminal；
- TerminalRecord 不进入 Provider；
- Snapshot 只读；
- `/new` 清空，不跨进程恢复。

### 21.3 ToolExecutor

- Pydantic Schema 输出和严格参数验证；
- 非 JSON、非 object、字段类型/范围、extra 字段；
- unknown tool、not found；
- timeout、普通异常、internal、取消；
- 单结果截断和 Cycle 等额上限；
- retryable 不触发 Runtime 自动重试；
- 每个已接纳 call 恰好一个 result。

### 21.4 Context 和产品投影

- Chat View 保持合法 tool history；
- Structured View 不含 ToolMessage 和中间 Assistant；
- Handoff fallback 不读取 content=None 或 envelope；
- Config extraction 不把 tool result 当用户指令；
- ContextBuilder 不修改 Session/Log/Handoff；
- 清旧 Cycle 只改 View；
- 硬裁不拆 Cycle、不丢当前 User；
- 保护集超预算时不调用 Provider；
- `complete_structured` 使用显式 UserMessage。

### 21.5 AgentLoop 和时间

- 至少两个工具步骤后最终回答；
- 一响应多 calls 按顺序执行；
- 每个 call 前检查 deadline；
- effective timeout 不超过剩余 run 时间；
- 工具错误后模型恢复；
- 模型无进展临时错误重试；
- 文本/tool fragment 进展后不重试；
- 模型流、工具前、工具中、部分结果后、Cycle 后取消；
- A A A 和 A B A B A B 重复；
- 每任务一组 started/completed；
- `Terminal.show_event` 级 mixed-content 序列测试：`text.delta → tool.status → text.delta → turn.completed`，分段正确且 final 不重复打印。

### 21.6 Stage 1 产品面

- 工具任务后普通对话；
- 工具任务后 `/handoff update` 生成合法 Handoff；
- 工具任务取消后 `/exit` dirty/fallback 行为；
- `/new` 清空 Log 和运行状态；
- StructuredCompletion 历史含 tool 消息时仍只输出目标 Schema；
- Session 没有双写；
- 十轮聊天、Provider、配置、Handoff、workspace 隔离、degraded mode、EOF 和 Ctrl+C 全量回归；
- stage-boundary 测试检查能力边界，不以目录名阻止合理实现。

默认验证至少包括：

```text
pytest
ruff check
ruff format --check
python -m compileall src tests
```

## 二十二、阶段完成标准

Stage 2 只有同时满足以下条件才能完成：

- Agent 从一个用户目标出发，经至少两个工具步骤给出最终回答；
- 已接纳 tool call 恰好一个 result；
- 任意退出不留 open Cycle；
- 半截 Provider stream 不进入历史；
- Provider 请求只含合法白名单字段；
- 工具参数、未知工具、not found、timeout、异常和截断形成规范结果；
- Runtime 不自动重试工具；
- 重复调用在阈值内提前停止，硬轮次上限始终兜底；
- 每个串行 call 都受总 deadline 约束；
- ContextBuilder 不改事实源、不做 LLM 摘要；
- Handoff/StructuredCompletion 不消费 ToolMessage 或中间 Assistant；
- 用户在模型或工具阶段取消后可以继续正常对话；
- 一个任务只有一次公开开始和一次完成；
- mixed content、工具步骤和最终答案在终端不重复且可区分；
- ConversationLog 只存在于当前进程，不改写 Handoff；
- Stage 1 全量离线回归通过；
- 没有 Stage 3/4/5 能力越界。

## 二十三、审批边界

本稿已经消除阻塞实施的架构歧义，但不再宣称所有可调数值和内部算法永远不可改变。

审批锁定：

- 阶段范围；
- 消息和 Provider wire；
- Adapter/AgentLoop/Conversation/Context/ToolExecutor 所有权；
- ToolCycle、取消和历史不变量；
- 单一聊天入口；
- Handoff/StructuredCompletion 安全投影；
- 开发者配置而非用户配置；
- 垂直切片实施顺序；
- 验收标准。

审批不锁死：

- 开发者 TOML 中的初始数字；
- canonical Cycle equality 的具体 hash 实现；
- 小职责最终是否独占一个 Python 文件；
- 终端文案的非语义措辞；
- 在不改变公开协议的前提下进行的内部性能优化。

实际审批结果：**批准**。

- 本稿的锁定条款已经机械合入[正式 Stage 2 路线](../roadmap/stage-2-agent-core.md)；
- 四个垂直切片已经写入[当前执行计划](../../.agent/PLAN.md)；
- 本文件保留 Review 裁决、设计理由和批准边界，不再与正式路线/执行计划并列维护实施状态。
