# Stage 8 — Chat 工作台补全实施计划

> 计划 ID：stage8-chat-workbench
> 建立日期：2026-09-05
> 状态：完整计划已建立；实施子计划 0/8 完成，当前无激活实施子计划。
> 下一步：子计划 1「交互合同与能力映射」。
> 当前授权：用户确认最终方案并要求生成完整 PLAN；本轮限计划与文档整理。
> 代码基线：dde2adabb94c1171ba9cde187a5fa9fbbedb6616；不以旧测试结果替代新验证。
> 上一主计划：[Stage 8 自适应编排与 GUI 控制面归档](archive/subplans/stage8-adaptive-orchestration-gui/README.md)。
> 路线依据：[Stage 8](../docs/roadmap/stage-8-adaptive-orchestration-and-gui.md)。
> 已确认方案：[最终改造方案](../docs/research/stage8-chat-centered-web-gui-final-proposal.md)。
> 命令基线：[157 项 CLI 命令/启动入口](../docs/research/stage8-chat-web-gui-cli-parity-matrix.md)。

## 1. 目标与完成定义

将 Web GUI 交付为用户的主要 Agent 工作入口：无需借助 CLI 配置，即可在浏览器完成
工作区创建、Session 管理、模型/思考/权限设置、附件输入、连续对话、工具执行、运行中纠正、
结果审阅，以及当前 CLI 全部产品管理操作。

Chat 是默认首页与中心工作区。左侧管理 Workspace/Session；中间显示消息、工具活动、
审批、任务结果及固定输入区；右侧默认收起，按需打开文件、Diff、Artifacts、Workflow
和上下文。沿用当前 Warm Paper、双主题和 React/TypeScript/Vite 技术栈。

实现范围包含既有 CLI 能力迁移和必要新增 Core 能力。任何单个中间里程碑，尤其“纯文本
Chat 可用”或“观察器能显示状态”，都不能替代最终交付。

## 2. 权威、授权和执行状态

1. 当前用户要求与后续明确纠正。
2. 当前代码及本轮实际执行的验证。
3. 本主计划和唯一激活子计划。
4. Stage 8 路线图。
5. 已确认提案及旧验收：背景和决策依据，不构成第二套运行规范。

当前请求只生成计划，不开始产品实现。后续用户要求“开始/继续执行该计划”时，可在该授权
范围内按顺序连续推进，不要求每个子计划重复确认。只有授权尚未覆盖的实际依赖新增、
bundled 策略默认值修改或公共事件生命周期变更，才按 AGENTS.md 处理；先完成可独立推进
的设计和验证，让需要决定的变更具体可审查。

本计划覆盖已确认的 Stage 8 Chat 补全，不开启 Stage 9。Live/真实网络测试和远程 push
没有获得授权；运行时使用现有网络/MCP 能力的产品接入属于计划范围，测试使用脚本化替身。

状态使用 [ ] 待开始、[>] 进行中、[x] 验证完成、[!] 真实阻塞。TODO 只复制当前激活
子计划的任务；无激活子计划时不得把未来所有任务堆入 TODO。TRACKER 记录当前状态与下一动作，
LOG 只记录决定、验证、失败和阻塞。

## 3. 必须覆盖的产品要求

| ID | 必须交付的行为 | 主要子计划 | 最终旅程 |
|---|---|---|---|
| C01 | 无 Provider/工作区也可启动 GUI 并完成首次配置 | 2、4、5 | A01 |
| C02 | 中心 Chat、持续可见输入区、按需右侧面板 | 3 | A02、A14 |
| C03 | 打开/创建目录、注册、切换、命名、relink、移除入口 | 4 | A01、A06 |
| C04 | Session 新建/历史/搜索/命名/置顶/归档/取消归档/fork | 3、4 | A02、A03、A13 |
| C05 | 流式多轮对话、Markdown、代码、工具与任务结果 | 2、3 | A02、A03 |
| C06 | steering、follow-up、排队撤回、停止、恢复、明确来源的重试 | 2、3 | A04、A10 |
| C07 | 幂等发送、重连、多标签同步、已提交历史不重复 | 2、3、4 | A05、A06 |
| C08 | Provider/Model 完整配置、会话选择和模型有效能力 | 5 | A01、A07 |
| C09 | 真实思考参数、快照冻结、下次生效、不公开隐藏 reasoning | 5 | A07 |
| C10 | 文本/源码/图片/PDF 上传、拖放、粘贴、@ 文件、真实输入 | 6 | A09 |
| C11 | 四种现有权限预设、具体范围授权、审批与撤销 | 3、5 | A08 |
| C12 | 普通聊天、编排建议、指定 Workflow；编辑/运行/结果返回 Chat | 7 | A11 |
| C13 | Preferences/Learning/Memory/Skill/MCP/Agent 完整管理 | 7 | A12 |
| C14 | Task/Artifact/Recovery/State/斜杠命令完整 GUI 等价 | 3、7 | A10、A12 |
| C15 | CLI 接入同一 Core、工作区隔离、无第二 writer | 4 | A05、A06 |
| C16 | 旧数据、附件保留/清理/备份、包分发、键盘和双主题 | 4–8 | A13–A15 |

