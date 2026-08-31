# Morrow Agent 未来修复清单

> 状态：候选 Backlog；不得替代当前阶段路线图
>
> 日期：2026-08-26
>
> 启动条件：已满足——[Stage 7 前 Direct Agent 可靠性修复清单](<Stage 7 前 Direct Agent 可靠性修复清单.md>)已通过 GO 审查
>
> 输入：[Morrow Agent 深度审计与可执行修复清单](<Morrow Agent 深度审计与可执行修复清单.md>)、Pi 基本能力对照、Aider/Cline/OpenHands 等成熟 Code Agent 能力面、当前代码事实

## 一、用途与边界

这份清单承接所有“有价值，但不应阻塞 Stage 7”的缺口。它解决的是后续效率、代码智能、跨平台、生态、产品化和长期维护问题，而不是当前 Direct Agent 的基本可靠性。

它与路线图的关系如下：

- Stage 7 的 AgentDefinition、Artifact Contract、Workflow Runtime、Compiler、Scheduler 和 Direct/Workflow 对照，仍以 [Stage 7 路线图](../roadmap/stage-7-workflow-runtime.md)为权威，不在本文重复设计。
- Stage 8 至 Stage 10 已明确拥有的 GUI、自适应编排、后台自动化和 1.0 产品化能力，实施时回到对应阶段文档。
- 本文用于记录从深度审计中剥离出的工程债务、成熟 Code Agent 差距和触发条件，防止它们被遗忘或错误升级成当前 P0。

## 二、为什么这些事项可以延后

Stage 7 前真正必须闭环的是：工具合同、项目指令、完整基本文件变更、真实验证/完成判定、上下文与预算、运行中控制、可观测性和稳定评测。

本清单中的事项满足至少一项：

- 不影响 AgentLoop 作为可靠的单 Agent 叶子执行器。
- 有可用但不够高效的替代路径。
- 只有在仓库规模、平台范围、并发规模或用户量达到阈值后才产生明显收益。
- 属于 Stage 8 至 Stage 10 的明确产品能力。
- 需要新的依赖、公开协议或安全边界，应该由独立决策和验收驱动。

“延后”不表示“不重要”。每项都给出启动信号，触发后应进入正式子计划，而不是无限期搁置。

## 三、与 Pi 和成熟 Code Agent 的差距归类

| 能力 | Stage 7 前处理 | Stage 7 路线图处理 | 本清单处理 |
|---|---:|---:|---:|
| read/write/edit/bash 的可靠合同 | 是 | 复用 | 继续优化 |
| AGENTS.md 项目指令 | 是 | 冻结到 AgentRun | 兼容更多格式 |
| 上下文压缩 | 是，先达到可靠恢复 | 按 ContextPolicy 冻结 | 高级语义/检索增强 |
| steering/follow-up | 是，基本安全点 | 节点控制复用 | 多客户端体验 |
| token/cost 与无头评测 | 是，最小可靠闭环 | 聚合 Workflow 成本 | 稳定 SDK/RPC 协议 |
| Agent Definition / 多 Agent / Workflow | 否 | 是 | 自适应编排在后续阶段 |
| Repo map / 符号索引 | 否 | 否 | 是 |
| LSP / AST 级代码智能 | 否 | 否 | 是 |
| 通用 checkpoint / undo | 否 | 可做 Writer Spike | 是 |
| PTY / 后台进程 | 否 | 否 | 是，主要属于 Stage 9 |
| 多工具并行 | 否 | Stage 7 只读节点并行不同于此项 | 是 |
| Linux/Windows 完整支持 | 否 | 否 | 是 |
| 原生多 Provider / multimodal | 否 | Definition 引用现有 Provider | 是 |
| IDE、GUI、公共 SDK | 否 | CLI/Query/Event 最小面 | 是，Stage 8/10 |
| CI、发布、安全元数据 | 否 | 否 | 是，Stage 10 前必须完成 |

