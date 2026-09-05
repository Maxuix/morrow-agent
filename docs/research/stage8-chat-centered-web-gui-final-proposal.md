# Morrow Web GUI：以 Chat 为中心的完整改造方案

> 日期：2026-09-05
> 基线：本次检查的代码提交 dde2ada；Stage 8 已完成子计划作为已有能力保留。
> 状态：用户已确认；已转为[完整执行 PLAN](../../.agent/PLAN.md)，8 个子计划尚未开始。本文作为决策依据，实施以当前 PLAN 为准；Stage 9 未激活。
> 目标：用户启动 Web GUI 后，能完全通过浏览器配置并使用当前 CLI 的产品能力。

## 1. 最终产品决定

**Chat 是默认首页、主要工作区和任务入口。** 工作区、Session、消息输入、模型、思考程度、
附件与审批模式组成主要操作链；Workflow、Artifacts、上下文和设置围绕当前对话展开。

本次交付包含三个部分，缺一不可：

1. 把 CLI 已有的对话、工具、会话生命周期、运行控制、恢复和管理能力接入 GUI。
2. 补齐浏览器独有或当前 Core 缺失的能力：多工作区入口、文件上传、多模态输入、
   思考参数、会话级设置、会话命名与草稿、可靠的消息传输。
3. 建立 CLI/GUI 逐项功能映射和真实用户旅程验收；不能再用“观察器已经完成”
   代替“个人 Agent 工作台已经完整可用”。

最终验收必须覆盖从首次配置到任务完成、追问和重新打开历史的全过程。
实施中可以分批交付，但仅能发送纯文本的版本是中间里程碑。

