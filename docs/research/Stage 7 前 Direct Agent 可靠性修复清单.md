# Stage 7 前 Direct Agent 可靠性修复清单

> 状态：S7P-00 至 S7P-09 已闭环；S7P-10 的初始 CONDITIONAL GO 已于 2026-08-30 经定向证明升级为 GO
>
> 日期：2026-08-26
>
> 范围：只处理会阻塞 Stage 7、破坏单 Agent 基本功能，或使简单/复杂代码任务无法稳定完成的问题
>
> 输入：[Morrow Agent 深度审计与可执行修复清单](<Morrow Agent 深度审计与可执行修复清单.md>)、[Stage 7 前置 Direct Agent 基线](../acceptance/stage7-direct-agent-baseline.md)、当前代码与离线验证结果
>
> 后续事项：[Morrow Agent 未来修复清单](<Morrow Agent 未来修复清单.md>)
>
> 当前结论：[S7P-10 Stage 7 进入审查](../acceptance/s7p-10-stage7-entry-review.md)的
> 定向条件已由 [GO 升级证明](../acceptance/s7p-10-go-upgrade-proof.md)满足。Stage 7 生产总计划已激活，
> 但生产代码尚未开始；Subplan 1 仍等待用户显式授权。

## 一、这份清单要达成什么

完成本清单后，Morrow 才允许进入 Stage 7。进入条件不是“功能看起来齐全”，而是同时满足：

1. Stage 7 可以把现有 AgentLoop 当作可靠的单 Agent 叶子执行器，不需要先修补工具协议、工作区写入、停止判断或诊断链路。
2. Direct Agent 的普通对话、读取、搜索、编辑、删除、移动、命令、Git 检查、审批、取消、恢复和长上下文路径全部能够正常工作。
3. 简单和中等代码任务可重复完成；较复杂任务至少具备稳定的完成能力，而不是偶然成功。
4. 失败可以被准确归因到模型、工具、预算、环境或任务语义，不能再用推测解释结果。
5. Stage 7 的 Direct/Workflow 对照拥有固定、可重复、包含成本与返工数据的基线。

本清单不是“追平所有成熟 Code Agent”的功能列表。Repo map、LSP、后台 Shell、跨平台、完整 SDK、GUI 等有价值但不阻塞 Stage 7 的能力，统一放入未来清单。

### 1.1 就绪结论的权威关系

- 原深度审计保留为问题输入和决策历史；本清单取代其中的优先级与修复顺序。
- 原 Direct Agent 基线保留为不可变证据快照；其中基于“基本工具已可用”得出的“Stage 7 可以开始”不再是当前就绪结论。
- 当前用户已把进入标准提高为“不会阻塞 Stage 7、单 Agent 全功能正常、稳定完成简单至较复杂任务”。因此只有 S7P-10 可以给出新的 GO。
- 未来实施若发现代码、测试或新评测与本文事实冲突，应更新本文，不为维持清单结论而忽略新证据。

## 二、事实基线

### 2.1 已验证事实

| 项目 | 当前事实 | 含义 |
|---|---|---|
| 当前离线回归 | 1079 passed、2 skipped、2 deselected | 既有单元/集成合同总体稳定，但不能证明真实复杂任务可用 |
| Direct 复杂任务复测 | 2 PASS / 7 FAIL / 1 BLOCKED | 当前不能作为“稳定复杂代码 Agent”进入 Stage 7 |
| 工具调用 | 311 次；257 succeeded、38 failed、16 denied | 54 次非成功，不是 54 次执行失败 |
| 参数错误 | 30 次 invalid_arguments，其中 run_command 23 次 | 工具可发现不等于模型可可靠调用，参数合同是首要缺口 |
| 任务写入 | 6 个 Morrow 历史任务均未产生预期修改 | Agent 在理解或执行闭环前耗尽轮次或失败 |
| 轮次上限 | MORROW-002 至 MORROW-005 达到 30 轮上限 | 30 轮已成为实际终止点；本清单决定将候选上限一次性放宽到 60，再用数据判断收益 |
| 意外文件 | 复测产生 6 个非预期文件 | 工作区纪律、任务理解或完成判断存在问题 |
| 变更能力 | create、patch、replace 已有；delete/move/rename 未闭环 | “没有 ChangeSet”不准确，但基本文件生命周期不完整 |
| Linux 沙箱 | Bubblewrap 后端已有实现，探测固定为不支持 | 属于未验证/未启用，不是完全未实现 |
| 上下文 | 当前按字符预算清空旧工具结果并丢弃历史 | 有压缩缺口，但本轮失败是否由压缩直接导致尚无证据 |
| 使用量 | Provider usage-only chunk 被忽略，AgentRun 无 token/cost | 无法满足 Stage 7 的成功率、成本、返工基线 |
| 验证状态 | 任意成功退出的命令都可能使 validation_passed 为真 | 当前“验证通过”指标可能是假阳性 |
| 项目指令 | Direct prompt 不自动发现 AGENTS.md/CLAUDE.md | 六个 Morrow 评测工作区均有 AGENTS.md，Agent 却未获得这层约束 |
| Pi 对照 | 尚无同模型、同环境、同任务、同预算的正式 A/B | 只能比较能力面，不能宣称质量已达到或未达到 Pi |