Pi 本身并不把 MCP、子 Agent、内置计划/Todo 或后台 Bash 都放入核心，因此这些能力不能被错误包装成“Pi 基本对齐”的 Stage 7 前门禁。成熟项目的额外能力应按真实收益逐步加入。

## 四、未来修复顺序总览

只有前一阶段的触发条件满足后才启动下一阶段；同一阶段内部也按编号排序。

| 阶段 | 顺序 | 编号 | 事项 | 主要收益 |
|---|---:|---|---|---|
| F1 基线通过后的工程化 | 1 | FUT-01 | CI、支持矩阵与可重复质量门禁 | 防止可靠性回退 |
| F1 | 2 | FUT-02 | Run-level ChangeSet、Checkpoint 与 Undo/Revert | 提高复杂修改安全性 |
| F1 | 3 | FUT-03 | 受控环境准备工作流 | 降低依赖缺失导致的环境阻塞 |
| F1 | 4 | FUT-04 | 命令流式输出、Artifact 与进程协议 | 提升长测试/构建可观察性 |
| F2 代码智能与效率 | 5 | FUT-05 | Repo map 与符号索引 | 降低大型仓库探索成本 |
| F2 | 6 | FUT-06 | LSP/AST 语义能力 | 提高跨文件修改准确率 |
| F2 | 7 | FUT-07 | 结构化 Git 写操作 | 提高提交、分支和恢复纪律 |
| F2 | 8 | FUT-08 | 安全的多工具并行 | 降低只读探索延迟 |
| F3 Provider 与平台 | 9 | FUT-09 | 原生 Provider 能力协商与多模态 | 减少兼容层损耗 |
| F3 | 10 | FUT-10 | Linux/Windows 沙箱与平台支持 | 扩展可部署范围 |
| F3 | 11 | FUT-11 | 网络、文档、浏览器与远程仓库工具 | 扩展外部信息与协作能力 |
| F3 | 12 | FUT-12 | PTY、后台进程与长任务控制 | 支撑服务、watcher 和 Stage 9 |
| F4 客户端与产品化 | 13 | FUT-13 | 稳定 SDK/RPC/事件兼容层 | 支撑 IDE、GUI 和自动化客户端 |
| F4 | 14 | FUT-14 | IDE/GUI 的代码任务体验 | 降低交互成本 |
| F4 | 15 | FUT-15 | 扩展评测、故障注入与安全测试 | 建立发布级信心 |
| F4 | 16 | FUT-16 | 大模块重构与性能治理 | 降低长期变更风险 |
| F4 | 17 | FUT-17 | 发布、许可证、安全与运维元数据 | 达到公开产品要求 |
| 条件性远期 | 18 | FUT-18 | 云端、多租户与远程沙箱 | 仅在产品方向明确时启动 |

## 五、F1：基线通过后的工程化

### FUT-01：CI、支持矩阵与可重复质量门禁

**当前事实**

- 仓库当前没有 GitHub Actions 工作流。
- 本地已有 pytest、Ruff、compileall、CLI help、git diff --check 等成熟命令。
- 两个真实 Seatbelt 测试在普通离线环境中跳过，需要 host 条件。

**为什么不阻塞 Stage 7**

本地可重复门禁已能支持当前开发；CI 缺失不会直接造成 Agent 工具失效。但 Stage 7 会增加领域模型、迁移和恢复路径，继续只靠人工执行会显著增加回归风险。

**解决方向**

- 先建立单一 Python 版本和当前 macOS/Linux 可行环境的最小 CI，不一次承诺所有平台。
- 分层运行：
  - 快速静态检查；
  - 离线测试；
  - migration/backup/recovery；
  - 平台沙箱；
  - 受控 live smoke，仅在有显式秘密与授权时运行。
- 固定 uv lock、缓存策略和失败产物；CI 日志遵守现有脱敏边界。
- 为可选平台和 Provider 建立明确 support tier，不把未测试平台标为 supported。

**启动信号**

- Stage 7 首个实现分支开始前，或出现第一次“本地通过、合并后回归”。

**验收条件**

