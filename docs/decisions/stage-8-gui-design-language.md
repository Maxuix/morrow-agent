# Stage 8 GUI 视觉设计语言决策

> 状态：已接受（2026-09-03）
> 范围：Stage 8 GUI 全部界面（Subplan 4 起）
> 方向：仿 Claude 设计语言——简约优雅、温暖的纸感中性色、克制的珊瑚橙强调、衬线字体的人文感

## 决策

Morrow GUI 采用 "Warm Paper" 设计语言。三条已确认的基础选择：

1. **主题**：Light/Dark 双主题均为一等公民，默认跟随系统偏好。
2. **字体**：捆绑开源字体——sans 用 Inter，衬线用 Source Serif 4 或 Newsreader（激活时二选一），
   等宽用 JetBrains Mono；字体资源随 GUI 自托管，均为开源许可。
3. **样式技术栈**：设计 tokens 定义为 CSS variables（light/dark 双套），Tailwind 负责布局与
   原子类；React Flow 主题从同一套 tokens 派生，保证节点图与其余界面观感统一。

## 色彩

暖中性色阶 + 唯一强调色。珊瑚橙全场克制使用，只出现在主操作、激活态与关键聚焦处。

| Token | Light | Dark | 用途 |
|---|---|---|---|
| `bg/base` | `#FAF9F5` | `#1B1A17` | 主背景（纸白 / 暖炭） |
| `bg/raised` | `#FFFFFF` | `#262522` | 卡片、面板 |
| `border/subtle` | `#E8E6DC` | `#3A3833` | 发丝级分隔线，优先于阴影 |
| `text/primary` | `#201F1B` | `#EDEBE4` | 暖黑，不用纯黑纯白 |
| `text/secondary` | `#6D6A5F` | `#A3A094` | 次级信息 |
| `accent` | `#C15F3C` | `#D97757` | 主操作、激活态、聚焦 |

运行状态色低饱和化：running 灰蓝、completed 苔绿、failed 陶红、blocked 琥珀、paused/draining
暖灰、queued 仅描边。按 roadmap §16.5，色彩不是唯一状态信号：节点状态一律"状态点/图标 +
文字标签"，色块仅作辅助。

## 字体分工

- 衬线（display serif）：聊天消息正文、Artifact 内容、页面大标题等"阅读材料"。
- 无衬线（sans）：全部 UI 骨架——按钮、标签、导航、状态栏、Inspector 表单。
- 等宽：命令、diff、路径、预算数字、审批脱敏预览。

## 质感与动效

4px 间距网格；8–10px 圆角；分隔用发丝边框而非投影，投影仅用于浮层；动效 120–200ms
ease-out，无弹性动画。密度高于消费级聊天产品、低于 IDE：开发者工具的信息量，Claude 式的
留白。

## 信息架构映射

- Active Context Bar 是安静的文字摘要（secondary 色，hover 才显示可点）。
- Workflow 节点为白色小卡 + 左侧状态点；选中态用 coral 描边。
- 审批卡片是唯一允许"跳出来"的元素：风险等级用图标 + 文字，预览区等宽字体。
- Direct 单节点任务渲染为线性卡片，不强行占大画布（roadmap §8.3）。

## 约束

- 前端不复制 WorkflowCompiler（roadmap §11.4）；本设计只规定视觉层。
- 字体与 Tailwind 属于 Subplan 4 前端工具链授权范围的一部分，激活 Subplan 4 时一并确认。
- 可访问性基线从第一个 GUI 子计划开始执行：键盘可达、状态不以色彩为唯一信号、双主题对比度
  满足 WCAG AA。