157 是现有 Typer 命令与启动入口的基线，不包括 REPL 分发子操作和 manage 内部子类型；
子计划 1 须展开后两者。必须逐项区分业务能力、已退役命令提示和仅属于 CLI 载体的启动/输出
参数，所有例外有具体 GUI 等价说明，不能用“高级功能留在 CLI”消除缺口。

## 4. 核心技术决定

### 4.1 单一执行与消息权威

- InteractionService 协调结构化 send/steer/follow-up/explicit_workflow，CLI/GUI 调用同一服务。
- SessionOrchestrator、SessionPersistence、TurnSubmissionCoordinator、RuntimeControlService、
  AgentRunPreparation 保持各自职责；普通聊天继续进入 AgentLoop.run_task。
- Session-owned ConversationLog 是唯一聊天历史 writer；不得由 API、队列、Workflow 或 GUI
  手工拼接第二份聊天历史。
- 首条输入也接收客户端幂等 ID，接纳、实际 Turn、AgentRun 和 Log append 贯穿一次生命周期，
  不先 submit_turn 再通过另一入口重复提交。
- 任务请求/Workflow 结果以有来源的 timeline 卡展示，叶子完整对话不并入用户 Session。

### 4.2 所有者与多工作区

Core Host 的所有者线程/事件循环继续拥有事务和 durable 写入。管理启动不依赖 Provider、
活动模型或 Session；执行运行时按需组成。

WorkspaceRuntimeRegistry 持有工作区身份、服务和 writer lock；SessionRuntimeManager 持有独立
Session/log/设置；SessionRunSupervisor 确保一个 Session 一个 driver。现有 Workflow
RunSupervisor 保留，与顶层执行归属协调。

同一工作区的顶层执行排队，避免共享目录被多个 Session 同时修改；多个 Session 可同时打开、
读取、编辑草稿。不同工作区并行必须有显式 cwd/env/路径与权限隔离的实测证据；全局共享的
宿主资源串行保护。保留既有 Workflow 内的只读并行，不增加自动 worktree 产品。

切换网页或关闭标签只改变订阅；显式 Stop 和 Core 退出分别走用户取消与非用户取消收束。
独立 CLI 保留；CLI 接入已运行 Core 时共享其 writer，绝不绕过 WorkspaceWriterLock。

### 4.3 历史与传输

持久历史投影来自 ConversationLog/Turn、TaskOutcome 与受控工具/Artifact 引用。回复流复用
WebSocket 设施；ApplicationEvent 继续只保存有界状态事实，不塞入聊天正文或 token 记录。

消息有稳定 ID、来源和顺序；实时帧有 stream_epoch、sequence 与目标 run/message ID。
快照水位与续接协议避免漏帧；scope 过滤不等于游标缺口。慢客户端执行有界缓冲/resync，
不阻塞 Agent。Core 存活期间恢复实时草稿；崩溃后恢复已提交历史，未提交流式尾部可能丢失，
但不能被伪装为成功回复或触发用户请求重放。

每次提交冻结意图、文本、附件引用/digest 和设置；同幂等键同载荷返回同一回执，不同载荷
冲突。mutation bus 不等待完整模型生成/文件解码，接纳后由 supervisor 驱动。
停止、审批、背压、OCC 和重连回执必须可独立处理。

### 4.4 模型、权限与附件

设置优先级为本次显式值 → Session → Workspace → Global → Adapter 默认；新 AgentRun
冻结最终值。运行中更改默认标记为下次生效，需要立即应用则先收束再开启新 Run。
显式 Workflow 节点保持自己的模型定义。

思考选项取 Adapter 与精确 Model 的有效交集，支持参数必须真正进入 SDK 请求并进入恢复
快照；不硬编码通用档位，不静默丢参数，不展示 Provider 隐藏 reasoning。