### 2.2 尚未被证实的因果关系

以下内容只能作为待验证假设，不能直接写成修复结论：

- “30 轮预算就是失败主因”——仍缺少因果证据；将上限放宽到 60 是减少过早终止风险的工程决策，不等于根因结论。
- “上下文截断就是所有复杂任务失败主因”——缺少每轮上下文事件和截断前后证据。
- “并行工具调用会显著提高成功率”——现有数据只支持可能降低延迟，不支持减少模型轮次。
- “多 Agent 会自然解决 Direct Agent 的失败”——Stage 7 仍复用同一个 AgentLoop，叶子执行器问题会被放大。
- “增加更多工具即可解决问题”——目前最突出的证据是现有工具合同和闭环不可靠。

## 三、纳入与排除规则

### 3.1 必须在 Stage 7 前完成

一个事项满足以下任一条件即进入本清单：

- Stage 7 的 AgentDefinition、Direct Workflow 或 Workflow Node 会直接依赖它。
- 缺失会让常见代码修改无法安全完成。
- 缺失会造成模型反复调用错误工具、虚假完成或无法恢复。
- 缺失会让评测结果无法归因、无法复现或无法比较。
- Pi 的基本单 Agent 工作方式依赖该能力，且 Morrow 当前不存在等价路径。

### 3.2 明确不在本轮扩张

- 不以一次失败为理由引入完整 IDE/LSP、AST 编辑或 Repo map。
- 不在没有数据前移除所有硬预算或改成无界循环。
- 不以“成熟项目有”为理由引入 MCP 之外的任意网络、浏览器或云能力。
- 不把 Stage 7 的 Workflow、Reviewer、Planner 或多 Agent 本身提前塞入 AgentLoop。
- 不改变公开事件生命周期、运行策略默认值或增加第三方依赖，除非在对应实施项开始前单独批准。

## 四、修复顺序总览

| 顺序 | 编号 | 工作包 | 为什么必须按此顺序 |
|---:|---|---|---|
| 0 | S7P-00 | 冻结评测与失败分类 | 先固定尺子，避免修复后只得到不可比较的新结果 |
| 1 | S7P-01 | 建立安全的 AgentRun 可观测性与无头执行入口 | 后续所有修改都必须能看到真实参数错误、上下文、用量和停止原因 |
| 2 | S7P-02 | 修复模型可见工具 Schema 与参数恢复合同 | 当前最大、最直接的工具失败来源 |
| 3 | S7P-03 | 建立 Direct Coding Agent 提示与项目指令装配 | 让模型知道如何勘察、修改、验证并遵守仓库规则 |
| 4 | S7P-04 | 补齐基本工作区变更生命周期 | delete/move/rename 不闭环会直接阻塞真实重构和清理任务 |
| 5 | S7P-05 | 修复验证真实性与完成判定 | 防止“命令成功”等价于“任务完成” |
| 6 | S7P-06 | 冻结双倍轮次预算并修复上下文、无进展循环 | 先有观测，再以 60 轮固定候选验证收益和停止质量 |
| 7 | S7P-07 | 补齐模型调用韧性与运行中控制 | 避免瞬态 Provider 故障或无法注入纠正导致整项任务报废 |
| 8 | S7P-08 | 单 Agent 功能矩阵回归 | 证明所有基本功能路径都正常，不只证明评测样本通过 |
| 9 | S7P-09 | 重复复杂任务评测与同条件 Pi 对照 | 冻结 Stage 7 需要的成功率、成本和返工基线 |
| 10 | S7P-10 | Stage 7 进入审查 | 只在所有硬门禁闭环后给出新的 GO/NO-GO |
| 10 | S7P-10 | Stage 7 进入审查 | 只有全部硬门禁通过后才开始 Workflow 实现 |

S7P-01 至 S7P-05 不应并行大范围改动 AgentLoop。它们分别先稳定观测、合同、提示、变更和完成语义，再处理策略优化，能避免把不同原因的失败混为一谈。

## 五、逐项修复清单

### S7P-00：冻结评测协议与失败分类

**优先级：P0；依赖：无**

#### 已存在的问题

