# 阶段 1：方向确定与可运行原型

> 状态：2026-08-14 修复与最终树离线、Live、人工验收全部通过；阶段 1 完成
> 阶段结果：一个可在终端连续对话、识别工作空间并可靠留下项目交接的原型  
> 上级文档：[开发路线总览](../ROADMAP.md)  
> 下一阶段：[阶段 2：Agent 核心能力](stage-2-agent-core.md)

## 一、阶段目标与完成定义

Morrow 的第一步不是做出能修改代码的 Agent，而是证明“项目连续性”这件事值得做，并把以后改动代价最高的边界固定下来。

阶段 1 主要验证两件事：

1. 在真实项目目录中，模型调用、流式终端交互和进程内多轮对话能否稳定运行。
2. Morrow 能否可靠识别项目、隔离状态，并在用户回来时提供一份可查看、可控制的下一棒。

阶段完成定义：

> 在任意项目目录启动 Morrow，它知道这是哪个工作空间；只有用户明确选择时才加载旧交接；对话结束后能留下新的有效交接；失败、并发或状态损坏都不会静默覆盖最后一份好数据。

核心闭环：

```text
进入项目目录或执行 morrow --dir PATH
→ 解析工作空间身份
→ 首次配置或加载模型凭据
→ 加载 Profile，并只展示可用的 Handoff
→ 用户选择接力或开始独立会话
→ ContextBuilder 组装上下文
→ 连续流式对话
→ 接力会话退出时更新 Handoff，失败则使用确定性兜底
→ 下次启动继续
```

### 实施切片与门禁

| 切片 | 目的 | 必须完成的范围 | 结果 |
|---|---|---|---|
| **1A：垂直闭环（P0）** | 尽快验证产品方向 | 启动与认证、流式多轮、工作空间识别、Profile/Handoff 存储、显式加载、退出交接、确定性兜底、离线 Fake Provider 测试 | 可用于真实项目试跑，也可开始讨论阶段 2 |
| **1B：边界稳定（P1）** | 避免工具循环建立在含糊边界上 | 编排层、ContextBuilder、三层偏好、受门控的自然语言配置、最小会话控制、Provider 本地管理、并发与恢复契约 | 阶段 1 完成，可开始阶段 2 实现 |
| **后续阶段** | 等真实使用后再定产品细节 | 完整会话库、长期记忆、完整 Provider/Model 控制面、通用撤销、彻底删除、完整 TTY 回归平台 | 不阻塞阶段 1 |

P0 不是整份路线的缩写版：它先贯通同一套核心接口，P1 在其上补稳定边界，不另写第二套实现。阶段推进只依据门禁结果，不依据工期估算。

## 二、产品方向与身份

- 发展顺序：先成为编程助手，再逐步扩展为通用个人助理。
- 初始定位：为独立开发者保持项目连续性的个人 Agent。
- 核心价值：持续理解项目目标、已有决定、当前进展、阻塞事项和下一步行动。
- 差异化方向：围绕个人协作方式和项目连续性设计，不依靠工具或模型数量竞争。
- 数据原则：个人配置和项目状态由用户拥有，可以查看、修改、导出并在产品化阶段彻底删除。
- 阶段 1 不保存完整聊天记录；只在当前进程保留消息，并持久化轻量 Profile 与 Handoff。

项目身份固定为：

- 英文主名：Morrow。
- 中文名：承序。
- CLI：`morrow`。
- Python 发布包：`morrow-agent`。
- Python 导入包：`morrow`。
- 本地数据目录：`~/.morrow`。
- 标语：`Pick up where you left off.`

阶段 1 的终端界面、命令帮助和错误文案先只提供简体中文；`preferences.language` 只控制模型回复语言。完整界面国际化留到阶段 7。

## 三、阶段边界

### 阶段 1A 包含

- `morrow [--dir PATH]` 启动 REPL。
- OpenCode Go 首次配置、凭据安全保存和显式连接测试。
- 同一进程内多轮消息、流式文本和当前回答取消。
- 工作空间身份解析与跨项目状态隔离。
- 工作空间级 `profile.yaml` 和 `handoff.yaml`。
- 启动时展示而不静默注入 Handoff。
- 启动提示中的最小 `/continue`，只用于显式加载已展示的 Handoff。
- 接力会话退出交接、确定性兜底与原子写。
- `/workspace`、`/handoff`、`/status`、`/exit` 的最小入口。
- Fake Provider、离线测试和一组真实 Provider 冒烟测试。

### 阶段 1B 包含

- `SessionOrchestrator`、`CommandService` 与 `ContextBuilder` 的稳定职责边界。
- 全局、工作空间和当前会话三层 Preferences。
- `/config` 的查看与确定性编辑，以及受显式意图门控的自然语言配置。
- `/workspace edit/reset` 与 `/handoff update/edit/clear`。
- 会话过程中 `/new` 与 `/continue` 的最小安全切换语义。
- 通用 Provider 注册表，以及 add/list/show/configure/test 与 model list/current。
- 状态 revision、进程锁、Schema 版本拒绝和恢复路径。

### 明确不包含