权限复用 manual、auto-safe、auto-sandboxed、full-access-manual；有效授权具体且可撤销。
工具免审批、自动运行 Draft、低风险 Replan 自动接受分别设置，GUI 不自动点击审批。

附件原始内容与解析产物使用现有 Artifact/blob 体系；Log 经原 writer 保存不可变引用。
支持文本/源码、图片与 PDF；非支持格式和不兼容模型提前给出可执行处理选项。
上传、处理、上下文预算与保留清理各有实际限制；不能以上传成功冒充模型已读取。

### 4.5 兼容与边界

- 新 GUI 使用显式 workspace/session 作用域；旧 /v1 默认路由映射启动工作区。
- 保留 loopback、Origin/Host、鉴权和 CSP；凭据只经过专用写入接口与 CredentialStore。
- 查询/事件/日志不公开 reasoning、系统提示、完整敏感工具参数/结果、SDK 或 traceback。
- Markdown/文件预览不执行来源内容；正文/Diff 走受控内容接口。
- 旧消息、快照、Session、Artifact 保持可恢复；迁移取实施时最新 schema 编号并提供备份。
- 原 WorkflowCompiler、权限和 Artifact 权威、future-only continuation、lineage 计量保持不变。
- 模板仍是普通可编辑脚手架；请求总数/时间限制只来自显式配置，不添加猜测预算门槛。

## 5. 子计划顺序

技术依赖与执行顺序都在下表冻结。各子计划列出具体任务、文件归属、验证与交付物。

| 顺序 | 子计划 | 依赖 | 状态 | 分支 |
|---|---|---|---|---|
| 1 | [交互合同与能力映射](subplans/1-interaction-contracts-and-parity.md) | 本计划 | [ ] | docs/chat-interaction-contracts |
| 2 | [Core Chat 执行与实时流](subplans/2-core-chat-runtime-and-stream.md) | 1 | [ ] | feat/chat-core-runtime |
| 3 | [中心 Chat 与 Session 入口](subplans/3-chat-workspace-and-session-entry.md) | 2 | [ ] | feat/chat-workspace |
| 4 | [工作区与 Session 完整管理](subplans/4-workspace-and-session-management.md) | 3 | [ ] | feat/chat-workspaces-sessions |
| 5 | [模型、思考与权限设置](subplans/5-model-reasoning-and-permissions.md) | 4 | [ ] | feat/chat-model-permissions |
| 6 | [附件与文件上下文](subplans/6-attachments-and-file-context.md) | 5 | [ ] | feat/chat-attachments |
| 7 | [Workflow 融合与 CLI 功能闭环](subplans/7-workflow-and-cli-parity.md) | 6 | [ ] | feat/chat-cli-parity |
| 8 | [迁移、全场景验收与交付](subplans/8-migration-and-product-acceptance.md) | 1–7 | [ ] | test/chat-workbench-acceptance |

里程碑 M1（2–3）：当前工作区中真实聊天闭环；M2（4–6）：完整日常操作；
M3（7–8）：CLI 产品等价和可安装交付。M1/M2 不代表整个主计划完成。

## 6. 验证与证据

| 检查级别 | 要求 |
|---|---|
| 文档/合同 | 链接、子计划顺序、需求映射、命令基线、归档校验、git diff --check |
| 每个实现子计划 | 触及模块定向离线测试、Ruff format/check、compileall、diff --check |
| Core/协议/迁移子计划 2、4、5、6、7 | 完整 pytest -m 'not live'；测试使用 fake SDK/scripted Provider |
| GUI 子计划 3–8 | pnpm typecheck/test/build（含体积预算）、脚本化浏览器旅程 |
| 子计划 1 | 文档校验；除非新增可执行合同检查，不制造无价值实现镜像测试 |
| 最终子计划 8 | 完整离线 + GUI + CLI help + GUI 先构建后 uv build + 隔离 wheel 验证 |

实际命令：

    uv run pytest -m 'not live'
    uv run pytest -q <本子计划实际修改的测试文件>
    uv run ruff format --check .
    uv run ruff check .
    uv run python -m compileall -q src tests
    uv run morrow --help
    pnpm --dir gui typecheck
    pnpm --dir gui test
    pnpm --dir gui build
    uv build
    git diff --check

不把历史测试数当作本次通过数。异步竞态用事件/屏障，浏览器等待明确状态；不以固定 sleep
推断成功。首次搭建或锁文件变更才执行必要的 uv sync/pnpm install；新增第三方依赖按授权处理。