- 当前报告包含首次运行、修复后复测、外部 Agent 结果和 verifier 结果，但部分统计口径混合。
- 54 次非成功实际由 38 failed 和 16 denied 构成；安全拒绝不能算成工具实现失败。
- 每个任务只运行一次，无法区分稳定能力和偶然采样。
- 当前没有固定的 Provider/model revision、采样参数、工作区哈希、工具集快照、预算快照和执行版本清单。

#### 为什么会阻塞 Stage 7

Stage 7 的完成标准要求证明 Workflow 相比 Direct 有量化收益。如果 Direct 基线本身不可复现，后续任何收益声明都不可信。

#### 解决方向

- 为每次评测生成不可变 Run Manifest：代码 commit、脏工作区摘要、评测集 revision、模型、Provider、参数、工具清单、权限策略、预算、系统提示版本和项目指令来源。
- 固定任务结果分类：
  - PASS：外部 verifier 全部通过且工作区无非预期修改。
  - FAIL_MODEL：工具可用、环境正常，但推理或实现不正确。
  - FAIL_TOOL_CONTRACT：模型按公开合同仍无法合法调用。
  - FAIL_RUNTIME：AgentLoop、持久化、恢复或事件生命周期失败。
  - DENIED_POLICY：策略按设计拒绝，单独统计。
  - BLOCKED_ENV：Provider、凭据、平台或依赖环境阻塞。
  - BUDGET_EXHAUSTED：独立分类，不直接等同模型失败。
- 保存 verifier 原始结果、Git diff 摘要、预期/意外文件清单和结构化停止原因。
- 每个基线任务至少重复两次；不以单次成功认定稳定。

#### 验收条件

- [ ] 同一 Run Manifest 可在新临时工作区重建等价测试条件。
- [ ] 汇总数字可以从原始 Run 记录机械推导，failed、denied、blocked 不再混用。
- [ ] 每个任务都有外部 verifier、工作区 diff 和停止原因。
- [ ] 基线阈值在任何修复前冻结，修复期间不临时降低。

---

### S7P-01：建立安全的 AgentRun 可观测性与无头执行入口

**优先级：P0；依赖：S7P-00**

#### 已存在的问题

- AgentRun 没有持久 token、输入/输出用量和成本字段。
- OpenAI-compatible adapter 会忽略不含 choices 的 usage-only chunk。
- ContextPack 虽计算 estimated_request_chars、cleared tool results 和 dropped turns，但没有进入可评测记录。
- durable tool result 只保留 error_code 和字符摘要；参数校验的字段路径、错误类型等安全诊断未持久化。
- CLI 没有稳定的非交互 run + JSONL 事件入口，批量评测依赖终端驱动，容易引入交互噪音。
- 当前公开事件不能直接承载完整工具参数、结果、reasoning 或 traceback；这一安全边界必须保留。

#### 为什么会阻塞 Stage 7

没有这些数据就无法判断失败来自工具合同、上下文、预算还是模型，也无法建立 Stage 7 要求的成本基线。Workflow 会产生更多 AgentRun，诊断缺口会按节点数放大。

#### 解决方向

- 在 Provider Adapter 到 AgentRun 指标链路中保留标准 usage 事件；对不提供 usage 的 Provider 明确标记 unavailable，而不是填零。
- 为每轮模型请求记录安全指标：估算/真实 token、上下文预算占比、清空工具结果数、丢弃 turn 数、工具轮次、模型尝试和停止原因。
- 将 Pydantic 校验错误转换成有界、可脱敏、模型可恢复的结构化诊断，例如字段路径、错误类型、允许的形状；不得保存原始敏感参数。
- 提供脚本友好的无头执行入口，复用 AgentLoop、Application Service 和同一事件投影，不建立第二套运行状态机。
- 支持将安全事件输出为 JSONL 或等价机器可读格式；新增公开事件类型或字段前执行单独的生命周期兼容审查。
- 为评测输出 run_id，允许从 Operational Store 查询对应 Session、TaskRun、AgentRun、ToolExecution 和指标。

#### 验收条件

- [ ] scripted Provider 的 usage-only chunk 能进入 AgentRun 汇总，且不产生额外文本。
- [ ] 每个模型请求可查询预算、截断/清理、工具轮次和 stop code。
- [ ] invalid_arguments 记录足以回答“哪个字段、什么类型、为何非法”，但不泄漏值。
- [ ] 无头入口与交互入口对相同 scripted Provider 产生等价 ConversationLog 和终态。
- [ ] 取消、失败、预算耗尽和正常完成的每个 tool_call 都有唯一终态。
- [ ] YAML、事件、数据库普通字段和终端均不出现凭据、reasoning、完整工具参数/结果或 traceback。

---

### S7P-02：修复模型可见工具 Schema 与参数恢复合同

**优先级：P0；依赖：S7P-01**

#### 已存在的问题

