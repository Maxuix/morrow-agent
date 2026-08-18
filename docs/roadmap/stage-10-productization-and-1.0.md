# Stage 10：产品化与 Morrow 1.0

> 状态：未开始
> 阶段结果：Morrow 成为可安装、可升级、可诊断、可迁移、可长期日用的个人 Agent 1.0
> 上级文档：[开发路线总览](../ROADMAP.md)
> 上一阶段：[Stage 9：后台任务与可靠自动化](stage-9-background-automation.md)

## 一、阶段目标

Stage 10 不再增加一条新的 Agent 核心路线，而是把前九个 Stage 收口成一个可信赖的个人产品。

Morrow 1.0 应满足：

- 新用户能完成安装、Provider 配置和第一次真实开发任务。
- 日常用户能在 CLI 与 GUI 中看到一致的 Session、Task、Workflow、Learning 和 Skill 状态。
- 升级、迁移、崩溃、磁盘损坏和扩展失败不会静默破坏个人数据。
- 用户能查看、导出、备份、恢复和删除自己的全部长期状态。
- 默认权限保守，自动化、学习和 Multi-Agent 的成本与副作用可见。
- 核心保持个人、本地优先，不为发布 1.0 强行转向多用户 SaaS。

## 二、进入条件

- Stage 3 的 Code Agent 已在真实项目中稳定使用。
- Stage 4 的 Session/Task/Artifact 可恢复。
- Stage 5 的学习可审查、可关闭、可撤销。
- Stage 6 的 Skill/MCP/Provider 有完整生命周期。
- Stage 7/8 的 Workflow 与 GUI 已通过 Direct 基线评估。
- Stage 9 的 daemon、Schedule 和后台恢复可靠。
- 数据 schema、事件 API 和配置格式已形成迁移纪律。
- 已收集足够真实使用反馈来确定默认功能，而不是仅凭展示效果选择。

## 三、1.0 产品范围

### 3.1 稳定入口

Morrow 1.0 保留：

- CLI：开发、诊断、自动化和无 GUI 环境的稳定入口。
- Local GUI：日常任务、Context、Learning、Skill 和 Workflow 的主要可视化入口。
- Core daemon/service：统一业务与运行状态。

不要求同时支持移动端、浏览器云端、Slack、Discord、Teams 等多个渠道。

### 3.2 桌面形态

建议最终形态：

```text
Desktop Shell
├── Web UI assets
├── secure local IPC
└── Morrow Core sidecar/service
```

可以评估 Tauri 等轻量桌面壳，但选型必须通过：

- Python Core 打包与启动。
- macOS/Windows/Linux 进程管理。
- 自动更新。
- 本地权限和签名。
- 崩溃诊断。
- 安装体积和维护成本。

若桌面壳不成熟，1.0 可以先发布稳定 Local Web GUI + CLI 安装包；产品完整性高于形式统一。

## 四、安装与首次使用

### 4.1 安装目标

- 明确支持的平台和 Python/系统要求。
- 提供可复现安装方式。
- 不要求用户手动拼接复杂前后端环境。
- 安装不修改用户项目文件。
- 卸载与删除数据分开，避免误删个人状态。

### 4.2 首次引导

最短闭环：

```text
安装
→ 启动 Morrow
→ 选择/配置 Provider 与 Credential
→ 连接测试
→ 选择工作空间
→ 解释数据位置和权限
→ 执行一个只读探索任务
→ 用户决定是否允许写入/命令
→ 完成首个真实任务
```

不在首次启动强迫用户配置 Multi-Agent、Skills、Learning 和后台 Schedule。高级能力在需要时渐进展示。

### 4.3 Provider 体验

- 缺少 Credential 时给出明确步骤。
- Test 与实际聊天使用同一 Adapter 配置。
- 不静默切换 Provider/Model。
- 显示模型能力、成本来源和上下文限制。
- Credential 轮换与删除不泄漏原文。

### 4.4 工作空间建立

- 清楚显示解析后的路径和 workspace_id。
- 解释 Morrow 默认可访问范围。
- 展示已有未提交改动。
- 引导创建最小 Profile，而不是长问卷。
- Learning 默认策略可跳过或保持 `review_only`。

