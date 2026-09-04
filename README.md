# Morrow

Morrow（承序）是一个以工作空间为边界的终端 Code Agent。当前版本提供可恢复的持久化对话、
有界的本地读搜与冲突安全文件修改、直接 Host 命令、当前 macOS 原生沙箱、只读 Git 检查、
经确认的 Profile 与 Preferences 配置、Provider/Model 管理，以及受治理的 Skill/MCP 扩展
与可审查的任务后学习。

长期产品方向与阶段边界见 [开发路线总览](docs/ROADMAP.md)。

## 安装

需要 Python 3.12 或更新版本，以及 [`uv`](https://docs.astral.sh/uv/)。在仓库根目录执行：

```bash
uv sync
uv run morrow --help
```

Morrow 的状态默认保存在 `~/.morrow`，不会写入选中的项目目录。当前工具只在冻结的工作空间内
读取目录、UTF-8 文本和搜索结果，或通过冲突安全的精确补丁/受控文件创建修改项目文件；Manual 与 Auto Safe
中的项目命令在非隔离 Host 中直接执行，Host 代码可能以当前用户权限访问工作空间外资源。
当前 macOS 原生后端支持 Auto Sandboxed 在临时快照中自动执行项目命令，默认断网且不会直接修改真实工作区；
Linux 在真实 runner 验证前保持 unsupported，后端不可用时 fail closed。

文件与搜索仍拒绝工作空间逃逸、外部符号链接和不支持的文件类型，但不会因为 `.git`、`.env`、
`secret`、凭据示例或 PEM 文本等普通关键词隐藏工作区内容。仓库检查可通过 `bash` 运行只读
`git status`/`git diff`。`edit` 和
`write` 会在执行端自动冻结当前文件版本、判断 create/replace，并在发布时再次检查冲突；模型不需要传
SHA-256 或写入模式。现有文件保留统一换行格式；混合换行文件会明确返回不支持，不会静默改写为 LF。

## 使用

```bash
morrow [--dir PATH] [--permission-mode manual|auto-safe|auto-sandboxed|full-access-manual]
morrow [--dir PATH] --session-id SESSION_ID
morrow provider list
morrow provider presets
morrow provider add --preset opencode-go
morrow provider add --preset opencode-go-mimo
morrow provider configure opencode-go
morrow provider configure opencode-go --replace-credential
morrow provider test opencode-go
morrow model current
morrow grant list --agent-run-id AGENT_RUN_ID
morrow grant create TASK_RUN_ID AGENT_RUN_ID --reason "一次明确的本地验证"
morrow grant revoke GRANT_ID --reason "用户撤销"
morrow session list --dir PATH --limit 50 --json
morrow session resume SESSION_ID --dir PATH
morrow task list SESSION_ID --dir PATH --limit 50 --json
morrow artifact list --dir PATH --limit 50 --json
morrow recovery show SESSION_ID --dir PATH
morrow recovery resolve REPORT_ID RESOLUTION --dir PATH
morrow state doctor --workspace-id WORKSPACE_ID
morrow state cleanup --workspace-id WORKSPACE_ID [--apply]
morrow learning status --workspace-id WORKSPACE_ID
morrow learning set-mode review-only --workspace-id WORKSPACE_ID
morrow learning inbox --workspace-id WORKSPACE_ID
morrow learning review REVIEW_ID --workspace-id WORKSPACE_ID
morrow learning retry REVIEW_ID --workspace-id WORKSPACE_ID
morrow memory selection list --workspace-id WORKSPACE_ID
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

## Web GUI 与任务规划

```bash
morrow serve          # 仅启动 headless Core API（loopback + 一次性会话 token）
morrow gui            # 启动同一个 Core 服务器并打开浏览器中的 Web GUI
```

`morrow gui` 与 `morrow serve` 是同一个前台 Core 进程：只监听 loopback，Ctrl+C 优雅退出；
GUI 静态资源由 Core 服务器直接提供，浏览器地址中的一次性会话 token 位于 URL fragment，
不会作为 HTTP URL 发送到服务器。GUI 提供 Session/Task/Workflow/节点/Artifact/用量观察、
运行控制与审批、Workflow Draft 编辑器和 Agent Inspector。编辑器中的“根据任务生成 Draft”
会按任务范围、风险与审查价值组合已发布 Agent；简单任务优先 Direct。生成后可编辑、冻结并手工运行。
生成过程不发布 Agent、冻结 Revision 或启动 Workflow；尚无 Direct/Multi 对照收益证据时，
即使保存了自动运行偏好，也仍需手工确认。

CLI 使用相同 Planner 和策略服务：

```bash
morrow workflow plan planning.json --workspace-id ws_example
morrow workflow policy show --workspace-id ws_example
morrow workflow policy set policy.json --expected-revision 0 --workspace-id ws_example
```

`planning.json` 的最小示例（先发布所需的 AgentDefinition）：

```json
{
  "draft_id": "wdraft_example",
  "workflow_definition_id": "my_task",
  "name": "My task",
  "task": {"objective": "Research storage designs", "scope": ["SQLite", "Postgres"]},
  "use_model": true,
  "scout": false
}
```

`use_model` 使用当前模型做一次无工具的结构化分类，失败时显示诊断并使用本地规则；
设为 `false` 可完全不调用 Provider。可选 Scout 只做一次有界项目目录检查。
`requested_roles` / `excluded_roles` 可声明角色；`budget` 可显式设置已有 Workflow 请求上限、
默认节点请求上限和准入超时，省略时保持无上限。模型与 Skill 参数化通过选取已有精确 Agent 版本完成，
不自动创建或扩大权限。所有生成图仍串行执行。

`policy.json` 例如 `{"scope":"workspace","multi_agent":false}`。策略保存在现有的全局/工作空间
`extensions.yaml.orchestration` 中，按任务类型匹配，工作空间的完整策略覆盖全局策略。
修改使用该 Extension 文档的 revision，保留 Skill/MCP 设置。`auto_run_mode` 默认
`approval_only`；`allow_promoted` 仅保存用户偏好，当前没有已推广的任务类型。
`auto_replan_mode` 默认 `approval_only`，自动 Replan 由后续 Subplan 交付。

源码检出中构建 GUI 资源（发布 wheel/sdist 的前置步骤，缺少资源时 `uv build` 会显式失败）：

```bash
cd gui && pnpm install && pnpm build   # 产出 src/morrow/gui_static/
```

## 运行策略配置

Morrow 随程序发布只读的 `morrow/resources/runtime-policy.toml` 作为 AgentRun 和 Preference
Review 的默认运行策略。Learning Reviewer 继承 main Agent 的模型和上下文预算；Learning 与
Preference Reviewer 的单次超时统一为 5 分钟。用户不需要也不应修改安装包资源；如需调整，可在自己的
`~/.morrow/config.yaml` 中添加可选覆盖，例如：

```yaml
runtime_policy:
  agent_run:
    tool_timeout_seconds: 180
    reserve_tokens: 20000
```

覆盖在进程启动时加载。未知字段、错误类型、非有限数、违反字段组合或超过代码级安全上限的值会使
配置整体拒绝加载，不会部分生效。权限、审批、密钥/路径过滤、schema/payload/storage
预算与重试的错误分类边界不允许通过 YAML 放宽；重试次数等可调字段受代码级上限约束；Agent 的配置
与学习工具也不能写 `runtime_policy`。完整字段
和硬编码分类见 [Runtime Policy Configuration Boundary](docs/decisions/runtime-policy-configuration.md)。

REPL 常用命令包括 `/workspace`、`/workspace edit summary ...`、`/workspace reset`、`/status`、
`/preferences`、`/task`、`/accept`、
`/grant`、`/recovery`、`/new` 和 `/exit`。默认启动会创建新的 Session；若要继续已有 Session，
使用 `--session-id SESSION_ID` 或 `session resume SESSION_ID`。检测到已有可恢复 Session 时，启动会
显示其 ID；恢复后如有未完成的安全对账，先用 `/recovery` 查看并处理，再继续同一回合。
独立 CLI 的 `recovery resolve` 会自动生成幂等 command ID；需要安全重试同一请求的客户端仍可显式传入
`--command-id`。其中 `resume` 只持久化恢复决策并准备新的 AgentRun，不会调用 Provider；随后请用
`morrow --dir PATH --session-id SESSION_ID` 继续模型回合。
所有确定性编辑和自然语言配置都会先显示作用域、目标、操作、字段和值，确认后才写入。
自然语言配置只在用户明确要求保存、写入、记住或更新时调用标准
`manage_preferences` 工具；`update_configuration` 只管理 Workspace Profile。本次回答风格、
问题、解释、假设、引用和否定句不会持久化。
`session`、`workspace`、`global` 分别表示本次会话、当前工作空间和全局 Preferences；Profile 只允许
在当前工作空间修改。每个工具调用独立确认和提交，多个调用不会组成跨调用事务；前一个调用成功、后一个
调用被拒绝或失败时，前一个结果保留并分别报告 `applied`、`unchanged` 或失败状态。

普通对话统一经过 Agent Loop。支持 OpenAI-compatible function calling 的 Adapter 会向模型提供七个
核心编码工具：`read`、`ls`、`find`、`grep`、`edit`、`write` 和 `bash`。配置、Preference、Artifact
与 Skill 工具只在对应能力被组合时额外提供；支持原生沙箱的 Auto Sandboxed 组合还会启用
`promote_sandbox_changes`。不支持 function calling 的 Adapter 不会启用这些工具，但 `/workspace`、
`/preferences` 等确定性命令仍可用。终端以 `↳ 工具步骤 n/m：工具名`
展示活动；有副作用的
配置调用仍在工具执行前由终端审批；普通工作空间文件工具和 Host 命令直接执行。Full Access 与扩展能力
仍使用各自的授权/审批合同。审批拒绝、审批通道不可用或审批等待超时都会安全地形成普通工具结果，
模型可以继续恢复；默认单工具超时为 120 秒，并可在安全上限内通过用户运行策略覆盖。
当前 long-horizon 运行没有累计模型请求、工具轮次、调用次数、总时长或重复循环上限；上下文压缩、
单次工具超时和结果预算仍会以稳定的 `stop_code` 或工具结果显式结束相应边界。

Host 命令接受 argv 或 shell，支持 Git、管道和重定向，不按命令字符串启发式拒绝或要求审批。命令输出
保持有界，并只遮蔽当前运行已知凭据的精确值；完整命令、输出和秘密不进入公开事件或持久状态。

`full-access-manual` 不会自动获得能力：只有本地 REPL 的 `/grant` 确认或 CLI `grant create` 命令能为一个前台
AgentRun 授予 `unconfined_host_process`，且每次 opaque Host 命令仍要单独审批。审批前会明确说明该进程没有操作系统
隔离，可能以当前用户权限触达用户文件、网络、凭据、套接字和 Morrow 状态；Grant 只对创建它的 AgentRun
有效，过期、撤销或崩溃恢复后的新 AgentRun 都会 fail closed。

`session list`、`task list` 和 `artifact list` 在文本模式下会显示可用的
`next_cursor`；`--json` 保留 `items` 和 `next_cursor`，适合脚本分页。`state doctor`
仍会输出完整诊断报告，但只有 health OK 时 exit 0，其他状态 exit 2。

Agent 运行时可继续输入：Enter 将文本作为 steering，在下一个安全点结束当前 Turn 并提交新的
持久化 Turn；Alt+Enter 将文本作为 follow-up，仅在 Agent 正常停止后按 FIFO 执行。已接纳的工具
批次不会因 steering 被跳过或中断。`Ctrl+C` 仍在模型或工具活动期间取消当前任务，之后可以直接
继续对话。`/new` 创建并切换到新的
Session，不删除或归档旧会话；仅当对话仍只存在于进程内时才要求确认丢弃。`/exit` 和输入 EOF 在
已持久化会话上直接退出并保留历史；仅进程内未保存对话仍需确认丢弃，取消则留在 REPL，确认提示
期间 EOF 返回 2 且不重置会话。

## 静态 Workflow

Stage 7 的 Workflow 是显式选择的前台串行运行；普通聊天仍默认走 Direct。`morrow agent` 管理
Agent desired source、纯只读 validate、显式 publish、Head enable/disable 与精确不可变版本 revoke；
`morrow workflow` 提供对应的定义管理，以及 `run/status/resume/abandon` 和 `node show`。`validate`
不会创建 Version/Revision 或推进 Head；plain `run` 必须给出已经发布的精确 `--revision`，而显式
`--ensure-published` 与 `--revision` 互斥，会先发布并回显所选 Revision，同时区分新建与 content-hash
复用。`create/edit` 只接受 `origin=user` 的文件，内置源
只读；如需定制，请以新 ID 创建用户定义。

```bash
morrow agent list --dir PATH
morrow agent validate DEFINITION_ID --dir PATH
morrow agent publish DEFINITION_ID --expected-head-revision HEAD_REV --command-id COMMAND_ID --dir PATH
morrow workflow list --dir PATH
morrow workflow clone builtin_explore_implement_verify MY_WORKFLOW \
  --expected-revision SOURCE_REVISION --dir PATH
morrow workflow validate DEFINITION_ID --dir PATH
morrow workflow publish DEFINITION_ID --expected-head-revision HEAD_REV --command-id COMMAND_ID --dir PATH
morrow workflow runs --dir PATH
morrow workflow run DEFINITION_ID --revision WORKFLOW_REVISION_ID \
  --session SESSION_ID --root-task TASK_RUN_ID --expected-task-version TASK_ROW_VERSION --dir PATH
morrow workflow status WORKFLOW_RUN_ID --dir PATH
```

`workflow run` 在持久化 Start 后、调用模型前回显 `workflow_run_id`，因此前台进程意外退出后可直接
使用 `workflow status/resume`；若未保存该行，可用 `workflow runs` 查询持久化 Run。命令完成时，
`completed`（包括 `needs_revision`）返回 0，`failed/cancelled/blocked` 返回 1，参数或配置错误返回 2。

内置 Workflow 只提供 Direct 和 Explore Implement Verify 两个起点。后者是最小示例，不是固定
协议：三个普通节点只通过同一种 `TextResult@1/result` 链接传递上一节点的最终结果。先用
`workflow clone` 复制成 `origin=user` 的 desired source，便可像其他 Workflow 一样增删或替换任意
节点、角色、边和绑定，例如在 Coder 与 Reviewer 之间插入 Web Developer；之后再 `edit`、
`validate`、`publish`。历史 Revision 和用户定义里的 `EvidenceBundle`、`ReviewReport` 等结构化合同
继续可读、可运行，也可由高级自定义图显式选择，但不再是内置多 Agent 链路的前提。

总 Agent generation request 上限、节点默认上限和 admission timeout 都是可空的用户 guardrail；
内置起点不预设这三项，因此不会因系统猜测的请求次数或任务时长自动终止。显式填写正数时仍由
持久化准入层按原语义执行，`max_concurrency=1` 继续描述当前串行 Scheduler。无论是否设置上限，
模型请求与 usage 都照常记录；用户可用前台 `Ctrl+C` 或 `workflow pause/resume` 控制运行。
重新运行会创建新 Run，不会把旧 Run 的叶子当缓存。

普通 disable 只阻止新的 Workflow/Agent admission，已接纳 Run 继续使用冻结 Revision；emergency
revoke 针对精确 `adev_...` 或 `wrev_...`，是带原因和 command ID 的永久单向安全刹车。blocked Run
应先对账未知 Tool 结果再 `resume`；只有无本进程 live handle 的 OCC-current blocked Run 才能
`abandon`，该操作保留未知证据。当前格式的 `state backup`/`state verify-backup` 包含 definition YAML
与完整 SQLite Workflow 记录，restore 仍只写新的隔离目标；`state doctor` 会把 desired-ahead 作为
局部 warning，把不可变引用/hash/运行关系损坏报告为 repair error。

`ImplementationPatch`、`TestReport` 等捕获型合同仍要求其对应的安全捕获后端；通用内置起点统一
使用 `TextResult`，不会再让平台能力改变 Agent 间通信协议。
常见错误的处理方式是：unpublished 先 `publish`，stale revision 重新读取 Head/source revision，disabled
显式 enable，revoked 发布新版本替代，blocked 先对账再 resume/abandon。

## 状态与恢复边界

当前持久化内容包括工作空间身份、Profile、全局/工作空间 Preferences、Provider 配置、凭据引用，
以及数据根 Operational Store 中的 Session / TaskRun 状态、版本化 TaskOutcome、Turn / ConversationLog 与受控 Artifact
和 ToolExecution 恢复证据。最终回答只把 TaskRun 置为待接受；普通追问继续同一 TaskRun，只有显式
`/accept`、`/task new`、取消、放弃或恢复命令才改变任务语义。
普通 Turn、新 TaskRun 或 TaskRun resume 要求 Session 同时为 `active` 和 health `ok`。
归档前必须先明确关闭 current TaskRun；归档不会自动 cancel/abandon 或改写任务历史。
Session 的 `updated_at` 会随任务、对话、lifecycle、health 和 recovery 变化以整秒精度严格递增，
可用作乐观并发 token。
可用同一 `session_id` 在重启后恢复合法对话；确定性上下文 checkpoint 会保留完整最近 Turn、source provenance
和 checkpoint 后的新输入，conversation Fork 通过 parent/cut lineage 创建隔离子 Session。
Fork child 创建时不继承父 TaskRun，持久化后可创建并拥有自己的 TaskRun、Turn 和本地记录；
父历史不变。工具恢复仍遵循持久化证据分类。
工作空间/代码回退不属于 Stage 4，任务后可审查的长期偏好与项目知识学习留到
Stage 5。当前可通过 `morrow memory selection list` / `show <selection-id>` 查看一次 AgentRun
冻结的 Memory Selection；输出只包含引用、原因、预算和 digest，不默认展开 Knowledge 内容。

Stage 5 的 Learning 默认是 `review-only`：每个合格的已完成普通 Turn 会在同一事务写入
Preference Review job 与当前用户 Evidence，提交后只唤醒进程内 Worker；Provider 超时/重试不阻塞
前台 Turn。Preference proposals 通过 `preferences inbox` 审查，接受后由同 scope 原子 Writer 写 YAML；
明确管理使用 `/preferences` 或受审批的 `manage_preferences`。`/accept` 仍只接受 Task 结果。
普通用户 Turn 统一作为中性的 behavioral Evidence 交给 Reviewer，不按“以后”“这次”“例如”或
否定、引用、假设等自然语言关键词预先分类复杂语义。
既有 Profile 候选会经配置 Promotion Saga 更新 YAML，
Project Knowledge 进入 SQLite 版本化记录；Skill、Workflow 和 Orchestration 候选只保留为候选，
不会创建文件、工具、权限或运行时规则。`explicit-auto` 被拒绝，`off` 可关闭任务后 Review。

Headless `learning accept/edit/reject`、全新 Project Knowledge 首次 Promotion、新进程、重启、Doctor
与 backup verify 均由离线测试覆盖。真实 Provider 质量评估需要用户显式授权和兼容凭据。

`state doctor` 对当前 Preference Review/Evidence/Proposal/Writer、既有 Learning Review、Candidate、决策、Promotion、Knowledge、Memory Selection
和 AgentRun 冻结引用执行只读检查；它也会检查 Skill 包、Draft、Usage、Binding、Selection 和 MCP 引用。
`state backup` 只生成当前完整 bundle：隔离的 Operational SQLite、Artifact、当前 Preference/Profile、
脱敏后的 Provider/Model 与扩展 YAML，以及被引用的 managed Skill 版本。`state verify-backup` 只校验该格式；
恢复只允许写入新的隔离目标，不包含 CredentialStore、Keychain 或凭据字节。
禁止原始 Reviewer 输出、Provider reasoning、密钥和受保护内容进入事件、日志、候选、YAML 或模型上下文。
当前确定性离线安全门禁与正式 CLI 链路可行性测试见
[`docs/acceptance/current-chain-feasibility.md`](docs/acceptance/current-chain-feasibility.md)；真实 Provider 质量评估仍需显式授权和兼容凭据，未授权时不运行。

Stage 6 当前已提供受治理的 Skill 生命周期、生成 Draft 审查、按 AgentRun 冻结的 Selection/Context、Usage、受限脚本 Artifact，
Provider/Model 控制面，以及离线 Fake stdio MCP 的 Catalog、审批、结果归一化、崩溃隔离和恢复证据。可从
`tests/acceptance/test_stage6_integrated.py` 与 `docs/acceptance/stage6-skills-and-extensions.md` 查看隔离验收入口；它们不读取真实用户状态、
凭据或外部 MCP。

Artifact cleanup 默认只 dry-run，并以同一 data root 内所有 workspace 的 metadata 与
reference 为权威。`--apply` 不销毁字节：它只会把经目录、类型、权限、单链接和事务内
全局权威复查的非托管候选原子移入随机私有 quarantine。成功报告是
`removed=0` 与 `quarantined=1`；原字节仍保留，无法证明安全时 fail closed。

状态写入经过校验、revision 检查、同目录临时文件、文件/目录 `fsync` 和原子替换，并保留 `.bak`。
Profile 损坏或版本较新时，工作空间持久状态进入只读模式；workspace Preferences 损坏时只隔离该层。

当前核心本地工具只通过冻结工作空间服务读取、搜索和修改项目文件；Git 状态/Diff 与项目校验统一通过经过策略检查的 `bash` 执行；
它们本身不提供网络能力。受治理的 MCP 只能在审核证据与 AgentRun、Server、配置、Catalog 及工具完全匹配时，
将 network、loopback、credential 或 external-effect 风险提升为逐调用审批；未提供或未审批的能力继续拒绝。配置工具只通过应用服务更新既有的 Profile/Preferences 状态。阶段 3 已交付
三轴权限模型、工作空间能力冻结、能力策略、动态系统边界、通用本地审批端口、终端审批 UI，以及有界目录/文件读取、
搜索、SHA-256 冲突安全编辑、原子文件创建、当前运行 ChangeSet/Diff、有界 Host 命令和当前 macOS 的原生
Auto Sandboxed 快照执行；支持后端时还提供始终需审批的当前运行沙箱变更推广。Stage 3 的当前 macOS 验收已完成，
Linux 原生运行仍在真实 runner 验证前保持 unsupported。每次完成工具轮次后，终端可显示一行由本地 ToolFacts/metrics
生成的有界事实摘要；该摘要不进入 Provider、公开事件或持久状态。
`auto-sandboxed` 在 native backend 不可用或无法证明时会 fail closed。持久化聊天历史、Artifact、恢复、
checkpoint、fork、按 AgentRun 冻结的 CapabilityGrant 与 Full Access Manual 属于 Stage 4；Full Access Auto
和 raw auto 仍不支持。可审查学习从 Stage 5 开始；Skills/MCP 与 Provider/Model 扩展已在 Stage 6 交付，
静态串行 Multi-Agent Workflow 已由 Stage 7 提供；Stage 8 已提供 GUI、运行控制、Draft 编辑和
建议式任务特化 GraphPlanner。全局自动 Replan、并发与后台任务仍属于后续阶段。