- 工具调用循环、文件读取或编辑、Shell、测试和 Git 命令执行。
- 完整会话持久化、历史聊天恢复、长期记忆、向量检索和 Embedding。
- Skills、MCP、插件、多模型路由、自动模型选择和故障切换。
- `/undo` 通用撤销、`workspace forget`、Provider 删除与完整数据清除产品流。
- 固定的 12 格会话矩阵、`--discard` 命令族和具体提示按键。
- Web、桌面端、消息平台、多用户、后台任务和定时任务。
- 对阶段 2 工具事件名称或 Agent Loop 入口作提前承诺。

其中 `/undo`、彻底删除和完整会话生命周期不是被否定，而是分别留给真实写入体验、阶段 4 和阶段 7 决定。阶段 1 仍通过预览确认、原子写和 `.bak` 保证安全。

## 四、首个模型服务与 Provider 边界

### 首个预设

- Provider 预设：OpenCode Go。
- Provider 实例 ID：`opencode-go`。
- Adapter：`openai-compatible`。
- SDK `base_url`：`https://opencode.ai/zen/go/v1`。
- Chat Completions 端点：`https://opencode.ai/zen/go/v1/chat/completions`。
- 首个模型：DeepSeek V4 Flash。
- API Model ID：`deepseek-v4-flash`。
- 内部 `ModelRef`：`opencode-go/deepseek-v4-flash`。