## 五、版本与发布策略

### 5.1 版本组成

至少版本化：

- Morrow Core。
- GUI Client。
- API/Event schema。
- Operational Store schema。
- YAML/Profile/Preferences schema。
- Skill Catalog schema。
- Builtin Agent/Workflow/Skill definitions。

### 5.2 兼容策略

- Core 与 GUI 在握手时检查 API 兼容范围。
- 新客户端不能假设旧 Core 支持未知 Command。
- Event consumer 忽略未知字段/事件，但不能忽略破坏性版本变化。
- Builtin Definition 更新不修改正在运行或历史 Revision。

### 5.3 发布通道

可采用：

```text
stable
preview
nightly（仅开发者）
```

1.0 默认 stable，不自动把用户切换到 preview。

### 5.4 发布门禁

每个发布候选至少通过：

- 离线测试。
- lint/format/compile。
- 打包安装/卸载。
- schema 迁移。
- 真实 Provider 冒烟（显式环境）。
- 真实工作空间只读/写入任务。
- GUI/Core 兼容。
- 崩溃恢复。
- 数据备份与恢复。
- 安全检查。

## 六、升级与迁移

### 6.1 升级顺序

```text
检查版本和兼容性
→ 停止新后台 Run
→ 等待/暂停活动 Run
→ 创建数据备份与迁移计划
→ 更新 Core/GUI
→ 执行 schema migration
→ 验证关键状态
→ 恢复服务
→ 失败则回滚或进入只读恢复模式
```

### 6.2 数据迁移原则

- 每个迁移有 from/to 版本。
- 迁移前备份。
- 支持 dry-run/预检。
- 迁移失败不覆盖原始数据库和 YAML。
- 不在启动时无提示执行不可逆大迁移。
- 旧 Artifact 不因索引变化丢失。
- Skill/Workflow 历史 Revision 保持可复现。

### 6.3 二进制与 schema 回滚

回滚要明确：

- 新 schema 是否能被旧 Core 读取。
- 需要回滚备份还是仅回滚二进制。
- 后台 Run 在迁移期间的状态。
- GUI 与 Core 版本不匹配时的只读/阻止策略。

### 6.4 遗留数据

旧 `handoff.yaml(.bak)` 等历史文件：

- 默认不隐式导入。
- 提供独立扫描、预览和选择性导入工具，或明确标记不支持。
- 删除必须由用户显式选择。
- 不把历史实验数据混入新的 Session/Memory 权威源。

## 七、备份、恢复、导出与删除

### 7.1 备份范围

用户可选择：

```text
configuration_only
workspace_state
sessions_and_tasks
skills_and_workflows
full_backup
```

Credential 默认不进入普通备份；如支持安全导出，必须是独立加密流程。

### 7.2 备份格式

备份清单至少记录：

- Morrow 版本。
- Schema 版本。
- 创建时间。
- 包含内容。
- 文件 hash。
- 敏感等级。
- 工作空间映射。

### 7.3 恢复

- 支持预览和冲突报告。
- 可恢复到新目录。
- Workspace 路径变化使用显式 relink。
- 不覆盖当前状态，除非用户明确选择并通过 revision 检查。
- 恢复后执行完整一致性验证。

### 7.4 导出

提供人类可读导出：

- Preferences/Profile。
- Knowledge 与 Evidence 摘要。
- Session/Task/Outcome。
- Agent/Workflow/Skill 定义。
- Schedule。

敏感 Tool 参数、Credential 和外部内容按策略脱敏。

### 7.5 删除

支持：

- 单条 Preference/Knowledge。
- Session/Task。
- Artifact。
- Skill/Workflow/Schedule。
- 单个 Workspace 全部状态。
- 全部 Morrow 数据。

删除显示依赖影响，并区分：

```text
disable
archive
logical delete
secure physical cleanup（受平台能力限制）
```

## 八、Doctor 与诊断

### 8.1 `morrow doctor`

检查：

