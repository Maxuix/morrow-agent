# 接口与工作台

[架构总览](../ARCHITECTURE.md) · [运行时](runtime.md) · [状态](state.md)

## 入口与组合

| 入口 | 实现 | 行为 |
| --- | --- | --- |
| `morrow` / `python -m morrow` | [interfaces/cli.py](../../src/morrow/interfaces/cli.py) | CLI 命令与交互 REPL |
| `morrow run` | 同一 CLI 与 bootstrap | headless AgentEvent JSONL；诊断去 stderr，审批默认拒绝 |
| `morrow serve` | [serve_cli.py](../../src/morrow/interfaces/serve_cli.py) | 前台 Core API，不启动独立 daemon |
| `morrow gui` | [gui_cli.py](../../src/morrow/interfaces/gui_cli.py) | 同一 Core 服务静态 GUI 并打开浏览器 |
| `morrow attach` | [core_client.py](../../src/morrow/interfaces/core_client.py) | 发现并连接已有本地 Core，不打开第二写库 |

[CoreHost](../../src/morrow/server/host.py) 持有唯一 Core 线程/事件循环与线程绑定 SQLite handle。
变更接纳经过有界串行 command bus，队列满返回背压；SQLite 投影仍在 Core owner 上执行。
工作空间文件读取和 Chat Artifact 文件校验/读取使用 worker；Artifact 权限、元数据和损坏标记
仍回到 owner 处理。timeline 分页包含持久化索引 reconcile，不能直接作为只读函数移交 worker。
长历史的同步 SQLite 查询仍可能占用 Core loop；当前不宣称全链路无阻塞。
Provider 等待和维护 worker 遵循专用边界，不能把绑定连接交给 ASGI worker。
`RunSupervisor` 为每个 WorkflowRun 持有至多一个 driver；Session runtime 各自持有 log 与 driver。
关闭 Core 取消进程内 driver，但不能伪造用户取消；durable 恢复状态保留。

[WorkspaceRuntimeRegistry](../../src/morrow/server/workspaces.py) 持有工作区锁、canonical root
和管理上下文，共享底层 Store。工作区移除只隐藏入口，同路径重新注册恢复原身份。
Chat/Workflow 共用 [execution gate](../../src/morrow/application/execution_gate.py)：同根、父子根
以及非 confined 执行冲突；只有已证明 confinement 的不相交根可并行，等待后再次检查。
Host/full-access、MCP 扩展等会影响 confinement 判定，不能只看不同 workspace ID 就并行。

## API 与安全投影

[server/app.py](../../src/morrow/server/app.py) 负责 ASGI 传输与本地安全中间件；各 route 解析
wire 模型、委托 Core/application 并投影结果。Route 不拥有第二套任务、审批或存储状态机。
`server/projections.py` 与领域投影采用显式字段，凭据、SDK 对象和 traceback 不出站。
模型/Provider 管理只有显式 test/discover 才发起对应探测，结果回 Core 时重新检查版本。

当前两种启动模式有不同认证规则，不能统一描述成“所有请求强制 Bearer”：

- headless `serve` 要求有效会话 token。
- GUI 模式允许无 token 的本地同源访问；仍检查 Host/Origin，未携带 token 的 mutation 和
  WebSocket 必须有合法 Origin。错误的显式 token 不会退回匿名 GUI 访问。
- cookie/token 路径共享本地 authority 校验。普通 mutation 要求 JSON；附件上传只有窄 MIME 例外，
  请求字节数有界。凭据不放入普通查询或下载 URL。

`attach` 从私有连接文件发现 loopback 地址，禁用代理和重定向；退出 attach 或关闭浏览器不表示 Stop。
安全行为以服务端中间件和当前 API 实现为准。

## 事件、回复和活动

| 流 | Owner 与用途 | 不承担的职责 |
| --- | --- | --- |
| AgentEvent | AgentLoop 生命周期、文本预览、工具事件，sequence 有序 | 不替代 durable command receipt |
| ApplicationEvent | 业务事务提交的事实与 workspace cursor | 不存 token，不重建 ConversationLog |
| Reply stream | `server/replies.py` 的有界 epoch/sequence ring 与草稿 | 不保证未提交尾部跨重启保留 |
| Activity stream | `server/activities.py`、`activity_projection.py` | 不持有执行权或完整工具结果 |
| Durable timeline | application timeline/index + SQLite | 不增加聊天历史 writer，详见状态专题 |

WebSocket 的 application cursor hint 只通知有更新；客户端从 durable event API 补拉并在 gap 时
resync。快照与水位一致，hint 在最外层事务提交后发出。回复文本可能是临时预览，重试/reset 时
用稳定身份替换；用户不能把临时 token、terminal stop 或 activity 完成等同任务成功。

