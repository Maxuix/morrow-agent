# Stage 3：本地 Code Agent 与安全闭环

> 状态：已完成（当前声明平台：macOS；Linux 原生运行在真实 runner 验证前保持 unsupported）
> 阶段结果：一个能够在指定工作空间中安全定位、修改并验证真实代码任务，并可在原生沙箱中自动运行项目命令的单 Agent Code Agent
> 上级文档：[开发路线总览](../ROADMAP.md)
> 上一阶段：[Stage 2：Agent 核心能力](stage-2-agent-core.md)
> 下一阶段：[Stage 4：Task、Session、Artifact 与持久化](stage-4-task-session-and-persistence.md)

## 一、阶段目标

阶段 3 把 Morrow 从“可以调用受限演示工具的 Agent”推进为“可以在真实本地仓库完成最小开发闭环的 Code Agent”。

阶段完成时，用户应当能够把一个明确的软件任务交给 Morrow，让它在当前工作空间中：

```text
理解任务
→ 定位相关文件
→ 读取必要上下文
→ 搜索符号与文本
→ 生成并应用受控修改
→ 展示 Diff
→ 运行项目已有测试或校验
→ 根据失败继续修正
→ 报告实际修改、验证结果和未解决问题
```

本阶段的成功标准不是工具数量，而是这个闭环在安全、取消、超时、输出限制和失败恢复条件下仍然可靠。

## 二、当前基线

已经完成的先行切片继续保留，不重复实现：

- 统一 `ToolDefinition`、`ToolRegistry`、冻结 `ToolSet` 和 `ToolExecutor`。
- 通用工具参数校验、执行预算、错误闭合与循环限制。
- 本地 `ToolEffect` 副作用分类、`ToolApproval` 审批要求和注入式 `ApprovalPort`。
- 三轴 `PermissionProfile`、冻结 `WorkspaceCapability`、参数化 `CapabilityPolicy`、动态系统边界，
  以及进程内有界 `ToolRunContext`/`ToolFact`。
- 终端审批 UI，以及拒绝、审批通道不可用和审批超时的安全结果。
- `update_configuration` 状态化工具及其 `ConfigPatchService` 边界。
- 带 tool calls 的 Assistant 先进入 `ConversationLog`，再执行工具并闭合 ToolCycle。

已完成工作区读搜、冲突安全文件变更/实际 Diff、审批后的非交互 Host 命令、只读 Git 检查，以及当前 macOS 的原生
沙箱/Auto Sandboxed 快照执行；完整阶段端到端验收、事实摘要和本地 metrics 验收已记录在
`docs/acceptance/stage-3-local-code-agent-evidence.md`。Linux 仅完成后端规则构造测试，尚未声明真实运行支持。

## 三、进入条件

- 阶段 2 的 AgentLoop、ToolCycle、预算、取消和 stop code 已通过验收。
- 工作空间身份与数据目录隔离规则稳定。
- 现有审批、配置服务和工具协议测试保持通过。
- 用户明确开启阶段 3 本地工具开发，允许新增文件、Shell 与 Git 能力。

## 四、阶段设计原则

### 4.1 AgentLoop 不理解领域工具

新增文件、搜索、Shell 或 Git 工具时：

- 不允许在 `AgentLoop`、`ToolExecutor` 或当前 `SessionOrchestrator` 中按工具名增加分支。
- handler 只把标准参数映射到受限能力。
- 路径、进程、Git 等副作用通过显式注入的 Service/Port 完成。
- 新工具只需注册定义、参数模型、handler 和本地策略，不修改普通 Agent 状态机。

### 4.2 工作空间是能力边界，不只是默认目录

所有路径参数必须经过统一解析：

1. 以当前工作空间根为基准。
2. 规范化 `.`、`..`、绝对路径、符号链接与平台路径分隔。
3. 对最终目标和必要父目录执行真实路径检查；文件符号链接的调用别名与解析目标都应用受保护路径策略。
4. 默认拒绝工作空间外读取与写入。
5. 对不存在的新文件，验证最近存在父目录仍在工作空间内。
6. 任何“路径看起来在工作空间内”的字符串判断都不能替代真实路径约束。

### 4.3 读取、写入和进程执行分开建模

