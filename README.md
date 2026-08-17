# Morrow

Morrow（承序）是一个以工作空间为边界、保存轻量项目交接的终端对话原型。

## 安装

需要 Python 3.12 或更新版本，以及 [`uv`](https://docs.astral.sh/uv/)。在仓库根目录执行：

```bash
uv sync
uv run morrow --help
```

Morrow 的状态默认保存在 `~/.morrow`，不会写入选中的项目目录，也不会读取项目源码或执行项目命令。

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
`MORROW_OPENCODE_GO_API_KEY` 环境变量提供；密钥只进入 CredentialStore，不写入 YAML、日志、事件或模型上下文。
环境变量优先于 CredentialStore；环境变量存在时必须先取消它，才能使用 `--replace-credential` 轮换存储凭据。

REPL 中常用命令：`/workspace`、`/workspace edit summary ...`、`/workspace reset`、`/handoff`、
`/handoff update`、`/handoff edit current_goal ...`、`/handoff clear`、`/continue`、`/status`、
`/config`、`/config edit workspace language 中文`、`/config reset workspace`、`/new`、`/exit`。
启动发现交接时只展示摘要，只有明确输入 `/continue` 才会将它加载进上下文。`Ctrl+C` 取消当前回答，
`Ctrl+D` 走正常退出流程。
所有确定性编辑和自然语言配置都会先显示作用域、目标、操作、字段和值，确认后才写入。

普通对话统一经过 Agent Loop。支持 OpenAI-compatible function calling 的 Adapter 会向模型提供
`lookup_record` 和 `calculate` 两个只读内存工具；模型可以连续调用多个步骤，终端以
`↳ 工具步骤 n/m：工具名` 分隔中间文本、工具活动和最终回答。工具参数错误、未找到、超时或执行失败
会作为有界结果交还模型，模型可以恢复并继续回答。达到模型调用、工具调用/轮次、总时间、上下文、
结果或重复循环上限时，任务会以稳定的 `stop_code` 安全结束。

`Ctrl+C` 在模型或工具活动期间取消当前任务；已经接纳的工具调用会先用真实或合成结果闭合，之后可直接
开始下一轮对话，无需重置会话。ConversationLog 只存在于当前进程，`/new` 会清空它；需要跨进程延续的
内容仍只能通过显式 Handoff 保存。

## 网络与恢复

本地查看命令不联网；Provider 测试和用户确认后的模型回合才访问网络。所有状态写入经过校验、revision 检查、
同目录临时文件、文件/目录 `fsync` 和原子替换，并保留一份 `.bak`。工作空间 clear 发布带 revision 的
`state: cleared` tombstone；损坏或未来版本状态不会被静默覆盖。Profile/Handoff 损坏时工作空间持久状态整体只读，
只有 workspace Preferences 损坏时则仅隔离该层。

阶段 2 的工具仅操作注入的内存数据，不读取或修改项目文件，不执行 Shell/Git，不访问网络。当前仍不包含
本地项目工具、持久化聊天历史、长期记忆/摘要、Skills、MCP、审批系统或后台任务；这些属于后续阶段。