- 安装与版本。
- Core/GUI/API 兼容。
- Provider 配置和 Credential 是否存在（不显示原文）。
- 工作空间索引与路径。
- YAML/SQLite schema 和一致性。
- Artifact 缺失/hash。
- Skill/MCP/Provider 扩展健康。
- daemon、Scheduler 和 Worker。
- 磁盘空间、权限和锁。
- 最近崩溃与恢复状态。

### 8.2 诊断包

用户可生成脱敏包：

- 版本和平台。
- 配置结构摘要。
- 最近错误代码和受限事件。
- schema 状态。
- 扩展列表。
- 不包含 Credential、完整 Prompt、完整项目文件和 Provider reasoning。

生成前显示将包含的内容。

### 8.3 日志

- 结构化、轮转和大小上限。
- 明确日志级别。
- 默认不记录完整 Tool 参数/结果。
- 用户可临时开启诊断级别，并看到隐私提示。
- 崩溃日志和任务权威记录分离。

## 九、稳定性与性能

### 9.1 性能基线

建立并持续监控：

- 冷启动和热启动。
- 工作空间识别。
- Session/Task 列表查询。
- 大 Session 恢复。
- Context 组装。
- Skill Catalog 加载。
- 大 Workflow GUI 渲染。
- Event throughput。
- SQLite/Artifact 磁盘增长。
- daemon 空闲资源。

### 9.2 资源上限

- Session/Artifact/Log 配额。
- 大目录扫描限制。
- GUI 只加载可见范围。
- Event history 分页。
- 后台 Worker 并发和内存。
- Skill/reference 按需读取。

### 9.3 崩溃与恢复目标

- Core 崩溃后权威状态可恢复。
- GUI 崩溃不影响 Core Run。
- 单 Agent/Node 失败不使整个数据库损坏。
- 扩展失败隔离。
- 重启恢复报告准确。

不承诺“永不崩溃”，承诺失败可诊断且不静默破坏数据。

## 十、安全与隐私收口

### 10.1 威胁模型

至少覆盖：

- 工作空间中的恶意 Prompt Injection。
- 恶意 Skill/MCP Server。
- loopback API 攻击。
- Credential 泄漏。
- 路径和符号链接逃逸。
- Shell/外部副作用。
- GUI Markdown/XSS。
- 自动学习敏感信息。
- 后台预授权滥用。
- 供应链和更新包。

### 10.2 安装包与更新

- 发布产物 hash/signature。
- 下载来源验证。
- 自动更新可关闭。
- 更新前显示版本和必要迁移。
- 不执行未验证的扩展更新。

### 10.3 本地 API

- loopback 默认。
- 强随机 token/IPC 权限。
- Origin/CSRF 防护。
- 最小 API 暴露。
- Credential 不通过普通 Query 返回。
- 版本握手。

### 10.4 隐私控制

用户可查看：

- 哪些数据保存在本地。
- 哪些内容发送给当前 Provider。
- 哪些 Artifact/Memory 被注入。
- 哪些 MCP/外部服务获得数据。
- Learning 是否开启。
- 后台任务有哪些预授权。

## 十一、GUI 日用体验

### 11.1 渐进复杂度

- 默认 Direct 任务界面简单。
- 只有 Multi-Agent 时展开 Workflow 图。
- 高级 Agent/Skill/Policy 配置放入 Inspector/Settings。
- 待确认 Learning 以数量提示，不阻塞每次任务。

### 11.2 键盘与可访问性

- 核心任务、审批、节点导航和搜索可用键盘完成。
- 焦点顺序明确。
- 状态不只依赖颜色。
- 支持屏幕阅读语义。
- 动画和高密度图可降级。

### 11.3 错误反馈

错误展示：

```text
发生了什么
影响了什么
是否已经有副作用
Morrow 做了什么保护
用户可以执行的下一步
诊断 ID
```

避免只显示 traceback 或“未知错误”。

### 11.4 首选项和高级设置

设置分组：

- Provider/Model。
- Workspace/Profile。
- Preferences/Learning。
- Skills/MCP。
- Agents/Workflows。
- Automation/Approvals。
- Data/Backup/Privacy。
- Diagnostics。

同一设置不在多个页面维护不同副本。

### 11.5 界面语言与本地化