以下是 Stage 3 本地工具的目标能力/风险分类，不是当前 `ToolEffect` 枚举的现状声明。当前枚举只有
`none`、`session_write` 和 `persistent_write`；激活 3B 前必须通过子计划或 ADR 决定扩展该枚举，
还是建立独立的能力/风险模型。无论采用哪种方式，这些元数据都只保留在本地策略层。

目标至少区分：

- `read`：只读文件、目录、搜索、Git 状态。
- `persistent_write`：创建、修改、补丁应用。
- `destructive`：删除、覆盖不可恢复内容、大范围替换。
- `process`：运行命令，风险由命令预检动态判断。
- `external_effect`：联网、推送、发布、部署；本阶段默认不可用。

### 4.4 修改必须可见并可验证

- 写入前生成预检摘要，写入后生成实际 Diff。
- 工具成功只能表示真实写入完成，不能只表示模型生成了内容。
- 最终回答必须区分“已修改”“已验证”“未验证”“验证失败”。
- 测试失败不能被自动包装成工具基础设施错误；命令退出码与诊断输出必须保留为类型化结果。

### 4.5 权限范围、审批与隔离正交建模

权限系统不实现为一个混合所有语义的“四档枚举”，而由三个正交维度组成：

```text
AccessScope: workspace | full_access
ApprovalMode: manual | auto_safe | auto
ProcessIsolation: host | native_sandbox
```

面向用户的模式只是这三个维度的预设：

| 用户模式 | AccessScope | ApprovalMode | ProcessIsolation |
|---|---|---|---|
| Manual | workspace | manual | host |
| Auto Safe | workspace | auto_safe | host |
| Auto Sandboxed | workspace | auto | native_sandbox |
| Full Access Manual | full_access | manual | host |
| Full Access Auto | full_access | auto | host |

Stage 3 只激活前三个工作空间模式。`full_access` 可以进入领域模型和策略合同，但在 Stage 4
建立可持久、可撤销、可冻结的 `CapabilityGrant` 前必须明确返回 unsupported，不能静默降级或临时绕过
工作空间解析器。

参数校验后，领域预检生成包含规范化路径、命令类别和风险原因的 `OperationIntent`；统一策略返回
`allow`、`require_approval` 或 `deny`。同一工具可以因实际参数得到不同结果，不能继续只依赖注册时的
静态 `never|required` 审批标记。拒绝必须发生在审批之前，用户确认不能提升当前模式根本未授予的能力。

### 4.6 成熟实现参考基线