`ModelContentObserver` 是运行时到展示层的观察接缝；Workflow bridge 同时提供叶子与根流的来源。
观察者失败只产生有界诊断，不改变执行结果。GUI 不自行推断服务端 allowed intents。

## GUI 导航与状态

真实页面入口由 [AppShell.tsx](../../gui/src/views/AppShell.tsx) 和
[navigation.ts](../../gui/src/state/navigation.ts) 组合：

| 页面/区域 | Owner | 当前职责 |
| --- | --- | --- |
| Chat | `ChatWorkspace`、`ChatStore` | Session 输入、草稿、历史、排队与运行投影 |
| 左侧导航与会话 | `WorkspaceSidebar`、sidebar store | 工作区/Session 选择、搜索、归档和页面导航 |
| 知识 | `WorkspaceKnowledgePage` | 项目画像、行为偏好、知识库 |
| 工具 | `WorkspaceToolsPage` | Skills 与 MCP 管理 |
| 设置 | `SettingsPage` | Provider、Agent、外观、诊断等管理 |
| 工作流定义 | `WorkspaceEditorPage` → `EditorShell` | 定义目录、Draft、画布编辑、检查与发布 |
| 右侧 Inspector | `ChatInspector` 与各 body | Context、Workflow、Artifacts、Terminal、Learning |

Chat 在切管理页面时保持挂载并隐藏；Session 列表由 Sidebar 持有，不再使用旧 portal 容器说明。
工作区切换重建 scoped ApiClient/SyncStore，旧响应以 generation 隔离；订阅有界，卸载不停止 Core。
`InlineApprovalCard` 和 `useApprovalDecision` 复用同一审批决策状态；审批在消息/活动中显示，
维护在诊断页确认。旧独立权限页、Operations/State 壳和阻断式审批弹窗不再是生产入口。
项目文件能力收敛到任务文件 Inspector；工作流定义页面不提供通用项目文件编辑器。

`ChatMessage`/`TaskResultMessage` 通过共享的 `MarkdownBody`（react-markdown + remark-gfm）渲染
Markdown：不执行原始 HTML、不自动加载远程图片、MDX 按文本处理；本地链接只作为面板动作，
文档相对链接按被查看文件目录解析，历史正文中的链接显式标注打开的是当前版本。
输入草稿与未确认发送按 workspace/session 隔离；不能把客户端缓存当成 durable 历史。

## 工作流编辑器

[WorkflowDraftController](../../gui/src/state/workflowDraft.ts) 管理编辑版本、串行保存、OCC、
请求代次和服务端读回。保存、检查、发布是独立状态；保存成功不能表示已经发布。
画布坐标/视口是有界 UI 状态，不写 Source、不触发编译；Source 保留所有未显示的合法字段。

`EditorShell` 组合目录、控制器、`WorkflowEditor` 和发布面板。步骤显示名从目标生成，仅作展示，
不改 node ID。连接编辑区分控制依赖和结果绑定；删除/修改先分析引用影响，不自动换输出或补边。
Compiler 是最终校验权威；前端预检用于定位问题。DirtyGuard 统一处理切页/切草稿/离开。

发布先确保最新保存和检查，调用既有 freeze；未知结果读回，冲突保留用户内容。
freeze 固定 Source/Revision，不调用 run/start。冻结草稿只读，继续编辑创建新草稿。
[PatchEditor](../../gui/src/views/PatchEditor.tsx) 共享编辑组件，但保持历史节点锁定、风险预览和
continuation 应用链，不能借模板发布绕过运行时边界。

## 附件与维护

[AttachmentService](../../src/morrow/application/attachments.py) 在 Core owner 发布 Artifact 和
保留边；[AttachmentParsePool](../../src/morrow/application/attachment_parsing.py) 使用最多两个
可终止子进程，调用 `morrow.adapters.attachment_parser`。这一 `python -m` 动态入口不能按
“没有 import”删除。解析进程不接收数据库/凭据，输入、CPU/时限、页数/像素和产物有界。
模型输入在 Provider 投影时校验 digest 与图像能力，Log 只保存版本化引用。

维护作业由 state routes 与原 doctor/backup/cleanup 服务组合，工作线程使用自己的只读 Store；
权威写入仍归 Core owner，退出等待写入结束再释放锁。目录选择、relink、附件输入和恢复均经
对应服务边界，不允许 UI 直接访问用户任意路径。

GUI 使用 pnpm 固定版本、Vite/React/TypeScript，构建到 gitignored `src/morrow/gui_static/`。
发布前先 GUI build（含体积门禁）再 `uv build`；包安装后的静态资源验证与开发服务器验证是不同证据。