以上端点和模型标识已于 2026-08-13 对照 [OpenCode Go 官方文档](https://opencode.ai/docs/go/)；官方同时说明模型列表可能变化。因此它们是可更新的预设数据，不是核心枚举。每个发布候选必须通过 Live 冒烟测试重新验证，不允许在模型失效时静默换用另一模型。

三个概念保持分离：

| 概念 | 阶段 1 示例 | 责任 |
|---|---|---|
| Adapter | `openai-compatible` | 实现一种 API 协议和响应归一化 |
| Provider 实例 | `opencode-go` | 保存端点、凭据引用和模型集合 |
| Model | `deepseek-v4-flash` | 表示 Provider 下的一个可调用模型 |

非敏感状态采用集合与动态 ID：

```yaml
providers:
  opencode-go:
    adapter: openai-compatible
    base_url: https://opencode.ai/zen/go/v1
    credential_ref: provider:opencode-go:<version>
    models:
      deepseek-v4-flash:
        api_model_id: deepseek-v4-flash

active_model:
  provider_id: opencode-go
  model_id: deepseek-v4-flash
```

固定约束：

- Provider ID 和 Model ID 是动态键，业务层不得使用 OpenCode Go 名称分支。
- Provider 必须引用已注册的 `adapter_id`，Model 必须属于所引用的 Provider。
- 运行时只接收已校验的 `ModelRef`；不得维护彼此可能冲突的“默认 Provider”和“默认模型”。
- 阶段 1 只真实验收一个 Provider 与一个模型，但合同测试必须能注册第二个 Fake Adapter/Provider 而不修改核心。
- OpenCode Go API Key 由用户从服务控制台取得，再通过 Provider 配置流安全录入；不伪装成 Morrow 自己的登录系统。
- 密钥只进入 `CredentialStore` 或显式环境变量，不进入 YAML、日志、事件或终端回显。
- Adapter 必须识别并隔离 Provider 特有的 reasoning 字段；推理内容不转成 `text.delta`，不进入消息历史或 Handoff。
- 阶段 1 不假设 Provider 原生支持 JSON Schema。`complete()` 可以使用提示约束、JSON 提取与 Pydantic 校验；原生结构化输出只能在能力探测通过后作为优化。

### 阶段 1 的 Provider/Model CLI

| 命令 | 阶段 1 行为 |
|---|---|
| `morrow provider add [--preset <preset-id>]` | 从注册表选择预设并完成配置、凭据录入和显式测试 |
| `morrow provider list` | 只从本地状态列出实例 |
| `morrow provider show <provider-id>` | 只从本地显示 Adapter、端点、模型、凭据是否存在和最近测试结果 |
| `morrow provider configure <provider-id>` | 修正该实例的非敏感设置或凭据，并重新测试 |
| `morrow provider test <provider-id>` | 明确联网验证端点、凭据和当前模型 |
| `morrow model list [--provider <provider-id>]` | 只查看已登记模型 |
| `morrow model current` | 显示当前 `ModelRef` |

- 首次没有 `active_model` 时复用 `provider add`，不另建 `auth login`。
- `list/show/model list/model current` 必须离线；只有 `test` 和用户已确认的配置测试步骤可以联网。
- 阶段 1 的 `add` 在没有当前模型时设置 `active_model`；已有当前模型时不自动切换。`model use/add/sync/remove` 与 Provider 删除进入阶段 5。
- 新凭据先以新的 `credential_ref` 暂存，连接测试和配置写入成功后再切换引用；失败时旧配置与旧凭据继续有效。新凭据清理失败只能留下不可达的钥匙串项，不能发布半配置。

### 首次引导

全局首次启动：

1. 解析本地 `active_model` 与 Provider 配置。
2. 按 Provider 实例 ID 查找显式环境变量或 `CredentialStore`。
3. 缺少有效模型连接时进入通用 `provider add`；这是进入对话的唯一硬性前提。
4. 根据终端区域设置选择初始回复语言，但不增加必填问题。

首次进入工作空间：

- 展示推断的项目名和规范化路径，允许接受或改名。
- 说明回答会保存到当前工作空间后，只问一个可跳过的问题：“项目要达成什么，以及现在准备推进哪一步？”
- “项目要达成什么”固定写入 `profile.summary`。
- “现在推进哪一步”固定写入 `handoff.current_goal`，并把该 Handoff 作为本次接力来源。
- 跳过时不生成虚构内容，以独立会话进入对话。
- 不询问 coding/办公/聊天模式；未来如有价值只作为可修改标签，而非能力开关。

## 五、技术栈与架构

### 技术栈

| 层面 | 选择 | 职责 |
|---|---|---|
| 语言与运行时 | Python 3.12 | 应用运行时 |
| 项目管理 | `uv`、`pyproject.toml`、`uv.lock` | 环境与依赖锁定 |
| 异步 | `asyncio` | 流式响应与取消 |
| 模型客户端 | OpenAI Python SDK 异步客户端 | OpenAI-compatible Adapter |
| CLI 参数 | Typer | 进程级命令与参数 |
| 交互输入 | prompt-toolkit | REPL、多行输入和中断 |
| 终端显示 | Rich | 流式文本、状态和错误 |
| 数据模型 | Pydantic v2 | 配置、事件与结构化结果校验 |
| 状态序列化 | PyYAML | 可读、可迁移的本地状态 |
| 凭据 | keyring | 系统钥匙串；环境变量用于测试与无交互运行 |
| 进程锁 | filelock | 跨平台状态写入与工作空间单写者锁 |
| 测试 | pytest、pytest-asyncio | 单元、合同与异步集成测试 |
| 质量 | Ruff | 格式化和静态检查 |

阶段 1 最大的实现风险是 prompt-toolkit、asyncio、Rich 与 `Ctrl+C/Ctrl+D` 的协同；先做最小终端 spike，再扩展命令面。

### 长期骨架

```text
src/morrow/
├── bootstrap.py
├── core/
│   ├── models.py
│   ├── events.py
│   └── ports.py
├── application/
│   ├── orchestrator.py
│   ├── commands.py
│   └── context.py
├── runtime/
│   ├── agent.py
│   └── session.py
├── services/
│   ├── workspace.py
│   ├── config.py
│   ├── handoff.py
│   └── provider.py
├── adapters/
│   ├── models/openai_compatible.py
│   ├── state/yaml.py
│   └── credentials/keyring.py
└── interfaces/
    ├── cli.py
    └── terminal.py
```

依赖方向：

```text
interfaces → application → runtime / services → core
adapters ───────────────────────────────→ core Protocol
bootstrap 负责唯一的具体实现组装
```

- `interfaces` 只收集输入、展示类型化结果和消费事件，不直接访问 Provider、YAML 或钥匙串。
- `SessionOrchestrator` 负责输入分发、确认流程、会话切换和退出码，不承担模型协议或存储细节。
- `CommandService` 协调 `/config`、`/workspace`、`/handoff`、`/new` 等用例。
- `ContextBuilder` 是 Profile、Preferences、Handoff 与消息进入模型的唯一入口。
- `AgentRuntime.run_turn()` 只表示一次普通模型回合；阶段 2 可以新增 `run_task()` 或等价 Agent Loop，不承诺把工具循环塞进该方法。
- `core` 不依赖 OpenAI SDK、Typer、Rich、PyYAML、keyring 或 filelock。
- Adapter Factory 按动态 `adapter_id` 注册；Provider 预设只包含数据。
- YAML 是状态适配器，不是业务协议。
- 不为工具、记忆、Skills 或调度器创建空模块。

`ProjectStateStore` 在阶段 1 是读取 Preferences、Profile 与 Handoff 的窄门面，所有项目级方法必须显式携带 `workspace_id`。阶段 4 的会话/消息存储必须使用新端口，不能继续向这个门面无限追加方法。

### 一轮输入的处理顺序

阶段 1A 尚未启用自然语言配置门控：非斜杠输入全部直接进入普通对话。以下完整分发顺序从阶段 1B 启用：

```text
原始输入
→ 斜杠命令？
   → 是：SessionOrchestrator 调用对应用例；只读命令不调用模型
   → 否：本地 ConfigIntentGate 判断是否存在明确、独立的持久化意图
       → 未命中：ContextBuilder → AgentRuntime.stream()
       → 命中：ModelProvider.complete() 提取受限 ConfigPatch
           → config_patch：预览并应用，本轮不再闲聊
           → clarification_required：只提出一个澄清问题
           → no_change：回到普通流式对话
```

- `ConfigIntentGate` 必须是本地、保守且可测试的规则。只有整条输入是独立配置请求，并且同时包含明确的持久化动作以及作用域、目标或可识别配置字段时才允许命中；单独出现“记住”“以后”“这个项目”“这次”“请”等词不能触发。
- 必须用正反例语料固定门控合同。应命中的例子包括“请记住这个项目以后用中文回复”“把这条约束写进项目档案”；不得命中的例子至少包括“这个项目用什么框架？”“以后再改”“记住刚才的报错”“这次先这样”“请帮我修复这个问题”。
- 普通消息绝不默认先 `complete()` 再 `stream()`，避免主路径双调用。
- 同时包含配置和任务的混合输入必须先由本地规则识别，零次调用 `complete()`，并要求用户拆分；不得静默删除任务部分，也不得让模型改写原始任务。
- `clarification_required` 只允许提出一个澄清问题，不建立隐式配置模式；下一条输入若不是对该问题的直接回答，立即回到普通分发流程。
- `/handoff update` 等明确需要生成结构化结果的命令可以调用 `complete()`，但不经过配置意图门控。

### ContextBuilder 契约

阶段 1 的最小上下文包固定为：

```text
system:
  1. Morrow 身份、当前能力与禁止项
  2. 合并后的 Preferences（仅协作风格）
  3. 当前 Workspace Profile（若有，按用户数据处理）
  4. 指定 revision 的 Handoff（仅接力会话）
messages:
  本进程内已接受的用户/助手消息，保持原序
```

- Profile、Handoff 与 Preferences 是低于固定能力边界的用户数据，不能授予文件、Shell 或外部操作权限。
- 普通流式回合、Handoff 生成和 ConfigPatch 提取若要把这些用户状态送入模型，都必须通过 `ContextBuilder` 的普通或用途限定上下文包；服务不得自行拼接第二套 system/Profile/Handoff/history 提示。
- `instructions[]` 按 global→workspace→session 合并，高优先级内容排在后面以解决用户偏好冲突，但永远不能覆盖固定系统边界。
- 启动时创建上下文基线；本会话内成功修改 Preferences、Profile 或 Handoff 后，必须替换相应类型化快照并在下一轮立即生效。
- Handoff 只在用户明确接力时注入；“发现并展示”不等于“加载进模型”。
- 超过上下文预算时始终保留固定 system、有效 Profile/Handoff 和当前用户消息，再从最新完整回合向前保留；阶段 1 不做摘要压缩。
- 当前用户消息单独超过预算时拒绝调用并提示缩短；不得静默截断用户输入。
- 用户消息在回合被接受时进入进程内历史；只有 `finish_reason=stop` 的完整助手消息进入历史。取消或失败时保留用户消息，但可见的部分助手输出不进入后续上下文，也不自动重放。

### Agent 与模型事件

`AgentRuntime.run_turn(session, message) -> AsyncIterator[AgentEvent]` 使用统一信封：

```yaml
schema_version: 1
type: text.delta
event_id: evt_xxx
session_id: ses_xxx
turn_id: turn_xxx
sequence: 4
timestamp: 2026-08-13T10:00:00+08:00
payload: {}
```

阶段 1 只有 `turn.started`、`status.changed`、`text.delta`、`error`、`turn.completed` 五类公开事件：

- 每个已接受回合恰好一个 started 和一个 completed，`sequence` 从 1 严格递增。
- 正常、取消和失败的 `finish_reason` 分别为 `stop`、`cancelled`、`error`。
- 取消不产生伪错误；致命失败先发 `error`，再发 completed(error)。
- completed 后不得继续发事件。
- 消费端忽略未知事件与未知字段；阶段 2 的具体事件名称由阶段 2 再确认。
- 公开事件不得包含密钥、原始 SDK 对象、未经清洗的异常或 reasoning 内容。

Provider 先把 SDK 响应归一化为内部 `ModelEvent`，再由 Runtime 生成 `AgentEvent`。阶段 1 内部只需要可见文本、完成和错误；Provider 专有 reasoning 被 Adapter 消费或丢弃。

模型错误码先固定为 `auth`、`network`、`rate_limit`、`timeout`、`invalid_response`、`internal`。`cancelled` 是完成原因，不是错误码。重试规则：

- 认证、无效响应和内部错误不自动重试。
- 网络、限流或超时只允许在尚未产生可见文本时自动重试一次；已经输出文本后不得重放整轮。
- 取消永不重试。
- 结构化生成可在总时间预算内进行一次 Schema 修复；随后进入对应确定性失败路径。

### 核心 Protocol

| Protocol | 责任 |
|---|---|
| `ModelProvider` | `stream()` 提供模型事件；`complete()` 提供需要校验的非流式结果 |
| `ProviderFactory` | 按 `adapter_id` 从已校验配置构造 Provider |
| `CredentialStore` | 按版本化凭据引用读取、保存和删除密钥 |
| `WorkspaceResolver` | 把输入目录解析为稳定的工作空间身份 |
| `GlobalConfigStore` | 以单一 revision 读改写包含全局 Preferences、Provider 集合和 `active_model` 的聚合配置 |
| `WorkspaceIndexStore` | 独立读写路径与 `workspace_id` 映射，不承载 Profile/Handoff 内容 |
| `ProjectStateStore` | 以类型化模型和预期 revision 读写阶段 1 项目状态 |

## 六、工作空间身份与状态所有权

### 启动形态与解析算法

```text
morrow [--dir PATH]
```

`--dir` 选择输入目录，缺省为当前目录；它不是跳过 Git 根探测的隐藏模式。目录必须存在且可访问。

解析流程固定为：

1. 对输入执行 `expanduser`、绝对化、解析符号链接并移除多余尾斜杠。
2. 若输入目录本身已在索引中，直接沿用其 `workspace_id`。
3. 在进程内向上查找最近的 `.git` 目录或 gitfile；不得调用 `git` 子进程。
4. 若 Git 根已登记则沿用；若未登记则把该根作为新候选。
5. 没有 Git 根时，优先沿用最近的已登记父目录；仍无匹配才把输入目录作为新候选。
6. 新候选经用户确认后生成一次随机 `workspace_id`，之后身份不由路径哈希重新计算。

路径比较保留文件系统原始大小写，不手动 `casefold`；两个现存路径使用 `samefile` 消除 macOS 大小写与别名差异。不同 Git worktree 的根路径不同，因此默认是不同工作空间。Git 分支不参与身份。

仓库移动或改名不能可靠自动推断：

- 新路径与旧索引不匹配时不得静默继承或覆盖。
- 若存在路径已失效且名称相似的工作空间，只展示候选。
- 阶段 1B 提供 `morrow workspace relink <workspace-id> --dir PATH`，在确认旧路径、新路径和目标未被占用后原子更新索引。
- 已登记目录后来执行 `git init` 时，输入路径的现有索引优先，因此保持原 ID。

工作空间探测只允许读取路径元数据以及 `.git` 目录/gitfile 的存在性；不读取源码、Git 配置、提交记录或项目内容。

### 并发边界

- 阶段 1 对同一工作空间采用单写者模型；REPL 生命周期持有 `~/.morrow/locks/<workspace-id>.lock`。
- 第二个写会话必须明确失败并显示占用信息，不得使用 last-write-wins。
- 全局配置与 workspace index 写入使用各自的短事务锁。
- 每次写入在锁内重新读取磁盘 revision；与调用方 `expected_revision` 不同则返回 `state_conflict`，不覆盖磁盘。
- 锁只位于 Morrow 数据目录，不在项目目录创建任何文件；进程崩溃后由操作系统释放锁。

### 状态布局

```text
~/.morrow/
├── config.yaml
├── workspace-index.yaml
├── locks/
├── logs/
└── workspaces/
    └── <workspace-id>/
        ├── preferences.yaml
        ├── profile.yaml
        └── handoff.yaml
```

| 状态 | 保存内容 | 不保存 |
|---|---|---|
| `config.yaml` | 全局 Preferences、Provider 集合、`active_model` | API Key、项目内容 |
| `workspace-index.yaml` | 路径映射、`workspace_id` 和必要展示元数据 | Profile/Handoff 内容 |
| `preferences.yaml` | 当前工作空间的协作偏好 | 项目事实和任务进度 |
| `profile.yaml` | 稳定项目事实 | 当前任务进度 |
| `handoff.yaml` | 当前接力点及必要恢复注记 | 完整聊天历史 |
| 当前会话内存 | 消息、会话偏好、加载的 Handoff revision、dirty 状态 | 退出后历史 |
| `logs/` | 清洗后的诊断信息 | 密钥和完整敏感配置 |

持久化契约：

- 所有 YAML 顶层包含 `schema_version`、递增 `revision` 和带时区 `updated_at`。
- `config.yaml` 是包含全局 Preferences、Provider 集合与 `active_model` 的单一聚合文档，只能通过同一个 `GlobalConfigStore`、revision 与事务锁做整单读改写；任一领域更新都必须保留其他领域字段。
- 完整 Pydantic 校验通过后，才用同目录临时文件、`fsync` 和原子替换发布。
- 每个可变 YAML 保留一份最近有效 `.bak`，用于损坏恢复，不作为用户级 `/undo`。
- 现有文件不可读、校验失败或 revision 冲突时拒绝覆盖。
- 未知且高于当前实现的 `schema_version` 永不自动降级或覆盖：全局配置/index 不兼容时阻止正常启动；任一 Profile/Handoff 不兼容时进入明确的工作空间状态只读降级——不加载 Handoff、不允许 `/continue` 或任何工作空间持久化写入，但仍允许普通独立对话、session Preferences、全局 Preferences 与 Provider 管理。有效的对应文档只可本地查看；缺失或合法 cleared 文档不触发降级。仅 workspace Preferences 损坏/不兼容时，将该层隔离为空且禁止覆盖或修改它，但不阻止有效 Profile/Handoff 的加载与 `/continue`。
- Workspace Preferences、Profile 与 Handoff 使用独立的版本 2 文档信封：顶层包含 `schema_version: 2`、递增 `revision`、带时区 `updated_at` 与 `state: present|cleared`。`present` 携带原有类型化 payload；`cleared` 不携带领域值。读取结果保持两轴：`StateLoadStatus` 仅有 ok/corrupt/unsupported schema，ok 状态再以 presence 区分 missing/cleared/present，合法 missing/cleared 不触发只读降级。版本 1 文档按 `present` 兼容读取且只在下一次成功写入时升级。缺失文件表示从未创建、revision 0；cleared 文件加载为合法空值并保留落盘 revision，后续重建必须基于该 revision，因此旧 revision 0 不能覆盖清除结果。
- `~/.morrow` 无法创建或无写权限时直接给出可执行错误，不回退写入项目目录或其他隐式位置。
- 环境变量凭据是启动、add/configure/test/show 与 active Provider 构建共享解析器的第一优先级来源，随后才查询 CredentialStore；它永不写入 YAML 或可见输出，错误和日志中统一脱敏。环境凭据存在时拒绝 `provider configure <id> --replace-credential`，并要求先取消该环境变量，避免新存储值被优先级规则静默遮蔽。

## 七、Preferences、Profile 与 Handoff

三个领域保持分离：

| 领域 | 作用域 | 核心字段 |
|---|---|---|
| Preferences | global / workspace / session | `language`、`response_detail`、`instructions[]` |
| Profile | workspace | `name`、`summary`、`goals[]`、`tech_stack[]`、`constraints[]`、`conventions[]` |
| Handoff | workspace | `current_goal`、`progress[]`、`decisions[]`、`blockers[]`、`open_questions[]`、`next_actions[]`、可选 `recovery_note` |

`decisions[]` 保留对象结构 `{decision, reason?}`，因为决定的原因是项目连续性的核心信息。为避免删除语义含糊，同一 Handoff 内 `decision` 文本规范化后必须唯一；删除按规范化后的 `decision` 精确匹配，零个或多个匹配都拒绝并要求澄清。

### Preferences 合并

```text
session > workspace > global > system default
```

- 标量由最高优先级且已设置的值覆盖；`unset` 移除当前层覆盖并露出下一层。
- `instructions[]` 按 global→workspace→session 合并；规范化后重复项只保留最高优先级来源。
- 列表删除只作用于明确作用域，不修改其他层。
- Profile 与 Handoff 不跨作用域合并，也不跨工作空间继承。
- 缺失的 `preferences.yaml` 表示空的 workspace 层；只有第一次成功的 workspace Preferences 补丁才创建该文件。

### 统一写入契约

自然语言配置与字段级确定性编辑都只能产生同一种受限补丁，再由对应应用服务校验：

```yaml
result: config_patch
scope: workspace
target: profile
operations:
  - op: append
    path: constraints
    value: 不引入 LangChain
reason: 用户明确要求
```

提取结果只有三种：

| 结果 | 行为 |
|---|---|
| `no_change` | 不写状态，回到普通对话 |
| `clarification_required` | 只提出一个澄清问题，不写状态 |
| `config_patch` | 预览后整体校验并原子应用 |

补丁约束：

- 作用域只允许 global、workspace、session；目标组合只允许 `global→preferences`、`workspace→preferences/profile/handoff`、`session→preferences`。
- 操作只允许白名单中的 `set`、`unset`、`append`、`remove`。
- 单值使用 set/unset；列表使用 append/remove；一个补丁整体成功或整体失败。
- path 必须来自类型化字段白名单，不能写任意 YAML 路径。
- 普通对话不得被静默提取为配置；只有本地门控识别出明确持久化意图时才调用结构化提取。
- 自然语言禁止修改凭据、Provider、Model、Base URL、`workspace_id`、路径映射、Schema/revision、权限和安全规则。
- 提取或校验失败最多修复一次；仍失败则零写入并解释原因。
- 退出交接和 `/handoff update` 生成的是完整 Handoff，不伪装成字段补丁；它们必须经过同一完整 Handoff Schema 校验后，由 `HandoffService` 使用 expected revision 做整单替换。`/handoff edit`、`/handoff clear` 及其他字段级修改仍只走 ConfigPatch。两条路径共用同一原子状态适配器，均不得接受任意 YAML。

阶段 1B 命令：

| 命令 | 行为 |
|---|---|
| `/config [global|workspace|session]` | 显示生效值、来源或指定层 |
| `/config edit [scope]` | 确定性选择字段与操作，预览后应用 |
| `/config reset <scope>` | 确认后清除该层 Preferences 覆盖 |
| `/workspace` | 查看身份、路径和 Profile |
| `/workspace edit` | 确定性编辑 Profile |
| `/workspace reset` | 只清 Profile，保留身份、Preferences 和 Handoff |
| `/handoff` | 查看最后成功保存的 Handoff，不调用模型 |
| `/handoff update` | 生成并保存新 Handoff |
| `/handoff edit` | 确定性编辑 Handoff 字段 |
| `/handoff clear` | 确认后清除 Handoff，并使本会话不再加载它 |

所有成功修改在下一轮 ContextBuilder 中立即生效。阶段 1 不做通用 `/undo`；重置与清除必须预览并确认，崩溃恢复使用 `.bak`。

清除 Profile、Handoff 或 workspace Preferences 时不删除主文档，而是通过相同的校验、同目录临时文件、文件与目录 `fsync`、原子替换和备份流程发布 `state: cleared`。`/handoff` 与 `/status` 将其显示为“无可用 Handoff”但保留 revision；`/continue` 拒绝加载；清除后的 Profile 可通过显式 onboarding/edit 以该 revision 重建；清除后的 workspace Preferences 等价于空层但下一次补丁仍使用该 revision。

## 八、轻量会话与可靠交接

阶段 1 不建设持久化会话库。会话内只保留：

```text
session_id
messages
session_preferences
handoff_source_revision: int | None
dirty: bool
```

- `handoff_source_revision is None` 表示独立会话；有值表示本会话明确加载了某份 Handoff。
- `dirty` 表示加载/创建检查点后至少接受了一条普通用户消息；只读命令不使其变脏。
- “独立/接力”是阶段 1 的描述，不承诺成为阶段 4 的持久化公开状态枚举。

启动与命令语义：

- 启动发现 Handoff 时只展示摘要；用户输入 `/continue` 才把指定 revision 注入新会话，直接聊天则保持独立。
- `/new` 创建新 `session_id`、清空消息和 session Preferences，不加载旧 Handoff。
- `/continue` 只有在切换时存在有效 Handoff 才创建新接力会话。
- `/status` 本地展示是否已加载 Handoff、是否有未交接内容和当前 revision，不访问网络。
- `Ctrl+C` 第一次只取消当前生成；`Ctrl+D` 走普通退出流程。

安全切换原则：

- 任何命令都不能静默丢弃 dirty 上下文。
- 接力会话 dirty 时，`/new` 或 `/continue` 先保存交接；只有模型结果或确定性兜底成功后才切换。
- 独立会话 dirty 时，切换前提供“保存为新 Handoff / 丢弃当前内存 / 取消”；具体按键和文案不是路线契约。
- 独立会话 dirty 时，普通退出必须明确告知本次内容不会进入 Handoff 并要求确认；确认后不覆盖旧 Handoff。
- 异常终止信号不临时调用模型，也不写不完整状态。

### Handoff 生成与确定性兜底

```text
模型生成完整 Handoff
→ Pydantic 校验
→ 必要时在原预算内修复一次
→ 仍失败则生成确定性最小 Handoff
→ revision 检查与原子写
```

确定性兜底规则：

- 接力会话：复制最后有效 present Handoff，保留原 `current_goal`、已有决定与开放事项，只增加 `recovery_note`，记录“摘要生成失败”以及经过长度限制的最近一次完整用户/助手回合；仅当复制结果的 `current_goal` 为空白、无法作为 present payload 发布时，才按下一条规则补齐。
- 用户明确要求保存独立会话（本会话未加载任何 Handoff）时：以脱敏后的最近用户请求作为 `current_goal`；若其为空则使用固定安全目标 `继续推进当前工作`，并写入同样受限的 `recovery_note`。即使磁盘存在一份仅被发现/展示而未显式加载的 present Handoff，也不得复制它；磁盘缺失或合法 cleared 时使用同一规则。cleared 写入本身仅发布无领域 payload 的 tombstone，绝不为清除动作构造占位 Handoff。
- `recovery_note` 不是完整历史，单条消息分别限制长度并经过与日志相同的敏感信息过滤；下一次成功的模型交接应吸收并清除它。
- 只有磁盘、权限、锁、revision 或确定性 Schema 本身失败时，才保留旧 Handoff 并把保存视为失败。
- `/new`、`/continue` 保存最终失败时保留原 `session_id`、消息、偏好和 Handoff 来源；不得半切换。
- `/exit` 最终失败时保留旧 Handoff，明确警告并以状态码 `2` 退出；模型失败但确定性兜底成功时以 `0` 退出并提示交接为降级版本。

显式交接与退出交接必须有总超时并支持 `Ctrl+C`。初始建议值可以是显式更新 60 秒、退出更新 30 秒，但它们是 Live 测试后可调的默认值，不是不可变产品契约。

退出交接生成期间按下 `Ctrl+C` 表示取消本次退出：取消模型调用、不执行确定性兜底、不写入状态，并完整保留当前会话后回到 REPL。只有超时、模型错误或无效结构化结果才进入确定性兜底并继续退出。

`/handoff update` 生成期间按下 `Ctrl+C` 同样取消当前命令：不执行确定性兜底、不写入状态并返回 REPL。其超时、模型错误或无效结构化结果仍按显式交接规则使用确定性兜底。

## 九、阶段交付物

### 阶段 1A

- 可通过 `morrow [--dir PATH]` 启动的终端程序。
- OpenAI-compatible Adapter、OpenCode Go 预设与安全凭据引导。
- 连续对话、流式显示和本轮取消。
- 工作空间解析、Profile/Handoff 状态与原子写。
- ContextBuilder 最小实现和显式 Handoff 加载。
- 模型交接与确定性兜底。
- Fake Provider、离线测试和最小终端冒烟测试。

### 阶段 1B

- SessionOrchestrator、CommandService 与稳定输入分发。
- 三层 Preferences 和统一 ConfigPatch 应用服务。
- 受门控的自然语言配置与确定性编辑入口。
- `/new`、`/continue` 和安全切换。
- Provider 本地管理、revision 冲突、进程锁和 Schema 恢复路径。
- README、配置说明、数据位置和故障排查说明。

## 十、验收策略

默认测试必须完全离线，不读取真实钥匙串、不访问用户 `~/.morrow`，也不依赖真实时间、随机 ID 或终端宽度。

测试分层：

| 层级 | 重点 |
|---|---|
| Unit | 类型模型、合并、补丁、工作空间算法和状态转换 |
| Contract | ModelProvider、CredentialStore、ProjectStateStore 与 Adapter 注册 |
| Integration | 直接驱动 SessionOrchestrator，使用临时状态目录和 Fake Provider |
| Terminal smoke | 只验证真实 REPL 的流式渲染、`Ctrl+C` 与 `Ctrl+D` |
| Live | 显式验证 OpenCode Go 当前预设和响应形态，不进入默认 CI |

固定测试替身：`ScriptedModelProvider`、`MemoryCredentialStore`、可注入时钟/ID/状态目录的 `AppFactory`、默认封锁 socket 的 `NetworkGuard`。不要求第一阶段建立覆盖所有命令组合的通用 PTY 测试平台。

### 阶段 1A（P0）门禁

| ID | 可执行验收 |
|---|---|
| `S1A-01` | 空状态完成 Provider 引导并进入对话；YAML、事件、终端和日志均无密钥。 |
| `S1A-02` | 连续对话十轮，后续请求按原序包含本进程历史；流式分片有序。 |
| `S1A-03` | 首次 `Ctrl+C` 只取消当前回答，产生 completed(cancelled)，随后仍可对话。 |
| `S1A-04` | 路径别名、Git 根、非 Git 目录和两个 worktree 按算法得到稳定身份；两个工作空间 Profile/Handoff 不串用。 |
| `S1A-05` | 启动只展示 Handoff；独立对话请求不含它，显式接力后上下文才包含指定 revision。 |
| `S1A-06` | 接力会话正常退出生成完整交接；模型错误、超时和无效 Schema 触发合法兜底；独立会话未显式保存时旧 Handoff 字节不变；任何路径都不写半份文件。 |
| `S1A-07` | 正常、取消和失败模型流满足事件生命周期；reasoning、密钥和原始异常不进入公开事件。 |
| `S1A-08` | 可注册第二个 Fake Adapter/Provider；核心无 Provider 名称分支；除 `.git` 存在性元数据外不读项目，不写项目，不调用子进程。 |

### 阶段 1B（P1）门禁

| ID | 可执行验收 |
|---|---|
| `S1B-01` | 三层标量覆盖、unset 和 instructions 合并正确；成功修改在下一轮上下文立即生效。 |
| `S1B-02` | 普通聊天及 must-not-trigger 语料零提取调用；独立明确配置语料产生 patch/澄清/no_change；混合输入本地拒绝且零提取；敏感字段被拒绝且零副作用。 |
| `S1B-03` | `/new`、`/continue` 和退出不会静默丢 dirty 上下文；保存最终失败不切换会话。 |
| `S1B-04` | 同一工作空间第二写进程被拒绝；revision 冲突、损坏文件和未知 Schema 均不覆盖旧状态。 |
| `S1B-05` | Provider 添加/修改失败时旧连接仍有效；所有本地查看命令在 NetworkGuard 下通过且不隐式联网。 |
| `S1B-06` | relink 只原子更新索引，Profile/Handoff 保持原 ID；Profile reset 与 Handoff clear 只影响声明的数据。 |

### 人工与 Live 验收

1. 从空状态使用真实 OpenCode Go API Key 完成配置，验证 DeepSeek V4 Flash 可流式回答。
2. 捕获一次真实流式响应，确认可见正文、reasoning 和完成信号被正确区分；若服务不提供独立 reasoning 字段，也不得假定其存在。
3. 真实终端连续十轮，在长回答中测试 `Ctrl+C`，并测试 `Ctrl+D` 的普通退出交接。
4. 在两个测试项目和一个移动后的测试仓库中验证隔离、提示和 relink。
5. 断网退出接力会话，确认确定性兜底可在下一次启动被展示和继续。

阶段门禁：

- `uv run ruff format --check .` 与 `uv run ruff check .` 通过。
- `uv run pytest -m 'not live'` 零失败、零意外跳过，并由 NetworkGuard 证明没有外部请求。
- 终端层只要求稳定的关键冒烟测试；业务矩阵在 Orchestrator 层完成。
- 发布候选至少完成一次 Live 与全部人工验收。
- 所有失败写入测试都断言旧文件仍可解析或字节不变；所有输出经过密钥哨兵扫描。

## 十一、已知风险与待验证假设

以下事项不改变已经锁定的边界，但必须用 P0/P1 实现证据收敛：

| 假设 | 验证方式 | 可能调整的部分 |
|---|---|---|
| OpenCode Go 模型目录与端点会变化 | 每个发布候选查询官方文档并跑 Live 测试 | 仅更新预设数据 |
| DeepSeek V4 Flash 的 reasoning/正文分片形态稳定 | 捕获脱敏后的真实流并做 Adapter 合同测试 | Adapter 映射，不改 Runtime |
| 非原生结构化输出足以生成 Handoff/ConfigPatch | 统计首次成功、修复和兜底比例 | 提示、解析器或默认模型；不能取消兜底 |
| 本地配置意图门控不会频繁误判普通对话 | 在真实使用中记录匿名计数和人工样例 | 门控词与交互文案；不改统一补丁契约 |
| 退出交接延迟可接受 | 记录模型路径与兜底路径耗时 | 默认超时，不改原子写与兜底语义 |
| “独立/接力”术语值得对外展示 | P0 真实项目试用 | 展示词；内部只依赖 revision/dirty |

## 十二、锁定结论

阶段 1 现在锁定的是：

- 产品身份、工作空间所有权与隔离算法。
- Adapter/Provider/Model 分层与 `ModelRef`。
- 密钥边界、状态 Schema、原子写、revision 与单写者并发语义。
- SessionOrchestrator、ContextBuilder、单轮 Runtime 的职责方向。
- AgentEvent 信封与阶段 1 生命周期。
- Preferences/Profile/Handoff 三分和统一受限补丁。
- Handoff 必须有不依赖模型成功的确定性退路。
- 阶段 1 不读源码、不改项目、不执行命令。

允许根据真实使用调整的是：模块文件名、终端文案与按键、配置意图门控词、交接超时默认值、是否向用户暴露“独立/接力”术语，以及 Provider 预设数据。

阶段 1A 用来尽早验证方向，阶段 1B 用来稳定阶段 2 会依赖的边界。两者都通过后，才进入工具调用循环的实现。