- UI locale 与控制模型回答语言的 `preferences.language` 分离，不能复用一个字段产生隐式联动。
- Stage 10 激活时锁定 CLI、GUI、帮助、错误和文档的支持语言矩阵与回退规则。
- 稳定错误码与本地化文案分离；CLI 与 GUI 对同一错误保持相同语义。
- 任何语言只有在核心安装、任务、审批、恢复和数据管理路径覆盖后才能声明受支持。

## 十二、跨平台与打包

### 12.1 支持矩阵

激活本阶段时依据真实用户选择首批平台。每个平台必须定义：

- 安装方式。
- CredentialStore。
- 文件锁和权限。
- daemon/autostart。
- 系统通知。
- 终端集成。
- 应用签名和更新。

### 12.2 Core 分发

评估：

- Python 环境随包。
- 独立二进制/目录包。
- 系统 Python + uv 管理。
- Desktop sidecar。

选择依据：可靠更新、可诊断性、体积和开发维护成本，而不是只看启动速度。

### 12.3 开发者模式

保留：

- `uv run morrow`。
- 独立 GUI dev server。
- Fake Provider/Tool/Clock。
- Fixture Operational Store。
- API schema generation。

发布架构不能让本地开发和测试变得不可控。

## 十三、文档与支持

### 13.1 用户文档

至少包括：

- 安装与升级。
- Provider 配置。
- 工作空间和安全边界。
- Code Agent 常见任务。
- Session/Task 恢复。
- Learning 与删除。
- Skill/MCP 来源与风险。
- Workflow 与成本。
- 后台任务与审批。
- 备份/恢复/doctor。

### 13.2 开发者文档

- Architecture。
- Command/Query/Event API。
- Tool/Skill/MCP/Provider 扩展。
- Agent/Workflow schema。
- Store migrations。
- 安全不变量。
- 测试与发布门禁。

### 13.3 错误码与诊断索引

稳定错误码有文档和建议操作，不要求用户搜索内部 traceback。

## 十四、质量与发布指标

### 14.1 可靠性

- Session/Task 恢复成功率。
- 未知副作用正确暂停率。
- schema migration 成功/回滚率。
- daemon crash recovery。
- 扩展隔离失败次数。

### 14.2 产品价值

- 真实开发任务完成率。
- 用户返工次数。
- 从安装到首次成功任务的阻塞点。
- Direct/Multi-Agent 选择准确度。
- Learning Candidate 接受/拒绝。
- Skill 实际收益。
- 周期任务成功和噪声通知。

### 14.3 安全与隐私

- 工作空间越界次数。
- 未经授权副作用。
- Credential/敏感内容泄漏测试。
- 恶意 Skill/MCP/Prompt Injection 防护。
- 用户删除请求完整性。

### 14.4 性能

性能目标在进入本阶段时根据真实数据制定，不在路线中虚构固定数字。每项必须有基线、回归阈值和可复现测试。

## 十五、实施切片

### 10A：发行架构与安装 Spike

交付：

- 平台支持决策。
- Core/GUI/daemon 打包 Spike。
- 安装、启动、卸载。
- 签名/更新可行性。

门禁：全新环境可以安装并完成首次只读任务。

### 10B：升级、Schema 迁移与回滚

交付：

- 版本握手。
- 迁移计划、dry-run、备份和失败恢复。
- Core/GUI 兼容矩阵。
- 历史 Revision 保留。

门禁：从至少两个旧 fixture 版本升级并可恢复失败。

### 10C：备份、恢复、导出与删除

交付：

- 分范围备份。
- 冲突恢复和 relink。
- 人类可读导出。
- Workspace/全局删除。

门禁：用户能验证备份、在新环境恢复，并彻底移除选定状态。

### 10D：Doctor、诊断与可维护性

交付：

- `morrow doctor`。
- 脱敏诊断包。
- 日志轮转和崩溃报告。
- 扩展/daemon/Store 健康检查。

门禁：常见安装、Credential、Store、Artifact、MCP 和版本问题可以定位。

### 10E：安全、性能与可访问性收口

交付：

