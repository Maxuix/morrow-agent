# 子计划 3 — 中心 Chat 与 Session 入口

> 主计划：[Chat 工作台补全](../PLAN.md)
> 状态：[ ] 待开始。
> 分支：feat/chat-workspace
> 依赖：子计划 2 的真实 Core Chat API 已验证并集成。
> 对应要求：C02、C04–C07、C11、C14；M1 交付。
> 主要旅程：A02–A05、A14。

## 目标

打开 GUI 就能创建 Session 并在中间完成连续对话、工具执行、审批和结果查看。
Chat 始终是默认工作区；现有 Task、Workflow 和 Artifact 界面作为相关详情保留。

## 修改归属

- gui/src/views：AppShell/SessionNav/TopBar 重组；新增 ChatWorkspace、Transcript、
  Composer、MessageItem、ToolActivity、ConversationApproval 和运行中输入队列视图。
- gui/src/api/client/types 与 state：历史分页、回执、回复流和复合键草稿/选择状态。
- 现有审批/运行控件：复用命令与判断，增加普通对话运行对象适配。
- GUI 测试及 scripts 中的隔离 scripted Chat smoke fixture；仅必要的 API 投影纠偏。

## 顺序任务

- [ ] 3.1 默认路由切到 Session Chat。左栏约 256px、中间自适应、正文约 860px；
  右侧详情默认收起。保持 Warm Paper tokens、双主题与原管理页可达。
- [ ] 3.2 补入创建 Session、列表、空状态和切换。无需先选 TaskRun，
  无 Provider 时提供可修复的配置状态；最终配置表单由子计划 5 补齐。
- [ ] 3.3 实现 Timeline 消息渲染：用户/助手、任务分段、工具活动、错误/恢复与结果卡。
  Markdown/代码可读可复制；不执行 HTML，不直接展示完整工具日志。
- [ ] 3.4 实现 Composer：多行、Enter/Shift+Enter、IME 组词保护、发送状态、
  pending/failed 乐观消息和按 workspace/session 保存的草稿。
- [ ] 3.5 接入运行中补充、完成后执行、队列位置/状态、撤回与 Stop；输入框运行时持续可用。
- [ ] 3.6 接通历史分页、上翻锚点、新消息提示、底部自动跟随、订阅去重/重连和提交替换。
  Session 切换后的旧网络响应不能覆盖当前视图。
- [ ] 3.7 在中心显示既有审批与 TaskOutcome 接受/修正动作；普通问答不强制弹验收框，
  运行中工具失败后继续纠正时不得提前将任务显示为终态失败。
- [ ] 3.8 接入文件/Artifact/Workflow/Context 右侧详情；打开关闭保留消息和草稿。
  复杂工作流编辑可全屏，返回恢复会话；完整编排入口在子计划 7 接通。
- [ ] 3.9 建立命令菜单机制并接通 /new、/status、/task、/accept、/compact 等已实现路径。
  其他能力据服务端 capabilities 显示，不能生成可点击的假功能。
- [ ] 3.10 完成浏览器单工作区旅程、键盘/双主题/窄屏与工程门禁。

## 交互约束

- 输入区显示当前有效模型/权限，完整选择器和作用域写入在子计划 5 实现。
  附件本体在子计划 6 实现；本切片不以装饰性按钮冒充可用。
- TaskRun 是 Session 内分段，不能把每次模型轮次都创建为独立 Session。
- 右侧面板不足宽度时变抽屉；Chat 不能被强制常驻图挤到不可读。
- 客户端草稿不是已发送事实；未收到接纳回执时显示待发送/失败。
- 凭据不得进入 localStorage/sessionStorage/IndexedDB；草稿与身份绑定并可清除。
- 设置页与工作流编辑有返回点；不会因换页意外停止当前运行。

## 验证用例

| 场景 | 预期 |
|---|---|
| 新建/连续两轮 | 真实 scripted Core 返回可见回复与正确历史 |
| 中文输入与键盘 | 组词 Enter 不误发，焦点可达，换行与发送明确 |
| 上翻/加载历史/切换 | 锚点、草稿和 Session 内容保持正确 |
| 两标签与掉线 | 消息不重叠，状态一致，重连显示已提交事实 |
| 运行中输入/审批/Stop | 使用真实 API 和队列，界面结果与服务端一致 |
| 结果/详情面板 | 普通回复、任务验收、Artifacts 和 Workflow 入口关系清晰 |
| 窄屏/双主题 | 输入区始终可用，状态不依赖颜色，无不可达控件 |

## 验证与退出

运行 GUI typecheck/test/build（含体积预算）、触及的 API 定向回归、Ruff format/check、
compileall 和 git diff --check。新 Markdown 依赖如确有必要，按主计划提供具体选型与授权。

真实浏览器必须完成“创建 Session → 连续对话 → 工具审批 → 运行中补充 → 查看结果 →
刷新继续”的完整 scripted 旅程，并保存关键状态截图和核心事实对照。
M1 只表明单工作区聊天主链已交付，不关闭完整主计划。下一子计划为 4。
