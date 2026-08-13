# 《Hermes 项目学习指南》

> 从源码理解一个现代 Agent 系统为什么会这样设计
>
> 本文基于 **Nous Research 的 Hermes Agent**（`hermes-agent`，MIT License，当前代码库版本 v0.20.0）编写。
> 所有架构结论均以真实源码与官方文档（`AGENTS.md`、`website/docs/`）为依据；个人推断处均已标注。
>
> **读者对象**：具备基本编程能力、能阅读 Python，对 LLM 与 Agent 有一定了解但未系统学习过 Agent 架构的开发者。
>
> **阅读目标**：不是简单了解 Hermes 有什么功能，而是（1）理解一个完整 Agent 系统如何设计与运行；（2）具备修改、扩展、甚至重新设计类似系统的能力。

---

## 目录

1. [Hermes 是什么：项目定位](#一hermes-是什么项目定位)
2. [Agent 基础知识：建立概念框架](#二agent-基础知识建立概念框架)
3. [项目整体架构：代码地图](#三项目整体架构代码地图)
4. [一次完整运行流程：请求的旅程](#四一次完整运行流程请求的旅程)
5. [核心模块分析](#五核心模块分析)
6. [技术栈分析](#六技术栈分析)
7. [关键数据结构](#七关键数据结构)
8. [Prompt 系统](#八prompt-系统)
9. [Tool 系统](#九tool-系统)
10. [Context 与 Memory](#十context-与-memory)
11. [Agent 控制逻辑：继续、调用工具、还是结束](#十一agent-控制逻辑继续调用工具还是结束)
12. [并发与异步模型](#十二并发与异步模型)
13. [错误处理与恢复](#十三错误处理与恢复)
14. [安全模型](#十四安全模型)
15. [设计模式：Hermes 中的软件工程思想](#十五设计模式hermes-中的软件工程思想)
16. [关键源码精读：10~20 个必读文件](#十六关键源码精读)
17. [设计决策分析：为什么这样设计](#十七设计决策分析为什么这样设计)
18. [完整架构总结：一张大图](#十八完整架构总结一张大图)
19. [开发者学习路线](#十九开发者学习路线)
20. [实践任务：从 Level 1 到 Mini Hermes](#二十实践任务从-level-1-到-mini-hermes)
21. [最终总结：掌握 Hermes 之后你学会了什么](#二十一最终总结)

---

# 一、Hermes 是什么：项目定位

## 1.1 一句话定位

> **Hermes 是一个"自我改进的通用个人 Agent"**——同一个 Agent 核心，运行在终端 CLI、TUI、Electron 桌面端、以及 25+ 消息平台（Telegram、Discord、Slack、WhatsApp…）之上；它跨会话学习（记忆 + 技能）、委托子代理并行工作、运行定时任务、操作真实终端与浏览器。

这是 Nous Research 出品的一个 **Agent Runtime + Agent Framework 的复合体**：它既是一个可以直接 `pip install` 后运行的完整产品，也是一个面向开发者的、可通过插件/技能扩展的框架。

## 1.2 它解决什么问题

Hermes 试图回答一个很实际的问题：

> **一个 LLM 要如何变成一个能"替你干活"的助手，而不是只会对话的聊天机器人？**

这需要把"智能"（LLM 的推理与语言能力）与"行动"（操作文件、终端、浏览器、消息平台、定时任务、其他模型）连接起来，并让这套系统**可靠、可解释、可扩展、能长期运行**。Hermes 正是为了满足这些工程要求而构建的。

它在设计上特别强调的四个能力（README 与 AGENTS.md 反复出现）：

1. **跨会话学习**：内置"学习回路"——从经验中创建技能（skills）、在使用中改进技能、主动把知识沉淀到记忆（memory）、搜索自己的历史会话、建立对用户的长期画像。
2. **无处不在**：CLI / TUI / 桌面 / 25+ 消息平台，一套核心处处运行。
3. **随处运行**：7 种终端后端（local、Docker、SSH、Singularity、Modal、Daytona、Vercel Sandbox），支持 serverless 持久化（空闲休眠、按需唤醒）。
4. **可扩展而不膨胀核心**：能力通过 **技能（skills）、MCP server、插件（plugins）** 在"边缘"生长，核心保持"窄腰"。

## 1.3 它属于什么类型的 Agent？

在 Agent 技术谱系中，Hermes 属于 **通用型、工具调用（Tool-Calling）驱动、长期运行（persistent）的个人 Agent**：

| 类型 | 代表 | Hermes 的定位 |
|---|---|---|
| 对话型 Chatbot | ChatGPT 网页版 | 只说话、不动手。Hermes **会动手**。 |
| 代码 Agent（code agent） | Claude Code、OpenAI Codex、OpenHands、OpenCode | 专注软件工程任务。Hermes 也能写代码，但**不止于此**——它还管记忆、技能、定时任务、多平台消息。 |
| 自治 Agent（autonomous agent） | AutoGPT | 长时间自主运行。Hermes 支持自主运行（cron、background delegation），但**默认要求人在环**（approval、clarify），并为此设计了完善的审批模型。 |
| 多 Agent 编排框架 | LangGraph、CrewAI | 框架，需要你写图/写角色来构建应用。Hermes 是**可直接使用的成品**，同时内部也支持子代理（delegation）与多代理看板（kanban）。 |
| Agent Framework | LangChain、Semantic Kernel | 库/框架。Hermes 是产品 + 框架的复合体。 |

### 1.3.1 与普通 Chatbot 的区别

Chatbot 是"单轮输入 → 文本输出"的映射。Hermes 的核心循环是：

```text
User Input
   ↓
LLM 推理 → 决定"我需要调用工具"（tool_calls）
   ↓
执行工具（读文件/跑命令/搜网页…）→ 得到 Observation
   ↓
把 Observation 送回 LLM → 继续推理
   ↓
直到 LLM 认为任务完成 → 输出最终答案
```

LLM 不是被当作"文本生成器"，而是被当作**一个能感知工具结果、并据此决策的推理引擎**。

### 1.3.2 与普通 LLM API Wrapper 的区别

一个 wrapper（例如 100 行的 OpenAI 封装）只做 `prompt → response`。Hermes 在 LLM 之上叠加了：

- 工具系统（注册、schema 收集、调度、审批、错误恢复）
- 上下文管理（压缩、prompt caching、会话持久化）
- 记忆与技能系统（跨会话）
- 多平台接入（gateway）
- 子代理、定时任务、插件、皮肤、i18n……

**LLM 只占 Hermes 代码的一部分。** 工程上真正的复杂度全在 LLM 之外的"环"。

### 1.3.3 与传统 Workflow 系统的区别

传统 workflow（如 Zapier、n8n）是**预编程的**：节点固定、路径固定。Hermes 是**模型驱动的**：每一步调用哪个工具、按什么顺序、循环多少次，由 LLM 在运行时根据上下文自主决定。Workflow 是"确定性程序"，Agent 是"概率性决策 + 约束护栏"。

## 1.4 Hermes 的核心能力清单（来自 README）

- **多入口**：CLI（`hermes`）、TUI（`hermes --tui`）、Electron 桌面、Gateway（Telegram/Discord/Slack/WhatsApp/Signal/邮件 等 25+ 平台）、ACP（VS Code/Zed/JetBrains IDE 集成）。
- **学习回路**：Agent 创建的技能（`created_by: agent`）会被后台 Curator 审查、归档；使用 `skill_manage` 可改进技能；`session_search` 用 FTS5 全文搜索 + LLM 摘要实现跨会话回忆；内存系统注入上下文。
- **调度**：内置 cron 调度器，支持自然语言计划（`"every monday 9am"`）、cron 表达式、ISO 时间戳、持续时长。
- **委托与并行**：`delegate_task` 生成隔离上下文子代理；`tasks: [...]` 批量并行；`background=true` 异步委托。
- **随处运行**：7 种终端后端；Daytona/Modal 的 serverless 持久化。
- **研究与数据**：批量轨迹生成（`batch_runner.py`）、轨迹压缩（`trajectory_compressor.py`）用于训练下一代 tool-calling 模型。
- **模型无关**：任意 provider（Nous Portal、OpenRouter、OpenAI、Anthropic、本地端点…），`hermes model` 热切换。

## 1.5 两条最重要的设计原则（AGENTS.md 明示）

在深入任何代码之前，必须理解这两条原则——它们塑造了 Hermes 几乎所有设计决策：

> **原则一：逐会话的 Prompt Caching 是神圣的（sacred）。**
> 长会话每一轮都复用缓存的提示词前缀。任何"中途修改历史上下文、中途切换工具集、中途重建 system prompt"的行为都会使缓存失效、成倍增加用户成本。Hermes 不做这些事（唯一例外是上下文压缩）。

> **原则二：核心是"窄腰"，能力在"边缘"。**
> 每个核心 tool 都会出现在每一次 API 调用里（每个 tool 的 schema 都会发给模型），所以新增核心 tool 的门槛极高。新能力应优先以 CLI 命令 + 技能、服务门控工具、插件、MCP server 的形式加入——**而不是扩大核心面**。

这两条原则分别解释了 Hermes 里两个最深刻的架构现象：

- 为什么 system prompt 是"三层缓存分级"（stable/context/volatile）？
- 为什么工具、记忆、记忆 provider、context engine 全部是可插拔的？

下文会反复回到这两条原则。

---

# 二、Agent 基础知识：建立概念框架

在进入 Hermes 源码之前，先用最必要的概念搭一个框架。下面每个概念都是"它是什么 → 为什么需要它 → Hermes 中如何体现"，读完本节后你会具备理解源码的词汇表。

## 2.1 从 LLM 到 Agent Framework：四层关系

```text
LLM（模型）        —— 概率性的 token 预测器，输入文本输出文本。
   ↓
Agent（个体）      —— 用 LLM 做"推理引擎"，让它循环地：思考 → 行动(工具) → 观察 → 再思考。
   ↓
Agent Runtime     —— 支撑 Agent 循环的运行时：工具调度、上下文管理、状态持久化、错误恢复、审批。
   ↓
Agent Framework   —— 把 Runtime 抽象成可复用、可扩展的接口/框架，供不同 Agent 复用。
```

- **LLM**：Hermes 通过 provider 抽象接入任意模型（见 `providers/base.py` 的 `ProviderProfile`）。
- **Agent**：`run_agent.py` 的 `AIAgent` 类（以及已迁出的 `agent/conversation_loop.py` 的 `run_conversation()`）。
- **Agent Runtime**：`model_tools.py`（工具编排）、`tools/registry.py`（工具注册表）、`hermes_state.py`（状态持久化）、`agent/context_compressor.py`（上下文压缩）等。
- **Agent Framework**：Hermes 的插件系统（`hermes_cli/plugins.py`）、技能系统、MCP 客户端——把"核心窄腰"扩展成可插拔框架。

## 2.2 Agent Loop（Agent 循环）

### 它是什么

Agent Loop 是一个 **while 循环**，是 Agent 的心脏。典型结构：

```text
User Input
   ↓
LLM Reasoning（模型决定下一步）
   ↓
是否要调用工具？ ──否──→ 输出最终答案，结束
   ↓是
Tool Selection（选择工具）
   ↓
Tool Execution（执行工具）
   ↓
Observation（观察结果）
   ↓
回到 LLM Reasoning（把结果喂回模型）
```

### 为什么需要它

单次 LLM 调用无法"做事"——它只能吐字。要把"读文件 → 改代码 → 跑测试 → 修 bug → 再跑测试"这种多步骤任务串起来，就必须让模型在**动作与观察之间循环**，每轮看到新信息再做决策。这就是 ReAct 范式的本质。

### Hermes 中如何体现

Hermes 的主循环在 `agent/conversation_loop.py:1634`：

```python
while (api_call_count < agent.max_iterations and agent.iteration_budget.remaining > 0) or agent._budget_grace_call:
    # 1. 检查用户中断
    if agent._interrupt_requested:
        break
    api_call_count += 1
    # 2. 预算检查（每轮消耗一次迭代预算）
    if not agent.iteration_budget.consume():
        break
    # 3. 组装 api_messages（克隆消息、剥离内部字段、注入瞬时上下文）
    ...
    # 4. 调用 LLM
    response = client.chat.completions.create(...)
    # 5. 解析响应
    if assistant_message.tool_calls:
        # 6. 校验/修复/去重 tool calls
        ...
        # 7. 执行工具（并发或顺序），结果以 role="tool" 消息追加回 messages
        agent._execute_tool_calls(...)
        # 8. 回到循环顶部，带着工具结果再次调用 LLM
    else:
        # 9. 无工具调用 → 这是最终回答
        final_response = ...
        break
```

（简化示意；`conversation_loop.py:1634-1660` 是真实主循环头，工具调用分支见 `conversation_loop.py:6349` 起，工具执行在 `conversation_loop.py:6766`。）

### 对整个 Agent 系统的影响

Agent Loop 的质量决定了整个系统的上限。Hermes 在"裸循环"之上加了大量工程约束：迭代预算（防止死循环）、中断（用户可随时打断）、角色交替校验（防止无效 API 请求）、无效工具名/无效 JSON 的重试与修复、预算宽限调用（grace call）等。理解这些护栏，是理解 Agent 工程的第一步。

## 2.3 Tool Calling（Function Calling）

### 它是什么

Tool Calling 是"让 LLM 在回答中**声明**要调用某个函数"的协议能力。模型不执行函数，它输出一个结构化的"函数调用请求"（工具名 + 参数 JSON）；由运行时来真正执行，再把结果作为新的消息喂回模型。

### Tool Schema 是什么

为了让模型知道"有哪些函数、各有什么参数"，运行时把每个工具的描述打包成 **JSON Schema** 发给模型（OpenAI 的 `{"type": "function", "function": {name, description, parameters}}` 格式）。模型据此选择。

### LLM 如何选择 Tool

LLM 在做"next token prediction"时，如果响应中出现了 `tool_calls` 字段，就表示它决定调用某个工具。这是一个**概率决策**——模型可能选错工具、给错参数、甚至编造不存在的工具，所以运行时必须有校验与容错（Hermes 为此实现了工具名修复、参数 JSON 校验、错误结果回喂等）。

### Tool 如何被执行 / 结果如何返回

运行时收到 `tool_calls` 后：

```text
Tool Call（名字+参数）
   ↓
查找 Tool Registry → 命中 handler
   ↓
（可选）安全检查 / 审批
   ↓
执行 handler(args)
   ↓
结果字符串以 {"role": "tool", "tool_call_id": "...", "content": "..."} 追加进 messages
   ↓
下一轮 LLM 调用能看到这个结果（Observation）
```

### Hermes 中对应的实现

- Schema 收集：`model_tools.py:get_tool_definitions()` + `tools/registry.py:get_definitions()`。
- 调度：`model_tools.py:handle_function_call()` → `tools/registry.py:dispatch()`。
- 结果消息：`{"role": "tool", "name": ..., "tool_call_id": ..., "content": <JSON 字符串>}`。
- 约定：**每个工具 handler 必须返回 JSON 字符串**（`tools/registry.py:958` 的注释明言；`tool_result()` / `tool_error()` 是统一帮手）。

## 2.4 Agent State（Agent 状态）

### 为什么需要状态

Agent 不是无状态函数。它需要记住：

| 状态类型 | 内容 | Hermes 中的承载 |
|---|---|---|
| Conversation State | 消息历史 | `messages` 列表（内存）+ SQLite `messages` 表（持久） |
| Task State | 当前任务的进行到哪了 | `task_id`、`todo` 工具、迭代预算 |
| Execution State | 正在跑什么、可否中断 | `_interrupt_requested`、`_executing_tools`、信号处理 |
| Tool State | 终端/浏览器/子代理等资源 | `task_id` 隔离的终端环境、`process_registry` |
| Memory State | 跨会话的知识 | `MEMORY.md` / `USER.md` 文件 + memory provider 插件 |

### Hermes 如何保存与管理

- **会话级**：`hermes_state.py:SessionDB`（SQLite，WAL 模式），消息逐条持久化；`append_message()` 是转录的"关键写路径"，失败即中止本轮。
- **运行级**：`messages` 在内存中维护，每轮克隆后发给 API（`_clone_message_for_send`），避免内部字段泄漏。
- **跨会话**：记忆（`MEMORY.md`/`USER.md`）与技能（`SKILL.md`）落盘，会话恢复时重新注入。

## 2.5 Memory（记忆）

### Context ≠ Memory

- **Context**：模型当前能看到的所有 token（系统提示 + 历史消息 + 工具结果），受上下文窗口限制，随对话增长而膨胀。
- **Memory**：Agent 主动保存的、**跨会话**仍有效的信息（用户偏好、项目约定、长期事实）。

对话历史 ≠ 记忆：对话历史是"这轮讲了什么"，记忆是"以后还要记住什么"。

### 记忆的类型

| 类型 | 生命周期 | Hermes 实现 |
|---|---|---|
| Short-term | 单会话 | `messages` 历史 |
| Long-term | 跨会话 | `MEMORY.md`（agent 用 `memory` 工具写入的事实）+ `USER.md`（用户画像） |
| Persistent | 磁盘/服务 | SQLite sessions、`~/.hermes/memories/`、memory provider 插件 |
| Retrieval-based | 按需 | `session_search`（FTS5 + LLM 摘要）、memory provider 的 `prefetch` |

### Hermes 是否实现

**是，而且很完整**。内置本地记忆（`MEMORY.md`/`USER.md`）之外，还有可插拔的外部 memory provider（honcho、mem0、supermemory 等，见 `plugins/memory/` 与 `agent/memory_manager.py`），以及跨会话搜索（`hermes_state_search.py`）。**关键设计**：记忆注入要"不破坏 prompt cache"——见 §10。

---

> 下一章进入代码地图。请记住本章的两个核心概念：**Agent Loop**（while 循环 + 工具往返）与 **Prompt Cache 神圣性**（系统提示词必须字节稳定）。它们是理解后面所有代码的钥匙。

---

# 三、项目整体架构：代码地图

## 3.1 目录树（基于真实代码）

> 文件数量持续变化，`AGENTS.md:263-309` 是项目自述的权威结构。以下是最重要的部分（文件行数基于 v0.20.0）：

```text
hermes-agent/
│
├── run_agent.py                # AIAgent 类 —— 核心 Agent 的门面（~8.3k 行）
├── model_tools.py              # 工具编排：get_tool_definitions / handle_function_call（~1.6k 行）
├── toolsets.py                 # TOOLSETS 定义 + _HERMES_CORE_TOOLS（核心工具清单）
├── cli.py                      # HermesCLI —— 经典交互式 CLI（~19k 行）
├── hermes_state.py             # SessionDB —— SQLite 会话存储（WAL + FTS5，~11k 行）
├── hermes_state_schema.py      # SQL schema 定义与迁移
├── hermes_state_search.py      # FTS5 会话搜索 + LLM 摘要 recall
├── hermes_constants.py         # get_hermes_home() —— profile 感知的路径解析
├── hermes_logging.py           # setup_logging() —— agent.log / errors.log / gateway.log
├── hermes_time.py              # 统一的"现在时刻"（可测试、可注入）
├── hermes_bootstrap.py         # 启动自举
├── batch_runner.py             # 批量轨迹生成（研究/训练数据）
├── trajectory_compressor.py    # 轨迹压缩（训练 tool-calling 模型用）
├── mcp_serve.py                # 把 Hermes 自身暴露为 MCP server
├── setup.py / pyproject.toml   # 打包（wheel 构建被禁用，仅 Nix/editable）
│
├── agent/                      # ★ Agent 内部核心（~140 个模块）
│   ├── conversation_loop.py    # ★★ run_conversation —— 真正的主循环（~7.7k 行）
│   ├── agent_init.py           # AIAgent 的真正初始化（构建运行时）
│   ├── turn_context.py         # 每轮开场的上下文构建（prologue）
│   ├── turn_finalizer.py       # 每轮收尾（finalize_turn）
│   ├── tool_executor.py        # 工具执行：并发/顺序/分段（~2.4k 行）
│   ├── agent_runtime_helpers.py# invoke_tool —— 单工具调用入口（~4k 行）
│   ├── tool_dispatch_helpers.py# 工具批分段规划（并行安全分析）
│   ├── system_prompt.py        # ★ 三层缓存分级 system prompt（stable/context/volatile）
│   ├── prompt_builder.py       # SOUL.md / context files / memory 格式化
│   ├── prompt_caching.py       # Anthropic cache_control 断点（system_and_3 策略）
│   ├── prompt_cache_boundary.py# 缓存前缀边界保护
│   ├── context_engine.py       # ContextEngine ABC —— 可插拔压缩引擎
│   ├── context_compressor.py   # 默认压缩引擎（有损摘要，4 阶段算法）
│   ├── conversation_compression.py / native_compaction.py  # 压缩的实现变体
│   ├── memory_manager.py       # MemoryManager —— 编排外部 memory provider
│   ├── memory_provider.py      # MemoryProvider ABC
│   ├── memory_store.py         # 内置文件记忆（MEMORY.md / USER.md）
│   ├── auxiliary_client.py     # 旁路 LLM（压缩摘要/视觉/embedding/title 等）
│   ├── chat_completion_helpers.py  # chat_completions / codex_responses 传输层
│   ├── anthropic_adapter.py    # Anthropic Messages API 格式转换
│   ├── codex_responses_adapter.py / gemini_native_adapter.py
│   ├── vertex_adapter.py / bedrock_adapter.py / azure_identity_adapter.py
│   ├── retry_utils.py          # 重试/退避工具
│   ├── error_classifier.py     # 错误分类（决定是否可重试/需换模型）
│   ├── iteration_budget.py     # IterationBudget —— 迭代预算
│   ├── subagent_lifecycle.py   # 子代理生命周期
│   ├── delegation_context.py   # 委托上下文
│   ├── curator.py / curator_backup.py  # 技能后台维护
│   ├── skill_commands.py / skill_utils.py / skill_preprocessing.py
│   ├── turn_summary.py         # 每轮小结
│   ├── background_review.py    # 后台记忆/技能审查
│   ├── trajectory.py           # 轨迹保存
│   ├── display.py              # KawaiiSpinner、工具预览
│   ├── model_metadata.py       # 模型上下文长度、token 估算
│   ├── models_dev.py           # models.dev 注册表集成
│   └── ...（monitoring、secret_sources、proxy_sources、transports、pet 等）
│
├── tools/                      # ★ 工具实现（一文件一工具，自动发现）
│   ├── registry.py             # ★★ ToolRegistry —— 工具注册表（~1k 行，零依赖）
│   ├── approval.py             # 危险命令检测 + 审批门（shell 级反混淆）
│   ├── terminal_tool.py        # 终端工具（7 后端、审批、sudo 处理，~4k 行）
│   ├── file_tools.py           # read_file / write_file / patch / search_files
│   ├── web_tools.py            # web_search / web_extract
│   ├── browser_tool.py         # 浏览器自动化（~10 个 browser_* 工具）
│   ├── code_execution_tool.py  # execute_code 沙箱
│   ├── delegate_tool.py        # delegate_task —— 子代理委托
│   ├── async_delegation.py     # 异步委托完成队列
│   ├── mcp_tool.py             # ★ MCP client（~7k 行：连接/生命周期/OAuth/动态发现）
│   ├── mcp_schema_cache.py     # MCP 工具 schema 磁盘缓存（惰性启动）
│   ├── mcp_stdio_watchdog.py   # stdio 子进程看门狗
│   ├── mcp_oauth.py / mcp_oauth_manager.py  # MCP OAuth 2.1 + PKCE
│   ├── memory_tool.py          # memory 工具（写 MEMORY.md）
│   ├── todo_tool.py            # todo 工具
│   ├── skills_tool.py / skills_hub.py  # 技能管理工具
│   ├── session_search_tool.py  # 会话搜索工具
│   ├── cronjob_tools.py        # 定时任务工具
│   ├── kanban_tools.py         # 多代理看板工具
│   ├── clarify_tool.py         # 向用户提问工具
│   ├── path_security.py        # 路径越权防护
│   ├── write_approval.py       # 写入审批
│   ├── url_safety.py           # URL 安全
│   ├── tirith_security.py / threat_patterns.py  # 注入威胁模式
│   ├── schema_sanitizer.py     # schema 清洗
│   ├── lazy_deps.py            # 按需安装依赖
│   ├── tool_search.py          # 工具搜索桥（大工具集场景）
│   ├── environments/           # ★ 终端后端：base/local/docker/ssh/modal/
│   │                           #   managed_modal/daytona/singularity/vercel_sandbox/file_sync
│   └── ...（41 个内置工具文件注册了工具；含 MCP/插件动态工具共 70+ 个，分布于 ~28 个 toolset）
│
├── hermes_cli/                 # ★ CLI 子命令与运行时
│   ├── main.py                 # 入口 main() —— 全部 hermes 子命令（~11k 行）
│   ├── commands.py             # COMMAND_REGISTRY —— 所有斜杠命令的单一事实来源
│   ├── config.py               # DEFAULT_CONFIG / OPTIONAL_ENV_VARS / 迁移
│   ├── config_defaults.py      # 默认配置
│   ├── plugins.py              # PluginManager —— 插件发现/加载/hooks
│   ├── skin_engine.py          # 数据驱动主题引擎
│   ├── tools_config.py         # hermes tools —— 按平台启停工具
│   ├── curses_ui.py            # 交互式菜单（强制用 curses）
│   ├── middleware.py           # 工具请求/执行中间件
│   ├── auth.py / runtime_provider.py  # provider 凭证与解析
│   ├── model_switch.py         # /model 热切换
│   ├── setup.py                # 安装向导
│   └── ...（196 个模块）
│
├── gateway/                    # ★ 消息网关（多平台）
│   ├── run.py                  # GatewayRunner —— 消息调度（~25k 行）
│   ├── session.py              # 网关会话
│   ├── delivery.py             # 出站投递
│   ├── pairing.py              # DM 配对授权
│   ├── stream_consumer.py / stream_dispatch.py  # 流式输出回传
│   ├── turn_context.py / turn_lease.py          # 轮次租约/上下文
│   ├── hooks.py / builtin_hooks/                # 网关 hooks
│   ├── platform_registry.py    # 平台注册表
│   └── platforms/              # 内置适配器（signal/weixin/whatsapp_cloud/yuanbao/webhook/api_server/qqbot/bluebubbles/...）
│
├── plugins/                    # ★ 插件（memory / context_engine / model-providers / kanban / ...）
│   ├── memory/                 # honcho, mem0, supermemory, hindsight, byterover, ...
│   ├── context_engine/         # 替代压缩引擎
│   ├── model-providers/        # ★ 每个推理后端一个插件（openrouter/anthropic/gemini/bedrock/...）
│   ├── kanban/                 # 多代理看板
│   └── ...
│
├── providers/                  # ProviderProfile 基类 + 注册表（plugins/model-providers 的落地层）
│   ├── base.py                 # ★ ProviderProfile dataclass（声明式）
│   └── __init__.py             # register_provider / _discover_providers
│
├── cron/                       # 定时任务（jobs.py 存储 + scheduler.py tick 循环）
├── skills/                     # 内置技能（按类别：software-development/mlops/...）
├── optional-skills/            # 可选重型技能（官方目录）
├── optional-mcps/              # comfy-cloud / figma / linear / n8n / unreal-engine
├── acp_adapter/                # ACP server（VS Code / Zed / JetBrains）
├── tui_gateway/                # TUI 的 Python JSON-RPC 后端
├── ui-tui/                     # Ink(React) 终端 UI（TypeScript）
├── apps/                       # Electron 桌面端
├── web/                        # Web 仪表盘
├── website/                    # Docusaurus 文档站
├── tests/                      # ~17k-25k 测试
└── docs/                       # 内部设计文档（security/observability/kanban/...）
```

## 3.2 分层架构图（官方 architecture.md + 实际代码）

```text
┌────────────────────────────────────────────────────────────────────┐
│                          入口层 Entry Points                        │
│   CLI(cli.py)   TUI(ui-tui)   Gateway(gateway/run.py)   ACP         │
│   BatchRunner   API Server    Electron桌面              Python库    │
└───────────────┬──────────────────────┬─────────────────────────────┘
                │                      │
                ▼                      ▼
┌────────────────────────────────────────────────────────────────────┐
│                 Agent 核心：AIAgent（run_agent.py）                  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                 │
│  │ Prompt      │  │ Provider    │  │ Tool        │                 │
│  │ Builder     │  │ Resolution  │  │ Dispatch    │                 │
│  │ (system_    │  │ (runtime_   │  │ (model_     │                 │
│  │  prompt.py) │  │  provider)  │  │  tools.py)  │                 │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘                 │
│  ┌──────┴───────┐  ┌─────┴───────┐  ┌─────┴───────┐                 │
│  │ Compression  │  │ 3 API Modes │  │ Tool        │                 │
│  │ & Caching    │  │ chat_comp.  │  │ Registry    │                 │
│  │ (context_    │  │ codex_resp. │  │ (registry)  │                 │
│  │  compressor) │  │ anthropic   │  └─────┬───────┘                 │
│  └──────────────┘  └─────────────┘        │                        │
└──────────────────┬─────────────────────────┼────────────────────────┘
                   ▼                         ▼
┌──────────────────────┐     ┌──────────────────────────────┐
│ 会话存储 SessionDB    │     │ 工具后端 Tool Backends        │
│ (SQLite + FTS5)      │     │ 终端(7后端) 浏览器(5) 网页(4)  │
│ hermes_state.py      │     │ MCP(动态) 文件 视觉 沙箱 等    │
└──────────────────────┘     └──────────────────────────────┘
```

**核心观察**：AIAgent 是唯一的"核心窄腰"——CLI、网关、ACP、cron、批量都复用同一个 Agent 类；平台差异只在入口层。这正呼应了官方架构文档的"Platform-agnostic core"设计原则。

## 3.3 关键依赖链：为什么工具注册发生在 import 时

`AGENTS.md` 与官方文档都强调这条依赖链：

```text
tools/registry.py    （零依赖 —— 所有工具文件都 import 它）
      ↑
tools/*.py           （每个工具文件在 import 时调用 registry.register()）
      ↑
model_tools.py       （import tools/registry + 触发工具发现）
      ↑
run_agent.py, cli.py, batch_runner.py, environments/
```

含义：**任何带顶层 `registry.register()` 调用的 `tools/*.py` 都会被自动发现，无需维护手工 import 列表**（`registry.py:discover_builtin_tools()` 用 AST 扫描检测）。"注册进 registry"（import 时发生）与"暴露给 agent"（必须出现在某个 toolset 里）是两件独立的事——`_HERMES_CORE_TOOLS` 不是死代码，它是每个平台基础工具集的默认底包（`toolsets.py:31`）。

## 3.4 会话持久化：state.db 的表结构

`hermes_state_schema.py` 定义 schema（当前版本 23），官方 session-storage.md 给了权威表结构。核心表：

```text
state.db (SQLite, WAL mode)
├── sessions             — 会话元数据（model/tokens/成本/title/lineage…）
├── messages             — 每会话的完整消息历史
├── session_model_usage  — 按模型/任务的 token 归属
├── messages_fts         — FTS5 虚拟表（content/tool_name/tool_calls）
├── messages_fts_trigram — trigram tokenizer（CJK/子串搜索）
├── messages_fts_cjk     — cjk_unicode61 tokenizer
├── state_meta           — KV 元数据
├── gateway_routing      — 网关路由元数据
├── compression_locks    — 跨进程压缩锁
├── async_delegations    — 异步委托记账
└── schema_version       — 迁移版本
```

**关键设计**：WAL 模式（多读单写，网关多平台并发）；FTS5 用触发器与基表同步；写入竞争用"短超时 + 应用层随机抖动重试"避免 SQLite 默认退避的队形效应（convoy effect）（`hermes_state.py:2428` 的大段注释是必读）。

---

# 四、一次完整运行流程：请求的旅程

选择官方文档最常用的例子：**"读取一个文件并总结内容"**。追踪它在 Hermes 内部的生命周期。

> 说明：CLI 模式走 `cli.py`；网关模式走 `gateway/run.py`，但二者最终都汇入同一条 `AIAgent → run_conversation` 链路。下面以 CLI 为线。

## 4.1 请求进入系统

```text
用户输入 "读取 main.py 并总结内容" 并回车
   │
   ▼
cli.py: HermesCLI 的交互循环（prompt_toolkit 读取输入）
   │
   ▼
cli.py: process_command() 判断这不是斜杠命令（/开头），而是普通消息
   │
   ▼
cli.py: self.agent.run_conversation(message, ...)
```

- 交互循环在 `cli.py` 的 `HermesCLI` 类中；`process_command()` 用 `hermes_cli/commands.py:COMMAND_REGISTRY` 解析斜杠命令；普通消息直接进入 agent。
- 网关模式中，`gateway/run.py:GatewayRunner._run_agent_inner()`（`gateway/run.py:25156`）在**线程池**中调用 `run_conversation`，以免阻塞事件循环。

## 4.2 Agent 接收与开场（Prologue）

```text
run_agent.py: run_conversation()          —— 转发器
   │
   ▼
agent/conversation_loop.py: run_conversation()  —— 真正的主循环（:1422）
   │
   ├─ build_turn_context()                —— 每轮开场（agent/turn_context.py）
   │     ├─ 生成 task_id / turn_id
   │     ├─ 恢复或构建 system prompt（_restore_or_build_system_prompt）
   │     ├─ 检查本轮是否需要"开场压缩"（preflight compression）
   │     ├─ 注入瞬时上下文（memory prefetch / plugin context）
   │     └─ 持久化开场状态（崩溃恢复）
   │
   ▼
```

`_restore_or_build_system_prompt`（`conversation_loop.py:555`）是缓存意识的关键：**系统提示词要么从会话恢复（保持字节稳定以命中 prompt cache），要么首次构建并缓存**。它绝不每轮重建。

## 4.3 组装 messages

```text
messages = [system, ...历史..., user]
   │
   ▼
conversation_loop.py 主循环（:1634）内：
   │
   ├─ 检查中断 _interrupt_requested
   ├─ 消耗迭代预算 iteration_budget.consume()
   ├─ 克隆每条消息（_clone_message_for_send）—— 防止内部字段泄漏到 API
   ├─ 弹出内部字段：api_content / display_kind / display_metadata / _row_id
   ├─ 注入 api_content（本次用户消息的真实 API 字节，可能含 memory prefetch 注入）
   ├─ 修复角色交替（repair_message_sequence）
   └─ 交给传输层
```

## 4.4 选择模型与 Provider

```text
providers/base.py: ProviderProfile（声明式，含 api_mode/auth/env_vars/模型目录…）
   │
   ▼
runtime_provider.resolve_runtime_provider()（hermes_cli/runtime_provider.py）
   │  把 (provider, model) 解析成 (api_mode, api_key, base_url)
   ▼
agent/chat_completion_helpers.py: 三种 API 模式之一
   ├─ chat_completions：OpenAI SDK，格式原样
   ├─ codex_responses：Responses API 格式转换
   └─ anthropic_messages：anthropic_adapter.py 转换格式
```

Provider 的解析顺序（官方 agent-loop.md）：显式 api_mode 参数 → provider 名检测 → base URL 启发式 → 默认 `chat_completions`。

## 4.5 调用 LLM

```text
client.chat.completions.create(model=model, messages=api_messages, tools=tool_schemas)
   │
   ▼
响应可能是：
   ├─ assistant 消息带 tool_calls（模型要调用工具）
   └─ assistant 消息纯文本（最终回答）
```

调用在**后台线程**执行以便中断（`_interruptible_api_call` 模式）；用户发新消息或 `/stop` 时通过 interrupt event 放弃在途请求。

## 4.6 解析响应，识别 Tool Call

```text
assistant_message.tool_calls 存在？
   │
   ├─ 否 → final_response = response.content，退出循环
   │
   ▼
   └─ 是（conversation_loop.py:6349 起）：
      ├─ 对重复 tool_call id 去重（_uniquify_tool_call_ids）
      ├─ 校验工具名：不在 agent.valid_tool_names 中的名字先尝试自动修复
      │   （_repair_tool_call：大小写/连字符/类名后缀归一 + 模糊匹配）
      ├─ 无效工具名：把错误结果注入 messages，让模型自纠（3 次上限）
      ├─ 校验参数 JSON：空串当 {}；无效 JSON 重试（3 次），仍失败则注入恢复结果
      ├─ 守卫：_cap_delegate_task_calls / _deduplicate_tool_calls
      └─ 追加 assistant 消息（含 tool_calls）
```

这段代码（`conversation_loop.py:6367-6560`）是**模型不可靠性工程**的教科书：模型会编造工具名、给残缺 JSON、批量里混入无效调用——运行时必须以"不浪费有效调用、不卡死会话"的方式处理。

## 4.7 执行 Tool

```text
agent._execute_tool_calls(assistant_message, messages, task_id, ...)
   │  （run_agent.py:7728，会先做"批分段规划" _plan_tool_batch_segments：
   │    只读/互不冲突的工具可并发，交互式/危险工具串行）
   │
   ▼
agent._invoke_tool(name, args, ...)   （agent/agent_runtime_helpers.py:invoke_tool）
   │
   ├─ 先拦截"agent-level 工具"：todo / memory / session_search / clarify /
   │   delegate_task 等（它们直接操作 agent 状态，不经过 registry）
   │
   └─ 其余 → model_tools.handle_function_call(name, args, ...)（:1160）
         ├─ coerce_tool_args：把字符串参数按 schema 转成类型（"42"→42）
         ├─ 插件 pre_tool_call hook / 中间件（可 block）
         ├─ ACP 编辑审批门（write_file/patch）
         ├─ registry.dispatch(name, args)  （tools/registry.py:801）
         │    ├─ 找到 ToolEntry → 调 handler（async handler 自动桥接）
         │    └─ 所有异常 → tool_error(JSON) 字符串返回
         └─ 结果规范化为 JSON 字符串
```

**以 `read_file` 为例**：`handle_function_call("read_file", {"path": "main.py"}, task_id)` → registry 命中 `file_tools.py` 的 handler → 路径安全检查（`path_security.py`）→ 读文件 → 返回 `{"success": true, "content": "..."}` JSON 字符串。

## 4.8 Tool Result 回到 Agent

```text
{"role": "tool", "name": "read_file", "tool_call_id": "...", "content": "{...}"}
   追加到 messages
   │
   ▼
循环回到 4.3，带上这个 Observation 再次调用 LLM
```

## 4.9 Agent 决定继续还是结束

- **有新的 tool_calls** → 继续循环（受 `max_iterations` 与迭代预算约束）。
- **纯文本响应** → 结束循环，进入 `finalize_turn`（`agent/turn_finalizer.py`）。

## 4.10 收尾与持久化

```text
finalize_turn:
  ├─ 把最终 assistant 消息 append 进 messages
  ├─ _persist_session：把本轮所有新消息 flush 到 SessionDB（SQLite）
  ├─ 触发 memory 同步（sync_turn）/ 技能审查（background_review）
  ├─ 计算 usage / 记账
  └─ 返回 {final_response, messages, api_calls, completed, ...}
   │
   ▼
CLI：打印最终响应框 → 会话结束
```

## 4.11 调用链速查（给读者的"代码导航图"）

```text
CLI 输入
  → cli.py: HermesCLI.process_command() / 直接转发
  → run_agent.py: AIAgent.run_conversation()        (:7894，转发器)
  → agent/conversation_loop.py: run_conversation()  (:1422，主循环)
      → agent/turn_context.py: build_turn_context()  (每轮开场)
      → (LLM 调用) agent/chat_completion_helpers.py / adapters
      → 有 tool_calls 时：
          → agent/conversation_loop.py: agent._execute_tool_calls() (:6766)
          → agent/agent_runtime_helpers.py: invoke_tool() (:2813)
          → model_tools.py: handle_function_call()   (:1160)
              → tools/registry.py: registry.dispatch() (:801)
              → tools/file_tools.py: <handler>
          → 结果 {"role":"tool",...} 追加回 messages
      → agent/turn_finalizer.py: finalize_turn()      (收尾)
  → hermes_state.py: SessionDB.append_message()       (持久化)
  → 返回 {final_response, ...} 给入口层展示
```

---

# 五、核心模块分析

> 本节是全文最重的一节。每个模块统一按"解决什么问题 → 基础概念 → 如何实现 → 核心源码 → 关键类/函数 → 调用关系 → 数据流 → 为什么这样设计 → 替代方案 → 优点与局限"组织。建议按顺序阅读，配合 §16 的精读文件清单。

## 5.1 Agent Runtime 与 Agent Loop

### 解决什么问题

把"一条用户消息"变成"一次完整的工具调用回合"，并保证这个回合可靠、可中断、可预算、可持久化。

### 基础概念

回顾 §2.2 的 Agent Loop。Hermes 的 runtime 在这个裸循环上叠加了大量工程约束。

### Hermes 中如何实现

历史上核心循环全在 `run_agent.py`（约 12k 行）。AGENTS.md 明确鼓励把 god-file 拆成模块，所以现在：

| 文件 | 职责 |
|---|---|
| `run_agent.py` | `AIAgent` **门面（Facade）**——构造函数转发给 `agent/agent_init.py`，`run_conversation` 转发给 `agent/conversation_loop.py`，`chat` 是简单接口 |
| `agent/agent_init.py` | 真正的初始化（`init_agent()`，`agent_init.py:459`）：解析凭证、构建 provider client、加载工具集、构建 memory/skills/todo、设置压缩引擎 |
| `agent/conversation_loop.py` | 真正的主循环 `run_conversation()`（`:1422`），每轮开场的 `build_turn_context` |
| `agent/turn_finalizer.py` | `finalize_turn()`——回合收尾（持久化、memory 同步、usage 记账） |

### 核心源码与关键函数

- `run_agent.py:412` `AIAgent`（门面类）
- `agent/conversation_loop.py:1422` `run_conversation()`——主循环
- `agent/conversation_loop.py:1634` `while (...)`——循环头（中断检查 + 预算消耗）
- `agent/conversation_loop.py:6349` 起——tool_calls 处理分支
- `agent/turn_context.py` `build_turn_context()`——每轮开场
- `agent/iteration_budget.py` `IterationBudget`——迭代预算

### 调用关系

```text
入口层 → AIAgent.run_conversation() → conversation_loop.run_conversation()
   → build_turn_context()          （开场）
   → while 主循环
        → 组装 api_messages
        → relay_llm / chat_completion_helpers（LLM 调用）
        → assistant_message.tool_calls ?
             ├─ 是 → _execute_tool_calls() → invoke_tool() → handle_function_call() → registry.dispatch()
             └─ 否 → final_response
   → finalize_turn()               （收尾）
```

### 为什么这样设计

1. **门面 + 模块拆分**：`AIAgent` 保持稳定的外部接口（CLI/gateway/cron 都依赖它），内部实现自由演进。这是大型单体的演进策略，而非一开始就设计好的。
2. **同步主循环 + 线程化工具**：主循环是同步的、可读的、易测试的；耗时工具（LLM 调用、终端命令）在后台线程跑，配合中断事件实现"可打断"。
3. **预算与宽限**：`max_iterations`（默认 90）+ `iteration_budget` 双闸门防止死循环；`_budget_grace_call` 给模型"最后一轮"收尾的机会；`execute_code` 回合 `refund()`（不耗预算，鼓励用程序化工具调用）。

### 优点与局限

- 优点：主循环高度可读；护栏完备（中断/预算/重试/修复）；测试覆盖广。
- 局限：`conversation_loop.py` 仍有约 7.7k 行，是新的 god-file；每轮深拷贝消息（`_clone_message_for_send`）有性能成本；同步模型限制了真正的并行回合。

## 5.2 Model Provider（模型供应商抽象）

### 解决什么问题

Hermes 声称"想用哪个模型用哪个"。要让几十个供应商（OpenRouter、Anthropic、OpenAI、Gemini、Bedrock、本地端点…）共享一套 agent 核心，必须把"供应商差异"隔离在一个薄层里。

### 基础概念

- **Adapter / Strategy 模式**：定义统一接口，每个供应商一个实现。
- **声明式配置**：用数据描述行为，而不是为每家写 if/else。

### Hermes 中如何实现

两层抽象：

1. **`ProviderProfile`（声明式）**——`providers/base.py:38` 的 dataclass。一个供应商的所有信息：`name`、`api_mode`、`aliases`、`env_vars`、`base_url`、`auth_type`（api_key / oauth_device_code / oauth_external / copilot / aws_sdk）、`fallback_models`、能力标志（`supports_vision` 等）、以及可覆写的 hook（`prepare_messages` / `build_extra_body` / `build_api_kwargs_extras` / `fetch_models`）。

2. **Transports（API 模式 → 传输层）**——`agent/transports/` 注册表，把 `api_mode` 字符串映射到 transport 类：

| api_mode | Transport | 说明 |
|---|---|---|
| `chat_completions` | ChatCompletionsTransport | OpenAI 兼容，最常见 |
| `codex_responses` | ResponsesApiTransport | OpenAI Codex / Responses API |
| `anthropic_messages` | AnthropicTransport | 原生 Anthropic Messages API |
| `bedrock_converse` | BedrockTransport | AWS Bedrock |

Transport 把各家原生响应**归一化成同一套 `NormalizedResponse/ToolCall/Usage`**，agent 核心永远只说 OpenAI 形状的消息。

### 核心源码

- `providers/base.py:38` `ProviderProfile`
- `providers/__init__.py:43-198` 注册中心（`register_provider()` / `_discover_providers()` 惰性发现）
- `plugins/model-providers/` 每个供应商一个插件目录
- `agent/transports/__init__.py:17` transports 注册表
- `agent/chat_completion_helpers.py` 三种模式的调用拼装
- `agent/anthropic_adapter.py` / `agent/codex_responses_adapter.py` / `agent/gemini_native_adapter.py` 等格式转换
- `agent/auxiliary_client.py`——旁路 LLM（压缩摘要/打标题/视觉/embedding），**复用同一套 provider 解析**，但走 `auxiliary.*` 配置（可指定便宜模型）

### 数据流

```text
(provider, model) → runtime_provider.resolve_runtime_provider() → (api_mode, api_key, base_url)
   → transports registry → Transport 类
   → 调底层 SDK（openai / anthropic / boto3 …）
   → 归一化响应 NormalizedResponse → conversation_loop 消费
```

### 为什么这样设计

- **单点真相**：每个供应商只在 `plugins/model-providers/<name>/__init__.py` 声明一次 `ProviderProfile`，`auth.py`/`config.py`/`models.py`/`doctor.py`/`auxiliary_client` 都从注册表读，而不是各层维护一份 flag。
- **api_mode 是"线协议"一等公民**：一个字符串决定走哪条 transport。强制协议的主机（Anthropic 必须 Messages、GPT-5.x 必须 Responses）在 URL 层硬编码，防止用户配错线导致 400。
- **hook 而非 name-check**：供应商怪癖用可覆写方法表达（如 DeepSeek 覆写 `build_api_kwargs_extras` 输出 `extra_body.thinking`），共享 transport 无需为每家写分支。

### 优点与局限

- 优点：新增供应商=写一个插件文件，不碰核心；aux 旁路复用同一套设施。
- 局限：provider 插件需要跟进 SDK 变化；`auth_type` 种类多（OAuth/copilot/aws_sdk）增加测试面。

## 5.3 Tool System（工具系统）

### 解决什么问题

工具是 Agent 的"手"。系统需要解决：工具如何定义、如何注册、如何暴露给模型、如何执行、结果如何返回、错误如何处理、能力如何按平台裁剪。

### 基础概念

回顾 §2.3 Tool Calling。关键区分：**注册**（声明工具存在）≠ **暴露**（出现在发给模型的 schema 里）。

### Hermes 中如何实现

#### 5.3.1 注册：`tools/registry.py`

- `ToolEntry`（`:201`）——单个工具的元数据：`name/toolset/schema/handler/check_fn/requires_env/is_async/description/emoji/max_result_size_chars/dynamic_schema_overrides`。
- `ToolRegistry`（`:414`）——进程级单例 `registry = ToolRegistry()`（`:955`）。
- `register()`（`:562`）——工具文件在 import 时调用；同名跨 toolset 冲突默认拒绝（防意外覆盖），插件 `override=True` + 运算符显式 opt-in 才能替换内置工具。
- 自发现：`registry.py:108 discover_builtin_tools()` 用 **AST 扫描** `tools/*.py` 里的顶层 `registry.register()` 调用（磁盘缓存摊销 ~100 文件的扫描开销）。

**handler 契约**：`handler(args: dict, **kwargs) -> JSON 字符串`。`_normalize_handler_result`（`:771`）强制结果形状；`tool_result()` / `tool_error()`（`:974`）消除几百处 `json.dumps` 样板。

#### 5.3.2 暴露：`model_tools.py` + `toolsets.py`

- `get_tool_definitions()`（`model_tools.py:305`）——按 toolset 过滤，组装 OpenAI 格式 schema 列表。有记忆化缓存（key 包含 registry 代数、config mtime 等）。
- `toolsets.py`——`TOOLSETS` 字典定义每个 toolset 包含哪些工具；`_HERMES_CORE_TOOLS`（`:31`）是所有平台默认继承的核心底包。
- **check_fn 门控**：`get_definitions` 只包含 `check_fn()` 返回 True（或无 check_fn）的工具。`check_fn` 探测外部状态（Docker daemon、浏览器二进制、API key 是否配置），有 **30s TTL 缓存 + 60s last-good 宽限**（`registry.py:257-379`），吸收瞬时抖动。

#### 5.3.3 执行：`model_tools.handle_function_call()`

`handle_function_call()`（`model_tools.py:1160`）是工具执行的咽喉：
1. `coerce_tool_args`：字符串参数按 schema 转类型（`"42"→42`）。
2. Tool Search 桥分发（`tool_search`/`tool_describe`/`tool_call` 内联处理）。
3. 插件 `pre_tool_call` hook + 中间件（可 block）。
4. ACP 编辑审批门（`write_file`/`patch`）。
5. `registry.dispatch(name, args)`（`registry.py:801`）：找 handler → 执行（async handler 自动桥接）→ 异常统一转 `tool_error(JSON)`。

#### 5.3.4 并发执行：`agent/tool_executor.py`

模型一次返回多个 tool_calls 时：
- 单个 → 顺序执行；
- 多个 → **先做"批分段规划"**（`_plan_tool_batch_segments`）：只读/互不冲突的工具并行，交互式/危险工具串行；同段内并行、段间按序（`run_agent.py:7728`）。

#### 5.3.5 代表性工具

- `tools/terminal_tool.py`：终端工具（~4k 行）。执行前 `check_dangerous_command`（`tools/approval.py:3416`）做危险命令检测；支持 7 种终端后端。
- `tools/file_tools.py`：`read_file/write_file/patch/search_files`，路径安全校验（`path_security.py`）。
- `tools/memory_tool.py` / `tools/todo_tool.py`：**agent-level 工具**——在 `agent_runtime_helpers.invoke_tool()`（`:2813`）中被**拦截**，直接操作 agent 持有的 `MemoryStore`/`TodoStore` 实例，不经过 registry。
- `tools/delegate_tool.py`：`delegate_task`（见 §5.9）。

### 为什么这样设计

1. **自注册 + AST 发现**：新增工具 = 写一个新文件 + 顶层 `registry.register()`，无需维护 import 列表。声明式接入。
2. **强制 JSON 结果契约**：日志/hook/预算/持久化需要对结果统一 slice/size，非字符串结果一律转错误。
3. **check_fn 分离**：可用性探针（外部状态）与执行逻辑解耦，且缓存吸收抖动——避免 Docker daemon 忙时整个工具集从子代理瞬间消失。
4. **toolset 隔离**：能力按平台/会话裁剪，schema 只在需要时才发给模型（窄腰）。

### 优点与局限

- 优点：扩展成本极低（写一个文件）；可用性门控精细；错误边界集中。
- 局限：`_last_resolved_tool_names` 是进程级全局（多会话网关下有污染风险，`delegate_tool` 需保存/恢复）；check_fn 缓存 30s TTL 意味着配置变更非即时生效。

## 5.4 MCP（Model Context Protocol 集成）

### 解决什么问题

Hermes 如何接入"任意外部工具生态"？MCP 是业界标准答案：**外部工具即插即用的唯一通道**（Footprint Ladder 第 5 档）。

### 基础概念

MCP（Model Context Protocol）是 Anthropic 发起的开放协议，定义了一种标准化的"模型与工具/资源/提示词交互"方式。**MCP Server** 提供能力，**MCP Client** 消费能力。传输层支持 stdio / HTTP(Streamable HTTP) / SSE。

```text
Agent（Hermes MCP Client）
   │  stdio / HTTP / SSE
   ▼
MCP Server（外部）
   ├── Tool A
   ├── Tool B
   └── Resource
```

### Hermes 中如何实现

- **客户端入口**：`tools/mcp_tool.py:6934 discover_mcp_tools()`，读 `config.yaml` 的 `mcp_servers` 段，并行连接并把工具注册进本地 registry。
- **`MCPServerTask`**（`mcp_tool.py:2058`）——单个 server 的完整生命周期：`connect → initialize → discover tools → serve → disconnect`，内置自动重连（指数退避）、keepalive ping、会话回收、reconnect 预算。按 config 是否含 `url` 分派 stdio / HTTP（含 SSE）路径。
- **工具映射**：MCP Tool → registry schema（`_convert_mcp_schema`，`:6000`），工具名前缀 `mcp__<server>__<tool>`，toolset 为 `mcp-<server>`。支持 include/exclude 过滤（glob）。动态工具发现：server 发 `notifications/tools/list_changed` → nuke-and-repave。
- **Schema 磁盘缓存**（`mcp_schema_cache.py`）：连接成功写盘，下次启动**懒注册**（工具先出现，真正调用时才连接），避免空闲时 spawn 子进程。
- **OAuth**：`mcp_oauth_manager.py` 支持 MCP OAuth 2.1 + PKCE + 动态客户端注册（DCR）；token 持久化到 `HERMES_HOME/mcp-tokens/`（0o600）；跨进程 mtime 监听。
- **stdio 看门狗**（`mcp_stdio_watchdog.py`）：`--ppid <parent_pid>` 包装，父死子亡，防孤儿进程。
- **反向能力**：`SamplingHandler`（server 反向向 Hermes 请求 LLM 推理）、`ElicitationHandler`（工具调用中向用户要结构化输入）。
- **把自己暴露为 MCP server**：`mcp_serve.py` 用 FastMCP 暴露 `conversations_list/messages_read/messages_send/...` 等工具，供 Claude Code/Cursor/Codex 调用 Hermes 的消息网关与会话存储。

### 为什么这样设计

MCP 是"能力放边缘"原则的最彻底体现：**零核心 schema 占用**（MCP 工具只在连接后动态出现），且对任何 MCP host 可复用。相比"往核心加一个工具"，MCP server 不增加核心面、不增加维护负担。

### 优点与局限

- 优点：生态互通（任何 MCP server 即插即用）；惰性启动省资源；OAuth/重连/看门狗等工程细节完备。
- 局限：依赖官方 `mcp` SDK 版本特性（部分能力用 `_MCP_*` 标志探测）；stdio server 需要子进程管理；工具 schema 归一化有碰撞风险（fail-closed 跳过）。

## 5.5 Gateway（消息网关）

### 解决什么问题

让 Hermes 通过一个常驻进程同时服务多个 IM 平台（Telegram、Discord、WhatsApp、微信、Signal、Slack…）。

### 基础概念

**Adapter 模式**：每个平台一个适配器，把平台协议差异收敛到一个统一消息模型。

### Hermes 中如何实现

- **`GatewayRunner`**（`gateway/run.py:5874`）——主控制器，用 Mixin 组合（Authorization + KanbanWatchers + SlashCommands）。启动时连接各平台，维护 per-session 状态，路由入站消息，运行 agent turn。
- **`BasePlatformAdapter`**（`gateway/platforms/base.py:2878`）——抽象基类：`connect/send/get_chat_info` 由子类实现，加一组能力标志（`supports_code_blocks`、`splits_long_messages` 等）。
- **`SessionSource`**（`gateway/session.py:149`）——标准化"一条消息从哪来"：`platform/chat_id/chat_type/user_id/thread_id/scope_id/profile`。`session_key` 由此确定性构造。
- **`TurnRunner`**（`gateway/run.py:3785`）——单 turn 协作器；`run_sync()` 在 executor 线程跑 agent turn，不阻塞事件循环。
- **`GatewayStreamConsumer`**（`gateway/stream_consumer.py:156`）——流式输出：agent 工作线程推文本增量，网关事件循环不断"编辑同一条平台消息"。

### 数据流

```text
平台 SDK 收到消息
  → adapter 归一化为 MessageEvent（含 SessionSource）
  → handle_message → _process_message_background
  → runner._handle_message：授权(pairing/allowlist) → 忙则 interrupt/queue
  → get_or_create_session → 得 session_id
  → acquire turn_lease → _run_agent → TurnRunner.run_sync（executor 线程）
  → AIAgent.run_conversation()
  → 流式事件 → GatewayStreamConsumer → adapter 渲染回平台
```

### 为什么这样设计

1. **平台差异收敛**：所有平台只暴露几个抽象方法 + capability flags，网关读 flag 而非写 if/elif。新增平台不碰核心。
2. **单向耦合**：adapter 不认识 agent（只调注入的 `_message_handler`）；runner 不认识平台协议（只面向 `MessageEvent`/`SessionSource` 编程）。回程同样：agent 只发结构化 `StreamEvent`，渲染由 adapter 决定。
3. **双键会话**：`session_key`（路由身份，确定性构造）与 `session_id`（持久 transcript）分离。

### 优点与局限

- 优点：平台生态扩展几乎零成本（写一个 adapter 插件）；agent 核心与平台彻底解耦。
- 局限：`gateway/run.py` 约 25k 行，是最大的 god-file；turn 在 executor 线程跑，事件循环与线程协作需要仔细的锁与队列设计。

## 5.6 子代理委托（Sub-Agent Delegation）

### 解决什么问题

父代理的上下文窗口是有限的。当子任务独立、推理密集、与主任务上下文重叠不多时，应该**隔离**出去跑——父代理只看到调用与最终摘要。

### 基础概念

**上下文隔离**：子代理有自己的消息历史、终端会话、file_state 缓存，中间 tool calls/reasoning 不进入父上下文。

### Hermes 中如何实现

- **`delegate_task` 工具**（`tools/delegate_tool.py:3132`）——入口。参数：`goal`（单任务）或 `tasks`（批量并行）、`context`、`role`、`background`、`output_schema`。
- **子代理构造**（`_build_child_agent`，`:1305`）：同进程新建一个 `AIAgent`——fresh conversation、独立 `task_id`、独立终端会话、`skip_context_files=True`、`skip_memory=True`、`quiet_mode=True`、`_delegate_depth` 递增。用 `delegation_context` ContextVar 标记子代理上下文。
- **角色分层**：
  - `role="leaf"`（默认）：被 `DELEGATE_BLOCKED_TOOLS` 剥夺 `delegate_task/clarify/memory/send_message/cronjob`（只读、无副作用、不递归）。保留 `execute_code`。
  - `role="orchestrator"`：恢复 `delegate_task` 可再派生出 worker；受 `delegation.max_spawn_depth`（默认 2）与 `orchestrator_enabled` kill switch 双重边界。
- **同步 vs 后台**：
  - 同步：单任务直跑，或 batch 用 `DaemonThreadPoolExecutor(max_workers=max_concurrent_children)` 并行 `_run_single_child`，聚合结果 JSON 返回。
  - 后台（`background=true`）：`dispatch_async_delegation_batch` 在 daemon executor 跑，完成事件经共享 `completion_queue`（`tools/process_registry.py`）回灌对话。
- **生命周期服务**：`agent/subagent_lifecycle.py` 的 `SubagentLifecycleService`——`launch/status/wait/cancel/result/reconnect`。

### 为什么这样设计

上下文隔离 + 并行 + 后台非阻塞。子代理是"同进程新 AIAgent"（而非独立进程），核心动机就是隔离父 context 窗口并让独立子任务并行推进。执行模型取"无墙钟超时但防卡死"：合法重活永不被通用秒表杀掉，卡死由 heartbeat/stale 进度监视器兜底。

### 优点与局限

- 优点：上下文节省显著；batch 真并行；后台委托不阻塞主对话。
- 局限：同进程线程模型共享全局状态（`_last_resolved_tool_names` 需保存/恢复）；子代理无法跨进程重启恢复。

## 5.7 技能系统与 Curator（Skills）

### 解决什么问题

把"一次性做成的成功做法"沉淀为**可复用的程序性记忆**——这就是"self-improving agent"的机制层。

### 基础概念

**技能（Skill）≠ 工具（Tool）**：Tool 是"可调用的函数"（registry 注册），Skill 是"指导如何做的知识"（SKILL.md 指令 + references/templates/scripts 支持文件）。

### Hermes 中如何实现

- **发现与索引**：`agent/skill_utils.py:877 iter_skill_index_files()` 扫描磁盘技能目录（bundled + `~/.hermes/skills/` + 外部目录）→ 平台/环境门控 → `build_skills_system_prompt`（`agent/prompt_builder.py:1680`）生成 **≤57 字符描述**的 `<available_skills>` 索引，常驻 system prompt（有磁盘快照缓存）。
- **加载**：模型用 `skills_list`（元数据）或 `skill_view`（全文 + 链接文件）按需加载；或用户 `/skill` 斜杠命令。技能内容作为 **USER MESSAGE** 注入（`agent/skill_commands.py:597`），而非 system prompt——这是**渐进式披露**：索引常驻、全文按需，既省 token 又保护 prompt cache。
- **生命周期**：`skill_manage` 工具创建/修改/归档技能；`.usage.json` 遥测（`tools/skill_usage.py`）记录 `use_count/view_count/patch_count/last_activity_at/state/pinned`。
- **Curator**（`agent/curator.py`）——后台技能维护：agent 空闲时对 **`created_by=agent`** 的技能做 stale/archive 状态迁移（LLM 汇总合并默认关闭 `DEFAULT_CONSOLIDATE=False`）。不变量：只归档不删除、pinned 豁免、hub/外部技能只读。
- **Skills Hub**（`tools/skills_hub.py`）——从 agentskills.io 等源安装技能（`SkillSource` ABC + quarantine 扫描）。

### 为什么这样设计

"渐进式披露 vs 一次性注入"：技能索引（≤57 字符）常驻 system prompt 供模型路由，全文按需 skill_view/斜杠命令加载，且以 USER MESSAGE 注入并配合 `prompt_cache_boundary` 注册稳定前缀——既省 token 又保护 prompt caching，易变指令留在缓存断点之后。

### 优点与局限

- 优点：自我改进闭环真实存在；所有权边界严格（只碰 agent 自建技能）。
- 局限：curator 汇总合并默认关（省 aux-model 成本）；技能描述 ≤60 字符的硬约束；遥测早期 use_count 常为 0。

## 5.8 Session（会话状态与持久化）

### 解决什么问题

Agent 需要把会话、消息、token、成本持久化，支持 `hermes chat --resume`、`/resume`、跨进程共享。

### Hermes 中如何实现

- **`SessionDB`**（`hermes_state.py:2420`）——SQLite 存储（WAL 模式），表结构见 §3.4。`append_message()`（`:7643`）是转录的关键写路径（失败中止本轮）。
- **FTS5 搜索**：`SessionSearchMixin`（`hermes_state_search.py:49`）——按查询形状路由（unicode61 → CJK → trigram → LIKE 兜底）；`session_search` 工具（`tools/session_search_tool.py:848`）暴露给 agent。
- **会话生命周期**：`create_session` / `end_session` / `reopen_session`；压缩时 `in_place: true` 默认在同 session id 内改写（旧行 `active=0, compacted=1` 软归档，仍可搜索）。
- **写入竞争**：多进程共享一个 state.db。SQLite 短超时(1s) + **应用层随机抖动重试**（20-150ms）避免 convoy effect；长 patience（60s）给转录写。

### 为什么这样设计

单一 SQLite 文件取代早期 per-session JSONL 方案：WAL 支持多读单写（网关多平台并发）；FTS5 支持全文检索；`api_content` 侧车字段保存"实际发给 API 的字节"以实现 prompt-cache 稳定重放。

### 优点与局限

- 优点：零外部依赖；事务安全；搜索与迁移体系完整。
- 局限：单文件多进程写入需精心协调；超大历史（10GB+ 量级）下 FTS5 维护需要 bounded-merge 等特殊处理。

## 5.9 Cron（定时任务）

- **`cron/jobs.py`**——作业存储；**`cron/scheduler.py`**——tick 循环。
- 计划格式：`"30m"`、`"every 2h"`、`"every monday 9am"`、5 字段 cron 表达式、ISO 时间戳（一次性）。
- 每作业字段：`skills`（加载特定技能）、`model/provider` 覆盖、`script`（预采集脚本，stdout 注入 prompt）、`context_from`（链式）、`workdir`（加载该目录 AGENTS.md）、多平台投递。
- 硬化不变量（AGENTS.md）：**3 分钟硬中断**（cron 会话防止失控 agent 垄断调度器）；catchup 窗口；`~/.hermes/cron/.tick.lock` 文件锁防重复 tick；`skip_memory=True` 默认（记忆 provider 不在 cron 运行）。

## 5.10 插件系统

- **`hermes_cli/plugins.py` PluginManager**——三个发现源：`~/.hermes/plugins/`、`./.hermes/plugins/`、pip entry points。插件暴露 `register(ctx)`，可：
  - 注册生命周期 hooks：`pre_tool_call` / `post_tool_call` / `pre_llm_call` / `post_llm_call` / `on_session_start` / `on_session_end`；
  - `ctx.register_tool(...)` 注册工具；
  - `ctx.register_cli_command(...)` 注册 CLI 子命令。
- **memory provider 插件**（`plugins/memory/`）——实现 `MemoryProvider` ABC（`agent/memory_provider.py:81`），由 `agent/memory_manager.py` 编排。单激活。
- **model-provider 插件**（`plugins/model-providers/`）——每个推理后端一个目录，`register_provider(ProviderProfile(...))` 自注册；扫描顺序 bundled→user→legacy，last-writer-wins。
- **context-engine 插件**（`plugins/context_engine/`）——替代默认 `ContextCompressor` 的可插拔引擎。
- 硬规则（AGENTS.md）：插件**不得修改核心文件**；新 memory provider 必须独立仓库；第三方产品插件不进树。

## 5.11 安全与审批（Security / Approval）

### 解决什么问题

Agent 能执行真实命令、写真实文件、访问网络——它**有权限**，所以必须有护栏。Hermes 的安全模型见 §14，这里先看实现落点。

### 实现要点

- **危险命令检测**：`tools/approval.py:3416 check_dangerous_command()`（终端工具执行前调用）：
  1. **Hardline floor**：无恢复路径的命令（`rm -rf /`、`mkfs`、`dd` 到裸设备、`shutdown/reboot`、fork bomb、`kill -1`）**无条件阻止**，即使 --yolo 也拦（yolo 是信任 agent 用你的文件，不是信任它抹盘/关机）。
  2. 用户 deny 规则（`approvals.deny`）→ 阻止。
  3. `--yolo` 绕过 → 放行。
  4. 永久 allowlist → 放行。
  5. `detect_dangerous_command()`（`:2175`）——**shell 解析感知**的危险命令检测：处理命令替换、环境变量、引号、混淆，识别 `rm -rf`、`sudo` 等。
  6. `_run_approval_gate`——人工审批：`[o]nce / [s]ession / [a]lways / [d]eny`，超时 fail-closed。
- **路径安全**：`tools/path_security.py`——防止越权读/写（含 symlink、跨 profile 写保护）。
- **写审批**：`tools/write_approval.py`。
- **子代理**：默认 `_subagent_auto_deny`（子代理线程里危险命令自动拒绝），`delegation.subagent_auto_approve: true` 才改为自动批准。

## 5.12 配置系统

- `hermes_cli/config.py`——`DEFAULT_CONFIG`（顶层 section：`model/agent/terminal/compression/display/stt/tts/memory/security/delegation/smart_model_routing/checkpoints/auxiliary/curator/skills/gateway/logging/cron/profiles/plugins/honcho`）+ `OPTIONAL_ENV_VARS`（**只放密钥**）。
- 三个加载路径（AGENTS.md：CLI 用 `load_cli_config()`；子命令用 `load_config()`；网关直接读 YAML）。
- `auxiliary.*`：每类旁路 LLM 任务（压缩/视觉/embedding/打标题/session_search）可单独指定 provider/model。
- 用户配置：`~/.hermes/config.yaml`（设置）+ `~/.hermes/.env`（密钥）。profile 隔离：每个 profile 有自己的 `HERMES_HOME`。

## 5.13 日志与可观测性

- `hermes_logging.py`——`setup_logging()`：`agent.log`（INFO+）、`errors.log`（WARNING+）、`gateway.log`。profile 感知。
- `docs/observability/`、`plugins/observability/`——metrics/traces/logs 插件（第三方产品插件策略：独立仓库）。
- 关键日志细节：工具分发时长（`duration_ms`）通过 `post_tool_call` hook 暴露给插件做延迟面板/预算告警（`model_tools.py:1437`）。

---

> 继续：§6 技术栈分析、§7 关键数据结构。请记住 §5 里反复出现的两个词汇：**"窄腰核心 + 边缘能力"** 与 **"缓存神圣性"**——它们是这些模块设计的共同分母。

---

# 六、技术栈分析

## 6.1 总览

Hermes 是一个 **Python 为核心、TypeScript 为前端** 的大型单体。`pyproject.toml` 明言：核心依赖"每个直接依赖都精确 pin 到 ==X.Y.Z"（无范围）——这是 2026 年 5 月 mistralai 供应链攻击后收紧的策略（见 `pyproject.toml:19-39` 注释）。

| 技术 | 类型 | Hermes 中的作用 |
|---|---|---|
| Python `>=3.11,<3.14` | 语言 | Agent Runtime 全部后端逻辑 |
| asyncio / threads | 并发模型 | 网关事件循环 + agent 工作线程 + 工具并发 |
| openai SDK | HTTP 客户端 | chat_completions / codex_responses 两种主链路 |
| anthropic SDK（optional） | HTTP 客户端 | 原生 Anthropic Messages API |
| httpx / requests | HTTP | 内部工具、web_search、远程调用 |
| SQLite (stdlib sqlite3) + FTS5 | 存储 | 会话/消息/搜索（`hermes_state.py`） |
| rich | 终端 UI | CLI 的 banner/面板/样式 |
| prompt_toolkit | 终端 UI | CLI 输入、自动补全 |
| Ink (React) + TypeScript | TUI | `hermes --tui` 终端界面 |
| Electron + React | 桌面 | `apps/desktop/` |
| FastAPI / uvicorn | Web | dashboard、API server |
| Pydantic | 数据模型 | schema 校验（SDK 内部） |
| croniter | 调度 | cron 计划解析 |
| tenacity | 重试 | 通用重试原语 |
| mcp SDK（optional） | 协议 | MCP client/server（`tools/mcp_tool.py`） |
| Jinja2 | 模板 | 部分 prompt/文档渲染 |
| yaml (PyYAML + ruamel) | 配置 | config.yaml |
| psutil / ptyprocess / pywinpty | 进程 | 跨平台进程管理、PTY |
| pytest | 测试 | ~17k-25k 测试 |
| uv (Astral) | 包管理 | 依赖解析、安装、Python 版本管理 |
| Nix | 分发 | 可复现构建（`nix/`、flake.nix） |

## 6.2 关键选型为什么

### 6.2.1 为什么是 Python？

Agent 生态在 Python 最为成熟（LLM SDK、MCP SDK、科学计算、notebook）。Hermes 用 Python 承载所有"环"逻辑，把 TUI/桌面这类 UI 交给 TS 生态。

### 6.2.2 为什么 SQLite 而非外部数据库？

- 个人 Agent 的部署面是**单机**（或单 VPS）——不需要 PostgreSQL/Redis 的运维负担。
- SQLite 单文件、零服务、事务完整、WAL 支持多读单写，完全满足"一个进程服务多个平台"的写入模型。
- FTS5 虚拟表提供开箱即用的全文搜索。
- 代价：多进程共享一个 state.db 时需要精心处理写竞争（见 §5.8）。

### 6.2.3 为什么 asyncio 与线程混用？

- 网关是**事件循环驱动**的（每个平台 adapter 是异步的），所以 `gateway/` 大量用 asyncio。
- Agent 主循环是**同步**的（可读、可测、易中断），耗时调用放在后台线程（`_interruptible_api_call`、`TurnRunner.run_sync` 在 executor 线程）。
- 工具并发用 `ThreadPoolExecutor`（`_execute_tool_calls_concurrent`）。
- 这是一个务实的混合：**事件循环管"多路 I/O 到达"（消息平台），线程管"单个回合内的并行工作"（LLM + 工具）**。

### 6.2.4 为什么依赖精确 pin + 惰性安装？

- `pyproject.toml:19-39`：精确 pin 让"新版本到达用户"只有一条路径（维护者主动升级 + 重新 `uv lock`）。这直接针对 2026-05 mistralai 2.4.6 恶意版本事件。
- `tools/lazy_deps.py`：provider 特定依赖（`anthropic`、`firecrawl-py`、`fal-client`…）不在 `[all]` 里，**用户选那个后端时才按需安装**——缩小每次安装的攻击面。
- 核心依赖只放"每个会话都需要"的包（Scope rule，`pyproject.toml:34`）。

### 6.2.5 前端家族

```text
package.json (npm workspace)
├── ui-tui/      Ink(React) 终端 UI —— hermes --tui
├── web/         Dashboard 前端
├── apps/desktop/ Electron 桌面端
├── apps/shared/ 共享 JSON-RPC 客户端（@hermes/shared）
└── website/     Docusaurus 文档站
```

TUI 用 Ink（React 渲染终端）是重要选型：TypeScript 拥有屏幕，Python 拥有会话/工具/模型调用，二者通过 **newline-delimited JSON-RPC over stdio** 通信（`tui_gateway/server.py`）。这让 Hermes 的"核心窄腰"也能服务一个完全不同的前端。

## 6.3 测试体系

- `scripts/run_tests.sh`——**必须**用它而非直接 `pytest`：它强制与 CI 一致的 hermetic 环境（清空凭证 env、HOME 重定向到临时目录、TZ=UTC、LANG=C.UTF-8、每测试文件子进程隔离）。
- 每测试文件跑在独立子进程（`run_tests_parallel.py`），防止模块级状态泄漏。
- `AGENTS.md` 对测试有严格规范：不要写"变化检测器"测试、不要读源码文本做断言、用行为契约而非快照。

---

# 七、关键数据结构

Agent 系统本质上是**数据结构之间的转换**。Hermes 中最重要的数据形状：

## 7.1 Message（消息）

```python
# OpenAI 兼容格式（所有内部代码统一用这一形状）
{"role": "system", "content": "..."}
{"role": "user",   "content": "..."}
{"role": "assistant", "content": "...", "tool_calls": [...], "reasoning": "..."}
{"role": "tool",   "tool_call_id": "...", "name": "...", "content": "..."}
```

- **谁创建**：agent 循环 append；工具结果消息由 `handle_function_call` 返回后包装。
- **谁读取**：LLM 传输层（`chat_completion_helpers.py`）；SessionDB 持久化。
- **谁修改**：压缩、undo、消息修复（`repair_message_sequence`）。
- **内部字段**：`api_content`（API 字节侧车，prompt-cache 稳定重放）、`display_kind`/`display_metadata`（展示用）、`_row_id`（持久化行 id）——发送前 `_clone_message_for_send` 剥离。

## 7.2 ToolEntry / Tool（工具注册条目）

```python
# tools/registry.py:201
ToolEntry(name, toolset, schema, handler, check_fn, requires_env,
          is_async, description, emoji, max_result_size_chars, dynamic_schema_overrides)
```

- **谁创建**：工具文件 import 时 `registry.register()`。
- **谁读取**：`get_tool_definitions()` 读 schema；`dispatch()` 读 handler。
- **谁修改**：MCP 动态刷新 nuke-and-repave、插件 override。

## 7.3 ToolCall（模型发出的调用请求）

```python
# OpenAI 形状
{"id": "call_xxx", "type": "function",
 "function": {"name": "read_file", "arguments": "{\"path\": \"main.py\"}"}}
```

- 生命周期：模型响应 → 校验/修复/去重 → 执行 → 生成 `{"role":"tool"}` 结果 → 配对存储。

## 7.4 ToolResult（工具结果）

约定：**handler 必须返回 JSON 字符串**。`tool_result()` / `tool_error()` 是标准帮手。

```python
# 成功
{"success": true, "content": "..."}
# 失败
{"error": "file not found", "code": 404}
```

## 7.5 Session（会话记录）

`hermes_state.py` 的 `sessions` 表行：`id/source/user_id/model/system_prompt/parent_session_id/message_count/token_counts/cost/title/...`。`SessionSource`（网关）描述"一条消息从哪来"。

## 7.6 ProviderProfile（供应商声明）

```python
# providers/base.py:38
ProviderProfile(name, api_mode="chat_completions", env_vars=(), base_url="",
                auth_type="api_key", fallback_models=(), ...)
```

## 7.7 数据流总图

```text
User Message
   ↓
Message（role=user）         ← 由入口层创建
   ↓
messages 列表                ← AIAgent 持有，逐轮追加
   ↓
api_messages                 ← 克隆 + 剥离内部字段 + 注入瞬时上下文
   ↓
ModelRequest（SDK 调用）     ← 经 api_mode transport
   ↓
ModelResponse
   ├─ 文本 → Message（role=assistant）→ final_response
   └─ tool_calls → [ToolCall]
          ↓
      handle_function_call → registry.dispatch → handler
          ↓
      ToolResult（JSON 字符串）
          ↓
      Message（role=tool）→ 追加回 messages
   ↓
finalize_turn → SessionDB.append_message（逐条持久化）
   ↓
返回 {final_response, messages, api_calls, completed}
```

---

# 八、Prompt 系统

> 回顾 §1.5 的"缓存神圣性"原则。Hermes 的 Prompt 系统是这一原则最直接的落地。

## 8.1 三层缓存分级（`agent/system_prompt.py`）

`build_system_prompt_parts()`（`:152`）返回三段，按 `stable → context → volatile` 拼接：

| 层 | 内容 | 稳定性 |
|---|---|---|
| **stable** | SOUL.md 身份、Hermes 帮助指引、任务完成指引、工具行为指引（memory/session_search/skills 各自的 guidance）、平台提示、tool-use enforcement、并行工具调用指引 | 跨会话字节稳定 |
| **context** | coding workspace（AGENTS.md/CLAUDE.md 等）、context files、调用者传入的 system_message | 会话级稳定（可能跨会话变） |
| **volatile** | skills 索引、内置记忆快照（MEMORY.md）、用户画像快照（USER.md）、外部 memory provider 静态块、时间戳/会话/模型/平台行 | 每次重建最可能变 |

**为什么这样分层**：provider 侧的 prompt cache 是"最长公共前缀"匹配。把最可能变的内容放在最后，当 prompt 必须重建时（压缩后、恢复时），前面的稳定脚手架仍落在缓存前缀内，命中的部分不用重付费用。文档明确说：时间戳只精确到**日期**（"Conversation started: Monday, August 11, 2026"）而非分钟——分钟精度会让每天多次重建都破坏缓存（`system_prompt.py:543-551`）。

## 8.2 System Prompt 如何构建（`build_system_prompt`, `:569`）

- **缓存于 `agent._cached_system_prompt`**，会话生命周期内不重建；只有压缩事件才 `invalidate_system_prompt()`。
- **SOUL.md 优先**：`load_soul_md()`（`prompt_builder.py`）读取 `~/.hermes/SOUL.md` 作为身份；不存在则回退 `DEFAULT_AGENT_IDENTITY`。
- **context files 优先级**：`.hermes.md/HERMES.md`（走到 git root）→ `AGENTS.md`（CWD）→ `CLAUDE.md`（CWD）→ `.cursorrules`（CWD）。**只加载一种**（first match wins）。所有文件做**注入安全扫描**（不可见 unicode、"ignore previous instructions"、凭据外泄）与**截断**（上限随模型上下文窗口伸缩，20k-500k 字符，70/20 head/tail 分割）。
- **技能索引**：≤57 字符描述常驻 system prompt。
- **记忆快照**：MEMORY.md / USER.md 的**冻结快照**进 volatile 层。

## 8.3 API 调用时注入（缓存之外）

以下内容**故意不进缓存 system prompt**：

- `ephemeral_system_prompt`
- prefill 消息
- 网关的每轮提示（`_gateway_turn_context_notes`）
- 外部 memory provider 的 prefetch 结果
- `pre_llm_call` 插件上下文

这些通过 `compose_user_api_content()`（`agent/turn_context.py:53`）**追加到本轮 user 消息的 API 副本**，并用 `api_content` 侧车持久化（保证重放字节一致）。**存储内容保持干净**，注入只发生在发给 API 的副本上。

## 8.4 模型特定的行为指引

- **tool-use enforcement**：`agent.tool_use_enforcement` 配置（auto/true/false/list）。对 GPT/Codex 等容易"只说不做"的模型注入"你必须调用工具行动"。匹配模型名子串。
- **平台提示（platform hint）**：`PLATFORM_HINTS`（`system_prompt.py`）——"你是 CLI agent，少用 markdown"之类；可由 `platform_hints.<platform>` 配置 append/replace。
- **Google 模型操作指引**：对 Gemini/Gemma 注入（简洁、绝对路径、并行工具调用等）。
- **任务完成指引**：`TASK_COMPLETION_GUIDANCE`——防"任务做到一半就停、编造完成"。

## 8.5 对读者的建议

不要改 `agent/prompt_builder.py` 的模板——那是全产品级改动。要定制身份改 `SOUL.md`，要定制仓库规则改项目 context files，要可复用流程就写技能。这是 Hermes 刻意设计的"支持面"（§prompt-assembly.md 明确）。

---

# 九、Tool 系统

> 本节的"实现细节"已在 §5.3 展开，这里补充**概念模型与完整链路**。

## 9.1 概念链

```text
Tool Definition（写一个 tools/xxx.py，声明 schema + handler）
      ↓
Tool Registry（import 时 registry.register() 自注册）
      ↓
Tool Schema（get_tool_definitions() 按 toolset 组装，check_fn 门控）
      ↓
LLM（在响应中输出 tool_calls）
      ↓
Tool Call（名字 + 参数 JSON）
      ↓
Executor（handle_function_call → 校验 → 审批 → dispatch）
      ↓
Tool Result（JSON 字符串 → {"role":"tool"} 消息回喂模型）
```

## 9.2 工具如何注册（`tools/registry.py:562`）

```python
registry.register(
    name="example_tool",
    toolset="example",
    schema={"name": "example_tool", "description": "...", "parameters": {...}},
    handler=lambda args, **kw: example_tool(param=args.get("param", ""), task_id=kw.get("task_id")),
    check_fn=check_requirements,      # 可用性探针（可选）
    requires_env=["EXAMPLE_API_KEY"], # 缺失时 UI 提示（可选）
)
```

规则（AGENTS.md 新增工具章节 + 源码）：

1. 工具文件模块级调用 `registry.register()`，**自动被发现**（AST 扫描）。
2. **必须**加入某个 toolset（`_HERMES_CORE_TOOLS` 或新 toolset）才会暴露给 agent——"注册"与"暴露"是两件事。
3. handler **必须返回 JSON 字符串**。
4. schema 里引用路径用 `display_hermes_home()` 使其 profile 感知；持久状态用 `get_hermes_home()`。
5. 想加"本地工具"？优先走**插件**（`~/.hermes/plugins/<name>/__init__.py` + `ctx.register_tool(...)`），不碰核心。

## 9.3 工具如何暴露给模型（`model_tools.py:305`）

`get_tool_definitions()` 的输出是 OpenAI function 格式数组。所有工具名出现在 `agent.valid_tool_names`；工具 schema 会随**每次 API 调用**发送——这就是"核心工具门槛高"的原因：每个核心工具的 schema 都是持续的 token 成本。

## 9.4 参数如何验证

- `coerce_tool_args`（`model_tools.py`）：按 schema 把字符串参数转成类型。
- 循环里：无效 JSON 参数 → 3 次重试 → 注入恢复工具结果。
- `registry.dispatch` 里：handler 异常 → 统一 `tool_error(JSON)`。

## 9.5 工具如何执行（`registry.py:801`）

```python
def dispatch(self, name, args, **kwargs):
    entry = self.get_entry(name)
    if not entry:
        return tool_error(f"Unknown tool: {name}")
    try:
        if entry.is_async:
            result = _run_async(entry.handler(args, **kwargs))
        else:
            result = entry.handler(args, **kwargs)
        return self._normalize_handler_result(name, result)
    except Exception as e:
        return tool_error(f"Tool execution failed: ...")
```

## 9.6 错误如何处理

- handler 异常 → `tool_error(sanitized)`，sanitize 去掉 framing token / CDATA / fence 等可能误导模型的结构噪声（`_sanitize_tool_error`）。
- 无效工具名 → 循环层自动修复（`_repair_tool_call`）→ 仍无效则把错误结果喂回模型让模型自纠（3 次上限）。
- 工具结果过大 → 有 `max_result_size_chars` 截断预算。

## 9.7 MCP 与 Tool 系统的关系

MCP 工具通过 `_register_server_tools`（`mcp_tool.py:6281`）转换成 registry schema 注册进**同一个** `ToolRegistry`，toolset 为 `mcp-<server>`。所以对 agent 循环来说，MCP 工具与内置工具**无差别**——都走 `handle_function_call → registry.dispatch`。这是"MCP 即插即用"的核心：协议差异在注册边界被吸收。

---

# 十、Context 与 Memory

## 10.1 问题：上下文窗口有限，但 Agent 任务可能运行很久

Hermes 的解法分两层：

1. **上下文压缩**（回收 token）——§10.2。
2. **记忆系统**（跨会话保留）——§10.3。
3. **prompt caching**（降低复用成本）——§10.4。

## 10.2 上下文压缩（`agent/context_compressor.py`）

### 何时触发

- **Agent 内压缩**（主）：`compression.threshold`（默认 0.50 = 上下文 50%）。基于 API 报告的 prompt_tokens 判断（`_compressor.last_prompt_tokens`）。
- **网关卫生压缩**（安全网）：85% 阈值，turn 之间运行（防会话隔夜膨胀）。
- 压缩前先 **flush 记忆到磁盘**（防数据丢失）。

### 4 阶段算法（`ContextCompressor.compress()`）

```
Phase 1: 剪除旧工具结果（无 LLM，纯确定性）——>200 字符的旧工具输出替换为占位符
Phase 2: 确定边界——保护 head（system+首轮，protect_first_n）+ 保护 tail（token 预算，
         默认 ~20K token，绝不切断 tool_call/result 组，保留最近 user/assistant）
Phase 3: 结构化摘要——用 auxiliary LLM 把中间轮次总结成结构化模板
         （Goal / Progress(Done,InProgress,Blocked) / Key Decisions / Relevant Files / Next Steps / Critical Context）
Phase 4: 组装——head + 摘要消息（角色避开连续冲突）+ tail；清理孤儿 tool_call/result 对
```

- **迭代式再压缩**：第二次压缩时把之前的摘要传给 LLM"更新"而非重新总结，信息跨多次压缩存活。
- **摘要预算**：`content_tokens × 0.20`（最小 2000，最大 `min(ctx×0.05, 12000)`）。
- **in_place 压缩**（默认）：同一个 session id 内改写消息列表，旧轮次软归档（`active=0, compacted=1`）仍可搜索；替代了早期"轮换新 session id"的方式。
- **per-model 阈值覆盖**：`compression.model_thresholds` 按模型名子串匹配（最长匹配胜出），小上下文模型有 0.75 下限。

### 压缩是"缓存破坏豁免"

AGENTS.md 明言：**压缩是唯一允许中途改变上下文的时刻**。它同时重建 system prompt（`invalidate_system_prompt`），缓存从压缩边界后重新建立。

## 10.3 记忆系统（三层次）

| 层 | 机制 | 注入方式 | 生命周期 |
|---|---|---|---|
| 内置文件记忆 | `MemoryStore`（MEMORY.md / USER.md） | system prompt volatile 层冻结快照 | 跨会话 |
| 外部 memory provider | `MemoryProvider` ABC（honcho/mem0/...） | 静态块进 system prompt + prefetch 注入本轮 user 消息 | 跨会话 |
| 会话搜索 | FTS5 全文检索（`session_search` 工具） | agent **主动调用**工具按需检索 | 全历史 |

关键设计点：

- **记忆 ≠ 实时**：MEMORY.md 的 system prompt 注入是 `load_from_disk()` 时的**冻结快照**——mid-session 写入不改变已构建的 system prompt，从而保住 prefix cache（`memory_tool.py:682-693` 注释明言）。
- **prefetch 走 user 消息**：外部 provider 的每轮 recall（`MemoryManager.prefetch_all` → `compose_user_api_content`）注入**本轮 user 消息**的 API 副本，用 `<memory-context>` 围栏包裹并注明"这是召回的记忆、非新用户输入"。存储内容保持干净。
- **门控**：`is_trivial_prompt` 过滤零信号输入（"hi"/"thanks"）不触发 prefetch。
- **turn 边界同步**：`sync_turn` 在 turn 结束后异步执行（绝不阻塞热路径；有 Hindsight 配置错误阻塞 298s 的教训）。`queue_prefetch_all` 为下一 turn 预热。
- **工具级记忆**：`memory` 工具（写 MEMORY.md/USER.md）、`session_search` 工具（检索历史）。

## 10.4 Prompt Caching（`agent/prompt_caching.py`）

- **Anthropic 显式缓存**：`cache_control` 断点，"system_and_3"策略——system prompt + 最近 3 条消息的滚动窗口（最多 4 个断点）。
- **隐式缓存**：OpenAI/其他 provider 的长公共前缀命中，依赖字节稳定。
- **缓存保护规则**（AGENTS.md 反复强调）：
  1. 系统提示词字节稳定（三层分级 + 冻结快照）。
  2. 消息角色严格交替（`user→assistant→user...`；绝不连续两个同角色；绝不中途注入合成 user 消息）。
  3. toolset 会话中不变更。
  4. 改技能的 slash 命令默认"下会话生效"（`--now` 才立即失效）。
- **模型身份是缓存 key 的一部分**：中途 `/model` 切换或凭证池轮换会让缓存归零（成本警告）。

## 10.5 结论

Hermes 对"长会话"的答案不是单一机制，而是**组合拳**：压缩（回收 token）→ 缓存（降低成本）→ 记忆（保留知识）→ 搜索（按需召回）。这三者围绕同一个约束——**别动已经发送过的字节**。

---

# 十一、Agent 控制逻辑：继续、调用工具、还是结束

## 11.1 主控制

核心判断在 `conversation_loop.py:6349`：`if assistant_message.tool_calls:`。这就是全部——**模型自己决定**下一步。Hermes 不搞复杂的 planner 状态机来决定"继续 vs 结束"；控制权交给模型，运行时只提供护栏：

| 护栏 | 位置 | 作用 |
|---|---|---|
| `max_iterations` / `IterationBudget` | `conversation_loop.py:1634` | 防死循环 |
| `_budget_grace_call` | 同上 | 预算耗尽后给模型最后一轮 |
| `_interrupt_requested` | 同上 | 用户中断 |
| `_invalid_tool_retries` (3) | `conversation_loop.py:6408` | 无效工具名 → 错误回喂模型 → 3 次后停 |
| `_invalid_json_retries` (3) | `conversation_loop.py:6520` | 无效参数 JSON → 3 次后注入恢复结果 |
| 工具名自动修复 | `conversation_loop.py:6369` | `_repair_tool_call` 归一化 + 模糊匹配 |
| `_cap_delegate_task_calls` | `conversation_loop.py:6563` | 限制委托批量规模 |
| `_deduplicate_tool_calls` | `conversation_loop.py:6566` | 去重重复调用 |

## 11.2 有"规划器"吗？

Hermes **没有**显式的 Planner / State Machine / Workflow DSL。它采用的是**单循环 + 模型自主决策**，外加"外部任务组织"手段：

- **todo 工具**：把大任务拆成子项（`TodoStore`），但拆不拆、怎么拆由模型决定。
- **delegate_task**：把子任务隔离给子代理（§5.6）。
- **kanban 看板**：多代理/多 profile 的任务队列（不是 agent 内部规划器）。
- **cron**：定时任务。
- **`clarify` 工具**：模型不确定时向用户提问。

**为什么不引入 planner 状态机？** 推测（源码无明证）：模型驱动的循环在现代 LLM 上效果更好、更简单；状态机把"灵活性"与"可维护性"都牺牲给了一个预定义的图，而 LLM 作为 planner 可以随任务动态应变。Hermes 选择"运行时护栏 + 模型自主"，把确定性留给工具执行、把灵活性留给模型。

## 11.3 有 Reflection / Critic 吗？

- **没有**通用的 reflection 阶段。
- 但有一个**验证门**（`verification_stop.py`、`verify_hooks.py`）：`_pending_verification_response` 机制（`conversation_loop.py:1590`）——在特定配置下，最终回答会先经过验证门，未过则扣住，继续循环。
- 后台审查（`agent/background_review.py`）在回合结束后做记忆/技能审查——是"事后反思"而非"回合内反思"。

## 11.4 Max Steps

`max_iterations` 默认 90（子代理 `delegation.max_iterations` 默认 50）。预算耗尽时返回"已完成工作摘要"而非错误（`finalize_turn` 的 partial 语义）。

---

# 十二、并发与异步模型

## 12.1 先建立概念

```text
同步         Task A ──────────>  Task B ──────────>
异步         Task A ──wait───────>   Task B ──────>
并行         多个 Task 同时推进（需要多线程/多进程/多核）
```

- **Event Loop**：单线程的事件调度器，负责"谁准备好了谁来"。适合 I/O 密集型。
- **Coroutine / await**：可暂停的函数；`await` 把控制权交还给事件循环。
- **Task**：调度到事件循环上的协程。
- **线程**：操作系统级并行（受 GIL 限制，但 I/O 密集可并行）。

## 12.2 Hermes 哪些部分是异步的

| 部分 | 模型 | 说明 |
|---|---|---|
| 网关事件循环 | asyncio | 各平台 adapter 异步收发（`gateway/`） |
| Agent 主循环 | 同步 | `conversation_loop.py`（可读、可测） |
| LLM 调用 | 线程 | `_interruptible_api_call` 在后台线程跑，主线程等 interrupt |
| 工具执行 | 线程池 | `_execute_tool_calls_concurrent`（`ThreadPoolExecutor`） |
| 网关里的 agent turn | 线程 | `TurnRunner.run_sync` 经 `to_thread` 在 executor 线程运行 |
| 记忆 sync/prefetch | 线程 | `MemoryManager` 单工作线程执行器 |
| MCP server 连接 | asyncio | `MCPServerTask` 在专属 asyncio Task 里跑 |
| stdio MCP 子进程 | 子进程 | 看门狗包装 |
| 子代理 | 线程 | 同进程新 AIAgent，daemon 线程 |

## 12.3 为什么这样混合

- **网关必须异步**：同时服务 25+ 平台，每个平台是独立 I/O 流。asyncio 是天然fit。
- **Agent 循环要同步**：工具调用链有严格顺序依赖（模型→工具→模型），同步代码最清晰；且中断检查在每个迭代点发生。
- **并行工具**：一次多个 tool_calls 时，只读/互不冲突的可以并行（`_plan_tool_batch_segments`）。但注意——LLM 本身不能并行调用（每轮只有一次 API 调用），并行的是**工具执行**。
- **锁与共享状态**：`_last_resolved_tool_names`（进程全局）、`registry._lock`（RLock）、`_pending_input` 队列（CLI）——这些跨线程共享点正是 bug 高发区（AGENTS.md 反复警告）。

## 12.4 对 Agent 的影响

理解混合模型很重要：**主链路（LLM+工具循环）是同步的**，异步只出现在"边缘"（平台收发、记忆后台、MCP 连接）。这意味着如果你给 Hermes 加一个长时间运行的工具，它默认会**阻塞主循环**（除非走后台 delegation 或 background terminal）。

---

# 十三、错误处理与恢复

## 13.1 错误分类

Hermes 不把所有错误一刀切处理，而是**分类**（`agent/error_classifier.py`）：

- **可重试的瞬时错误**（网络、429、5xx）→ 重试 + 退避。
- **认证错误**（401/403）→ 尝试凭证刷新/轮换 → 失败后 fallback 模型。
- **上下文溢出**（413、context length exceeded）→ 压缩后重试。
- **模型自身错误**（无效工具名、无效 JSON、空响应）→ 错误回喂/重试（见 §11）。
- **不可恢复错误** → 优雅停止 + partial 结果。

## 13.2 具体机制

| 机制 | 实现 |
|---|---|
| 重试 + 退避 | `agent/retry_utils.py`、`tenacity` |
| Fallback 模型 | `fallback_providers` 配置；按顺序尝试 |
| 凭证轮换 | `agent/credential_pool.py`（OAuth 池）；401 触发 refresh |
| 上下文溢出重试 | `conversation_loop.py` 里 413/overflow handler → 压缩 → 重发 |
| 无效工具名修复 | `_repair_tool_call`（归一化 + 模糊匹配） |
| 无效 JSON 恢复 | 3 次重试 → 注入恢复工具结果 |
| 空响应恢复 | `_empty_content_retries`、prefill 恢复 |
| 截断检测 | `finish_reason="length"` → 分段续写（`truncated_response_parts`） |
| 错误消息注入 | 工具错误 JSON 直接喂回模型，让模型自纠 |
| 崩溃恢复 | 回合开场 `_persist_session`；tool-call 执行前先持久化（`conversation_loop.py:6718`） |
| 超时 | LLM 调用、工具、子代理各有超时/看门狗 |
| 熔断 | MCP provider 熔断器（连续失败暂停）、rate limit guard |

## 13.3 关键设计：错误也是模型的输入

Hermes 的一个重要哲学：**大部分错误不是"抛异常给用户"，而是"构造一个结构化的错误消息喂回模型"**。模型读到 `{"error": "file not found"}` 后可以自行调整策略（换个路径、用别的方式）。这让 Agent 有了"自我纠错"的能力，但也要求错误消息**必须干净**（`_sanitize_tool_error` 去 framing token、`_bound_error_text` 限长）——否则错误字符串本身会成为注入向量。

## 13.4 中断（Interrupt）是头等公民

用户随时发新消息或 `/stop`：`_interrupt_requested` 在每个迭代点检查；`_interruptible_api_call` 把在途 HTTP 调用放后台线程、主线程等 interrupt event。中断不是异常路径——是**正常操作**。

---

# 十四、安全模型

## 14.1 为什么 Agent 必须考虑安全

Agent 有真实权限（执行命令、写文件、上网），且**会犯错或被 prompt injection 操纵**。Hermes 的立场是：**不可逆 / 越权 / 会污染未来行为的动作，最终由人类拍板**。

## 14.2 分层防护

```text
Layer 1  输入防护：prompt injection 扫描（context files、网页内容、SOUL.md）
Layer 2  工具可用性门控：check_fn（能力没配好就不暴露工具）
Layer 3  命令防护：hardline 无条件拦截 → 危险模式检测 → 人工审批
Layer 4  文件防护：路径越权校验（path_security）、写审批（write_approval）
Layer 5  网络防护：URL 安全（url_safety）、network egress isolation
Layer 6  沙箱：终端后端隔离（Docker cap-drop、Modal 云沙箱…）
Layer 7  权限模型：allowlist、DM pairing、--yolo 显式授权
Layer 8  子代理降权：leaf 角色剥离危险工具、subagent_auto_deny
```

## 14.3 命令审批细节（`tools/approval.py`）

执行顺序（`check_dangerous_command`，`:3416`）：

1. **Hardline floor**（`detect_hardline_command`, `:520`）：`rm -rf /`、`mkfs`、`dd` 到裸设备、`shutdown`、fork bomb、`kill -1`——**无条件阻止**，即使 --yolo 也拦。
2. **用户 deny 规则**（`approvals.deny`）：阻止。
3. **--yolo**：绕过审批（但 hardline 仍拦）。
4. **永久 allowlist**：放行。
5. **`detect_dangerous_command`**（`:2175`）：**shell 解析感知**的检测——处理命令替换、env 变量、引号混淆，识别 `rm -rf`、`sudo`、危险重定向等。
6. **`_run_approval_gate`**：人工审批 `[o]nce/[s]ession/[a]lways/[d]eny`，超时 **fail-closed**。

网关模式：审批经 per-session 队列走平台消息（如 Telegram 内联按钮）。CLI 模式：`approval_callback` 交互提示。cron 模式：无人在场，默认拒绝危险命令（`approvals.cron_mode` 配置）。

## 14.4 沙箱（终端后端）

7 种后端是一个"隔离强度与成本"的光谱：

| 后端 | 隔离 | 场景 |
|---|---|---|
| local | 无（宿主机全权） | 默认 |
| docker | 容器级（cap-drop ALL + no-new-privileges + pids-limit + tmpfs 限大小） | 安全包安装 |
| singularity | 容器级（HPC） | 科研集群 |
| ssh | 远程机器 | 借用远端 |
| modal / daytona / vercel_sandbox | serverless 云沙箱 + 快照持久化 | 托管/RL/benchmark |

**"容器即边界"是审批跳过的前提**：在隔离容器里 agent 可以自由装包、跑危险命令，因为伤不到主机。

## 14.5 工具权限

- `requires_env` / `check_fn`：能力未配置不暴露。
- `--yolo`：跳过审批的显式授权（进程级 / 网关会话级）。
- `approvals.*` 配置：allowlist / deny rules / cron_mode。
- 子代理：`_subagent_auto_deny` 默认（子代理线程危险命令自动拒绝），`subagent_auto_approve: true` 才自动批准。

---

# 十五、设计模式：Hermes 中的软件工程思想

> 不是单独介绍设计模式，而是看 Hermes 如何用它们解决真实问题。

## 15.1 Registry 模式（工具/命令/平台/插件/Provider）

- `tools/registry.py` `ToolRegistry`——工具自注册、查询、分发。
- `hermes_cli/commands.py` `COMMAND_REGISTRY`——斜杠命令单一事实源，被 CLI/gateway/Telegram/Slack/autocomplete/help 多方复用。
- `agent/transports/__init__.py`——api_mode → transport 类注册表。
- `gateway/platform_registry.py`——平台适配器注册表。

**为什么**：Registry 把"有哪些东西"集中管理，新增一项只需"注册"而非到处 if/else；消费者只依赖注册表的查询接口。这正是"窄腰"的核心机制。

## 15.2 Adapter / Strategy 模式（供应商/平台/后端差异隔离）

- `ProviderProfile` + transports——模型供应商差异（§5.2）。
- `BasePlatformAdapter` + 各平台实现——消息平台差异（§5.5）。
- `BaseEnvironment` + 7 种终端后端——终端差异（§5.12）。
- `MemoryProvider` ABC + 各家实现——记忆后端差异。
- `ContextEngine` ABC + 默认 ContextCompressor——压缩引擎差异。

**为什么**：所有"同一职责、多种实现"的地方都用 Adapter。策略通过注册表选择（配置驱动），而非硬编码。

## 15.3 门面（Facade）模式

- `AIAgent` 是门面：外部接口稳定，内部实现可自由重组（god-file 拆分的产物）。
- `PluginContext` 是门面：插件只能通过受控接口与宿主交互。

## 15.4 观察者 / 回调模式（Hooks & Callbacks）

- 插件 hooks：`pre_tool_call` / `post_tool_call` / `pre_llm_call` / `post_llm_call` / `on_session_start` / `on_session_end` / `transform_tool_result`。
- agent 回调：`tool_progress_callback` / `stream_delta_callback` / `clarify_callback` / `step_callback` 等（§agent-loop.md）。
- 网关事件：`StreamEvent`（agent 发结构化事件，adapter 决定如何渲染）。

**为什么**：主循环保持纯粹（不关心 UI/插件），所有副作用通过回调/事件在边界消费。

## 15.5 事件驱动（网关 + StreamEvent）

agent 只发结构化事件（`stream_events.py`），投递/渲染由 adapter 决定。这使 Telegram 能用原生草稿流式、iMessage 能吃掉工具 chrome。

## 15.6 模板方法模式

`BaseEnvironment.execute()` 把执行流程固化在基类（快照、CWD 跟踪、中断、超时、bounded capture），子类只需实现 `_run_bash` + `cleanup` + 少量 hook（`_before_execute`）。

## 15.7 依赖注入（轻量）

- 工具 handler 通过 `**kwargs` 接收 `task_id/session_id` 等上下文。
- agent 持有 `MemoryStore`/`TodoStore` 实例并注入工具（`invoke_tool` 拦截）。
- 大量"局部 import"（函数内 import）避免循环依赖，也是轻量 DI 的一种。

## 15.8 ABC（抽象基类）驱动的插件系统

Memory provider、context engine、model provider、image gen、platform——全部是 **ABC + 注册表 + 每插件一个目录**。这是 AGENTS.md 的明确方针：当多个 PR 集成同一类东西时，设计一个 ABC + orchestrator，把内置实现作为第一个 provider，让竞争者变成插件。

---

# 十六、关键源码精读

> 按推荐阅读顺序排列。每个文件说明：为什么重要、阅读前需要什么、负责什么、最重要的函数、重点观察什么。

## 16.1 推荐阅读顺序（10~20 个文件）

```text
1.  tools/registry.py                  —— 工具系统的地基，零依赖，最先读
2.  toolsets.py                        —— 工具集与核心工具清单
3.  model_tools.py（get_tool_definitions / handle_function_call）
4.  agent/conversation_loop.py         —— 主循环（大文件，先读循环头 + tool_calls 分支）
5.  run_agent.py（AIAgent 门面 + _execute_tool_calls）
6.  agent/agent_runtime_helpers.py（invoke_tool）
7.  agent/turn_context.py（build_turn_context / compose_user_api_content）
8.  agent/turn_finalizer.py（finalize_turn）
9.  agent/system_prompt.py（build_system_prompt_parts）
10. agent/prompt_builder.py（load_soul_md / build_context_files_prompt）
11. agent/context_compressor.py（compress 4 阶段）
12. agent/prompt_caching.py（system_and_3 策略）
13. providers/base.py + plugins/model-providers/<一个示例>
14. agent/auxiliary_client.py（_resolve_auto）
15. hermes_state.py（SessionDB.append_message）
16. hermes_state_search.py（search_messages）
17. tools/mcp_tool.py（MCPServerTask / _register_server_tools）
18. tools/delegate_tool.py（delegate_task / _build_child_agent）
19. tools/approval.py（check_dangerous_command / detect_dangerous_command）
20. gateway/run.py（GatewayRunner._run_agent_inner / TurnRunner.run_sync）
```

## 16.2 精读要点（每个文件）

### 16.2.1 `tools/registry.py`（~1k 行）

- **为什么重要**：整个工具系统的地基，被所有工具文件 import。
- **阅读前**：懂 OpenAI function schema。
- **最重要函数**：`register()`（`:562`）、`get_definitions()`（`:717`）、`dispatch()`（`:801`）、`discover_builtin_tools()`（`:108`）、`tool_result/tool_error`（`:974`）。
- **重点观察**：
  - `check_fn` 的 **30s TTL + 60s last-good 宽限** 缓存（`:257-379`）——外部状态探测的抖动吸收。
  - `_normalize_handler_result` 的强制 JSON 契约（`:771`）。
  - `register()` 对跨 toolset 覆盖的**拒绝策略**与插件 override 的信任门（`:586-622`）。
  - `_generation` 计数器——MCP 动态刷新如何让 schema 缓存失效（`:430-436`）。

### 16.2.2 `toolsets.py`

- **最重要**：`_HERMES_CORE_TOOLS`（`:31`）——核心工具清单，每个平台的默认底包；`TOOLSETS`（`:107`）；`resolve_toolset`（`:755`）。
- **重点观察**：工具为什么分 toolset？——schema 按平台裁剪（窄腰）。

### 16.2.3 `model_tools.py`

- **最重要**：`get_tool_definitions()`（`:305`）、`handle_function_call()`（`:1160`）。
- **重点观察**：
  - schema 组装的选择逻辑：enabled/disabled toolset 的交并集、kanban 特例。
  - `handle_function_call` 的完整链条：Tool Search 桥 → 中间件 → 插件 hook → ACP 审批 → `registry.dispatch`。
  - `_emit_post_tool_call_hook` 带 `duration_ms`——可观测性。

### 16.2.4 `agent/conversation_loop.py`（~7.7k 行）

- **为什么重要**：真正的主循环。
- **阅读前**：§2.2 Agent Loop、§11 控制逻辑。
- **最重要**：`run_conversation()`（`:1422`）、循环头（`:1634`）、tool_calls 分支（`:6349`）。
- **重点观察**：
  - `build_turn_context` 是每轮开场（`:1524`）——恢复/构建 system prompt、压缩检查、瞬时上下文注入。
  - 循环头的三闸门：interrupt、api_call_count、iteration_budget（`:1634-1669`）。
  - tool_calls 的"不信任"链：去重 id → 修复工具名 → 无效名错误回喂 → JSON 校验 → 截断检测 → 恢复结果注入（`:6367-6560`）。
  - 角色交替修复（`repair_message_sequence_with_cursor`，`:1821`）。
  - `_execute_tool_calls`（`:6766`）前的持久化（防崩溃丢状态）。

### 16.2.5 `run_agent.py`

- **为什么重要**：AIAgent 门面 + 大量历史代码。
- **最重要**：`__init__`（`:435`，转发到 agent_init）、`run_conversation`（`:7894`，转发到 conversation_loop）、`_execute_tool_calls`（`:7728`）、`_dispatch_delegate_task`（`:7772`）。
- **重点观察**：门面模式——稳定接口如何让内部重构无痛；`_execute_tool_calls` 的"批分段规划"。

### 16.2.6 `agent/agent_runtime_helpers.py`

- **最重要**：`invoke_tool()`（`:2813`）。
- **重点观察**：agent-level 工具拦截（todo/memory/session_search/clarify/delegate_task 直接操作 agent 状态）；else 分支回落到 `handle_function_call` 走 registry。

### 16.2.7 `agent/turn_context.py`

- **最重要**：`build_turn_context()`、`compose_user_api_content()`（`:53`）。
- **重点观察**：`api_content` 侧车的设计——持久化"实际发送的字节"以保持 prompt-cache 稳定重放。

### 16.2.8 `agent/system_prompt.py`

- **最重要**：`build_system_prompt_parts()`（`:152`）、`build_system_prompt()`（`:569`）。
- **重点观察**：三层缓存分级的划分逻辑；**日期精度时间戳**（`:551`）；volatile 层里技能/记忆/画像的顺序。

### 16.2.9 `agent/prompt_builder.py`

- **最重要**：`load_soul_md()`、`build_context_files_prompt()`。
- **重点观察**：context file 优先级（first match wins）、注入安全扫描、截断（70/20 head/tail）。

### 16.2.10 `agent/context_compressor.py`

- **最重要**：`compress()` 4 阶段算法。
- **重点观察**：Phase 1（确定性剪枝，无 LLM）→ Phase 3（结构化摘要模板）→ 迭代式再压缩（`_previous_summary`）。"head/tail 保真、middle 可摘要"的分层资产观。

### 16.2.11 `providers/base.py`

- **为什么重要**：供应商抽象的最小核心。
- **最重要**：`ProviderProfile` dataclass + hooks。
- **重点观察**：声明式 vs 过程式——为什么用 hook 表达供应商怪癖而不是 name-check。

### 16.2.12 `agent/auxiliary_client.py`

- **最重要**：`_resolve_auto`——旁路任务（压缩/视觉/embedding/打标题/session_search）如何解析 provider/model。
- **重点观察**：`auxiliary.*` 配置如何让每个旁路任务用便宜的模型。

### 16.2.13 `hermes_state.py`（SessionDB）

- **最重要**：`append_message()`（`:7643`）、`create_session()`、`_execute_write`。
- **重点观察**：WAL + 应用层抖动重试的写竞争处理（`:2428` 注释）；`api_content` 列；`_check_transcript_write_guards`。

### 16.2.14 `hermes_state_search.py`

- **最重要**：`search_messages()`（`:1410`）。
- **重点观察**：按查询形状路由（FTS5 unicode61 → CJK → trigram → LIKE 兜底）。

### 16.2.15 `tools/mcp_tool.py`

- **最重要**：`MCPServerTask`（`:2058`）、`_register_server_tools`（`:6281`）、`discover_mcp_tools`（`:6934`）。
- **重点观察**：连接生命周期（重连/回收/看门狗）；MCP 工具如何归一化成 registry schema；schema 缓存懒启动。

### 16.2.16 `tools/delegate_tool.py`

- **最重要**：`delegate_task`（`:3132`）、`_build_child_agent`（`:1305`）。
- **重点观察**：角色分层（leaf/orchestrator）、深度限制、同步/后台两条路径。

### 16.2.17 `tools/approval.py`

- **最重要**：`check_dangerous_command`（`:3416`）、`detect_dangerous_command`（`:2175`）。
- **重点观察**：hardline floor 的无条件拦截；shell 解析感知的去混淆检测；fail-closed。

### 16.2.18 `gateway/run.py`

- **最重要**：`GatewayRunner._run_agent_inner`（`:25156`）、`TurnRunner.run_sync`（`:4568`）。
- **重点观察**：平台事件 → session 解析 → turn lease → executor 线程跑 agent 的完整链路。

---

# 十七、设计决策分析：为什么这样设计

## 17.1 为什么用 Registry 而不是大量 if/else

if/else 的问题：新增能力要改多处（schema 收集、分发、权限、UI 展示、帮助文本）。Registry 让"新增一项"变成"注册一条"，消费者统一查表。Hermes 里工具、命令、平台、provider、插件、transport 全部 registry 化——**这是"窄腰核心"得以维持的机制**。

## 17.2 为什么用 Provider 抽象而不是直接调某个 SDK

直接调 SDK 的代价：换供应商 = 改代码。Provider 抽象把差异隔离在 profile + transport，主循环永远说 OpenAI 形状的消息。**收益是模型无关性**——`hermes model` 热切换、aux 旁路用便宜模型、新增供应商不碰核心。

## 17.3 为什么 Agent Runtime 和 Tool 分离

工具是"执行器"（有副作用），runtime 是"编排器"（决策）。分离让：
- 工具可以独立测试、独立 gate（check_fn）；
- runtime 可以支持任何工具（包括 MCP 动态工具）；
- 工具可以按平台/会话裁剪（toolset）。

## 17.4 为什么 Prompt 与 Agent Logic 分离

Prompt 是"产品内容"（每会话稳定），逻辑是"引擎"。分离让：
- Prompt 可以被缓存（字节稳定）；
- Prompt 可以按平台/模型/技能裁剪而不动代码；
- Prompt 修改（SOUL.md、context files、技能）不需要发版。

## 17.5 为什么 system prompt 缓存是"神圣的"

因为**成本**。长会话每轮复用缓存前缀；破坏一次 = 全量重读，费用成倍。Hermes 把"不破坏缓存"当作代码审查的准绳（AGENTS.md：任何中途改上下文/换 toolset/重建 prompt 的改动都会被拒）。这不是洁癖——是**每用户每会话的真金白银**。

## 17.6 为什么技能以 USER MESSAGE 注入而非 SYSTEM PROMPT

注入 user message 让易变指令留在缓存断点之后，系统提示词前缀保持稳定。技能内容变化不破坏缓存；索引（≤57 字符）常驻 system prompt 提供路由信号。**渐进式披露**：索引常驻、全文按需。

## 17.7 为什么工具 handler 必须返回 JSON 字符串

统一结果形状让日志、hooks、预算、持久化都能安全 slice/size；JSON 让模型能结构化地读错误并自纠。代价是 handler 作者要处理序列化（`tool_result`/`tool_error` 帮手消除样板）。

## 17.8 为什么压缩是"唯一缓存破坏豁免"

上下文有限是物理约束，压缩不可避免。但 Hermes 把它做成**显式的、可控的、一次性的**（而不是每个迭代悄悄改历史），且压缩会重建 system prompt、从新边界重新建立缓存。这是"在不可避免的破坏里选择最小破坏"。

## 17.9 为什么子代理用"同进程线程"而非独立进程

同进程新 AIAgent 的核心动机是**隔离上下文窗口**（父只看调用+摘要）并让独立子任务并行，而不是隔离进程崩溃。进程隔离的代价（IPC、状态序列化）对一个个人 Agent 不值得。安全性靠 role 降权 + approval 门控而非进程边界。

## 17.10 为什么模型自决而不引入显式 planner

现代 LLM 作为 planner 足够好，且状态机把灵活性和可维护性都牺牲给了预定义图。Hermes 把确定性留给工具执行与护栏，把灵活性留给模型。

---

# 十八、完整架构总结：一张大图

```text
                        用户 / 消息平台
                              │
                     ┌────────▼─────────┐
                     │     入口层         │
                     │ CLI / TUI / 桌面 / │
                     │ Gateway / ACP /  │
                     │ cron / 批量 / 库   │
                     └────────┬─────────┘
                              │
                     ┌────────▼─────────┐
                     │    AIAgent 门面   │
                     │   (run_agent.py)  │
                     └────────┬─────────┘
                              │ run_conversation
                     ┌────────▼─────────┐
                     │   Agent 核心循环   │
                     │  conversation_loop│
                     │  ───────────────  │
                     │  turn_context     │  每轮开场
                     │  tool_executor    │  工具执行
                     │  turn_finalizer   │  回合收尾
                     └───┬─────────┬────┘
                         │         │
            ┌────────────▼──┐   ┌─▼───────────────┐
            │   Prompt 系统  │   │  Provider 抽象    │
            │  system_prompt│   │  ProviderProfile  │
            │  prompt_build │   │  transports       │
            │  prompt_cach  │   │  auxiliary_client │
            │  context_comp │   └─┬───────────────┘
            └────────────┬──┘   │
                         │      ▼
              ┌──────────▼──┐  LLM API（多家）
              │  工具系统    │
              │  registry   │──> 内置工具（terminal/file/web/browser/...）
              │  model_tools│──> MCP client ──> 外部 MCP server
              │  toolsets   │──> delegate_task ──> 子代理 AIAgent
              └──────────┬──┘──> execute_code 沙箱
                         │
              ┌──────────▼──┐
              │  状态与记忆   │
              │  SessionDB  │  SQLite + FTS5（会话/消息/搜索）
              │  MemoryStore│  MEMORY.md / USER.md
              │  MemoryMgr  │  外部 memory provider
              │  skills     │  技能系统 + Curator
              └─────────────┘
```

**横切系统**（贯穿各层）：配置（config.yaml + .env）、插件（PluginManager）、皮肤（skin_engine）、日志（hermes_logging）、审批（approval）、命令注册表（COMMAND_REGISTRY）、profile 隔离（HERMES_HOME）。

**一句话总结**：Hermes 是一个"**窄腰核心 + 边缘能力**"的 Agent 系统——核心循环是同步的、可缓存的、可中断的；能力（工具/MCP/技能/插件/provider/平台）全部在边缘通过注册表与 ABC 接入；所有设计（三层 prompt、压缩豁免、USER 注入、check_fn 门控、审批分层）都围绕**缓存神圣性**与**人在环**两个约束展开。

---

# 十九、开发者学习路线

## Stage 1：理解 Agent 基础

学习：LLM → Prompt → Tool Calling → Agent Loop。
材料：本文 §2；OpenAI/Anthropic 的 function calling 文档；跑通一个最小的 tool-calling demo。

## Stage 2：理解 Hermes 架构骨架

阅读路径：`hermes` 启动脚本 → `hermes_cli/main.py:main()` → `cli.py:HermesCLI` → `run_agent.py:AIAgent` → `agent/conversation_loop.py:run_conversation`。

重点：看"消息如何进入 → 如何构造 prompt → 如何调 LLM → 如何执行工具 → 如何返回"这条主链（§4）。

## Stage 3：理解 Tool 系统

阅读路径：`tools/registry.py`（地基）→ `toolsets.py`（分组）→ `model_tools.py`（编排）→ 一个代表性工具（`tools/file_tools.py`）→ `tools/approval.py`（审批）。

重点：写一个你自己的工具（§20 Level 2），从"注册 → 暴露 → 执行"走一遍。

## Stage 4：理解 Context / Memory

阅读路径：`agent/system_prompt.py`（三层分级）→ `agent/prompt_builder.py`（SOUL.md/context files）→ `agent/context_compressor.py`（压缩）→ `agent/memory_manager.py` + `tools/memory_tool.py`（记忆）→ `hermes_state.py`（持久化）。

重点：理解"为什么记忆是冻结快照"、"为什么压缩是唯一缓存破坏豁免"。

## Stage 5：理解高级能力

阅读路径：`tools/delegate_tool.py`（子代理）→ `tools/mcp_tool.py`（MCP）→ `gateway/run.py`（网关）→ `cron/`（调度）→ `hermes_cli/plugins.py`（插件）→ `providers/base.py` + 一个 provider 插件。

重点：理解"边缘能力"如何不碰核心地接入。

## Stage 6：动手扩展

完成 §20 的实践任务 Level 1~8。

---

# 二十、实践任务：从 Level 1 到 Mini Hermes

> 每个任务都给出"目标 → 改动文件 → 验收标准"。

## Level 1：修改 System Prompt

- **目标**：给 agent 加一条全局指令（例如"回答永远用中文"）。
- **改**：`~/.hermes/SOUL.md`（身份层），或 `agent/system_prompt.py` 的 `DEFAULT_AGENT_IDENTITY`（回退身份）。验证 SOUL.md 优先。
- **验收**：新会话中 agent 行为改变；**不要**改已缓存 system prompt 的部分（理解"缓存神圣性"）。

## Level 2：新增一个 Tool

- **目标**：写一个 `word_count` 工具，统计文本单词数。
- **改**：新建 `tools/word_count.py`（模块级 `registry.register(...)`），加入 `toolsets.py` 的 `_HERMES_CORE_TOOLS` 或新 toolset。
- **验收**：`hermes` 里让 agent 调用它；工具 schema 出现在 API 请求中；返回 JSON 字符串。

## Level 3：新增一个 Model Provider

- **目标**：接入一个 OpenAI 兼容的自定义端点。
- **改**：新建 `plugins/model-providers/myprovider/__init__.py`，`register_provider(ProviderProfile(...))`。
- **验收**：`hermes model` 能看到它；`hermes setup` 能配好；主链路能调用。

## Level 4：新增一个 MCP Tool

- **目标**：写一个本地 MCP server 暴露一个工具，让 Hermes 接入。
- **改**：任何语言写一个 stdio MCP server；`config.yaml` 的 `mcp_servers` 段注册；`hermes mcp` 验证。
- **验收**：工具以 `mcp__<server>__<tool>` 出现在 schema 中，agent 能调用；理解"MCP 即插即用"。

## Level 5：修改 Agent Loop

- **目标**：让循环在"纯文本回答但包含危险词"时继续追问一次。
- **改**：`agent/conversation_loop.py` 的 no-tool-call 分支（或 `finalize_turn` 前）。
- **验收**：理解循环的每个闸门（interrupt/budget/retry），改动不破坏角色交替。

## Level 6：实现一个简单 Memory

- **目标**：写一个把记忆存成 JSON 文件的 `MemoryProvider` 插件。
- **改**：实现 `agent/memory_provider.py` 的 ABC（`name/is_available/initialize/get_tool_schemas` + `prefetch/sync_turn`），注册进 `memory.provider`。
- **验收**：turn 结束后记忆写入 JSON；下一会话 prefetch 注入上下文。

## Level 7：实现一个 Sub-Agent

- **目标**：理解 `delegate_task` 的隔离语义。
- **改**：不写代码，配置 `delegation.max_concurrent_children`，观察 batch 并行；写一个 role=leaf 被剥夺 delegate_task 的测试。
- **验收**：理解上下文隔离、深度限制、后台队列。

## Level 8：实现一个 Mini Hermes

- **目标**：用 300~500 行写一个最小 agent 系统，包含：LLM + Agent Loop + Tool Calling + Tool Registry + Context + Memory。
- **结构建议**：

```text
mini_hermes/
├── registry.py      # 工具注册表（register/get_definitions/dispatch）
├── tools.py         # 两个示例工具（read_file、run_python）
├── agent.py         # run_conversation：while 循环 + LLM 调用 + 工具执行
├── memory.py        # 简单的 JSON 文件记忆（load/save）
└── main.py          # CLI 入口
```

- **验收**：能对"读取文件并总结"这类任务工作；能理解它和 Hermes 的每个差距（无审批、无缓存、无压缩、无持久化、无中断——这些正是 Hermes 在"裸循环"之上加的工程）。

---

# 二十一、最终总结

## 如果一个开发者真正理解 Hermes，他掌握了什么？

### Agent Engineering
- Agent Loop 是核心，但工程难点在"环"周围的护栏：预算、中断、重试、修复、角色交替。
- 错误是模型的输入（结构化错误回喂），不是用户的负担。
- 控制权交给模型，确定性留给运行时。

### LLM Engineering
- Provider 抽象 + api_mode transport：让系统模型无关。
- Prompt 是产品内容，要可缓存、可分平台、可裁剪。
- 供应商怪癖用 hook 表达，不用 name-check。

### Software Architecture
- "窄腰核心 + 边缘能力"：注册表 + ABC + 插件是可持续扩展的钥匙。
- 门面模式让 god-file 可以安全拆分。
- 事件驱动让核心与 UI 解耦。

### Async Programming
- 事件循环管"多路 I/O 到达"，线程管"单回合内并行"，两者混合是务实的工程选择。
- 理解共享状态的锁与队列是 Agent 系统的常见 bug 来源。

### Tool System
- 注册 ≠ 暴露：工具注册、schema 收集、check_fn 门控、dispatch 是四层。
- 结果形状统一（JSON 字符串）让整个管道可预测。
- MCP 让外部工具零侵入接入。

### Context Engineering
- 上下文是分层资产：head/tail 保真、middle 可摘要。
- 压缩、缓存、记忆、搜索是组合拳，不是单一机制。
- **字节稳定**是最高优先级的约束。

### Memory System
- Context ≠ Memory；记忆要冻结快照、要按需注入、要异步同步。
- 自动注入（prefetch）与按需检索（session_search）分离。

### Agent Runtime
- 同步主循环 + 线程化耗时操作 + 中断事件 = 可靠且可打断。
- 每轮持久化（崩溃恢复）是长期运行的基础。

### MCP
- 标准协议让"工具生态"即插即用；客户端工程（重连/OAuth/看门狗/缓存）是隐藏复杂度。

### Security
- Agent 有权限，所以必须有护栏：hardline 无条件拦截、危险模式审批、沙箱、fail-closed。
- "容器即边界"、"yolo 不是抹盘许可"——这些边界划分值得深思。

## 这些知识如何迁移到其他 Agent 项目

- **任何 tool-calling agent**：registry + dispatch + check_fn 的模式直接复用。
- **任何多供应商系统**：ProviderProfile + transport 的抽象直接复用。
- **任何长会话系统**：三层 prompt 分级 + 压缩 + 缓存的组合是通用答案。
- **任何多平台/多前端系统**：门面 + adapter + 事件驱动是通用结构。
- **任何可扩展产品**：ABC + 注册表 + 插件是"窄腰"的通用机制。

> **最后一句话**：Hermes 最值得学的不是某一个功能，而是它的**权衡方式**——在"模型不可靠、上下文有限、缓存神圣、人在环"这四大约束下，它如何把"能力生长在边缘"这一原则贯彻到底。理解了这套权衡，你就理解了现代 Agent 系统为什么长成这个样子。