- 威胁模型与安全测试。
- 资源/性能基线。
- GUI 键盘和可访问性。
- UI locale 与模型回答语言分离、本地化覆盖和回退测试。
- 长期运行和磁盘增长测试。

门禁：没有已知阻塞性越权、数据损坏、资源泄漏和不可操作 UI 问题。

### 10F：文档、发布候选与 1.0 验收

交付：

- 用户/开发者文档。
- 发布 Checklist。
- Preview 反馈修复。
- Stable 1.0 产物。

门禁：从全新安装到真实 Code Agent、恢复、Learning、Skill、Workflow 和备份的完整人工验收通过。

## 十六、1.0 验收场景

至少完成：

1. 全新用户安装、配置 Provider、进入仓库并完成一个 bug 修复。
2. 中途关闭 Core，恢复 Session/Task，并继续验证。
3. 接受一条 Preference Candidate，下一任务正确生效，再撤销。
4. 从重复任务创建 Skill Draft，测试、批准、启用和回滚。
5. 运行 Direct 与 Explore–Implement–Verify，观察 Node/Artifact/Review。
6. GUI 中暂停并修改 Pending Node，产生新 Revision 后继续。
7. 创建一个只读周期任务，重启后正确执行并通知。
8. Provider/MCP/Skill 故障时主状态保持完好。
9. 执行备份，在干净环境恢复并 relink 工作空间。
10. 运行 doctor 并生成脱敏诊断包。
11. 升级到新 schema，模拟失败并恢复原数据。
12. 删除一个工作空间的全部 Morrow 状态，确认不影响其他工作空间。

## 十七、阶段交付物

- 稳定 CLI、Local GUI、Core daemon 的发行形态。
- 安装、卸载、升级、自动更新或明确手动更新流程。
- Schema migration、备份与失败回滚。
- 数据导出、恢复和删除。
- Doctor、诊断包、日志和崩溃处理。
- 安全威胁模型和测试。
- 性能、资源、兼容性和可访问性基线。
- 用户/开发者文档与发布维护手册。
- Morrow 1.0 Stable 发布产物。

## 十八、Stage 10 完成标准

1. 新用户可在支持平台完成安装、Provider 配置和首次真实任务。
2. CLI、GUI 和 daemon 共享同一状态与行为，不复制核心逻辑。
3. Core/GUI/API/schema 版本不兼容时安全阻止或降级。
4. 升级前备份，迁移失败不静默覆盖个人状态。
5. 用户能备份、恢复、导出和删除全部长期数据。
6. Doctor 能诊断最常见安装、Provider、Store、Artifact、扩展和 daemon 问题。
7. Credential、reasoning、完整敏感 Tool 数据不出现在普通日志、事件、诊断包或备份。
8. GUI 可通过键盘完成核心操作，状态和错误可理解。
9. 真实任务、恢复、Learning、Skill、Workflow 和 Schedule 全链路通过发布验收。
10. 资源使用和磁盘增长有边界，长期运行无阻塞泄漏。
11. 默认体验保持 Direct、保守权限和可审查学习；高级自动化按需开启。
12. 发布文档准确描述现有能力和限制，不把未来设想写成已实现功能。
13. UI locale 与模型回答语言相互独立，声明支持的界面语言覆盖核心用户路径并有回退测试。

## 十九、1.0 仍然不包含

- 多租户 SaaS。
- 团队共享工作空间与组织权限。
- 分布式 Agent 集群。
- 默认跨设备同步。
- 全渠道消息入口。
- 无边界自我修改和无限自主循环。
- 自动代表用户进行高风险发布、支付或权限变更。
- 为追逐功能数量而把办公、浏览器和所有外部服务内置进 Core。

## 二十、1.0 后演进方向

1.0 后的能力应优先通过既有扩展面实现：

- 办公与文档处理：Skills + MCP + Workflow。
- 浏览器与远程环境：受控 Tool/Extension。
- 新模型与本地模型：Provider Adapter。
- 新 Agent 角色：AgentDefinition。
- 新复杂流程：WorkflowTemplate。
- 用户长期习惯：Learning Candidate + OrchestrationPolicy。

只有当扩展面无法表达一类普遍、稳定且高价值需求时，才考虑修改 Core。
