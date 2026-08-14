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

## 网络与恢复

本地查看命令不联网；Provider 测试和用户确认后的模型回合才访问网络。所有状态写入经过校验、revision 检查、
同目录临时文件、文件/目录 `fsync` 和原子替换，并保留一份 `.bak`。工作空间 clear 发布带 revision 的
`state: cleared` tombstone；损坏或未来版本状态不会被静默覆盖。Profile/Handoff 损坏时工作空间持久状态整体只读，
只有 workspace Preferences 损坏时则仅隔离该层。

阶段 1 不包含工具调用、文件读写、Shell/Git 执行、持久化聊天历史、长期记忆、Skills 或后台任务。