- RunCommandArguments 的 argv 和 shell 在生成的 JSON Schema 中都可省略；“必须且只能提供一个”的约束只在运行时 model_validator 执行。
- 评测中 30 次 invalid_arguments 有 23 次来自 run_command。
- read_file、search_text 和 write_file 也出现参数错误，但当前持久证据不足以准确判断具体字段。
- 描述文字、Provider 可见 Schema、Pydantic 运行时验证和 handler 实际语义不是同一个可验证合同。

#### 为什么会阻塞 Stage 7

Stage 7 的每个节点仍调用同一工具。Schema 不可满足或约束隐藏会让 Explore、Coder、Reviewer 都重复失败，增加成本、轮次和恢复复杂度。

#### 解决方向

- 建立 Tool Contract Audit，逐个比较：
  1. Provider 可见 JSON Schema；
  2. 参数模型验证；
  3. Capability 声明；
  4. handler 语义；
  5. 错误 envelope。
- 对 one-of 参数关系使用 Provider 确实支持的 Schema 表达；如果兼容性不足，拆成语义单一的工具或设计显式 discriminated shape，不仅依赖描述文字。
- 为 path、line range、query、replacement、command 等常用参数统一命名、类型和错误语义。
- 对可恢复错误给出短、明确、可操作的反馈；同一错误重复时纳入无进展检测。
- 用实际发送给 Provider 的最终 Schema 做快照/合同测试，不只测试 Pydantic 模型。
- 针对支持差异大的 Provider 建立 capability negotiation 或保守 Schema 子集；Stage 7 前只要求当前受支持 Provider 路径可靠。

#### 验收条件

- [ ] 所有注册工具的 Provider Schema 均能表达 handler 的必需字段和互斥关系，或有经测试的等价拆分。
- [ ] 空 run_command、同时给 argv/shell、错误路径类型等均在模型调用前或首次执行时返回确定诊断。
- [ ] scripted Provider 测试证明 Agent 能读取错误反馈并在下一次调用纠正参数。
- [ ] 完整评测中 invalid_arguments 比例不高于全部工具调用的 1%，且没有任务因同一参数错误重复三次而终止。
- [ ] denied、invalid_target、not_found、search_failed 和 execution_failed 保持不同错误码。

---

### S7P-03：建立 Direct Coding Agent 提示与项目指令装配

**优先级：P0；依赖：S7P-02**

#### 已存在的问题

- 当前 Direct system prompt 主要描述安全、真实性和工具边界，没有完整代码任务工作协议。
- 没有自动发现和装配工作区 AGENTS.md 或兼容的 CLAUDE.md。
- 六个 Morrow 评测工作区都包含根 AGENTS.md，但 Direct Agent 没有自动读取。
- 外部成熟 Code Agent 通常提供最小但明确的勘察、编辑、验证、停止和仓库指令规则；Pi 会自动加载项目指令。

#### 为什么会阻塞 Stage 7

Stage 7 的 AgentDefinition role_prompt 建立在固定安全提示和 ContextPolicy 之上。如果 Direct Coding profile 尚未稳定，未来每个角色都要重复修补基本行为，且 Direct Workflow 迁移会发生能力回退。

#### 解决方向

- 新增明确的 Direct Coding profile，由固定安全层、代码任务工作协议、项目指令、Skill/Memory/Preference 和用户输入分层装配。
- 工作协议至少包含：
  - 修改前先定位相关文件和现有约束；
  - 使用读取/搜索结果形成最小修改面；
  - 保护用户已有改动；
  - 修改后运行与风险相称的验证；
  - 不把“工具调用成功”当作“任务完成”；
  - 在无法完成时报告具体 blocker 和已验证事实；
  - 不制造计划、报告、临时脚本等非预期文件。
- 实现从工作区根到目标文件目录的项目指令解析，定义优先级、作用域、大小上限、冲突和符号链接边界。
- 兼容 AGENTS.md 为必需；是否兼容 CLAUDE.md、项目自定义名称可独立配置，但不得隐式执行文档中的 Shell。
- 将解析后的来源、hash 和版本写入 AgentRun context snapshot，不持久化秘密内容。
- 保证固定安全边界高于用户可编辑 role_prompt 和项目指令。

#### 验收条件

- [ ] 根 AGENTS.md 会自动进入相关代码任务上下文。
- [ ] 嵌套指令只影响其目录作用域，冲突优先级有确定性测试。
- [ ] 超大、损坏、越界或符号链接指令文件 fail closed，并给出有界诊断。
- [ ] 项目指令不会被当作命令自动执行，不扩大工具权限。
- [ ] 评测变更任务不再生成说明文件或临时脚本，除非任务明确要求。
- [ ] Direct Coding profile 可由未来 AgentDefinition 复用，而不是硬编码进 Workflow。

---

### S7P-04：补齐基本工作区变更生命周期