- [ ] 新提交自动运行离线门禁，结果与本地命令一致。
- [ ] migration/recovery 与平台测试有独立状态，不被普通单测掩盖。
- [ ] CI 不打印凭据、完整工具参数/结果或 traceback 中的敏感内容。
- [ ] 支持矩阵准确区分 supported、experimental、unsupported。

---

### FUT-02：Run-level ChangeSet、Checkpoint 与 Undo/Revert

**当前事实**

- Morrow 已有 ChangeSetService、沙箱变更收集和结构化 create/patch/replace。
- Stage 7 前清单要求补齐 delete/move/rename。
- 当前没有将整个 AgentRun 的多次变更作为一个可审计单元进行 checkpoint、预览、撤销或重放的完整用户能力。

**为什么不阻塞 Stage 7**

基本变更安全闭环完成后，Git 和现有沙箱可以支撑单 Writer Direct/Workflow。通用 undo 涉及用户脏改动、并发、崩溃和不可逆副作用，不能为了“功能齐全”仓促加入。

**解决方向**

- 为每个 AgentRun 形成不可变 ChangeSet manifest，记录 base revision、路径操作、内容 hash、审批和 promotion 结果。
- checkpoint 只捕获 Morrow 拥有的工作区变更，不把用户并发改动纳入可回滚所有权。
- undo 优先产生反向预览和冲突报告；无法证明安全时拒绝，不自动覆盖。
- Git 仓库可使用 tree/blob 证据；非 Git 工作区使用内容寻址 Artifact。
- Workflow 中将 ChangeSet 绑定到 Writer NodeRun，Reviewer 只读。

**启动信号**

- Stage 7 Writer 节点需要多步跨文件变更，或评测显示人工恢复/返工成为主要失败来源。

**验收条件**

- [ ] 能预览并撤销一个只包含 Agent 自有改动的 Run。
- [ ] 用户在 Run 后修改同一路径时，undo 报告冲突而非覆盖。
- [ ] create/delete/move/rename 的反向操作均有崩溃点测试。
- [ ] undo 不能伪造外部命令、网络或 outcome unknown 副作用已回滚。

---

### FUT-03：受控环境准备工作流

**当前事实**

- 复杂代码任务常依赖安装依赖、生成环境、选择解释器或启动辅助服务。
- 当前 Shell 可执行相关命令，但没有“识别环境 → 预览 → 授权 → 准备 → 验证”的独立合同。
- 增加第三方依赖或访问网络仍应显式授权。

**为什么不阻塞 Stage 7**

当前 Code Agent Mini Eval 可使用预建环境；环境准备不是现有失败的已证实主因。把包管理自动化塞进普通 Shell 反而会扩大风险。

**解决方向**

- 先只读检测 lockfile、toolchain、虚拟环境和缺失命令。
- 将环境变更建模为独立受审批 Operation，记录网络、磁盘、命令和预期产物。
- 区分项目依赖、系统依赖和临时工具；禁止默认全局安装。
- 支持 dry-run、缓存、超时、取消和清理；生成可复现环境摘要。
- 与 CapabilityPolicy 和 CredentialStore 集成，不通过 prompt 暗示授予权限。

**启动信号**

- 评测中 BLOCKED_ENV 比例成为主要失败类别，且确认是可自动恢复的依赖准备问题。

**验收条件**

- [ ] 无网络条件下能准确报告缺失项而非反复重试。
- [ ] 安装前明确展示目标、来源、作用域和副作用。
- [ ] 取消/失败后不留下被错误标记为 ready 的环境。
- [ ] 环境快照可以进入 AgentRun/WorkflowRun 的可复现证据。

---

### FUT-04：命令流式输出、Artifact 与进程协议

**当前事实**

- Shell 工具有输出上限、超时和取消边界。
- 长测试、构建或日志可能在完成前缺少可见进展；过大输出会被截断。
- 当前不需要完整 PTY 就能运行大多数一次性命令。

**为什么不阻塞 Stage 7**

Stage 7 前复杂任务可以使用有界同步命令完成验证。这里主要改善体验、长任务诊断和大输出保存。

