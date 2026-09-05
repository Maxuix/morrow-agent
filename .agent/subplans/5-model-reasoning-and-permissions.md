# 子计划 5 — 模型、思考与权限设置

> 主计划：[Chat 工作台补全](../PLAN.md)
> 状态：[ ] 待开始。
> 分支：feat/chat-model-permissions
> 依赖：子计划 4 的工作区/Session 作用域与生命周期已集成。
> 对应要求：C01、C08、C09、C11。
> 主要旅程：A01、A07、A08。

## 目标

在输入区选择真实可用的模型、思考程度和审批模式，在设置页完成 Provider/Model 全部 CLI
管理操作。参数实际影响模型请求和权限解析，并在每次运行冻结、恢复时保持一致。

## 修改归属

- application/providers、services/provider、configuration：复用控制服务与凭据机制。
- core/models/agent_runs、runtime/provider port、adapters/models/registry：
  生成参数与精确能力、请求映射和旧快照兼容。
- Session/Workspace 设置和 AgentRunPreparation/permission snapshot 接缝。
- server：Provider/Model 控制、Session 设置、能力目录和专用凭据写入。
- GUI：ModelSelector、ReasoningSelector、PermissionSelector、ProviderSettings、授权管理。
- tests：Provider、参数/快照/恢复、权限、配置冲突和 GUI 控件契约。

## 顺序任务

- [ ] 5.1 通过已有服务暴露 Provider 的预设/自定义新增、配置、更换凭据、删除、
  查询与测试；Model 的新增/映射/发现/选择/移除/当前值全部有 GUI 对应。
- [ ] 5.2 定义 Session/Workspace 设置、revision 和默认值优先级。
  输入区默认更改当前 Session；设为工作区/全局默认是独立显式动作。
- [ ] 5.3 扩展 Adapter/精确模型能力，声明支持的思考选项和参数映射，
  以声明与实际实现交集决定 Catalog。未知/不支持参数显式拒绝。
- [ ] 5.4 增加类型化生成参数穿过 InteractionService、AgentRunPreparation、Provider port
  和 SDK 请求；不从任意客户端 JSON 放行任意供应商参数。
- [ ] 5.5 将实际模型、生成选项、设置版本与权限来源冻结到运行快照，
  更新 digest/rehydrate；旧快照保留旧值和缺省行为。
- [ ] 5.6 实现运行中设置变更显示“下次运行生效”。正在排队的输入按提交时绑定值执行；
  若值已不可用，返回可修复状态，不换另一个模型偷偷运行。
- [ ] 5.7 输入区提供四种现有权限预设及可用性；后台复用策略计算。
  原生沙箱不可用时明确禁用该模式，不能降级为宿主自动执行。
- [ ] 5.8 统一审批卡与权限页：允许一次/具体会话范围/拒绝、grant 查询与撤销、
  当前快照查看；多个客户端的重复审批和撤销竞态只产生一个有效决定。
- [ ] 5.9 完成无 Provider 首次配置旅程；读取设置、Catalog、选择模型不自动发起模型测试。
  用户点击测试/发现才进行相应调用，离线测试使用 fake Provider。
- [ ] 5.10 完成 API/SDK/恢复/权限/UI 等价测试，记录实际支持能力与验证证据。

## 参数与授权语义

- 优先级：本次显式值 → Session → Workspace → Global → Adapter 默认。
- 运行接纳前校验并冻结；当前运行不随选择器变化。立刻改变需经正常收束进入新 Run。
- Workflow 节点显式模型不被聊天选择器批量覆盖。
- 思考程度是请求参数，不是展示隐藏 reasoning 的入口；reasoning 仍被隔离。
- auto-safe / auto-sandboxed / full-access-manual 保持现有含义；不新增全局“批准所有”按钮。
- tool approval、auto-run Draft、low-risk auto-replan 不共用一枚开关。
- 具体 grant 的范围与有效期由既有服务决定；“允许会话”不得等同于任何操作都免审批。
- 凭据只经专用写入与 CredentialStore；浏览器仅暂存输入直到提交，不持久化/回显 secret。
  失败、receipt、events、采集日志均不可记录凭据值。
- 不能以更改 bundled 默认策略来让界面看起来更自动；本计划未请求该默认变更。

## 验证用例

| 场景 | 必须断言 |
|---|---|
| 两组不同模型能力 | UI 只展示真实支持值，SDK 请求确实携带选择值 |
| 无思考能力模型 | 不发送无效字段，不假称使用了某思考档位 |
| 当前运行与下一次选择 | 当前快照不变，新输入采用明确绑定版本 |
| 运行恢复/旧快照 | 还原原参数；旧数据保持原默认行为 |
| Provider 更换/移除与过期设置 | OCC/引用约束生效，不能错误回退到别的服务 |
| 权限预设与沙箱故障 | GUI 和 CLI 同 verdict；不静默扩权/降级 |
| 重复审批/撤销竞争 | 一次工具执行，另一客户端状态同步且不重复授权 |
| 凭据/隐藏 reasoning 注入 | 所有公开投影和日志均不出现敏感原文 |

## 验证与退出

完成 Provider 模型管理与 Adapter 请求定向测试、权限/恢复/快照回归和完整离线门禁；
GUI typecheck/test/build、Ruff format/check、compileall、CLI help、git diff --check。
至少两种不同能力配置证明真实参数映射，而不是仅断言下拉菜单值。

用户在浏览器配置模型并选择自动审批完成一次 scripted 工具任务后，可关闭本切片。
下一子计划为 6。
