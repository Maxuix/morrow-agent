# Stage 6：Skills 与扩展生命周期

> 状态：已完成（Subplans 63–77 已在本地完成，含 runtime-policy 与 Skill Script diagnostics/context 修复；macOS 离线验收通过，Linux 原生运行仍 unsupported）
> 当前进展：Skill Catalog/生命周期、Selection/Context、Draft/Usage、受限脚本、Provider/Model 控制面，
> MCP desired state、stdio Catalog discovery、确定性命名空间、Operational Store v16、run-scoped lazy runtime、
> 安全策略桥接、结果 Artifact 引用、普通 ToolExecutor 接入、Doctor 和当前完整 Backup 均已完成。
> 阶段结果：Morrow 可以在隔离状态中发现、加载、测试、版本化和治理 Skills，并以统一安全边界接入 MCP 与更多 Provider；
> 完整离线验收证据见 [Stage 6 acceptance](../acceptance/stage6-skills-and-extensions.md)
> 上级文档：[开发路线总览](../ROADMAP.md)
> 上一阶段：[Stage 5：可审查学习与长期记忆](stage-5-reviewable-learning-and-memory.md)
> 下一阶段：[Stage 7：Agent Definition 与静态 Workflow Runtime](stage-7-workflow-runtime.md)
> 最终方案：[Stage 6 Skills 与扩展生命周期最终方案](../reviews/stage-6-skills-and-extensions-final-proposal.md)

## 一、阶段目标

阶段 6 把变化频繁、面向特定任务的能力移出 Agent 核心，形成受治理的扩展层。

本阶段重点解决三个问题：

1. **Skills**：用户和 Morrow 如何把可复用流程保存为长期、可测试、可回滚的能力。
2. **MCP**：第三方工具如何在不绕过 Morrow 审批、权限和审计的前提下接入。
3. **Provider/Model 扩展**：如何继续通过 Adapter Registry 增加模型能力，而不修改 AgentLoop 和会话核心。

对于自动 Skill，核心原则是：

> **任务经验只能产生 SkillCandidate；系统先创建 Draft，经过验证、权限检查和用户审查后，才成为 Active Skill。**

## 二、进入条件

- Stage 5 能生成有来源的 SkillCandidate，但不会直接创建 Skill。
- Session、Task、Artifact、LearningCandidate 和版本化状态可靠。
- 本地工具权限、审批、超时、取消和审计可以被外部能力复用。
- ContextAssembler 能按预算选择性加载长期状态。
- 用户已明确允许进入 Skills/MCP 扩展阶段及必要第三方依赖评估。

## 三、Skill 兼容方向

Morrow 优先兼容开放的 Agent Skills 目录模式：一个 Skill 以目录为单位，核心入口为带元数据的 `SKILL.md`，并可包含 scripts、references 和 assets。

Morrow 特有字段应：

- 使用 namespaced frontmatter，例如 `morrow.permissions`；或
- 使用可选 `morrow.yaml` sidecar；
- 不破坏其他 Agent 对标准 `SKILL.md` 的基本读取。

参考规范：