**解决方向**

- 把 stdout/stderr chunk 作为内部受限进度流，公开事件只提供有界摘要。
- 大输出写入受控 Artifact，模型按需读取相关片段，不把完整日志注入上下文。
- 记录命令开始、心跳、退出、取消、超时和 artifact hash。
- 保持 terminal 输出与 durable evidence 分离；避免 ANSI、控制字符和秘密泄漏。

**启动信号**

- 长构建/测试成为常见任务，或输出截断导致诊断失败。

**验收条件**

- [ ] 大输出不耗尽模型上下文，仍可定位失败片段。
- [ ] 流式输出顺序稳定，取消后进程和读取任务全部闭合。
- [ ] Artifact 有大小、hash、MIME/encoding 和生命周期限制。

## 六、F2：代码智能与效率

### FUT-05：Repo map 与符号索引

**当前事实**

- 当前主要依赖目录列表、文本搜索和文件读取探索仓库。
- Aider 等成熟项目使用 repo map 降低大型仓库的上下文成本。
- 当前 Mini Eval 尚未证明缺少 repo map 是失败主因。

**为什么不阻塞 Stage 7**

文本搜索对小到中型仓库已有可用路径。Repo map 是规模效率优化，不应掩盖工具合同和完成判断问题。

**解决方向**

- 从确定性、语言无关的文件/符号摘要开始，不先引入复杂向量数据库。
- 索引遵守 ignore、工作区边界、文件大小和敏感路径策略。
- 以 revision/hash 增量更新，明确 stale 状态。
- ContextPolicy 按任务相关性选择 map 片段，不默认把全图塞入 prompt。
- 用大仓库评测比较 token、首次相关文件命中率和成功率。

**启动信号**

- 仓库规模导致探索 token 或 tool rounds 明显高于修改/验证阶段，且文本搜索命中率不足。

**验收条件**

- [ ] 索引结果可追溯到当前文件 revision。
- [ ] stale map 不会被当作权威事实。
- [ ] 在固定大仓库任务中降低探索成本，且不降低 verifier 成功率。

---

### FUT-06：LSP/AST 语义能力

**当前事实**

- 当前修改以文本 patch/replace 为主。
- 缺少定义/引用、类型诊断、可靠重命名和语法树级编辑。
- LSP、tree-sitter 或编译器 API 会引入语言特定依赖、进程管理和版本兼容。

**为什么不阻塞 Stage 7**

稳定文本工具足以完成通用代码任务。语义工具是大型或强类型项目的准确率增强，不是当前基本工具失效的修复。

**解决方向**

- 先按语言和任务证据选一个最有收益的 Spike，不承诺一次覆盖所有语言。
- 将 definition、references、diagnostics、rename 等建模为结构化只读/写工具。
- LSP 进程由受控 ProcessAdapter 管理，继承超时、取消、输出限制和工作区隔离。
- 文本版本或 document revision 不匹配时拒绝应用 edit。
- AST edit 必须回落到可审计的文本 diff，并经过现有 ChangeSet/审批路径。

**启动信号**

- 跨文件引用遗漏、错误 rename 或类型问题成为评测中的主要语义失败类别。

**验收条件**

- [ ] stale document edit 被拒绝。
- [ ] rename 结果经编译/测试验证，且所有写入进入统一 ChangeSet。
- [ ] LSP 崩溃不影响主 Session，可重启并报告状态。

---

### FUT-07：结构化 Git 写操作

**当前事实**

- 当前 Git 能力以只读 status/diff 等为主。
- Agent 可以通过审批后的 Shell 调用 Git，但缺少针对 commit、branch、stash、merge 的结构化风险合同。
- Git 是恢复事实来源，但错误自动化可能破坏用户历史。

**为什么不阻塞 Stage 7**

Stage 7 前不需要 Agent 自动提交；用户或开发流程可完成 Git 操作。过早开放高风险 Git 写入弊大于利。

**解决方向**