Stage 3 以 [earendil-works/pi](https://github.com/earendil-works/pi) 的 coding-agent 工具内核作为主要
成熟实现参照，首个固定研究基线为 `@earendil-works/pi-coding-agent 0.84.2`、提交
[`209bc7b9a89b01c8fd05861cf5bbdda3e300037a`](https://github.com/earendil-works/pi/commit/209bc7b9a89b01c8fd05861cf5bbdda3e300037a)。
后续比较必须引用固定提交，不能以持续变化的 `main` 作为验收事实。

重点借鉴：

- 小而稳定的 read/write/edit/bash/grep/find/ls 工具内核和可替换 operations/adapter。
- 行数与字节数双重输出限制、可继续读取提示和有界流式命令输出。
- 唯一文本匹配、多处不重叠编辑、展示 Diff 与 unified patch。
- 同一文件写入串行化，以及 Shell 超时、取消、进程树终止和 faux provider 离线测试。

明确不照搬：

- Pi 默认继承启动用户的文件、进程、网络和凭据权限；Morrow 必须执行本阶段的工作空间、审批与原生
  沙箱边界。
- 不接受绝对路径或默认工作空间外访问，不自动下载 `rg`/`fd` 等宿主工具。
- 不直接覆盖写入，不采用无 revision/hash 的 last-write-wins，也不自动应用模糊文本匹配。
- 不把任意 Shell、测试或项目脚本在 Host 上归类为可自动安全执行。

目标表述固定为：**Pi 级编码工具体验，加上 Morrow 级工作空间安全、可见修改和可审计策略。**
Pi 使用 MIT License；若实质复制而非独立重写代码，必须保留对应版权和许可声明。

## 五、计划范围

### 5.1 Workspace Filesystem 基础

已建立统一的工作空间文件能力：

```text
WorkspacePathResolver
WorkspaceFileService
FileReadResult
FileWritePreview
FileMutationResult
```

要求：

- 统一处理路径解析、文件类型、大小上限、编码和二进制拒绝。
- 文本默认使用 UTF-8；无法解码时返回明确错误，不静默替换内容。
- 读取支持行范围、最大字符数和截断信息。
- 目录列表支持深度、最大条目数、隐藏文件策略和排序。
- 大文件、设备文件、Socket、FIFO 与特殊文件默认拒绝。
- 读取期间文件发生变化时，结果包含必要的 revision/mtime/size 信息。

当前已交付的首批工具：

| 工具 | 目的 | 默认风险 |
|---|---|---|
| `list_directory` | 查看目录及基础元数据 | read |
| `read_file` | 按范围读取文本 | read |
| `find_files` | 按 glob/名称查找文件 | read |
| `search_text` | 在受限范围内搜索文本 | read |
| `apply_patch` | 以 SHA-256 和唯一精确编辑修改单个文件 | workspace write |
| `write_file` | 创建或按 SHA-256 替换单个 UTF-8 文件 | workspace write |
| `show_changes` | 查看当前运行实际 ChangeSet/Diff | read |
| `run_command` | 在审批后的非隔离 Host 执行有界非交互命令 | process |

工具命名可以在子计划中调整，但能力边界必须保持一致。

### 5.2 搜索能力

搜索应覆盖两个常见问题：

- “文件在哪里？”——文件名、路径、扩展名与 glob。
- “代码在哪里？”——文本、正则、符号名和上下文行。

实现要求：

- 第一版可以调用已安装的 `rg`，也可以实现 Python fallback；选择必须通过 ADR 固定。
- 外部二进制只是 Adapter，不得绕过路径和输出限制。
- 把 `.git`/`.morrow` 作为受保护路径，即使用户直接把它们指定为搜索根也不能读取内容；同时尊重虚拟环境、构建产物和用户配置的 ignore。
- 返回稳定的文件、行号、匹配片段和截断标记。
- 搜索无结果是正常结果，不是异常。
- 对超大仓库设置文件数、总字节数、耗时和结果条目预算。

### 5.3 文件修改与补丁

第一版以“精确补丁优先、受控整文件写入作为补充”为目标。

建议能力：

- `apply_patch`：基于上下文的增删改，检测基线不匹配。
- `write_file`：仅用于新文件或用户明确允许的整文件替换。
- `delete_path`：不进入 MVP；若加入，必须单独确认并限制范围。
- `show_changes`：读取当前运行实际 ChangeSet/Diff，不以模型生成的预览替代；Git 工作树差异留给后续 `git_diff`。

固定约束：

- 修改请求携带预期基线，例如内容哈希、mtime 或补丁上下文。
- 文件已变化时返回 conflict，不使用 last-write-wins。
- 写入采用同目录临时文件、必要 `fsync` 和原子替换；保持合理权限。
- 对换行符、结尾换行和编码变化进行显式处理；统一换行风格保持不变，混合换行源文件明确拒绝修改，避免无意重写全文件。
- Patch 失败不得退化为“猜测位置后强制写入”。
- 大范围修改、覆盖已有文件和删除操作需要更高风险等级。
- 当前实现只允许工作空间内的 create/patch/replace；不提供 delete、rename、chmod 或 link。
- Auto Safe 的单次 patch 限制为最多 8 个精确编辑、64 个插入加删除行、4 KiB 变更字节和不超过既有非空行的 25%；超过阈值转审批。
- 每次变更都记录 before/after revision、实际统一 Diff 和当前运行 ChangeSet fact；陈旧 SHA 返回 conflict。

### 5.4 Diff 与变更集合

Morrow 需要一个独立于最终回答的变更事实源：

```text
ChangeSet（当前进程内运行范围）
- entries[]: create | modify | unchanged
- before/after metadata
- unified_diff
- truncated
```

要求：

- 每次成功写入后更新任务内 ChangeSet。
- 用户可在审批前看预览，也可在任务后查看实际 Diff。
- 最终回答引用实际 ChangeSet，不根据对话回忆生成文件列表。
- 若工作区在 Morrow 之外发生变化，标记外部变更或基线漂移。
- 当前 ChangeSet 只保留在 `ToolRunContext`，不写入 ConversationLog 或持久状态。

### 5.5 受控 Shell 与测试执行

建立 `ProcessExecutionService`，统一负责：

- 工作目录固定在当前工作空间或其子目录。
- 命令、参数、环境变量白名单/黑名单与风险预检。
- 超时、取消、进程组终止和子进程清理。
- stdout/stderr 分流、字符与行数限制、截断标记。
- 退出码、信号、开始/结束时间和资源摘要。
- 非交互执行；需要 TTY、密码或全屏 UI 的命令明确拒绝。
- 默认只传递最小环境，不把凭据或全部用户环境暴露给子进程；Host 执行仍不是操作系统隔离。

建议工具：

| 工具 | 说明 |
|---|---|
| `run_command` | 执行非交互命令，返回结构化结果 |
| `run_tests` | 可选薄封装；复用同一进程服务与策略，不复制执行逻辑 |

Shell 风险预检至少识别：

- 删除、覆盖、权限修改、进程控制、包安装、系统目录访问。
- `git commit/push/reset/clean` 等有副作用 Git 命令。
- 网络下载、上传、发布、部署和远程执行。
- 命令替换、重定向、管道与 shell-specific 语法。

第一版若无法可靠解析复杂 shell 字符串，应优先接受 `argv[]`，并把需要 shell 解释器的命令提升为高风险或默认拒绝。

进程执行分成两个后端，但共享同一 `ProcessExecutionService`、`CommandResult`、预算、取消和输出合同：

- `HostProcessAdapter`：服务 Manual 和受限 Auto Safe。工作目录与命令预检仍然有效，但宿主进程没有
  操作系统级工作空间边界；任何项目代码、测试或不透明命令都必须逐次审批，并在预览中明确显示
  有界脱敏命令与“非沙箱宿主进程”。shell 包装、命令替换或直接 shell 中的 Git 命令按 Git 写入风险
  在审批前拒绝。
- `NativeSandboxProcessAdapter`：服务 Auto Sandboxed。macOS 以 Seatbelt 类原生机制为目标，Linux
  以 bubblewrap 类原生机制为目标；具体可用性在启动时探测，不自动安装宿主组件。Linux 在真实 runner
  通过前固定报告 unsupported，即使主机已经安装 `bwrap` 也不声明支持。

Auto Sandboxed 不把真实工作区直接作为可写执行目录。推荐执行过程固定为：

```text
当前工作区（包含未提交修改）
→ 创建临时项目快照
→ 以快照为可写根启动原生沙箱
→ 收集 CommandResult、Artifact 和实际 Diff
→ 丢弃快照
→ 用户要保留命令修改时，通过受控 ChangeSet/Patch 路径写回真实工作区
```

首版沙箱合同：

- 真实工作区不得以可写方式暴露给沙箱命令。
- 项目快照可写；系统与必要工具链只读；临时目录为任务私有可写目录。
- 默认不暴露用户 Home、凭据、SSH/GPG Agent、Docker Socket 或完整宿主环境。
- 默认禁止全部网络，包括 loopback；未来 loopback 必须作为独立能力逐次授权，不能隐含在“测试”中。
- 后端不可用、规则生成失败或能力无法证明时 fail closed，绝不回退到 Host 后自动执行。
- 快照准备/收集在线程启动前预留任务私有根，使用协作式取消；超时先等待后台阶段停稳，再清理临时项目副本。
- 沙箱内修改不会自动进入真实工作区；推广修改仍受文件冲突检测、Diff 和审批策略约束，并记录到统一的当前运行 ChangeSet。

### 5.6 Git 只读检查

本阶段至少提供：

- 当前仓库是否存在。
- `status`、工作树/暂存区 Diff。
- 当前分支或 detached 状态。
- 最近必要提交信息，可选且受结果限制。

建议通过 `GitInspectionService` 或受控 `git` Adapter 实现。默认不包含：

- 自动 `commit`、`push`、`pull`、`merge`、`rebase`。
- `reset --hard`、`clean`、强制切分支。
- 自动修改 Git 配置或凭据。

即使 Shell 能表达这些命令，策略层仍应默认拒绝或要求单独显式授权。

### 5.7 工具审计与公开事件

在不泄漏完整参数和结果的前提下，扩展 `tool.status` 或内部审计记录：

- 工具名、call_id、序号、状态。
- 风险等级与是否经过审批。
- 工作空间相对路径摘要或命令类别，不记录密钥。
- 开始、结束、耗时、退出码/错误码、是否截断。
- 产生的 ChangeSet / Artifact 引用。

阶段 3 仍不需要完整持久化事件库；阶段 4 才建立跨进程 Operational Store。但内存模型和事件字段应避免阻塞后续持久化。

## 六、建议实施切片

### 3A：通用策略与配置工具先行切片——已完成

保留当前实现与验收，不重做。

### 3B：路径安全、目录与文件读取

交付：

- WorkspacePathResolver。
- list/read/find/search 的最小能力。
- 二进制、大文件、路径穿越和符号链接测试。
- Fake Provider 驱动的“搜索并解释代码”端到端用例。

门禁：Agent 可以在测试仓库定位并读取问题相关文件，且无法读取工作空间外文件。

### 3C：补丁、写入与 Diff

交付：

- Patch/Write 服务。
- 基线冲突检测。
- 原子写入。
- ChangeSet 与实际 Diff。
- 写入审批与拒绝恢复。

门禁：Agent 可以修改一个 Fixture Bug，用户能够看到实际 Diff；冲突时不覆盖外部修改。

### 3D：Host Shell、测试与取消（已交付）

交付：

- ProcessExecutionService。
- HostProcessAdapter 与动态 OperationIntent 风险判定。
- 超时、输出限制、进程树取消。
- 受控环境与命令风险预检。
- Manual 下运行项目测试的审批闭环。

门禁：Agent 可以在修改后运行测试，并在失败后继续修正；取消不会留下失控子进程。

### 3E：原生沙箱与 Auto Sandboxed（已交付当前 macOS 切片）

交付：

- NativeSandboxProcessAdapter 与平台能力探测。
- macOS/Linux 后端的统一合同和 fail-closed 选择。
- 包含当前未提交修改的临时项目快照。
- 默认断网、最小环境、只读工具链和私有临时目录。
- 沙箱 Diff/Artifact 收集及受控 ChangeSet 推广。
- 快照阶段超时取消、后台线程收敛和预留临时根清理。
- 沙箱逃逸、符号链接、子进程、网络和真实工作区保护测试。

门禁：当前 macOS 的 Auto Sandboxed 已在宿主级 Seatbelt 测试中通过临时快照执行、工作区/Home/受保护文件/网络
逃逸阻断，并保持真实工作区不变；后端不可用时形成明确受限结果，且不会回退宿主执行。Linux 后端尚未因缺少真实
Linux runner 而声明支持。

### 3F：Git 检查与完整 Code Agent 验收

状态：已完成。只读 Git、两个 Fixture 产品故事、真实终端组合、宿主级 Seatbelt 验收、事实摘要/metrics、质量门禁与
wheel smoke gate 均通过；当前 macOS 是声明平台，Linux 仍需真实 runner。

交付：

- Git status/diff 检查。
- 最终结果摘要以 ChangeSet 与 CommandResult 为事实源。
- 多个真实风格 Fixture 项目。
- 人工项目试跑与 acceptance 记录。

门禁：完成“定位—修改—验证—报告”的完整闭环，详见
[`Stage 3 验收证据矩阵`](../acceptance/stage-3-local-code-agent-evidence.md)。

## 七、应用服务与依赖方向

推荐新增或演进的边界：

```text
RegisteredTool handler
    ↓
WorkspaceFileService / SearchService / PatchService
ProcessExecutionService
GitInspectionService
    ↓
Filesystem / Subprocess / Git adapters
```

注意：

- 现有 `CommandService` 已表示斜杠命令服务，不应再用同名对象表示 Shell。
- 所有服务显式接收 `workspace_id`、已解析 workspace root 或构造时冻结的 WorkspaceCapability。
- Adapter 返回领域结果，不直接发布 UI 事件。
- ToolExecutor 负责通用审批、预算、取消和结果大小；领域服务负责路径、文件与进程语义。

## 八、权限与审批策略

Stage 3 的确定策略：

| 操作 | Manual | 受限 Auto Safe | Auto Sandboxed |
|---|---|---|---|
| 工作区列目录、读取、搜索、Git status/diff | 自动允许 | 自动允许 | 自动允许 |
| 新文件、精确小范围 Patch | 每次审批 | 自动允许 | 自动允许；仍由结构化文件工具写真实工作区 |
| 整文件覆盖、大范围修改 | 强确认 | 强确认 | 强确认 |
| 删除、权限变更、链接、清理、reset | 默认拒绝 | 默认拒绝 | 默认拒绝 |
| 测试与项目命令 | Host 每次审批 | Host 每次审批 | 临时快照内自动允许 |
| 任意或不透明 Shell | Host 每次审批 | 审批或拒绝，不自动 | 沙箱内允许；结果修改不自动推广 |
| Git 写入 | 不提供 | 不提供 | 不提供 |
| 网络、工作区外直接访问 | 拒绝 | 拒绝 | 由沙箱强制拒绝 |

审批预览必须展示用户能理解的信息：

- 作用域和相对路径。
- 操作类型与文件数量。
- 命令和工作目录的脱敏形式。
- 风险原因。
- 超时与输出预算。

不要把完整文件内容、完整环境或可能包含密钥的参数直接显示在统一审计事件中。

## 九、暂不包含

- 持久化 Session、TaskRun、跨进程恢复和长期记忆。
- 自动学习偏好或自动生成 Skill。
- MCP、浏览器、云端机器、远程执行环境。
- 多 Agent、Workflow、后台和周期任务。
- 自动 Git 提交、推送、发布、部署。
- Full Access 及任意工作空间外文件访问；Stage 4 建立 CapabilityGrant 后再激活。
- 交互式 TTY、sudo、密码输入和全系统包管理。
- 通用撤销系统；本阶段依靠 Diff、Git 工作树和冲突保护，完整恢复策略进入后续阶段。

## 十、阶段交付物

- 工作空间路径与文件能力层。
- 目录、读取、文件查找和文本搜索工具。
- 精确 Patch、受控写入和实际 Diff。
- 受控 Shell/测试执行、超时、取消和输出限制。
- 原生沙箱 Adapter、临时项目快照与 Auto Sandboxed 模式（当前 macOS 已验证；Linux 需独立真实 runner）。
- 只读 Git 状态与 Diff。
- ChangeSet、CommandResult 与工具审计模型。
- 统一风险策略和审批预览。
- 离线 Fake Provider 端到端测试。
- 临时仓库安全测试与真实项目 acceptance 记录。
- 更新后的 `README.md`、`docs/ARCHITECTURE.md` 与工具使用说明。

## 十一、验收场景

### 11.1 正常修复闭环

给定一个包含确定性失败测试的 Fixture：

1. Agent 搜索相关符号。
2. 读取最少必要文件。
3. 应用补丁。
4. 展示实际 Diff。
5. 运行测试。
6. 测试通过后报告修改与验证证据。

### 11.2 测试失败后继续修正

第一次修改使部分测试通过但仍有失败。Agent 能读取失败输出、再次定位和修改，最终产生明确终态，不进入无界循环。

### 11.3 路径逃逸

以下均被拒绝且不泄漏工作空间外内容：

- `../` 路径穿越。
- 指向外部的符号链接。
- 绝对路径。
- 不存在目标文件但父目录经符号链接逃逸。
- 大小写或路径别名绕过。
- 指向 `.env`、`.git` 或其他受保护目标的工作区内文件符号链接别名。
- 嵌入 PKCS#8、RSA、EC、DSA 或 encrypted PEM 私钥的普通文件名内容。

### 11.4 外部并发修改

Morrow 读取文件后，测试在写入前模拟外部修改。Patch 返回 conflict，原文件保持不被覆盖，Agent 可重新读取后决定下一步。

### 11.5 命令风险与取消

- 危险命令被拒绝或要求确认。
- 用户拒绝后 Agent 收到普通工具结果并安全收尾。
- 超时或 Ctrl+C 能终止进程组。
- shell 包装的 Git 写命令在审批前被拒绝，审批预览只显示有界脱敏命令。
- 超大输出被截断，Agent 仍得到退出码和截断标记。

### 11.6 状态诚实性

- 写入失败时，最终回答不得声称文件已修改。
- 测试未运行时，最终回答明确标注未验证。
- Git 工作区原有用户改动不会被归因于 Morrow。

### 11.7 原生沙箱与真实工作区保护

- Auto Sandboxed 在包含当前未提交修改的临时快照中运行测试。
- 沙箱无法读取用户 Home、凭据、宿主 Socket 或工作区外测试文件。
- 网络和 loopback 默认不可用。
- 沙箱内删除或改写项目文件不会直接改变真实工作区。
- 沙箱生成的修改只有经过 ChangeSet 预览、冲突检查和适用审批后才能推广。
- 快照准备或收集超时后不遗留 `morrow-sandbox-*` 项目副本。
- 缺少平台后端或后端启动失败时不执行命令，也不回退 Host。

## 十二、测试与验证门禁

至少覆盖：

- 路径解析、符号链接、特殊文件与编码单元测试。
- Patch 成功、上下文不匹配、revision 冲突与原子写测试。
- Shell 退出码、超时、取消、子进程、输出截断测试。
- 沙箱规则、网络、挂载、环境、临时快照、逃逸和 fail-closed 测试。
- 风险策略和审批正反例。
- ToolCycle 在成功、失败、拒绝、取消下保持闭合。
- Fake Provider 驱动的端到端任务。
- Windows、macOS、Linux 的平台差异测试策略；无法在本机运行的平台通过 CI 补充。

默认完成命令继续遵守仓库 `AGENTS.md`：离线测试、相关测试、Ruff、Compileall 和 `git diff --check`。Live 测试必须单独授权。

## 十三、阶段指标

- Fixture Code Task 成功率。
- 每个成功任务的平均工具调用、模型轮次和修正次数。
- 工具失败、审批拒绝、超时和取消后的恢复率。
- 路径安全用例拦截率。
- 修改后测试通过率。
- 最终回答中“声称执行但没有对应工具证据”的数量，目标为 0。

## 十四、主要风险与缓解

| 风险 | 缓解 |
|---|---|
| Shell 变成绕过所有工具边界的万能入口 | 动态风险预检、受控工作目录、最小环境、默认拒绝外部/危险命令 |
| 原生沙箱只保护宿主却允许命令破坏真实项目 | 在临时项目快照中执行，真实工作区不以可写方式挂载，修改通过 ChangeSet 推广 |
| 沙箱不可用时自动降级为 Host | 启动能力探测、明确 unsupported、任何自动模式 fail closed |
| 补丁误覆盖用户并发修改 | 基线哈希/mtime、上下文匹配、冲突返回、不做 last-write-wins |
| 搜索和大文件耗尽上下文 | 文件/字节/结果预算、截断标记、分范围读取 |
| 跨平台进程取消不一致 | 独立 Process Adapter、平台测试、进程组清理 |
| 用户无法判断实际修改 | ChangeSet 事实源、实际 Diff、最终回答引用验证结果 |
| 工具数量膨胀 | 以稳定能力服务为核心，工具保持少量、可组合 |

## 十五、阶段完成标准

只有同时满足以下条件，Stage 3 才能标记完成：

1. Agent 能在至少两个不同结构的 Fixture 项目中完成定位、修改和验证。
2. 默认无法读取或修改工作空间外路径，所有逃逸测试通过。
3. Patch 能检测并发变化，不静默覆盖。
4. Shell 超时、取消和超量输出不会卡死 AgentLoop 或留下失控进程。
5. Manual 与受限 Auto Safe 不会在宿主上自动运行项目代码或不透明命令。
6. Auto Sandboxed 可以在临时项目快照中自动运行项目命令，且平台缺失或隔离失败时不会回退 Host。
7. 沙箱默认无网络、无用户 Home/凭据/宿主 Socket，且不能直接修改真实工作区。
8. 高风险命令和修改不会在缺少明确授权时执行。
9. 用户可以查看实际 Diff、命令结果和已发生副作用。
10. 最终回答的修改与测试声明均能追溯到工具事实。
11. 相关离线测试、静态检查、平台隔离测试和人工 acceptance 通过。
12. `README.md` 与 `docs/ARCHITECTURE.md` 准确描述当前已实现能力。

Stage 3 完成后，Morrow 才具备后续持久 Task、学习和 Workflow 所需的真实任务基础。
