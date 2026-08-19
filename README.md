# Morrow

Morrow（承序）是一个以工作空间为边界的终端 Code Agent。当前版本提供进程内连续对话、
受保护的本地读搜与冲突安全文件修改、审批后 Host 命令、当前 macOS 原生沙箱、只读 Git、
经确认的 Profile 与 Preferences 配置，以及 Provider 管理。

长期产品方向与阶段边界见 [开发路线总览](docs/ROADMAP.md)。

## 安装

需要 Python 3.12 或更新版本，以及 [`uv`](https://docs.astral.sh/uv/)。在仓库根目录执行：

```bash
uv sync
uv run morrow --help
```

Morrow 的状态默认保存在 `~/.morrow`，不会写入选中的项目目录。当前工具只在冻结的工作空间内
读取目录、UTF-8 文本和搜索结果，或通过冲突安全的精确补丁/受控文件创建修改项目文件；Manual 与 Auto Safe
中的项目命令仅能在明确审批后的非隔离 Host 中执行，已批准的 Host 代码仍可能访问工作空间外资源。
当前 macOS 原生后端支持 Auto Sandboxed 在临时快照中自动执行项目命令，默认断网且不会直接修改真实工作区；
Linux 在真实 runner 验证前保持 unsupported，后端不可用时 fail closed。

文件与搜索保护同时检查用户可见路径和工作区内解析后的符号链接目标；`.git`、`.morrow`、凭据文件和
常见私钥内容只返回受保护元数据，仓库检查应使用专用 `git_status`/`git_diff`。现有文件的 patch/replace
保留统一换行格式；混合换行文件会明确返回不支持，不会静默改写为 LF。

## 使用

```bash
morrow [--dir PATH] [--permission-mode manual|auto-safe|auto-sandboxed]
morrow provider list
morrow provider presets
morrow provider add --preset opencode-go
morrow provider add --preset opencode-go-mimo
morrow provider configure opencode-go
morrow provider configure opencode-go --replace-credential
morrow provider test opencode-go
morrow model current
```

用于 OpenCode Go Mimo v2.5 的持久化验收环境可使用仓库内包装命令；首次执行会隐藏输入
API Key，并将凭据保存到 macOS Keychain，配置状态保存到 Morrow 的标准持久目录 `~/.morrow`，之后无需重复配置：

```bash
scripts/morrow-mimo provider add --preset opencode-go-mimo
scripts/morrow-mimo provider test opencode-go
scripts/morrow-mimo model current
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
`list_directory`、`read_file`、`find_files`、`search_text`、`apply_patch`、`write_file`、`show_changes`、`run_command`、`git_status`、`git_diff` 和
`update_configuration`；支持原生沙箱的 Auto Sandboxed 组合额外启用 `promote_sandbox_changes`。不支持 function calling 的 Adapter 不会启用这些工具，但 `/config` 等确定性命令仍可用。终端以 `↳ 工具步骤 n/m：工具名`
展示活动；有副作用的
配置调用和 Host 命令在工具执行前由终端审批。审批拒绝、审批通道不可用或审批等待超时都会安全地形成普通工具结果，
模型可以继续恢复；随包策略的工具超时仍为 120 秒。`/config edit` 保持标量 `set/unset` 语法，列表的
`append/remove` 由自然语言工具提供。达到模型、工具、时间、上下文、结果或循环上限时，任务以稳定的
`stop_code` 结束。

Host 命令审批会展示有界且脱敏的 argv/shell、工作目录、类别和超时；shell 包装的 Git 命令仍按
Git 写入风险在审批前拒绝。命令文本只用于本地审批，不进入 `CommandResult`、Provider、公开事件或持久状态。

`Ctrl+C` 在模型或工具活动期间取消当前任务，之后可以直接继续对话。`/new` 创建并切换到新的
Session，不删除或归档旧会话；仅当对话仍只存在于进程内时才要求确认丢弃。`/exit` 和输入 EOF 在
已持久化会话上直接退出并保留历史；仅进程内未保存对话仍需确认丢弃，取消则留在 REPL，确认提示
期间 EOF 返回 2 且不重置会话。

## 状态与恢复边界

当前持久化内容包括工作空间身份、Profile、全局/工作空间 Preferences、Provider 配置、凭据引用，
以及数据根 Operational Store 中的 Session / TaskRun 状态、版本化 TaskOutcome、Turn / ConversationLog 与受控 Artifact
和 ToolExecution 恢复证据。最终回答只把 TaskRun 置为待接受；普通追问继续同一 TaskRun，只有显式
`/accept`、`/task new`、取消、放弃或恢复命令才改变任务语义。
可用同一 `session_id` 在重启后恢复合法对话；conversation Fork、工具恢复和确定性上下文
checkpoint 仍未实现。工作空间/代码回退不属于 Stage 4，任务后可审查的长期偏好与项目知识学习留到
Stage 5。

旧版本可能留下 `handoff.yaml` 或 `handoff.yaml.bak`。当前版本不读取、校验、迁移、覆盖或自动删除
这些遗留文件；是否导入或清理需要未来单独的产品与数据决策。

状态写入经过校验、revision 检查、同目录临时文件、文件/目录 `fsync` 和原子替换，并保留 `.bak`。
Profile 损坏或版本较新时，工作空间持久状态进入只读模式；workspace Preferences 损坏时只隔离该层。

当前生产工具只通过冻结工作空间服务读取、搜索、修改项目文件和只读 Git 状态/Diff，并通过审批后的非隔离 Host 命令执行校验；
网络能力始终不提供；配置工具只通过应用服务更新既有的 Profile/Preferences 状态。阶段 3 已交付
三轴权限模型、工作空间能力冻结、能力策略、动态系统边界、通用本地审批端口、终端审批 UI，以及有界目录/文件读取、
搜索、SHA-256 冲突安全编辑、原子文件创建、当前运行 ChangeSet/Diff、有界 Host 命令和当前 macOS 的原生
Auto Sandboxed 快照执行；支持后端时还提供始终需审批的当前运行沙箱变更推广。Stage 3 的当前 macOS 验收已完成，
Linux 原生运行仍在真实 runner 验证前保持 unsupported。每次完成工具轮次后，终端可显示一行由本地 ToolFacts/metrics
生成的有界事实摘要；该摘要不进入 Provider、公开事件或持久状态。
`auto-sandboxed` 在 native backend 不可用或无法证明时会 fail closed。持久化聊天历史、Artifact、
恢复属于 Stage 4；checkpoint、fork 和 Full Access Manual 仍在 Stage 4 后续子计划；可审查学习从 Stage 5 开始，Skills/MCP、
Multi-Agent Workflow、GUI 和后台任务属于更后续阶段，当前均未实现。