- 优先提供结构化 diff、blame、log、show 等只读证据增强。
- 写操作从 create branch、stage explicit paths、commit 开始，禁止隐式 all。
- reset、clean、force push、rebase 等高风险操作默认不提供或要求独立高风险审批。
- 每次操作检查工作区 revision 和用户并发修改。
- 与 Run-level ChangeSet 对齐，commit message 和路径清单可审计。

**启动信号**

- 用户明确要求端到端提交工作流，或 Workflow Writer 需要隔离提交作为 Artifact。

**验收条件**

- [ ] 只 stage 明确拥有的路径。
- [ ] 不覆盖、不 stash、不提交用户未知改动。
- [ ] 分支和 commit 操作有预览、审批和恢复说明。

---

### FUT-08：安全的多工具并行

**当前事实**

- 复测的 235 个 tool batches 中，57 个包含多个调用，最大 4 个。
- 当前 batch 顺序执行。
- 数据只证明存在潜在延迟收益，没有证明并行会降低轮次或提高成功率。

**为什么不阻塞 Stage 7**

顺序执行是正确但可能较慢的替代路径。Stage 7 自身的只读 Node 并行与单个 AgentLoop 内并行工具调用是两个不同问题。

**解决方向**

- 仅并行声明为 read-only、彼此无依赖且共享资源安全的调用。
- Writer、审批、同一文件读取/修改依赖和不确定副作用继续串行。
- 保持 tool_call 顺序与 ToolMessage 闭合顺序可预测。
- 并发共享总 deadline、输出预算和取消信号。
- 用真实 batch 轨迹比较 wall time、token 和错误率，再决定默认策略。

**启动信号**

- 工具等待时间成为总耗时主要部分，且多数 batch 是独立只读调用。

**验收条件**

- [ ] 并行与串行结果语义等价。
- [ ] 取消能闭合所有已接纳调用。
- [ ] 并发不绕过全局工具/输出预算。
- [ ] 无共享缓存、cwd 或文件句柄竞态。

## 七、F3：Provider、平台与外部能力

### FUT-09：原生 Provider 能力协商与多模态

**当前事实**

- 当前 OpenAI-compatible 路径可以覆盖基本流式文本和工具调用。
- 不同 Provider 对 JSON Schema、usage、reasoning、cache、并行工具和错误分类支持不同。
- 兼容层可能损失原生能力，但当前尚未以同模型 A/B 量化损失。

**为什么不阻塞 Stage 7**

Stage 7 可以冻结当前受支持 Provider/ModelRef。多 Provider 广度不应优先于一条可靠路径。

**解决方向**

- 定义 ProviderCapabilities：schema dialect、usage、stream event、tool choice、context、cache、multimodal 和 retry hints。
- 将公共 ModelEvent 与 Provider 原生对象隔离；原始 SDK 对象不进入持久化或公开事件。
- 逐个实现原生 adapter，以合同套件保证事件和错误语义一致。
- 多模态只在明确任务与 Artifact 安全边界后加入，不把二进制直接塞进 ConversationLog。

**启动信号**

- 同模型兼容层对照显示明显质量/成本损失，或产品明确支持新的 Provider/多模态场景。

**验收条件**

- [ ] 每个标记 supported 的 Provider 通过同一 adapter 合同。
- [ ] usage、tool call、取消、限流和上下文错误有稳定映射。
- [ ] 不支持能力会在运行前拒绝或降级，不在任务中途静默变化。

---

### FUT-10：Linux/Windows 沙箱与平台支持

**当前事实**

- macOS Seatbelt 是当前实际安全路径。
- LinuxBubblewrapBackend 已有命令构造实现，但 probe 固定返回不支持，缺少真实 Linux runner 验收。
- Windows 没有等价的已验证沙箱路径。

**为什么不阻塞 Stage 7**

当前开发和基线可在 macOS 完成；声称跨平台支持才需要此项。它不是“后端完全没写”，而是未验证和未启用。

**解决方向**