**优先级：P0；依赖：S7P-02、S7P-03**

#### 已存在的问题

- 结构化 MutationOperation 目前覆盖 create、patch、replace。
- 沙箱变更检测能够识别 deleted，但 promotion eligibility 只覆盖 created/modified。
- run_command 将 rm、mv、cp 等视为破坏性命令并进入沙箱；删除结果无法正常推广。
- 因此“没有 ChangeSet”不准确，真实缺口是删除、移动、重命名及跨操作提交没有完整安全闭环。

#### 为什么会阻塞 Stage 7

复杂代码任务经常需要删除废弃文件、重命名模块、移动测试或清理生成物。缺少这些基本操作会迫使模型绕路、反复失败，或留下错误文件。Stage 7 的单 Writer 节点也需要明确的变更集语义。

#### 解决方向

- 为 delete、move、rename 建立结构化操作；复制是否作为独立操作由实际任务证据决定。
- 定义每种操作的：
  - 目标规范化与工作区边界；
  - 是否允许目录；
  - 符号链接行为；
  - 覆盖/冲突策略；
  - 审批等级；
  - 沙箱预览；
  - durable intent-before-effect；
  - 崩溃后的 outcome 分类。
- 扩展沙箱 promotion，使已批准的删除和移动可以原子应用；不得把路径缺失静默视为成功。
- 组合操作需要稳定顺序和失败语义。Stage 7 前至少保证单次操作可恢复、多个操作不会伪造原子成功；完整 run-level undo 可后移。
- Git 继续作为代码恢复来源；不得用隐式 reset 或 checkout 覆盖用户未提交改动。

#### 验收条件

- [ ] create、patch、replace、delete、move、rename 均有成功、冲突、越界、符号链接、取消和恢复测试。
- [ ] 沙箱预览与 promotion 后的 diff 一致，删除不会在 promotion 时丢失。
- [ ] 移动目标已存在、源在操作前后变化、部分失败等情况不会伪造成功。
- [ ] 用户已有脏改动保持可识别且不会被静默覆盖。
- [ ] 评测任务可删除或重命名文件，并由 verifier 检查最终树。
- [ ] 所有副作用继续经过统一 ToolExecutor、CapabilityPolicy、审批和 durable execution。

---

### S7P-05：修复验证真实性与任务完成判定

**优先级：P0；依赖：S7P-04**

#### 已存在的问题

- 当前 validation_passed 可能因任意一个退出码为 0 的命令而置真，即使命令只是 ls、pwd 或 cat。
- AgentLoop 接受模型最终 stop，没有独立检查任务要求的文件是否修改、测试是否执行或 verifier 是否满足。
- 复杂任务中出现“没有写入但结束”“写入非预期文件”和“达到轮次上限”。

#### 为什么会阻塞 Stage 7

Workflow Reviewer 不能建立在虚假的叶子节点完成状态上。否则 Scheduler 会把未完成 Coder 节点当作成功并继续传播错误 Artifact。

#### 解决方向

- 将 CommandToolFact 的“命令成功”与 ValidationFact 分离。
- 只有匹配验证命令分类或明确验证声明的命令，才能贡献 validation_passed；记录命令类型、scope、退出码和证据摘要。
- 在任务开始时形成轻量 Outcome Contract：
  - 是否预期写入；
  - 目标/禁止路径；
  - 用户要求的测试；
  - 可选 verifier；
  - 是否允许仅解释不修改。
- 模型准备停止时执行完成检查：
  - change task 是否有相关 diff；
  - 是否存在未闭合工具调用；
  - 必需验证是否运行并通过；
  - 是否出现非预期文件；
  - 是否仍有已知失败。
- 完成检查只返回事实和下一步，不让运行时自行猜测业务正确性。允许模型在剩余预算内纠正一次或按明确 stop code 结束。
- verifier 是评测权威；生产环境无 verifier 时，必须把“已执行验证”和“推断完成”区分展示。

#### 验收条件

- [ ] ls/pwd/echo 成功不能令 validation_passed 为真。
- [ ] pytest、ruff、编译等验证命令按声明 scope 形成独立事实。
- [ ] 明确要求改代码但 diff 为空时，Agent 不能以普通 success 结束。
- [ ] 必需测试失败时，最终结果不能声明“全部通过”。
- [ ] 非预期文件、未闭合 tool_call 和预算耗尽有独立 stop/result 状态。
- [ ] completion check 不新增第二个聊天历史写入者，ConversationLog 仍仅由 Session 拥有。

---

### S7P-06：采用 Pi 长时任务循环并修复上下文、重试与输出边界

**优先级：P0；依赖：S7P-01、S7P-02、S7P-05**

#### 历史问题与当前结论

- 默认最大工具轮次为 30，四个历史任务曾达到上限；该数值属于历史 v1 运行证据，不是
  v2 的目标预算。
