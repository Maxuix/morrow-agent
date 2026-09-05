# 子计划 4 — 工作区与 Session 完整管理

> 主计划：[Chat 工作台补全](../PLAN.md)
> 状态：[ ] 待开始。
> 分支：feat/chat-workspaces-sessions
> 依赖：子计划 3 的中心 Chat 已验证并集成。
> 对应要求：C01、C03、C04、C07、C15、C16。
> 主要旅程：A01–A03、A05、A06、A13。

## 目标

用户在同一个 GUI 中创建/打开工作区、管理多个 Session，并在不串状态和不增加第二 writer
的前提下切换、继续、分叉和归档。CLI 可连接同一个 Core，独立模式仍可用。

## 修改归属

- services/workspace、application、bootstrap：工作区管理和共享/独占服务拆分。
- server/context/composition/host、protocol/routes：显式工作区作用域、registry 与 coordinator。
- core/domain、adapters/state：Session 展示元数据、取消归档和必要 revision/迁移。
- interfaces：CLI 的 Core 客户端接入适配；不引入 Shell 转发执行。
- GUI：WorkspaceManager、SessionActions、搜索/归档列表和复合键缓存。
- tests：新增 workspace/session/CLI attach/isolation 测试；复用 checkpoint/fork/backup 回归。

## 顺序任务

- [ ] 4.1 实现受控目录浏览/路径校验；区分“打开已有文件夹”和“新建文件夹”。
  canonical path、符号链接、Git 根和既有身份去重在后端完成。
- [ ] 4.2 接通新目录创建与工作区注册、命名、最近列表、relink 和从列表移除。
  初始化 Git 为显式独立可选动作，移除入口不删除项目文件。
- [ ] 4.3 引入 WorkspaceRuntimeRegistry；持有现有 writer lock、路径身份与服务生命周期。
  按需加载/卸载，活动工作和恢复状态不能被导航操作意外释放。
- [ ] 4.4 引入独立 SessionRuntimeManager 与 SessionRunSupervisor，复用同一 Core loop/journal
  owner。避免每个 Session 重建共享服务、共享可变 log 或遗留未关闭连接。
- [ ] 4.5 实现同工作区顶层执行排队、取消排队和占用说明；普通聊天与 Workflow 共用
  顶层归属。测试不同工作区显式 cwd/env/路径隔离与共享宿主资源保护。
- [ ] 4.6 新 GUI 改用显式工作区 API；旧 /v1 默认工作区路由保持兼容。
  snapshot/event/历史/回执/草稿全部绑定 scope，校验所有跨对象引用。
- [ ] 4.7 实现 Session 命名、置顶、搜索、归档/取消归档、checkpoint fork 和新任务入口。
  标题默认取首条安全有界摘要；不额外请求 LLM。
- [ ] 4.8 完善历史 Session 恢复与生命周期提示；分叉基于可恢复 checkpoint，
  编辑旧消息通过分叉发送。未来附件的引用边界在此保留，具体处理于子计划 6 验证。
- [ ] 4.9 增加 CLI 接入已有 Core 的明确路径，共享 API 与 writer；保留 standalone CLI。
  连接发现/凭据存入受控私有设施，不进入 YAML、事件、日志或通用查询。
- [ ] 4.10 实现对应 GUI、草稿恢复和管理操作冲突提示，验证旧数据迁移、CLI 等价与隔离。

## 运行与路径合同

- 浏览器提供文件字节不意味着获得服务端目录身份；目录选择管理动作独立校验。
- Session 的 health/lifecycle 仍由原 domain 状态权威管理；标题/置顶表不复制状态。
- 参数设置不得改变既有运行快照；新会话不继承任务专属 grant 和旧对话。
- 归档/取消归档/relink/移除遇到活动工作时，由应用服务提供有效行动，
  不能先删 UI 项再默默遗留执行。
- 同工作区排队是真实服务端状态，不由前端锁按钮实现。
- 不同工作区并行须测试无 cwd/env/文件权限/配置串用；不足证据时不能宣称隔离并行已完成。
- Core 退出不写用户取消；断开 CLI/GUI 不触发退出整个 Core。
- 工作区身份、目录和会话搜索结果均做范围过滤，包含结果数量和不存在对象的错误。

## 验证用例

| 场景 | 预期 |
|---|---|
| 创建目录/注册/重复路径/符号链接 | 身份唯一，未授权目录不能由模型请求变成新根 |
| 工作区失效/relink | 正确冲突处理，历史身份与 Artifact 关联可恢复 |
| 多 Session 和同根两个提交 | 各自 log 独立，顶层写任务明确排队 |
| 不同工作区并行 | 路径、cwd/env、Provider/权限、事件与取消互不污染 |
| Session 命名/归档/fork | OCC 与 CLI 一致，不修改原消息，状态允许后才能继续 |
| CLI 与两浏览器客户端 | 共享同一 driver/writer，重复命令一次生效 |
| standalone CLI 占锁 | 有可操作错误，不绕过锁或强制终止其他进程 |
| 旧 Session/无新元数据 | 默认展示可用，迁移和备份恢复不损坏 |

## 验证与退出

完成新定向测试、既有 Session/ContextFork/Workspace/Core API/并行/恢复/备份回归；
完整离线 pytest、GUI typecheck/test/build、Ruff format/check、compileall、CLI help、
git diff --check。浏览器覆盖创建两工作区、会话切换/分叉/归档与 CLI 接入的真实旅程。

迁移按当前 schema 编号实施，每一新增表/字段都有兼容默认和回滚所需备份说明。
下一子计划为 5。