- 在真实 Linux CI/runner 验证 bubblewrap 探测、挂载、网络、进程树取消、符号链接和 promotion。
- 明确没有 bwrap、容器内、受限 namespace 等降级行为；禁止静默回退到不安全 host 执行。
- Windows 先做安全模型和可行性 Spike，再选择 AppContainer、Job Object、容器或 unsupported。
- 平台能力进入 RuntimeCapabilities 和 support matrix。

**启动信号**

- 准备声称 Linux 支持、部署 Linux worker，或 Stage 9 后台执行需要 Linux。

**验收条件**

- [ ] 真实 runner 通过越界写、网络、进程逃逸、取消和 promotion 测试。
- [ ] 不支持平台 fail closed，错误可诊断。
- [ ] 文档与运行时探测一致。

---

### FUT-11：网络、文档、浏览器与远程仓库工具

**当前事实**

- Stage 6 已有 MCP 生命周期和安全边界，可作为外部能力扩展入口。
- 当前 Direct coding 的核心问题不依赖网络/浏览器。
- 网络读取、登录态浏览器和远程 Git 写入具有新的数据外传和副作用风险。

**为什么不阻塞 Stage 7**

本地代码任务可以离线完成。增加外部能力会扩大权限面，不能作为当前成功率问题的泛化修复。

**解决方向**

- 优先通过现有 MCP/Skill/Provider 扩展边界，而不是每个工具自建网络栈。
- 对域名、方法、下载大小、重定向、凭据作用域和内容类型建立策略。
- 区分只读文档查询、浏览器交互和远程写操作。
- 外部内容进入模型前带来源、时间和不可信输入标记，防范 prompt injection。
- 远程仓库写入单独审批，不继承本地 Git 权限。

**启动信号**

- 真实用户任务持续因需要最新文档、Issue/PR 或浏览器操作而阻塞。

**验收条件**

- [ ] 网络和浏览器能力有域/权限/凭据隔离。
- [ ] 外部不可信文本不能修改固定系统边界或直接触发副作用。
- [ ] 下载和工具输出有大小、类型和 Artifact 约束。

---

### FUT-12：PTY、后台进程与长任务控制

**当前事实**

- 当前 Shell 更适合一次性命令。
- 开发服务器、watcher、调试器和交互式程序需要持久进程、PTY、输入和日志读取。
- Stage 9 已规划可靠后台自动化，不应提前建立第二套后台状态机。

**为什么不阻塞 Stage 7**

静态 Workflow 可以先运行有界前台命令。后台进程需要租约、恢复、进程归属和资源治理，复杂度远高于普通 Shell。

**解决方向**

- 将 ProcessRun 建模为有 owner、状态、pid identity、cwd、环境摘要、输出 Artifact 和 lease 的持久对象。
- 区分 PTY 交互进程与无头后台进程。
- 支持 start/read/write/resize/terminate/wait，所有操作受统一 CapabilityPolicy。
- 重启后不得仅凭 PID 认领进程；需要强 identity 或明确 outcome unknown。
- 与 Stage 9 Worker/Schedule 统一，不创建临时旁路。

**启动信号**

- Stage 9 启动，或核心 coding eval 明确需要服务启动/交互调试。

**验收条件**

- [ ] 进程退出、取消、父进程崩溃和 PID 复用有确定处理。
- [ ] 输出受限且可按游标读取。
- [ ] Session/Workflow 结束后没有孤儿进程。

## 八、F4：客户端、质量与产品化

### FUT-13：稳定 SDK、RPC 与事件兼容层

**当前事实**

- Stage 7 前只要求最小无头运行和安全机器可读事件。
- 当前公开事件类型有限，但内部 Application Service、cursor event 和持久化边界已存在。
- 成熟 Agent 通常提供 JSON/RPC/SDK 模式，便于 IDE、GUI 和自动化集成。

**为什么不阻塞 Stage 7**

Workflow Runtime 可以先通过本地 CLI/Query/Event 验证。过早冻结公共 SDK 会固化尚未验证的 Workflow 领域模型。

**解决方向**