每个子计划输出 docs/acceptance/stage8-chat-subplan-N-<slug>.md，记录实际命令、结果、
关键状态证据和限制。新增浏览器证据使用隔离工作区和脚本化 Provider。
各 Acceptance 文档在实际验证后创建，不预先填通过记录。

## 7. 最终用户旅程

| ID | 必须通过的旅程 | 相关要求 |
|---|---|---|
| A01 | 空状态打开 GUI，配置 Provider，创建工作区和 Session，发送首条消息 | C01、C03、C08 |
| A02 | 从 CLI 旧工作区打开历史，在中心 Chat 流式继续，未产生重复身份/消息 | C02、C04、C05 |
| A03 | 多轮追问、新任务、新 Session 和 checkpoint fork 边界正确 | C04、C05 |
| A04 | 运行中 steering/follow-up/撤回/停止与核心队列、Turn 事实一致 | C06 |
| A05 | 刷新、断网、同键重试、两标签页提交不重复执行或误判完成 | C07、C15 |
| A06 | 切换/并行独立工作区；同根排队；消息、配置、文件、审批和草稿不串 | C03、C07、C15 |
| A07 | 真实 Adapter 请求携带模型/思考值；下次生效与冻结恢复正确 | C08、C09 |
| A08 | 各权限预设、一次/具体会话授权、撤销、重复审批与沙箱不可用 | C11 |
| A09 | 文本/图片/文字和扫描 PDF、@、附件独立消息、不兼容及超限处理 | C10 |
| A10 | 停止/崩溃后已知与未知副作用的恢复，不用重新发送冒充恢复 | C06、C14 |
| A11 | Chat 规划/选图、编辑运行、future-only 修改、结果和追问闭环 | C12 |
| A12 | 157 入口及展开后的 REPL/manage 业务操作逐项 GUI 等价 | C13、C14 |
| A13 | 旧状态升级、附件历史/fork、备份/校验/清理与引用完整性 | C04、C16 |
| A14 | 长历史、滚动锚点、键盘、中文输入法、窄屏和双主题 | C02、C16 |
| A15 | GUI 先构建后打包，在隔离 wheel 环境无 Node 日常启动 | C16 |

最终不得留下未解释的功能映射空项、占位按钮或“转去 CLI 执行”的产品缺口。
框架测试、浏览器行为和底层状态三者共同构成证据；Live 结果单独记录。

## 8. 激活、Git 与完成纪律

- 一次一个逻辑任务；任务进入实施时标 [>]，相关验证成功才标 [x]。
- 每个子计划从最新已验证 main 建立对应分支；默认不堆叠分支。
- 接受“执行整个计划”的授权后顺序推进，不把常规实现决定变成反复确认。
- 提交小而完整的已验证进展；风险操作前保留 checkpoint，不回退其他人的修改。
- 关闭子计划前提交更改、完成验证、更新 PLAN/索引/TODO/TRACKER/LOG，ff-only 合入 main，
  校验 topic 没有 main 缺失提交，再删除分支和干净 worktree。
- 当前远程 push 未授权；记录本地集成状态。获得授权后 push 并验证 upstream ahead/behind。
- 原 1–13 文件已归档，不复用编号、重开旧子计划或继续往旧 master 追加任务。
- 真实结构变化后更新 ARCHITECTURE；本次仅修改方向/执行文档，不把设计写成已实现结构。
- 若发现合同不成立，先更新当前主计划与受影响子计划，再调整实现；不能静默缩减范围。

## 9. 依赖与实际限制

新依赖只为可靠 Markdown、必要文件解析/图像处理或验证服务；选型在相关子计划提供具体
包、用途、替代方案、许可证/体积影响和授权记录。本计划不预先批准任何未命名的新包。

公开事件的增量协议必须列出具体变更与既有授权覆盖；优先保持 ApplicationEvent 的事实型
语义。没有新增事件需求时直接使用查询与已授权类型，不增加无必要的审批流程。

Core 崩溃可能丢失未提交回复尾部；同工作区顶层任务排队；模型附件/思考能力由实际支持决定。
这些行为须在 UI 和验收中诚实表达，不能成为遗漏已承诺功能的理由。

不包含后台 daemon/调度、远程/云执行、多用户、自动 worktree、桌面安装器、完整 IDE/PTY、
Office/音视频解码。现有本地工具、Shell、Git、网络、MCP、Skill 产品能力均在本次迁移范围内。