- [Agent Skills](https://agentskills.io/)
- [Model Context Protocol](https://modelcontextprotocol.io/)

这些是兼容目标，不意味着 Morrow 必须采用某个完整第三方运行时。

## 四、Skill 存储与来源

### 4.1 来源分区

建议目录：

```text
~/.morrow/
├── skills/
│   ├── builtin/       # 随 Morrow 发布，只读
│   ├── user/          # 用户手写，Agent 默认不可修改
│   ├── generated/     # Morrow 生成的 Draft/Active Skill，可版本化迭代
│   └── imported/      # 从外部来源安装，保留来源和校验和
└── workspaces/<workspace-id>/
    └── skills/
        ├── user/
        ├── generated/
        └── imported/
```

固定规则：

- Morrow 默认不向用户项目目录写 Skill 文件。
- 可以通过显式配置发现项目内 `.agents/skills`、`.morrow/skills` 等目录，但默认只读，并清楚展示来源。
- 同名 Skill 不静默覆盖；解析优先级和禁用规则必须可见。
- builtin、user、generated、imported 的可修改权限不同。

### 4.2 Skill 身份与版本

```text
SkillDefinition
- id
- name
- description
- source_kind
- scope
- source_locator
- created_at / updated_at

SkillVersion
- skill_id
- version_id             # Morrow 分配的安全不透明 ID，不直接使用外部 version 作路径
- display_version
- tree_checksum
- manifest
- required_capabilities[]
- required_tools[]
- compatible_morrow
- created_from_candidate_id
- created_by
- test_status
- approval_status

SkillBinding
- scope: global | workspace
- scope_id: null | workspace_id
- skill_id
- enabled
- pinned_version_id | null
- selection_mode: explicit | workspace_default | description_match
- revision

SkillSelection
- agent_run_id
- skill_id / version_id
- activation_reason
- content_checksum
```

SkillVersion 一旦获批后不可原地修改。更新产生新版本；某作用域是否启用、pin 或回滚由
SkillBinding 表达；某次 AgentRun 实际使用什么由 SkillSelection 冻结。不要在 SkillVersion 上再
维护一份 `active/disabled` 状态。

## 五、Skill 目录与 Manifest

建议结构：

```text
my-skill/
├── SKILL.md
├── morrow.yaml          # 可选 Morrow 扩展元数据
├── scripts/             # 可选
├── references/          # 可选
├── assets/              # 可选
└── tests/               # 可选本地验证资料
```

`SKILL.md` 负责：

- 名称与简短描述。
- 适用场景与不适用场景。
- 输入、输出和完成标准。
- 操作步骤和错误恢复。
- 必要资源说明。

Morrow 扩展元数据负责：

```text
schema_version
skill_id
version
permissions
required_tools
required_mcp_servers
entry_resources
context_budget
platform_constraints
requested_trust          # 非权威提示
```

不要把工具权限仅写在自然语言中；权限声明必须结构化，并由本地策略取交集。Skill 自报的 Trust
不能成为有效 Trust；有效 Trust 由 Morrow 根据 builtin 发行清单、用户目录、generated 审批记录或
imported 来源/校验和在本地计算，且不能绕过 CapabilityPolicy。

## 六、Skill 发现与渐进式加载

### 6.1 Catalog

启动或工作空间变更时建立 Skill Catalog，只加载：

- ID、名称、描述。
- 作用域、来源、版本、状态。
- 触发提示和能力需求摘要。

不把所有 Skill 正文直接注入上下文。

### 6.2 激活流程

```text
Task / AgentRun
→ 根据用户显式选择、AgentDefinition 或匹配策略得到候选 Skill
→ 检查作用域、版本、状态和依赖
→ 检查能力与权限交集
→ 加载 SKILL.md
→ 按需读取 reference/script 元数据
→ 冻结到 AgentRun SkillSet Snapshot
```

要求：

- 激活原因可解释。
- 每次 AgentRun 记录 Skill ID 与版本。
- Skill 被禁用后，新 AgentRun 不再加载；运行中的快照不被静默改变。
- Skill 内容属于不可信用户/扩展数据，低于固定系统安全边界。

### 6.3 Skill 选择

第一版支持：

- 用户显式指定。
- AgentDefinition 固定启用（Stage 7）。
- 工作空间默认启用。
- 基于描述的保守匹配，并有数量和上下文预算。

不要在本阶段建立复杂学习型路由；Skill 使用结果先形成评估数据。

## 七、Skill 生命周期

### 7.1 用户创建与导入

支持：

```text
morrow skill list
morrow skill show <skill-id>
morrow skill validate <path-or-id>
morrow skill install <path-or-source>
morrow skill enable <skill-id> [--workspace]
morrow skill disable <skill-id> [--workspace]
morrow skill pin <skill-id>@<version>
morrow skill rollback <skill-id>
morrow skill remove <skill-id>
```

- 安装前展示来源、校验和、权限、脚本和依赖。
- 未知来源默认禁用，安装不等于启用。
- 删除 imported/generated 不影响 user/builtin 同名 Skill。

### 7.2 自动生成 Draft

Stage 5 的 SkillCandidate 通过专用服务转为 Draft：

```text
SkillCandidate
→ Draft Generator
→ 生成 SKILL.md + 可选资源
→ 静态校验
→ 权限与敏感信息扫描
→ 示例/测试生成
→ 隔离验证
→ 用户查看 Diff
→ Accept / Edit / Reject
→ Active SkillVersion
```

工具边界建议：

- `propose_skill`：只创建或补充 SkillCandidate。
- `create_skill_draft`：只写 `generated/` Draft 区，必须有候选来源和审批。
- `activate_skill_version`：不暴露给普通 Agent；由 SkillLifecycleService 在用户确认后执行。

普通 Agent 永远不能：

- 修改 builtin Skill。
- 静默修改 user Skill。
- 覆盖 imported Skill 的校验版本。
- 创建同名 Skill 劫持更高优先级来源。
- 通过 Skill 授予自己更多工具权限。

### 7.3 Draft 验证

至少包含：

- Frontmatter/Manifest Schema。
- 名称、版本、目录和引用路径。
- 引用文件不得逃逸 Skill 根目录。
- 脚本和命令静态风险扫描。
- 声明的 Tool/MCP 依赖是否存在。
- 与 Morrow 版本和平台兼容性。
- 样例任务或测试是否通过。
- Prompt Injection、密钥和隐私扫描。

脚本运行必须经过与本地 Shell 相同或更严格的审批与沙箱策略。实现使用专用
`SkillScriptExecutionService`：它以冻结 Skill 根和隔离输出目录为边界，只与本地 Shell 共享更底层
的 ProcessAdapter、超时/取消、脱敏和 CapabilityPolicy，不直接复用工作空间根绑定的
`ProcessExecutionService`。

### 7.4 更新、回滚与退休

- 自动改进只能针对 generated Skill 的新版本 Draft。
- 每次更新显示与当前 Binding 选定版本的 Diff。
- 用户可 pin 版本，阻止自动切换。
- 失败率上升时提示回滚，不自动删除历史版本。
- 长期未使用可以建议 retired，但不自动删除。

## 八、Skill 评估

每次使用记录：

```text
SkillUsage
- skill_id / version
- task_id / agent_run_id
- activation_reason
- result_status
- user_correction
- token / time / tool metrics
- test_artifact_ids[]
- failure_category
```

评估问题：

- Skill 是否被正确触发？
- 是否减少探索和重复提示？
- 是否提高成功率或验证率？
- 是否造成错误路径、过度步骤或权限请求？
- 新版本是否优于旧版本？

没有对照数据时，不得仅因为“经常使用”就自动改写 Skill。

## 九、MCP 接入

### 9.1 MCP Client 与 Server 配置

```text
McpServerDefinition
- id
- transport
- command_or_endpoint
- credential_refs[]
- workspace_visibility
- local_review_status
- enabled
- timeout_policy
- capability_policy
```

支持的传输和 SDK 在实施 ADR 中确认。第一版只选择一个最容易离线测试和隔离的传输方式。

### 9.2 工具发现与命名空间

- MCP 工具使用稳定且兼容 Provider 工具名约束的命名空间，例如
  `mcp__<server-slug>__<tool-slug>`；超长或 slug 碰撞用确定性 digest 后缀消歧，并保留远端原名映射。
- 发现结果进入本地 Tool Catalog，再冻结进 AgentRun ToolSet。
- 同名工具不覆盖本地工具。
- MCP Schema 转换失败只隔离该工具或 Server。
- Server 描述和工具注解不被视为可信安全策略。

### 9.3 权限和审批

MCP 工具必须映射到 Morrow 的本地能力模型：

- read / persistent_write / destructive / external_effect。
- 工作空间可见范围。
- 网络与凭据使用。
- 审批要求。
- 超时、取消、结果限制和审计。

若 MCP 声明“只读”，但本地策略无法验证，仍以 Morrow 配置为准。

每次 MCP 调用同时形成两类风险事实：

1. **Launch Intent**：Server 的 executable/argv、隔离、网络、凭据和工作空间外访问；
2. **Tool Intent**：本次远端工具的 read/write/destructive/external-effect 语义。

二者都由同一 CapabilityPolicy 评估，最终决定按 `DENY > REQUIRE_APPROVAL > ALLOW` 合并。
本地 MCP 配置和审核快照只提供事实证据，不是第二套授权权威；真正授权仍由
`PermissionSnapshot + CapabilityPolicy + ApprovalService` 决定。

第一版中，只有与 AgentRun/Server/配置/Catalog/工具完全匹配的本地审核证据，才能把 MCP 的
network、credential、loopback 或 external-effect 从硬拒绝提升为逐调用审批，不能直接允许；
destructive、outside-workspace、privilege escalation 和 git write 仍拒绝，bundled policy 默认值
不变。

### 9.4 进程与故障隔离

- MCP Server 启动、停止和崩溃不破坏主进程。
- 为连接、工具调用和关闭设置超时。
- stdout/stderr 进入脱敏诊断，不混入工具结果。
- 失败 Server 自动标记 degraded，不无限重启。
- 运行中的 AgentRun 使用冻结的工具快照；Server 消失时形成普通工具错误。
- MCP 调用不自动重试，即使工具被标记为 read；handler entry 后丢失结果统一进入现有
  `outcome_unknown/requires_reconciliation` 恢复语义。
- 第一版结果适配覆盖 text、image、audio、resource link、embedded resource 和 structured
  content；二进制进入 ArtifactStore，模型上下文只接收受限引用和摘要。

## 十、Provider 与模型扩展

延续现有：

- `ModelProvider`
- `ProviderFactory`
- `AdapterRegistry`
- 动态 Provider ID
- `ModelRef`

Stage 6 完成：

```text
morrow provider add --adapter <adapter-id> --name <provider-id>
morrow provider remove <provider-id>
morrow model add --provider <provider-id>
morrow model sync --provider <provider-id>
morrow model show <provider-id>/<model-id>
morrow model use <provider-id>/<model-id>
morrow model remove <provider-id>/<model-id>
```

能力描述分为 Adapter 默认能力和精确 Model 覆盖，至少包括：

- 流式文本。
- Tool calling 协议。
- 多工具调用。
- 结构化输出。
- 上下文上限。
- 支持的输入类型。
- 成本元数据来源与更新时间。

固定规则：

- 不建立与 `active_model` 冲突的“默认 Provider”。
- 不静默故障切换到另一模型。
- 自动模型路由留到有评估数据后；Stage 7 的 AgentDefinition 可以显式选择不同 ModelRef。
- 新 Adapter 不修改 Session、Task 或 AgentLoop 核心。

## 十一、扩展治理

### 11.1 Trust Level

建议：

```text
builtin
trusted_local
user_authored
verified_import
unknown_import
```

Trust Level 影响默认启用、审批、脚本执行和更新策略，但不绕过全局安全边界。

Trust Level 的有效值只由 Morrow 本地 provenance 和生命周期记录产生；Skill 包中的声明只能作为
requested hint，不能提升自身 Trust。

### 11.2 冲突与依赖

- Skill ID、版本和来源冲突有确定性解析。
- 缺失工具、MCP Server 或平台依赖时，Skill 显示 unavailable，不导致主进程失败。
- 循环依赖和 Skill 互相注入默认不支持。
- 更新扩展前检查依赖兼容，并支持回滚。

### 11.3 供应链安全

- imported Skill 保存来源 URL/路径、校验和和安装时间。
- 自动更新默认关闭。
- 脚本内容和权限变化要求重新审批。
- 诊断包不打包凭据、私有 Skill 内容或完整脚本，除非用户明确选择。

### 11.4 持久化与备份边界

- Provider/Model、SkillBinding 和 MCP desired state 使用 versioned YAML；CredentialStore 仍是
  secret authority。
- Skill 包使用 managed filesystem version 与逐文件/树摘要；Catalog、Draft、Usage 和 AgentRun
  扩展证据使用 SQLite。
- AgentRun 主快照只保存 Skill/MCP 证据引用和 digest，完整 Skill context 放入专用、受预算的运行
  表，不能依赖一个正文字符上限推断 64 KiB 主快照必然合法。
- Stage 6 新增显式 当前完整 Backup bundle，覆盖 SQLite、Artifact、Extension YAML 和被引用的 managed
  Skill versions；现有 v1 的含义和 verifier 保持不变，CredentialStore 永不打包。
- 安装、加载和执行都校验 canonical package tree 与文件摘要；symlink、路径逃逸和检查后替换失败
  关闭。

## 十二、实施切片（Subplans 63–75 已完成）

### 6A：Skill 规范、Catalog 与加载

- 目录、Manifest、来源、版本和状态。
- Catalog、渐进式加载和 AgentRun SkillSet Snapshot。
- 手工 Skill 的 validate/enable/disable。

### 6B：Skill 生命周期与 Draft

- SkillCandidate → Draft。
- Generated 目录、Diff、验证、批准和回滚。
- SkillUsage 与评估。

### 6C：MCP Client 与安全适配

- 首个 Transport。
- Server 生命周期、工具发现、命名空间。
- Tool Policy 映射、审批、超时和故障隔离。

### 6D：Provider/Model 控制面

- 自定义 Adapter/Provider/Model 管理。
- 能力探测和配置迁移。
- 第二个真实或完整 Fake Adapter 验收。

### 6E：扩展治理与综合验收

- 来源、版本、冲突、依赖和供应链安全。
- 一个手写 Skill、一个 Generated Skill Draft、一个 MCP 示例和一个额外 Provider 示例。

## 十三、暂不包含

- 托管式公开 Skill/插件市场。
- 未经确认自动启用 generated/imported Skill。
- Agent 自动修改 user/builtin Skill。
- Skill 作为绕过本地权限的可执行插件。
- 多 Agent Workflow 和自适应编排。
- 后台自动更新和无人值守脚本执行。
- 自动模型路由、故障切换和成本优化器。
- 默认捆绑大量第三方集成。

## 十四、阶段交付物

- 兼容开放目录格式的 Skill 规范与加载器。
- Skill Catalog、作用域、版本、来源与渐进式激活。
- Draft、验证、审批、Active、更新、回滚和退休生命周期。
- `propose_skill` / `create_skill_draft` 的受限能力。
- SkillUsage 与版本评估。
- MCP Client、Server 配置、命名空间与安全适配层。
- 扩展后的 Provider/Model 管理。
- 扩展冲突、依赖、故障隔离和供应链治理。
- 示例 Skill、Generated Draft、MCP Server 和额外 Adapter。
- 相关 CLI、Query、事件、文档和 acceptance。

## 十五、验收场景

### 15.1 手写 Skill

用户创建一个合法 `SKILL.md`，验证后只在指定 Workspace 启用。新 AgentRun 能看到该 Skill，其他 Workspace 不加载。

### 15.2 自动 Skill Draft

多个已接受任务形成 SkillCandidate。Morrow 生成 Draft、展示 Diff 和权限；用户编辑后批准。未批准前任何 AgentRun 都不会自动使用。

### 15.3 Skill 回滚

新版本导致 Fixture 任务失败率上升。用户 pin/rollback 到旧版本，后续 AgentRun 使用旧快照，历史 Task 仍指向当时版本。

### 15.4 来源保护

Generated Skill 尝试与 user Skill 同名，不会静默覆盖；系统显示冲突并要求改名或显式选择。

### 15.5 MCP 故障

MCP Server 在调用中崩溃。对应工具形成受限错误，AgentRun 可以恢复；主进程、Session 和其他工具保持可用。

### 15.6 MCP 权限

未知 MCP 工具声称只读但配置为 external_effect。Morrow 采用本地风险策略，要求审批或拒绝。

### 15.7 新 Provider

注册第二个 Adapter/Provider 后，可以在不修改 AgentLoop、TaskStore 或 SessionStore 的情况下调用模型并冻结能力快照。

## 十六、测试与验证门禁

- Skill Manifest、路径、引用、版本和冲突测试。
- Catalog 发现、缓存失效和作用域隔离测试。
- SkillSet Snapshot 可复现性测试。
- Draft 静态扫描、权限变化和回滚测试。
- Generated/User/Builtin/Imported 修改权限测试。
- MCP Fake Server 合同、超时、崩溃、Schema 错误和取消测试。
- MCP 工具统一 ToolCycle、审批和结果限制测试。
- Provider Adapter 合同与能力探测测试。
- 扩展损坏不影响核心启动的隔离测试。
- 默认离线；真实 MCP/Provider 集成只在显式授权下运行。

## 十七、阶段指标

- Skill 激活准确率、使用成功率和用户纠正率。
- Generated Draft 接受、编辑后接受和拒绝比例。
- 新 Skill 版本相对旧版本的成功率、成本和时间变化。
- MCP 工具失败后 Agent 恢复率。
- 扩展导致主进程失败的数量，目标为 0。
- 权限变化被重新审批的覆盖率。
- Provider Adapter 合同通过率。

## 十八、主要风险与缓解

| 风险 | 缓解 |
|---|---|
| 自动 Skill 把一次任务过拟合成永久流程 | 多任务 Evidence、Draft、测试、用户批准、版本评估 |
| Skill 内容成为 Prompt Injection | 低于系统边界、来源可见、按需加载、敏感扫描 |
| 脚本绕过 Shell 策略 | 专用 SkillScriptExecutionService，共享底层进程适配并统一走 CapabilityPolicy |
| 同名 Skill 劫持 | 稳定 ID、来源分区、显式冲突处理 |
| MCP 注解被错误信任 | 本地风险映射和 deny-wins |
| 扩展故障拖垮主进程 | 进程/错误隔离、超时、degraded 状态 |
| Provider 扩展污染核心 | Adapter 合同、动态注册、能力快照 |

## 十九、阶段完成标准

1. 用户能创建、验证、安装、启用、停用、pin、回滚和删除 Skill。
2. AgentRun 只加载相关、Active、权限允许的 Skill，并记录精确版本。
3. SkillCandidate 只能创建 Draft；未经批准不会成为 Active。
4. Agent 不能静默修改 user/builtin/imported Skill 或提升自身权限。
5. MCP 工具沿用同一 ToolExecutor、审批、预算、取消和审计规则。
6. MCP Server 或扩展失败不会损坏主状态或阻断无关能力。
7. 新 Provider/Model Adapter 不需要修改 AgentLoop、Session 或 Task 核心。
8. 权限、来源、版本、依赖、失败和更新 Diff 对用户可见。
9. 至少一个手写 Skill、一个 Generated Draft、一个 MCP 示例和一个额外 Adapter 通过验收。

Stage 6 完成后，Morrow 才具备构造可配置 AgentDefinition 和可复用 Workflow 节点所需的能力模块。