- 旧实现按字符预算清空旧工具结果并丢弃旧 turn，没有语义摘要、精确 token window 或
  可恢复的 Artifact continuation。
- 旧实现缺少 Pi 风格的 context-overflow recovery、Provider retry/backoff 和工具族输出
  截断边界。
- 重复模式和推断无进展不能成为 v2 的默认终止条件；它们保留为 v1 历史兼容字段或观测，
  不得以别名重新启用任务级 stop。

#### 已冻结的当前决策

- 新 v2 运行继续直到模型返回无工具的正常 stop、abort 或 error；不设置累计 model request、
  tool round、tool call、task-time、重复或 inferred-no-progress stop。
- v2 必须绑定 exact model `context_window_tokens`，按 Pi 的
  `contextTokens > contextWindow - reserveTokens` 触发压缩，默认
  `reserveTokens=16384`、`keepRecentTokens=20000`。
- 压缩写入不可变 `pi_compaction` ContextCheckpoint，保留结构化摘要、来源 digest、完整
  ToolCycle 边界、recent tail 和累计文件列表；原始 ConversationLog 不被覆盖。
- Provider retry 默认最多 3 次，退避 2/4/8 秒，provider delay 上限 60 秒；上下文溢出只走
  一次压缩恢复。read/search 使用 2000 行/50 KiB/500 字符的 Pi 对应边界，命令输出使用
  tail 截断并提供安全 Artifact continuation。
- v1 snapshot/resume 继续使用历史有界字段；显式 v2 缺少精确 context capability 时失败，
  不回退到猜测的小型字符窗口。评测 watchdog 仍属于外部 S7-09 harness。

#### 为什么会阻塞 Stage 7

Stage 7 会将预算下发到每个 AgentRun，并聚合 Workflow 总预算。如果单 Agent 的预算和压缩行为未经测量，Workflow Compiler 无法给出合理策略，失败也无法归因。

#### 解决方向

本项已按以下顺序完成：

1. 以确定性 Provider/fake clock fixture 验证了长于 120 次模型请求、512 次工具调用、3600 秒
   假时间以及重复循环后成功 stop 的轨迹。
2. 实现了显式 v1/v2 RunPolicy、exact capability admission、v1 resume 兼容和无默认累计/无进展
   终止；旧 v1 字段不以 sentinel integer 表示 v2 的 unlimited。
3. 为被压缩的旧内容生成有界、可验证的结构化摘要，至少保留：
   - 用户目标与约束；
   - 已查看文件和关键发现；
   - 已做变更及 diff 摘要；
   - 已运行验证及结果；
   - 未解决错误与下一步；
   - 安全的工具错误诊断。
4. 摘要具有来源范围和 hash，可通过 immutable checkpoint 重建而不覆盖原始 durable 事实；不
   保存 reasoning、秘密或完整敏感参数。
5. 实现了 Pi 风格 context accounting/compaction chain、一次 overflow recovery、retry/backoff、
   UTF-8 安全的 head/tail truncation、continuation 和 redacted Artifact 读取。
6. 取消、异常、重启、工具配对和 retry 路径均由离线回归覆盖；无进展/重复只作为观测或 v1
   兼容行为，不作为 v2 的 stop。

#### 验收条件

- [x] v2 长时循环、重复循环后正常 stop、host stop 边界与 v1 resume 兼容测试通过。
- [ ] 同模型 Pi/Morrow 30 轮与 60 轮 candidate 对照报告——旧 30/60 设计已被当前决策
      取代，正式 A/B 延后到 S7P-09，届时使用外部等价 watchdog 与新的评测协议。
- [x] 上下文压缩前后，用户目标、约束、进展、下一步和累计文件列表保持可回答且有界。
- [x] immutable checkpoint 重启恢复不会覆盖或重复 durable 对话/工具事实。
- [x] 同一 invalid_arguments、not_found 或无 diff 模式不会触发 v2 的隐式任务级 stop。
- [x] 任何模型/工具/取消/上下文错误都保留合法 ToolCycle、明确既有 stop code 和可恢复状态。

---

### S7P-07：补齐模型调用韧性与运行中控制

**优先级：P1 但属于进入门禁；依赖：S7P-01、S7P-06**

#### 已存在的问题

- 模型流失败当前只有一次立即重试，没有退避或 Retry-After 处理。
- MORROW-006 因连接超时 BLOCKED。
- 终端运行中主要依赖 Ctrl+C，不能在安全点注入 steering 或排队 follow-up。
- Pi 的基本交互能力包括 steering/follow-up 队列；这不是多 Agent 功能。

#### 为什么会阻塞 Stage 7