布局参考 Codex 的项目/会话组织和 Claude Code 的提示区操作：官方界面资料包含项目、
会话和附件入口；Claude Code 文档将项目目录、模型和权限模式放在任务输入附近，
并支持文件引用和附件。[Codex 界面资料](https://learn.chatgpt.com/docs/features)、
[Claude Code Desktop](https://code.claude.com/docs/en/desktop)。
本方案中的尺寸、功能范围和运行语义是 Morrow 的设计决定，不代表对这些产品的逐项复制。

## 2. 代码基线与实际缺口

| 能力 | 当前事实 | 本次改造 |
|---|---|---|
| 主工作区 | TaskWorkspace 只有 Task/Artifacts，没有输入区和消息列表 | ChatWorkspace 成为默认中心 |
| 普通对话 | SessionOrchestrator 已有流式分发；普通聊天最终进入 AgentLoop.run_task | 提取可供 GUI 调用的交互服务，复用原循环 |
| 历史与恢复 | ConversationLog、持久化记录、SessionRestore、Recovery 已有 | 提供面向用户的历史投影与恢复入口 |
| 创建 Session | Application API 和 HTTP POST /v1/sessions 已有；前端缺少入口 | 补齐浏览器创建、命名、切换、继续对话 |
| 运行中输入 | CLI 已有安全点 steering、完成后 follow-up 及持久化队列 | 补齐 API、客户端幂等键、队列状态和控件 |
| 工作区 | 已有目录识别、注册和 relink；ServerContext 绑定单个 workspace_id | 增加创建目录/打开目录 UI、多工作区路由和运行时管理 |
| 模型 | 已有 Provider/Model 管理、能力目录和运行快照 | 增加会话级选择、GUI 配置和请求级冻结 |
| 思考程度 | 当前 ModelProvider 调用没有完整的可选思考参数链路 | 增加能力声明、生成参数、Adapter 映射及快照 |
| 附件 | UserMessage.content 为字符串；声明 input_types 不等于附件可用 | 增加附件存储、引用、输入投影和实际多模态序列化 |
| 自动审批 | 已有 manual、auto-safe、auto-sandboxed、full-access-manual | 以既有权限语义提供选择器和授权管理 |
| 实时状态 | HTTP 查询及 WebSocket 事件流已有；ApplicationEvent 是有界事实 | 保留事实流，另接可见回复流，不把聊天正文塞进审计事件 |
| 设置 | GUI 已有部分 Context/Learning/Skill/Workflow 管理 | 补齐 Provider、MCP、完整生命周期、状态维护等剩余入口 |

主要依据：
[TaskWorkspace](../../gui/src/views/TaskWorkspace.tsx)、
[SessionOrchestrator](../../src/morrow/application/orchestrator.py)、
[HTTP 路由](../../src/morrow/server/app.py)、
[OperationalApplicationService](../../src/morrow/application/api.py)、
[运行中输入](../../src/morrow/application/runtime_control.py)、
[消息模型](../../src/morrow/core/models.py)、
[权限预设](../../src/morrow/core/capabilities.py)、
[Server composition](../../src/morrow/server/composition.py)。

## 3. 默认布局与导航

保留现有 Warm Paper tokens、字体和双主题，调整信息层级。

| 区域 | 桌面布局 | 主要内容 |
|---|---|---|
| 左侧导航 | 默认 256px，可收起 | 工作区选择/新建、当前工作区 Session、新对话、搜索、运行/未读标记；底部设置入口 |
| 中间 Chat | 占据全部剩余空间；正文最大宽度约 860px | 会话标题、连续消息、进度、工具摘要、审批、结果；底部固定输入区 |
| 右侧检查面板 | 默认关闭，打开后约 360–440px，可调整 | 文件与 Diff、Artifacts、Workflow、当前上下文、任务详情 |
| 输入区 | 与正文对齐，随会话固定 | 附件、@ 文件、/ 命令、模型、思考程度、审批模式、发送/停止 |
| 管理界面 | 从左下角进入独立设置视图 | 模型服务、权限、MCP、Skills、偏好、学习、工作流管理、状态维护 |

交互规则：

- 打开 GUI 直接进入最近 Session；首次使用显示新对话页和必要的配置入口。
- Chat 不是“任务/Artifacts”旁边的第三个标签，也不以选中 TaskRun 为前提。
- 选择 Session 即可读历史并输入；TaskRun 作为会话内的任务分段和详情存在。
- 展开右侧面板时保留消息、输入草稿和滚动位置；关闭后 Chat 自动扩展。
- Workflow 图只在用户查看或编辑编排时展开；普通对话显示紧凑运行摘要。
- 设置和工作流全屏编辑可暂时使用中心区，返回时恢复原会话和草稿。
- 宽度不足时右侧改为抽屉，再收起左侧；不得挤成三个不可读的窄栏。
- 关键状态用文字与图标共同表达；全键盘可达，焦点返回来源控件。
- 手机窄屏可读、可发消息、可审批；复杂图编辑以全屏视图提供。

前端主入口围绕 workspace_id/session_id 路由，task_run_id/workflow_run_id 是可选详情。
所有草稿、面板状态和查询缓存使用工作区及 Session 复合键，避免切换串数据。

## 4. 核心用户流程

### 4.1 创建和打开工作区

“新建工作区”提供两种明确入口：

1. **打开已有文件夹**：浏览或输入 Core 所在机器的目录，识别 Git 根、已有工作区、
   路径是否失效、是否只读，再复用已有身份或注册。
2. **创建新文件夹**：选择父目录、输入名称、创建文件夹并注册；创建后直接进入新 Session。
   “初始化 Git”作为独立可选操作，不隐式创建仓库或覆盖目录。

工作区菜单支持重命名、最近访问、重新关联路径、从列表移除；移除工作区入口不删除项目文件。
初次打开项目时可查看/补充 Profile 和项目约定，并立即开始聊天。

Web 目录选择器由 Core 返回允许浏览的目录条目；路径规范化、符号链接与已有身份去重均由
后端完成。浏览器文件选择器只提供被选择文件的数据，不能把浏览器侧路径当成服务端工作目录。
拖入目录属于上传文件上下文，不等同于授权 Agent 操作那个本地目录。

注册工作区是用户在管理界面的显式动作；模型、网页和附件内容不能代替这个动作扩大工作区。
用户选择目录后，后端按该工作区的权限和路径边界执行 Agent 工作。

### 4.2 创建、打开与管理 Session

- 新对话立即创建可寻址的 Session，输入框获得焦点；不要求先填写 Task ID。
- 默认标题取首条用户消息的安全、有限长度摘要，不额外调用模型；支持手动重命名。
- 支持历史分页、搜索、置顶、归档、取消归档，以及从可恢复的 checkpoint 分叉。
- 归档、取消归档、重命名和置顶各有明确语义；活动任务归档前先处理当前运行。
- 分叉不修改原消息；必须保留 checkpoint、来源 Session 与附件引用的可恢复关系。
- 编辑已发送消息采用“从这里分叉后发送”，不原地改写历史和旧工具结果。
- 每个 Session 保留独立草稿、附件草稿、模型/思考/权限选择和未读状态。
- 新 Session 不继承旧 Session 的对话、任务专属授权或未完成工具周期。
- Session 可包含多个 TaskRun；自然追问继续使用现有 TaskService 语义。
  显式“新任务”在同一 Session 中创建新的任务段，不等于新建 Session。

历史 Session 的生命周期、健康状态和已冻结运行快照继续来自原权威存储；
新增标题/置顶等展示元数据不复制这些状态字段。

### 4.3 发送消息和运行中交互

空闲时 Enter 发送，Shift+Enter 换行；中文输入法组词期间 Enter 不触发发送。
附件未处理好时保留草稿并显示具体原因。输入区支持多行、粘贴和拖放。

运行期间输入框继续可用：

| 操作 | 产品语义 | 核心行为 |
|---|---|---|
| 补充指令 | 当前任务下一安全点采纳 | 使用既有 steering；不在工具副作用中途拼接消息 |
| 完成后执行 | 当前工作正常结束后继续 | 使用既有 follow-up 队列，明确展示排队位置和状态 |
| 停止 | 用户要求停止当前执行 | 持久化用户停止意图，调用现有取消与工具收束路径 |
| 取消排队 | 撤回尚未被消费的输入 | 只取消待处理提交，不删除已写入 ConversationLog 的消息 |
| 继续/恢复 | 继续可恢复的运行 | 先检查 Recovery，再走既有恢复入口 |
| 重新生成/重试 | 启动有明确来源的新尝试 | 使用已定义的 Task/Run 重试语义，不覆盖旧回复或重放未知副作用 |

队列增加浏览器提供的 client_message_id 和可查询回执。重复点击、断线重试、多标签提交
均不能重复计费执行或追加两条相同消息。运行中消息、附件和参数按提交时用户选择绑定。

普通聊天回复逐段显示，支持 Markdown、代码块、复制与可点击的文件/产物引用。
工具活动采用可折叠卡片，显示名称、状态、耗时和允许公开的摘要；代码变更打开 Diff 面板。
工具失败后如果 Agent 正在自我修正，界面不能提前把整个任务标为失败。

### 4.4 模型、思考程度和配置

输入区显示 Provider/Model 选择器及思考程度；设置页提供完整 Provider/Model 管理。

- 模型列表来自 Core Catalog，显示配置是否可用、工具/图像/文档能力和支持的思考选项。
- 思考程度由 Adapter 与精确 Model 的有效能力共同决定；显示“默认”和真实支持的档位。
  不把 low/medium/high 等固定集合强套到所有服务，也不把不支持的参数静默删除。
- 思考程度表示生成参数；界面不展示或保存 Provider 的隐藏 reasoning。
- Provider 设置覆盖预设、自定义服务、地址、Adapter、凭据设置/更换、模型发现、
  手动模型映射、连接测试与移除；仅展示凭据是否配置。
- 目录读取、模型选择和设置页初始化不触发模型测试；测试/发现由用户显式点击。
- 默认作用域为当前 Session；“设为工作区默认”“设为全局默认”是独立动作。
- 设置优先级：本次提交显式值 → Session → Workspace → Global → Adapter 默认值。
- 新 AgentRun 接纳前完成模型/参数/权限解析，并冻结快照；历史始终显示当时实际配置。
- 运行期间选择新模型或权限时显示“下次运行生效”。需要立即改变时使用停止/安全收束后
  的新 Run，不修改正在执行的冻结快照。
- 显式 Workflow 节点自己的模型保持节点定义；聊天选择器控制普通聊天及明确标注的默认值，
  不在后台批量覆盖已冻结图。

当前只有生成能力元数据和文本 ModelProvider 接口；本次必须补齐生成参数的端到端传递、
恢复快照和两个以上不同能力配置的离线 Adapter 契约测试，才能宣称“支持思考程度”。

### 4.5 文件与附件

提供三种入口：

1. “+”选择文件，以及文件拖放。
2. 粘贴图片或文件。
3. @ 搜索当前工作区文件，必要时选择目录作为有界文件集合。

本次基础支持：文本/源码/Markdown、图片、PDF。其他格式显示真实支持状态，
不得仅上传成功就标记“已被模型读取”。Office、音频、视频不是既有 CLI 能力；
其解码不作为本次“文件附件”的隐含承诺。

附件处理采用“上传或引用 → 校验 → 预览 → ready → 随消息提交”的状态机。
上传失败可重试，发送前可移除；显示文件名、类型、大小、读取方式及截断/省略提示。
先上传后发消息，不在一个长请求中同时等待上传和模型生成。

| 类型 | 给用户的体验 | Core 输入方式 |
|---|---|---|
| 文本/源码 | 片段预览，可定位文件/行 | 有界文本及来源引用，计入真实上下文预算 |
| 工作区文件 | @ 自动补全与引用卡片 | 在权限范围内读取，提交时冻结内容摘要与版本 |
| 图片 | 缩略图、移除、预览 | 经过校验的图像内容段，Adapter 必须真正支持图像 |
| PDF | 页数、解析结果、预览 | 有界文本提取；扫描页经支持的图像输入提供，均注明读取方式 |

模型不支持附件类型时，发送前提供切换模型或移除附件的具体操作；能够等价转换为文本时
显示转换结果，不静默丢弃图片、扫描页或长文件尾部。

原始文件和解析产物进入现有 Artifact/blob 存储体系；新增 Attachment 元数据负责上传状态、
来源、digest、引用与保留关系。项目文件不会因为上传而被写入项目目录。
ConversationLog 只通过原 writer 写入消息及不可变附件引用；上下文组装在受控边界解析内容。
旧消息的纯文本表示保持可读；附件引用必须随历史恢复、分叉、备份和清理一起维护。

上传体积、单次文件数、解码像素/页数、解析耗时和上下文占用分别受可配置限制，
这些是输入处理限制，不作为猜测的 Agent 请求总数上限。服务端下发实际限制和错误原因。
从模型输入到清理引用，都要按 workspace/session 校验所有权。

### 4.6 自动审批与工具授权

输入区始终显示有效审批模式，首次使用沿用现有 bundled 默认值。

| 显示名称 | 现有预设 | 行为 |
|---|---|---|
| 手动审批 | manual | 保留现有策略判定，需批准的操作展示审批卡 |
| 安全操作自动 | auto-safe | 现有规则认定安全的操作自动处理，其他操作按现有 ask/deny |
| 沙箱内自动 | auto-sandboxed | 在可用原生沙箱内执行允许的自动操作；不可用时明确禁用 |
| 完整访问·手动审批 | full-access-manual | 使用现有完整访问范围和手动审批语义 |

“自动审批”通过 Core 权限服务实现；前端不能自动点击所有待审批请求，也不能以完整访问
代替免审批。工具审批、任务编排自动运行、低风险 Replan 自动接受是三个独立设置。

审批卡在对应对话位置展示：动作、允许公开的预览、影响范围、原因与可用授权范围。
支持已有的允许一次、允许会话范围、拒绝等决定；所有范围由服务端实际策略提供，
“允许会话”只能授权可表达的具体范围，不能变成无限制通行证。
权限页可查看实际授权、作用域、运行快照和撤销入口。

多个标签页共享同一审批事实；决定提交后其他界面同步失效，重复决定不重复执行工具。
断线、未知状态与权限变更不自动推断为批准。

### 4.7 长任务、Workflow 与完成结果

输入区使用独立的执行方式入口：普通 Agent、编排建议、指定 Workflow。
普通 Agent 默认沿用当前 Direct 对话路径；“编排建议”生成可查看、编辑并提交的 Draft。
既有经过证据推广的自动执行/低风险 Replan 策略继续按原规则运行。

Workflow 的任务请求、计划、运行进度和 TaskOutcome 在中心时间线显示为明确标注的任务卡。
叶子 Session 的完整消息不并入用户 Session；最终产物作为结果卡引用，不由 GUI 冒充 assistant
追加到 ConversationLog。若需要文字解释结果，通过正常用户追问和 AgentLoop 生成。

右侧面板承载节点图、Agent、模型、工具、输入输出、Pause/Resume/Cancel/Rerun 和 future-only
编辑。审批和最终结果仍在中心可见，不能要求用户一直盯着节点图。
复杂编辑可全屏展开，完成后回到原 Chat。

完成时展示回复、实际变更、验证事实、Artifacts、未解决项以及现有 TaskOutcome 接受/修正动作。
“本轮已回复”和“任务已验收”分开显示；普通问答不强制弹出验收对话框。

## 5. CLI 完整迁移范围

“完整”按产品操作覆盖，而非把终端文本原封不动搬到网页。每个公开命令必须有 GUI 入口、
相同 Application Service、可理解的结果和验证用例。业务规则不在前端复制。

| CLI 能力组 | 必须覆盖的操作 | GUI 位置 |
|---|---|---|
| 普通 REPL / run | 提问、流式回复、工具执行、停止、持续对话 | 中心 Chat |
| 运行中输入 | steering、follow-up、排队状态 | 输入区及队列列表 |
| Session | list/create/status/resume/archive/fork | 左侧导航、Session 菜单 |
| Task | list/show/new/accept/cancel/resume | 会话内任务卡及详情 |
| Workspace | 识别/注册、relink、Profile 查看/编辑/重置 | 工作区切换器与设置 |
| Provider | list/show/presets/add/remove/test/configure | 设置 → 模型服务 |
| Model | list/show/add/sync/use/remove/current | 输入区选择器与模型设置 |
| Approval / Grant | 查询、决定、授权创建/查看/撤销 | 中心审批卡及权限页 |
| Context | 实际生效偏好、知识、项目约定、上下文压缩 | 会话状态及 Context Drawer |
| Preferences | 查询、原子规则增改删/启停、revision、批量 write | 设置 → 偏好 |
| Preference Inbox | jobs/job/status/retry/run-pending、候选查看/预览/审核/批量接受/拒绝 | 设置 → 学习与偏好收件箱 |
| Learning | 模式、review/request/retry、候选、审查、promotion、accept/edit/reject/undo | 设置 → 学习 |
| Memory | 知识列表/详情、启停/争议/删除、selection 查询 | 设置 → 记忆及上下文详情 |
| Skill | 安装/校验、启停、pin/rollback/remove、usage、完整 Draft 生命周期 | 设置 → Skills |
| MCP | add/list/show/inspect/status/enable/disable/remove/refresh | 设置 → MCP |
| Agent Definition | list/show/create/edit/validate/publish/enable/disable/revoke | 设置 → Agents |
| Workflow Definition | list/show/create/edit/clone/validate/publish/enable/disable/revoke | 设置 → Workflows |
| Workflow 执行 | plan/run/runs/status/pause/resume/cancel/rerun/abandon/node show | Chat 任务卡与右侧面板 |
| Patch / Replan / Policy | validate/save/apply、list/decide/process、show/set | Workflow 编辑与编排设置 |
| Evaluation / Feedback | 已有评估、反馈、策略候选和证据推广操作 | 任务结果与评估页 |
| Artifact / AgentRun | list/show、pin/release、运行观测 | 附件/产物面板与运行详情 |
| Recovery | 报告、未知结果处理、显式 resolve | 会话恢复卡与诊断页 |
| State | doctor/events/backup/verify-backup/cleanup | 设置 → 状态维护 |
| manage | 所有注册管理查询和命令 | 对应功能页，高级页可导入结构化请求 |
| 斜杠命令 | /new、/status、/workspace、/compact、/task、/accept、/grant、/recovery、/learn、/memory、/preferences | 输入区命令菜单及同名入口 |

额外补齐 Session 重命名、置顶、取消归档、搜索、附件和会话级参数；明确标记为新增能力。
/model、/thinking、/permissions 等可作为新增 GUI 快捷命令，但不得声称 CLI 已有这些命令。
可继续提供旧命令兼容提示；未知斜杠命令返回明确错误，不转成 Shell 执行。

进程与输出格式适配规则：

- /exit 在浏览器中离开当前交互或关闭连接，不关闭整个 Core。
- serve/gui 的启动参数由启动器保留；GUI 中提供状态和连接信息。
- headless JSONL、TTY 和 stdout 格式属于 CLI 载体；其任务行为在 Chat 等价提供。
- state 维护操作沿用维护锁/备份协议；需静止状态时显示实际阻塞的运行并收束，
  不能在后台绕过锁。备份文件提供下载/校验入口。
- 不用嵌入 Shell、要求用户输入 CLI 命令或只提供通用 JSON 编辑器充当功能迁移。

本次已从当前 Typer 注册生成 [157 项命令/启动入口的覆盖基线](stage8-chat-web-gui-cli-parity-matrix.md)，
并列出 REPL 分发入口。实施第一个子计划须核对是否有新增命令，进一步展开 Management 注册项，
逐条补齐“服务 → GUI → API → 验收用例”。上述分组表不能替代逐命令清单。

## 6. Core 与接口改造

### 6.1 唯一交互执行路径

增加面向客户端的 InteractionService，统一接收结构化提交：

    workspace_id + session_id
    client_message_id + command_id
    text + attachment_refs
    execution_settings_revision
    intent: send / steer / follow_up / explicit_workflow

InteractionService 协调接纳、排队和运行选择；复用 SessionOrchestrator、SessionPersistence、
TurnSubmissionCoordinator、RuntimeControlService 与 AgentRunPreparation。
AgentLoop.run_task 继续负责叶子模型/工具循环和经 Session-owned ConversationLog 的消息写入。

普通聊天不得同时调用一次原始 submit_turn 再启动一条自行创建新消息的 Orchestrator 路径。
接纳、client_message_id、run ID 和 Log append 必须贯穿同一生命周期。
当前首次 stream 没有接收外部消息幂等键的入口，应在共享应用层补齐，不能只改 HTTP handler。

CLI 和 GUI 是该应用服务的两个输入/输出适配器。能共享的配置预览、确认结果和命令结构
从 Terminal 中提取为类型化交互结果；Rich、PromptSession 和 HTTP 不进入业务服务。

### 6.2 多工作区与 Session 运行时

GUI 的启动和管理服务必须与 Provider/活动模型及具体 Session 解耦。无模型配置、无已注册
工作区时也能打开创建/配置界面；只有用户发送消息才按需组装执行运行时。无效模型配置
应在可修复的设置状态中呈现，不能令整个 GUI 无法启动。

保留一个 Core Host 所有者线程/事件循环，增加：

| 对象 | 职责 |
|---|---|
| WorkspaceRuntimeRegistry | 按工作区持有路径身份、服务与 writer lock；显式加载和卸载 |
| SessionRuntimeManager | 按工作区/Session 恢复独立 log、配置与运行时；防止共享可变 Session |
| SessionRunSupervisor | 每个 Session 至多一个活动 driver，负责停止、恢复和退出收束 |
| WorkspaceExecutionCoordinator | 同一工作区的顶层执行互斥和排队，避免多个 Session 同时改同一目录 |
| 原 RunSupervisor | 继续管理 Workflow driver，与顶层执行归属协调 |

数据库连接、事务和持久化写入仍归 Core Host；禁止把现有 build_session_application 原样
复制为任意多个无生命周期约束的实例。拆分进程/工作区共享服务与 Session 独占服务。
每个已打开工作区按现有规则持有 WorkspaceWriterLock。

同一工作区的多个 Session 可同时打开、读历史、编辑草稿和提交任务；顶层工作明确排队，
不在共享目录上假装实现了隔离并行。不同工作区可以并行，但必须证明工具使用显式 cwd/env、
路径与权限绑定，且不会污染其他工作区；任何共享的宿主执行资源仍串行保护。
已有 Workflow 内只读并行保留其验证过的语义。此处不引入自动 Git worktree 产品能力。

CLI 连接已运行 Core 时采用客户端接入模式，共享交互服务和 writer；保留无 Server 的独立 CLI。
遇到其他独立 CLI 已占锁时明确显示使用者/连接方式，不绕过锁创建第二个 owner。
CLI 接入凭据只经私有进程连接设施传递，不放入 YAML、日志或通用状态查询。

网页刷新、切换会话和关闭标签只影响订阅，Core 进程继续驱动已接受任务。
Core 退出走非用户取消的收束与持久恢复路径；后台常驻、系统重启自启与定时调度仍属 Stage 9。

### 6.3 历史查询与实时流

采用两种投影，并明确区分权威性：

1. **历史投影**：从 ConversationLog/Turn、TaskOutcome、受控工具事实和 Artifact 引用构建
   用户可读时间线；通过 Query API 分页读取。前端无 SQLite/YAML 访问权。
2. **运行中回复流**：复用已存在的 WebSocket 传输设施，增加经过筛选的 text delta、
   message commit、运行状态帧；原 ApplicationEvent 仍只存有界状态事实和引用。

每个时间线条目有稳定 ID、来源类型、session_id、turn_id、必要的 task/run 关联和顺序。
实时帧带 stream_epoch/sequence、目标消息 ID 与运行标识，不能仅凭字符串拼接去重。
订阅建立时使用一致的快照水位，再续接帧；断线重连先核对服务端状态与消息提交位置。
工作区过滤后的事件游标不一定连续，客户端不得把其他工作区的游标判成事件丢失。

同一 Core 存活期间，用有界内存缓冲和当前可见草稿快照恢复未提交回复；
完成后由持久化消息替换草稿。慢客户端溢出时明确要求 resync，不阻塞 Agent。
Core 崩溃后恢复已提交历史与 Recovery 状态；尚未提交的流式尾部可能丢失，
不能把残片恢复为成功回复，也不能自动重发已经执行过的用户提交。
如果以后要求逐 token 跨崩溃恢复，应另定义持久草稿协议，不把它混入本次审计事件。

模型生成等待、上传解码和长任务驱动不能占住串行命令队列；命令完成接纳后返回回执，
运行由 supervisor 驱动。停止和审批必须在模型等待期间仍可处理。

### 6.4 API 边界

新增工作区显式作用域；旧 /v1 路由继续映射启动时的默认工作区以保持兼容，
新 GUI 只调用显式工作区路由。身份、权限、游标与缓存不得依赖浏览器“当前选中项”。

下表是接口职责和建议路径，精确 DTO 在子计划 1 冻结：

| 路径/接口族 | 责任 |
|---|---|
| /v1/workspaces | 工作区目录、注册、创建、重命名、relink |
| /v1/workspace-locations | 受控目录浏览、路径校验与创建预览 |
| /v1/workspaces/{wid}/sessions | 创建/列举 Session，复用已有 create 语义 |
| /v1/workspaces/{wid}/sessions/{sid}/history | 有界、可分页的用户时间线 |
| /v1/workspaces/{wid}/sessions/{sid}/submissions | send/steer/follow-up/显式工作流提交及回执 |
| /v1/workspaces/{wid}/sessions/{sid}/stream | 可见消息实时投影与重连 |
| /v1/workspaces/{wid}/sessions/{sid}/execution | 运行查询、停止/恢复、排队撤回 |
| /v1/workspaces/{wid}/sessions/{sid}/settings | 会话模型、思考和审批设置及版本 |
| /v1/workspaces/{wid}/sessions/{sid}/actions | 命名/置顶/归档/取消归档/fork/compact 等类型化动作 |
| /v1/workspaces/{wid}/attachments | 上传/引用/状态/预览/释放草稿，受控内容读取 |
| /v1/workspaces/{wid}/files | @ 搜索、文件预览与受限目录枚举 |
| /v1/providers、/v1/models | 既有控制服务的管理接口；凭据仅专用写入 |
| 工作区作用域下的既有管理/运行 API | MCP、Skill、Workflow、Recovery、State 等功能等价 |
| /v1/interaction-capabilities | 当前可用命令、参数 schema、权限模式和功能能力 |

命令使用稳定幂等键；同键同载荷返回原结果，同键不同载荷返回冲突。
提交摘要覆盖文本、附件 digest/ID、意图与冻结设置，不能只对文本去重。
变更继续执行 OCC；重连不会替用户接受 stale revision。排队满载返回明确背压。

保留现有 loopback、Origin/Host 校验、鉴权和静态 CSP。WebSocket 开通/续接具有同等鉴权，
关闭或过期连接不扩权。Provider Key 只进入专门的写入接口和 CredentialStore，不进入
消息、ApplicationEvent、通用 command receipt、浏览器持久存储或错误输出。

### 6.5 数据与显示安全

新增或扩展的数据以最少权威对象表达：

- Session 展示元数据：标题、置顶；原 lifecycle/health 继续复用。
- Session/Workspace 执行设置及 revision；有效值进入 AgentRunSnapshot。
- 结构化生成参数及精确模型能力；Adapter 只接受经声明和校验的选项。
- Attachment 元数据、不可变 blob 与消息关联；与 Artifact 保留/清理/备份统一。
- 可幂等的待处理提交/控制队列；被消费后关联唯一 Turn，不作为第二聊天历史。

UserMessage 保持旧纯文本可读，增加版本化附件引用；附件独立时允许空正文的条件必须
在模型验证、Log grammar、持久化和 Adapter 一起修改。图片字节不写入 YAML 或状态事件。
参数和附件参与上下文预算、digest、恢复校验及快照兼容；旧快照字段缺失保留旧行为。

历史和回复 API 只公开用户/助手可见内容及经过筛选的活动事实；不发送系统提示、
隐藏 reasoning、SDK 对象、完整敏感工具参数/结果或 traceback。
文件和 Diff 正文通过各自权限检查后的内容接口获取，不通过展开原始工具日志获得。
Markdown 不执行 HTML；链接、图片、SVG 和预览均遵守现有 CSP 与内容类型限制，
远程图片不因消息渲染而自动请求。

## 7. 前端改造与代码归属

沿用 React/TypeScript/Vite、现有 API client 和 tokens；逐步替换中心区，不另建一套前端。

| 模块 | 具体变更 |
|---|---|
| AppShell / SessionNav / TopBar | Chat 默认路由、工作区导航、可折叠右侧和设置入口 |
| 新 ChatWorkspace / Transcript / Composer | 消息、草稿、流式更新、快捷命令、附件与运行中输入 |
| 新 WorkspaceManager / SessionActions | 工作区和 Session 完整生命周期 |
| 新 ModelSelector / ReasoningSelector / PermissionSelector | 输入区设置与生效状态 |
| ApprovalDialog / ApprovalsBar / RunControls | 复用既有命令，增加对话内呈现和普通聊天运行控制 |
| ContextDrawer / SkillManager / LearningManager | 保留能力、接入当前 Session，补齐命令映射 |
| 新 ProviderSettings / McpManager / StateMaintenance | CLI 管理功能的完整表单与结果页 |
| TaskWorkspace / WorkflowPanel / EditorShell / EvaluationPanel | 迁至任务详情、右侧面板或显式管理视图 |
| api / state | 工作区作用域、提交回执、历史分页、实时水位、独立草稿缓存 |
| application / bootstrap / server | 共享交互服务、Session runtime 生命周期和类型化协议 |
| core / adapters / state | 生成参数、附件引用、兼容迁移、Provider 序列化和内容存储 |

滚动规则：用户在底部时跟随输出，主动上翻时保持位置并提示新消息；加载旧消息保持锚点。
长历史采用分页及有界渲染。乐观消息必须显示 pending/failed，不冒充已提交的历史。
上传和订阅取消绑定各自对象；切换 Session 时旧请求返回不能覆盖当前视图。

## 8. 实施顺序与每步出口

建议作为“Stage 8 Chat 工作台补全”的独立主计划实施。每个子计划都可单独验证和恢复，
但全部完成后才能宣称“CLI 完整迁移到 GUI”。

| 顺序 | 子计划 | 交付与验证出口 |
|---|---|---|
| 1 | 交互合同与全量能力映射 | 固定逐命令清单、作用域/DTO、历史投影、设置/附件快照和迁移设计 |
| 2 | Core Chat 执行与流 | 当前工作区的发送、历史、恢复、停止、steering/follow-up；幂等及断线脚本通过 |
| 3 | 中心 Chat 与 Session 入口 | 浏览器创建 Session、连续对话、流式消息、审批、任务结果；第一个完整单工作区用户旅程 |
| 4 | 工作区与 Session 完整管理 | 创建目录、注册/切换、命名/搜索/归档/fork，多工作区隔离与 CLI 接入 |
| 5 | 模型、思考与权限设置 | Provider 全管理、会话参数、能力约束、实际 Adapter 请求和恢复快照；自动审批等价 |
| 6 | 附件与文件上下文 | 上传/粘贴/@、文本/图片/PDF、上下文预算、预览、恢复、清理和备份 |
| 7 | Workflow 融合及 CLI 剩余功能 | 全部管理功能和斜杠命令清单闭环；普通聊天、手工/自动编排语义一致 |
| 8 | 迁移、全场景验收与发布 | 全量离线/GUI 检查、浏览器旅程、旧数据恢复、wheel 安装后可用、完整证据索引 |

执行纪律：

- 先交付真正的聊天主链，再扩展管理深度；不先做几轮设置页面而继续缺少输入区。
- 激活新主计划时将现有 Stage 8 的全部旧子计划归档到专属目录，新序列从 1 开始。
- 每次只激活一个子计划；单独分支、小提交、相关验证、执行状态更新和可恢复合并。
- 子计划 1 的细化不能删除本文已承诺的产品范围；缩减范围必须明确记录并重新决定。
- 不覆盖旧验收结论；新验收补充“观察/控制面完成”之后的完整交互证据。

## 9. 验收标准

### 9.1 必须实测的用户旅程

1. 全新测试数据目录启动 GUI，在浏览器配置测试 Provider、创建工作区和 Session，
   发送首条消息并看到流式回复，全程不需要到 CLI 配置。
2. 打开已有 CLI 工作区和历史 Session，分页读取记录，继续对话；不会创建重复工作区或重复消息。
3. 连续两轮追问保持上下文；新任务和新 Session 的边界与 CLI 一致。
4. 运行中补充指令、排队追问、撤回排队和停止，均有对应的核心状态证据。
5. 刷新、断网重连、重复发送和两个标签页同时提交，不重复执行、不丢已提交消息、
   不把实时草稿当作成功历史；测试浏览器草稿与服务器记录各自的生命周期。
6. 在回复/工具进行中切换工作区和 Session，不串消息、文件、审批、模型或草稿。
7. 修改模型和思考程度，验证实际 SDK 请求；运行中变更显示下次生效，恢复沿用冻结值。
8. 测试各权限预设、一次/有界会话授权、撤销、原生沙箱不可用和重复审批，与 CLI 判定一致。
9. 上传代码、图片、文字 PDF、扫描 PDF；测试 @、多文件、附件独立消息、失败重试、
   不兼容模型、超限和截断。核实模型实际收到可用内容。
10. 停止或 Core 崩溃后重开 Session，对已完成/失败/未知工具副作用采取正确恢复路径，
    不能用重新发送冒充恢复。
11. 从 Chat 生成/选择 Workflow，编辑并运行，观察节点、审批、修改 Future、继续与查看结果，
    然后回到 Chat 追问，不污染用户 Session 的聊天记录。
12. 遍历所有公开 CLI 命令的 GUI 映射；Provider/MCP/Skill/学习/恢复/维护均有实测结果。
13. 使用改造前数据库/YAML/消息/Artifacts 升级，验证恢复、fork、备份/校验/清理。
14. 使用长历史、键盘操作、中文输入法、窄窗口及双主题，验证可读性、焦点与滚动行为。
15. 在 GUI 构建后的 wheel 安装环境启动，验证资源自包含、CSP 和无 Node 终端使用路径。

### 9.2 必须运行的工程检查

每个子计划运行触及模块的定向测试与 Ruff；核心/协议/迁移完成及最终交付运行完整离线门禁。
GUI 子计划运行 typecheck、test 和 production build（含体积预算），最后先构建 GUI 再打包。

    uv run pytest -m 'not live'
    uv run ruff format --check .
    uv run ruff check .
    uv run python -m compileall -q src tests
    uv run morrow --help
    pnpm --dir gui typecheck
    pnpm --dir gui test
    pnpm --dir gui build
    uv build
    git diff --check

浏览器测试使用隔离工作区和 scripted Provider/fake SDK chunks，检查用户看到的消息和底层事实。
竞态用事件/屏障控制，不以固定 sleep 假定成功。Live 测试需要用户明确要求和兼容凭据，
不以本提案或此前离线通过记录冒充真实模型验收。

最终证据包括逐命令覆盖表、上述旅程结果、关键界面截图、API/CLI 状态对照、
迁移与故障结果，以及仍然存在的实际限制。未完成项不能仅靠禁用控件或一句“后续支持”关单。

## 10. 迁移、依赖和范围边界

数据采用增量、可备份的 schema 迁移；具体版本号在实施时取当前最新版本，本文不抢占号码。
保留旧纯文本消息、已冻结模型/权限快照、旧 /v1 默认工作区路由和旧静态资源访问方式。
新客户端先检查服务端能力，版本不兼容时给出升级提示；不能调用失败后假装操作成功。
数据库升级后回退旧程序必须使用匹配的备份，不把降级兼容作为未经验证的承诺。

依赖选择范围仅限缺少的可靠 Markdown 渲染、必要的文件解析/图像处理和测试支持。
复用现有框架、WebSocket、Artifact 和安全设施；新增第三方包按仓库规则在实施时逐项确认，
当前不执行安装。输入/权限/事件契约的扩展必须进入子计划明确变更清单；
不悄悄改 bundled runtime-policy 默认值。

本次是现有本地 Agent 能力的完整 Web 产品化，不包含 Stage 9 定时任务、后台常驻、
多用户服务、云端执行、远程主机管理、桌面安装器或完整 IDE/PTY 产品。
Shell/Git/网络/MCP/Skill 等已实现的 Agent 功能照常接入聊天和设置，不因此延期。

**交付定义：用户能把 Web GUI 作为主要工作入口，独立完成工作区/会话创建、模型与权限选择、
文件输入、Agent 对话与工具执行、持续纠正、结果审阅，以及当前 CLI 的全部产品管理操作。**
