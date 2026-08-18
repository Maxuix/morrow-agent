# Morrow

Morrow（承序）是一个以工作空间为边界的终端 Agent。当前版本提供进程内连续对话、受限的
只读内存工具、经确认的 Profile 与 Preferences 配置，以及 Provider 管理。

长期产品方向与阶段边界见 [开发路线总览](docs/ROADMAP.md)。

## 安装

需要 Python 3.12 或更新版本，以及 [`uv`](https://docs.astral.sh/uv/)。在仓库根目录执行：

```bash
uv sync
uv run morrow --help
```

Morrow 的状态默认保存在 `~/.morrow`，不会写入选中的项目目录。当前工具不会读取项目源码、
执行项目命令或访问网络。

## 使用

```bash
morrow [--dir PATH]
morrow provider list
morrow provider add --preset opencode-go
morrow provider configure opencode-go
morrow provider configure opencode-go --replace-credential
morrow provider test opencode-go
morrow model current
```

首次启动需要配置一个 Provider。OpenCode Go 的 API Key 通过不回显的交互输入或显式的
`MORROW_OPENCODE_GO_API_KEY` 环境变量提供；密钥只进入 CredentialStore，不写入 YAML、日志、
事件或模型上下文。环境变量优先于 CredentialStore；环境变量存在时必须先取消它，才能使用
`--replace-credential` 轮换存储凭据。

REPL 常用命令包括 `/workspace`、`/workspace edit summary ...`、`/workspace reset`、`/status`、
`/config`、`/config edit workspace language 中文`、`/config reset workspace`、`/new` 和 `/exit`。
所有确定性编辑和自然语言配置都会先显示作用域、目标、操作、字段和值，确认后才写入。
自然语言配置只在用户明确要求保存、写入、记住或更新时调用标准
`update_configuration` 工具；本次回答风格、问题、解释、假设、引用和否定句不会持久化。
`session`、`workspace`、`global` 分别表示本次会话、当前工作空间和全局 Preferences；Profile 只允许
在当前工作空间修改。每个工具调用独立确认和提交，多个调用不会组成跨调用事务；前一个调用成功、后一个
调用被拒绝或失败时，前一个结果保留并分别报告 `applied`、`unchanged` 或失败状态。

普通对话统一经过 Agent Loop。支持 OpenAI-compatible function calling 的 Adapter 会向模型提供
`lookup_record`、`calculate` 和 `update_configuration`；不支持 function calling 的 Adapter 不会启用
这些工具，但 `/config` 等确定性命令仍可用。终端以 `↳ 工具步骤 n/m：工具名` 展示活动；有副作用的
配置调用在工具执行前由终端审批。审批拒绝、审批通道不可用或审批等待超时都会安全地形成普通工具结果，
模型可以继续恢复；随包策略的工具超时仍为 120 秒。`/config edit` 保持标量 `set/unset` 语法，列表的
`append/remove` 由自然语言工具提供。达到模型、工具、时间、上下文、结果或循环上限时，任务以稳定的
`stop_code` 结束。

`Ctrl+C` 在模型或工具活动期间取消当前任务，之后可以直接继续对话。`/new` 在会话干净时立即重置；
有进程内对话时必须明确确认丢弃。`/exit` 和输入 EOF 在会话干净时返回 0；有进程内对话时必须确认
丢弃，取消则留在 REPL，确认提示期间 EOF 返回 2 且不重置会话。

## 状态与恢复边界

当前持久化内容只有工作空间身份、Profile、全局/工作空间 Preferences、Provider 配置和凭据引用。
Session 持有的 ConversationLog 只存在于当前进程；退出或重启后不能恢复、列出、归档或继续旧会话。
持久化 Session、Fork 和上下文摘要留到 Stage 4；任务后可审查的长期偏好与项目知识学习留到 Stage 5。

旧版本可能留下 `handoff.yaml` 或 `handoff.yaml.bak`。当前版本不读取、校验、迁移、覆盖或自动删除
这些遗留文件；是否导入或清理需要未来单独的产品与数据决策。

状态写入经过校验、revision 检查、同目录临时文件、文件/目录 `fsync` 和原子替换，并保留 `.bak`。
Profile 损坏或版本较新时，工作空间持久状态进入只读模式；workspace Preferences 损坏时只隔离该层。

当前生产工具不读写项目文件、不执行 Shell/Git、不联网；配置工具只通过应用服务更新既有的 Profile/
Preferences 状态。阶段 3 已交付通用本地审批端口、终端审批 UI 和第一个状态化工具切片；本地项目工具、
文件读取、搜索、编辑和 Shell MVP 尚未完成，不能据此执行真实项目修改。持久化聊天历史/摘要、可审查
学习、Skills/MCP、Multi-Agent Workflow、GUI 和后台任务分别属于 Stage 4 及之后的路线，当前均未实现。