Workflow 节点通常更长，瞬态 Provider 故障或无法纠正正在偏航的 Agent 会浪费整个节点预算。取消、纠正、继续的语义也必须先在单 Agent 叶子执行器稳定。

#### 解决方向

- 按 ModelErrorCode 区分可重试与不可重试；只对连接失败、限流和明确瞬态服务错误实施有界指数退避。
- 遵守 Provider Retry-After；取消、认证、无效请求和上下文超限不得自动重试。
- 重试次数、等待上限和总 deadline 同时受 RunBudget 约束。
- 在模型调用结束、ToolCycle 闭合等安全点接收 steering；当前已接纳工具调用必须先得到终态。
- follow-up 作为下一用户 turn 排队，不篡改当前 ConversationLog，也不绕过 TaskRun/Session 写入边界。
- 中断后保留已完成事实，未完成调用按现有取消/恢复合同闭合。

#### 验收条件

- [ ] scripted Provider 的 timeout → success、429 + Retry-After → success、auth failure、取消路径均有确定测试。
- [ ] 任何重试都不会重复执行 outcome unknown 的写工具。
- [ ] steering 只在安全点生效，并进入正常上下文与审计链路。
- [ ] follow-up 保持 turn 顺序，重启后不丢失或重复。
- [ ] Ctrl+C、显式 cancel、deadline 和 steering 不破坏 tool_call/tool_result 配对。

---

### S7P-08：单 Agent 基本功能矩阵回归

**优先级：P0 Gate；依赖：S7P-01 至 S7P-07**

此项不是新增功能，而是证明用户要求的“单 agent 功能能全部正常运行”。

| 能力面 | 必测路径 |
|---|---|
| 普通对话 | 无工具回答、流式输出、多轮上下文、取消后继续 |
| 工作区发现 | list、find、search、read、范围读取、大文件限制 |
| 结构化修改 | create、patch、replace、delete、move、rename |
| Shell | argv、shell、cwd、env allowlist、timeout、cancel、非零退出 |
| 沙箱与审批 | auto/manual/deny、预览、promotion、越界、符号链接 |
| Git 只读 | status、diff、用户脏改动识别、无仓库情况 |
| 验证 | 成功、失败、未运行、错误 scope、完成检查 |
| Provider/Model | 本地配置、选择、探测、冻结 ModelRef、usage、瞬态/永久错误 |
| Session/Task | 新建、恢复、fork、continue、accept/cancel、checkpoint、幂等提交 |
| 持久化/Artifact | 重启恢复、产物引用、missing/corrupt、备份、恢复、schema migration |
| 上下文 | 压缩、重启后恢复、长工具输出、错误恢复 |
| Profile/Preference/Knowledge | 相关性最小注入、revision 冲突、review/promotion/revert、来源可查 |
| Skill | discover/select、版本快照、上下文注入、受限脚本、draft/usage/lifecycle |
| MCP | desired state、catalog、版本快照、schema 错误、server crash、timeout/cancel、恢复 |
| 权限与 Grant | capability snapshot、审批、撤销、过期、恢复时不扩大权限 |
| 运行控制 | cancel、steering、follow-up、deadline、预算耗尽 |
| 安全 | 凭据、reasoning、完整参数/结果、traceback 均不泄漏 |
| 入口一致性 | 交互 CLI 与无头入口复用同一 Application/AgentLoop 语义 |

#### 验收条件

- [ ] 每个矩阵格至少有一个离线自动化测试；高风险路径包含失败和恢复测试。
- [ ] Stage 1 至 Stage 6 已发布 acceptance 路径全部回归，不因 Direct Coding 修复破坏既有能力。
- [ ] Skill、MCP、Provider、Preference、Knowledge、ToolSet、权限和上下文策略均能形成 AgentRun 可复现快照。
- [ ] 当前平台真实 Seatbelt 集成测试在 host 条件下通过，不再仅依赖 mock。
- [ ] 全量离线测试、Ruff format/check、compileall、CLI help 和 git diff --check 通过。
- [ ] 不通过 live 网络测试代替离线可重复合同测试。
- [ ] 任何跳过项都写明平台原因、风险和进入 Stage 7 是否可接受；P0 路径不得跳过。

---

### S7P-09：重复复杂任务评测与同条件 Pi 对照

**优先级：P0 Gate；依赖：S7P-08**

#### 评测设计

- 使用 Code Agent Mini Eval 的固定 revision。
- 清洁临时工作区开始，每次运行保存 Run Manifest。
- 每项至少独立运行两次，禁止复用前次模型上下文或工作区。
- Morrow 与 Pi 使用同一模型、同一 Provider、相同采样参数、等价权限、相同任务文本和同一 verifier。
- Pi 固定版本；Morrow 固定 commit。版本变化必须建立新基线。
- 同时记录：
  - verifier pass/fail；
  - stop code 与失败分类；
  - token、成本、耗时；
  - 工具调用总数和非成功分布；
  - 首次相关读取、首次有效写入、首次验证所在轮次；
  - diff 规模、返工次数、用户纠正次数；
  - 意外文件和越界尝试；
  - 上下文压缩次数与恢复结果。