- 在 Stage 7 领域模型稳定后定义版本化 Command、Query、Event 和错误协议。
- 客户端只能调用 Application Service，不直接写数据库、YAML 或内存对象。
- 使用 cursor/resume、idempotency key、revision conflict 和 schema version。
- 兼容策略允许忽略未知字段/事件，但破坏性变化必须升版本。
- 提供语言无关协议，再按需求生成 Python/TypeScript SDK。

**启动信号**

- Stage 8 GUI、IDE 插件或外部自动化需要稳定集成。

**验收条件**

- [ ] CLI、SDK/RPC 对同一命令产生相同权威状态。
- [ ] 断线重连不会丢失或重复副作用命令。
- [ ] 协议不泄漏秘密、reasoning 或完整敏感工具数据。

---

### FUT-14：IDE/GUI 的代码任务体验

**当前事实**

- 当前主要入口是终端。
- Stage 8 和 Stage 10 已规划可视化工作台与产品化。
- UI 不能反向定义未冻结的运行状态。

**为什么不阻塞 Stage 7**

终端足以验证核心可靠性。先做 GUI 会掩盖底层状态和事件缺口。

**解决方向**

- 以稳定 SDK/RPC 为唯一写入口。
- 优先实现只读运行观察、diff、审批、Artifact 和诊断，再增加 Workflow 编辑。
- 明确展示模型建议、已执行事实、待审批副作用和验证状态。
- 大输出、事件虚拟化、无障碍和键盘路径纳入验收。

**启动信号**

- Stage 8 开始且 Stage 7 的 Query/Event 模型已冻结。

**验收条件**

- [ ] UI 与 CLI 显示同一 revision 和状态。
- [ ] 断线、重连、取消、审批和冲突处理不产生双写。
- [ ] 用户能区分计划、执行中、已验证和仅推断完成。

---

### FUT-15：扩展评测、故障注入与安全测试

**当前事实**

- Code Agent Mini Eval 适合 Stage 7 前诊断，但样本量不足以覆盖语言、仓库规模、恢复、安全和长期稳定性。
- 当前单元测试丰富，真实宿主、Provider、崩溃点和对抗输入覆盖仍可扩展。

**为什么不阻塞 Stage 7**

Stage 7 前已有明确的最小重复 Gate。更大评测应在核心稳定后建设，否则会把大量已知基础失败重复放大。

**解决方向**

- 扩展到 30–50 个分层任务，覆盖修 bug、跨文件重构、测试修复、配置、文档、恢复和拒绝。
- 增加 mutation-based verifier，避免只看测试退出码。
- 建立 crash-point、disk full、database busy/corrupt、Provider stream break、tool timeout、approval race 等故障注入。
- 对路径、Schema、事件、压缩摘要和恢复状态使用 property/fuzz 测试。
- 建立 prompt injection、symlink、secret exfiltration 和 malicious repo 测试集。
- 报告置信区间、重复稳定性、成本分位数和版本回归。

**启动信号**

- Stage 7 Direct/Workflow 首个对照完成，或准备发布 Beta。

**验收条件**

- [ ] 评测分层、版本化、可离线重放。
- [ ] verifier 与 Agent 输出解耦。
- [ ] 故障注入后权威状态可解释，没有伪成功。
- [ ] 安全回归是发布 Gate，不是可选报告。

---

### FUT-16：大模块重构与性能治理

**当前事实**

- runtime/agent.py、interfaces/cli.py、application/bootstrap.py、interfaces/terminal.py、services/files.py、runtime/tools.py 等模块较大。
- 大文件不是自动缺陷；当前需要基于变更热点、循环依赖、测试困难和性能数据判断。

**为什么不阻塞 Stage 7**

在 Stage 7 前大规模重构会同时改变稳定边界并干扰根因修复。仅凭行数拆分容易产生抽象搬运。

**解决方向**

