# Morrow

Morrow（承序）是一个以工作空间为边界的终端 Agent。当前版本提供进程内连续对话、受限的
只读内存工具、Profile 与 Preferences 配置，以及 Provider 管理。

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

普通对话统一经过 Agent Loop。支持 OpenAI-compatible function calling 的 Adapter 会向模型提供
`lookup_record` 和 `calculate` 两个只读内存工具。终端以 `↳ 工具步骤 n/m：工具名` 展示活动；
工具失败会作为有界结果交还模型。达到模型、工具、时间、上下文、结果或循环上限时，任务以稳定的
`stop_code` 结束。

`Ctrl+C` 在模型或工具活动期间取消当前任务，之后可以直接继续对话。`/new` 在会话干净时立即重置；
有进程内对话时必须明确确认丢弃。`/exit` 和输入 EOF 在会话干净时返回 0；有进程内对话时必须确认
丢弃，取消则留在 REPL，确认提示期间 EOF 返回 2 且不重置会话。

## 状态与恢复边界

当前持久化内容只有工作空间身份、Profile、全局/工作空间 Preferences、Provider 配置和凭据引用。
Session 持有的 ConversationLog 只存在于当前进程；退出或重启后不能恢复、列出、归档或继续旧会话。
持久化 Session、Fork、上下文摘要和长期记忆留到阶段 4 另行设计。

旧版本可能留下 `handoff.yaml` 或 `handoff.yaml.bak`。当前版本不读取、校验、迁移、覆盖或自动删除
这些遗留文件；是否导入或清理需要未来单独的产品与数据决策。

状态写入经过校验、revision 检查、同目录临时文件、文件/目录 `fsync` 和原子替换，并保留 `.bak`。
Profile 损坏或版本较新时，工作空间持久状态进入只读模式；workspace Preferences 损坏时只隔离该层。

阶段 2 的工具不读写项目文件、不执行 Shell/Git、不联网。当前不包含本地项目工具、持久化聊天历史、
长期记忆/摘要、Skills、MCP、审批系统或后台任务；这些属于后续阶段。