#### 建议冻结的 Stage 7 硬阈值

以下阈值是进入 Gate 的工程决策，不是从单次结果推导出的事实。实施前可以评审，但冻结后不得为了通过而临时修改：

1. 两次完整 Morrow 运行都满足：
   - 简单/中等任务 5/5 PASS；
   - 至少两个困难任务在两次运行中均 PASS；
   - 总成绩至少 7/10；
   - 0 个 FAIL_RUNTIME；
   - 0 个因缺失基本工具而 BLOCKED；
   - 0 个 change task 在无相关 diff 时报告 success；
   - 0 个非预期文件。
2. invalid_arguments 不超过全部工具调用的 1%，且不造成任务失败。
3. 所有工具调用 100% 进入 succeeded、failed、denied、cancelled 或等价终态。
4. 至少选择四个有代表性的任务做同条件 Pi A/B；Morrow 不得出现 Pi 没有的工具/运行时 blocker。
5. Morrow 与 Pi 的质量差距必须可归因。建议门槛为四项对照中 Morrow 最多少通过一项；若差距来自模型随机性，增加重复次数而不是主观豁免。
6. token/cost、耗时和返工指标必须完整，即使当前不设置绝对成本上限。

#### 验收条件

- [ ] 原始运行记录、汇总脚本、verifier 输出和最终报告可审计。
- [ ] 每个失败都能落入固定分类，不使用“似乎”“可能是”作为最终归因。
- [ ] 报告同时呈现单次结果和跨重复运行的稳定性。
- [ ] Pi 对照不混用其他模型、其他权限或其他任务版本。
- [ ] 当前 Direct 基线被标记为一个不可变版本，供 Stage 7 Direct Workflow 比较。

---

### S7P-10：Stage 7 进入审查

**优先级：最终 Gate；依赖：S7P-09**

进入审查只回答“单 Agent 叶子执行器是否足以承载 Workflow”，不提前设计 Workflow 实现。

#### 必须同时满足

- [ ] S7P-00 至 S7P-09 全部完成，无 P0 豁免。
- [ ] AgentLoop 仍是单 Agent、领域无关的叶子执行器。
- [ ] ConversationLog 的唯一写入所有权未被破坏。
- [ ] TaskRun、AgentRun、Artifact、恢复、权限和事件边界没有为评测临时绕路。
- [ ] Direct Coding profile、工具集、模型、Skill、权限、上下文和预算均可形成可冻结快照。
- [ ] Direct 单 Agent 的成功率、成本、返工和失败分类基线已发布。
- [ ] 现有基本任务迁移为 Stage 7 Direct Workflow 时，不需要改变 AgentLoop 的工具或完成语义。
- [ ] 对仍未修复事项逐项证明其不阻塞 Stage 7，并已进入未来清单。

审查结果只有三种：

- GO：全部硬门禁通过，开始 Stage 7。
- CONDITIONAL GO：只允许文档、Spike 或不接入生产路径的准备工作；不得开始 Direct Workflow 迁移。
- NO-GO：存在工具、运行时、恢复、完成真实性或评测可信度阻塞，继续处理本清单。

## 六、实施纪律

每个工作包按同一节奏执行：

1. 先写或冻结能复现当前问题的测试/评测证据。
2. 只修改该工作包对应的最小边界。
3. 运行触及范围的测试，再运行完整离线门禁。
4. 使用相同 Run Manifest 重跑对应评测，不用其他模型结果代替。
5. 记录修复前后数据、残余风险和回退点。
6. 提交小而可恢复的 commit；不把多项根因混进一个 commit。

每项完成声明至少附：

- 问题复现；
- 代码/合同变更；
- 自动化验证；
- 真实任务验证；
- 安全与持久化检查；
- 对指标的影响；
- 未解决事项及其去向。

## 七、明确延后到未来清单的事项

下列事项有价值，但不应挤占 Stage 7 前的根因修复：

- Repo map、符号索引、LSP、AST 编辑；
- 多工具并行执行；
- 完整后台 Shell、PTY 与进程管理；
- Linux/Windows 全平台支持；
- 原生 Anthropic/Google 等 Provider 的完整特性；
- 网络、浏览器、远程代码托管写操作；
- SDK/RPC/IDE/GUI；
- run-level 通用 undo/rewind；
- CI、发布签名、许可证、安全政策等公开产品化工作；
- 大模块重构；
- 云端、多租户、远程沙箱。

这些事项的事实状态、启动条件和验收方向见未来修复清单。