- 先收集 churn、缺陷密度、依赖方向、测试 setup 和 profile 数据。
- 围绕已稳定职责拆分，例如 PromptAssembler、RunDiagnostics、CompletionPolicy、CommandPresentation，而不是按行数切文件。
- 每次重构保持外部行为、事件顺序、ConversationLog 所有权和 durable schema 不变。
- 性能优化以 profile 和基线为准，重点关注上下文构建、SQLite 查询、Artifact I/O 和大仓库索引。

**启动信号**

- Stage 7 开发持续在同一模块产生冲突，或新增功能无法在不破坏所有权的情况下测试。

**验收条件**

- [ ] 依赖方向符合 Architecture。
- [ ] 关键行为用 characterization tests 锁定。
- [ ] 重构 commit 不混入功能变化。
- [ ] 性能改进有基线数据，不以主观“更快”验收。

---

### FUT-17：发布、许可证、安全与运维元数据

**当前事实**

- 仓库缺少完整 CI/发布工作流及公开项目常见的 LICENSE、SECURITY 等元数据。
- 当前是开发阶段，这些缺失不解释 Direct Agent 的任务失败。

**为什么不阻塞 Stage 7**

它们是分发和维护要求，而不是 Workflow Runtime 的技术进入条件。

**解决方向**

- 在发布目标明确后选择许可证，不由工程实现者自行猜测。
- 增加 SECURITY、支持范围、漏洞报告、隐私/遥测说明和威胁模型摘要。
- 建立版本、changelog、迁移、签名、SBOM、依赖审计和发布回滚流程。
- 安装包从空环境验证，覆盖数据库迁移、备份恢复和卸载/数据保留政策。

**启动信号**

- 准备外部 Beta、开源或分发二进制/包。

**验收条件**

- [ ] 用户可明确知道许可证、支持平台、安全报告方式和数据边界。
- [ ] 发布产物可追溯、可验证、可回滚。
- [ ] 从至少两个旧 schema fixture 升级并验证失败恢复。

---

### FUT-18：云端、多租户与远程沙箱

**当前事实**

- Morrow 当前定位为工作区范围的本地终端 Agent。
- 云端、多租户会引入身份、租户隔离、远程执行、计费、数据驻留和运营安全，不是本地 Agent 的自然小步扩展。

**为什么不设默认计划**

这属于产品方向变化，而不是修复。没有明确用户需求和威胁模型时提前设计会分散 Stage 7–10。

**解决方向**

- 仅在产品决策明确后建立独立路线图和安全评审。
- 本地领域模型可以保持可移植，但不得为了假想云场景破坏当前简单性。

**启动信号**

- 用户明确决定提供远程托管或团队多租户产品。

**验收条件**

- [ ] 独立完成身份、隔离、审计、凭据、网络、资源配额、数据生命周期和事件一致性设计。
- [ ] 远程执行不复用本地信任假设。

## 九、优先级调整规则

未来事项只有在有新证据时才能提前：

1. 新评测显示它是主要失败类别，而不是偶发失败。
2. Stage 7/8/9 的正式进入条件直接依赖它。
3. 当前替代路径造成不可接受的安全风险。
4. 用户明确改变支持平台或产品范围。

以下理由不足以提前：

- 其他项目有这个功能；
- 单个模型建议增加；
- 某次任务可能因此更快；
- 文件很大或接口看起来不够优雅；
- 可以顺手一起做。

每次提前必须记录：新证据、影响范围、为何当前阶段无法绕开、验收指标和被挤出的原计划事项。

## 十、未来每项进入实施前的模板

任何 FUT 项从候选 Backlog 进入正式计划前，必须补齐：

- 当前可复现问题与频率；
- 用户价值和不做的成本；
- 所属 Stage、领域所有者和架构边界；
- 是否新增第三方依赖、权限、公开事件或持久 schema；
- 最小 Spike 与退出条件；
- 安全/隐私/恢复模型；
- 离线测试、真实任务和性能基线；
- 迁移与回滚；
- 完成标准和明确不包含范围。

这样可以保证未来清单不会重新膨胀成“一次性重写 Agent”的计划，也不会把尚未证实的猜测伪装成 P0。
