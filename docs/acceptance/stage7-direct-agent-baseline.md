# Stage 7 前置：Direct Agent 复杂代码任务基线

> 执行日期：2026-08-25—2026-08-26  
> 基线 Revision：`05e3603090dce7955d89f72f08f7df2ed2d7b120`  
> 评测集：Code Agent Mini Eval（10 项，一次完整运行）  
> Agent / 模型：生产 Direct Agent / `opencode-go/mimo-v2.5`  
> 权限：`auto-sandboxed`；每项使用独立工作区；结果由工作区外 verifier 判定

## 结论

**基线已建立，Direct Agent 的复杂任务就绪度为 PARTIAL。** 修复前完整运行结果为
`2 PASS / 8 FAIL / 0 BLOCKED / 0 INCONCLUSIVE`；修复后完整复测为 `2 PASS / 7 FAIL /
1 BLOCKED / 0 NOT RUN / 0 INCONCLUSIVE`，总计 311 次工具调用（257 成功、54 失败）。
通过率没有提升，但工具级阻塞已转为可观察的策略拒绝/正常命令执行；当前 Direct Agent 本身
仍不适合复杂代码任务，结果可继续作为 Stage 7 Workflow 对照基线。

评测确认并修复了两个真正阻塞复杂任务的工具缺口：

1. 持久化工具执行只保存 Capability Policy 的拒绝结论，丢失安全原因码；Agent 收到通用拒绝，
   无法把重定向、管道或越界路径改写成允许的 `argv` 调用。
2. 原生 sandbox 的 `PATH` 固定为 `/usr/bin:/bin`，同时快照排除 `.venv` 且禁止读取原工作区；
   依赖型项目无法使用已经安装的测试运行时。

修复没有扩大权限：网络、安装依赖、Git 写入、Host HOME、原工作区写入及破坏性命令仍被拒绝。
代表性复测已经能在 sandbox 内运行项目测试，但两个复杂 Morrow 任务仍因过度探索、实现判断或
30 轮上限失败。这些剩余问题属于 Stage 7 要比较的 Agent/Workflow 行为，不再属于工具不可用。

## 测试依据

- 评测集 `self-check`：10/10 baseline 均失败、gold 均通过。
- 用户入口：使用生产 `morrow --dir WORKSPACE` REPL，提交一次普通任务请求，未调用内部捷径。
- Provider：使用现有 Keychain 凭据通过连接探测；凭据值、原始请求和推理未进入报告。
- Oracle：每项运行后使用工作区外 verifier；同时检查 Git diff 和非预期文件。
- 重复策略：修复前完整 10 项一次；修复后按用户要求再次完整运行 10 项。
- 成本限制：当前终端摘要不公开 Token 与货币成本，因此未推断或伪造这两项数据。

## 用户能力面清单

| 能力面 | 评测覆盖 | 证据 |
|---|---|---|
| 工作区进入与任务理解 | 是 | 10 项均由公开 REPL 读取 `TASK.md` |
| 目录、文件读取与代码搜索 | 是 | 所有任务均产生读取/搜索调用 |
| 文件修改与审批 | 是 | 4 项外部任务进入写入流程；修复前 12 次、修复后 15 次审批提示 |
| sandbox 命令与项目测试 | 是 | Morrow 任务暴露运行时缺口；修复后项目测试可执行 |
| Git 工作区差异检查 | 是 | 每项由外部脚本检查结果与意外文件 |
| MCP、Skill、浏览器、网络任务 | 否 | 不属于本代码 Agent 评测集，不能由本报告声称覆盖 |

## 完整基线结果

| ID | 难度 | 结果 | Verifier / 终止点 | 工具调用（成功/失败） | 用时 | 主要分类 |
|---|---|---|---|---:|---:|---|
| MORROW-001 | 简单 | FAIL | 5 项 verifier 失败；30 轮上限 | 42（31/11） | 464s | 工具阻塞 → 预算 |
| MORROW-002 | 中等 | FAIL | 3 项 verifier 失败；30 轮上限 | 44（34/10） | 198s | 工具阻塞 → 预算 |
| MORROW-003 | 困难 | FAIL | 2 项 verifier 失败；30 轮上限 | 46（36/10） | 166s | 工具阻塞 → 预算 |
| MORROW-004 | 中等 | FAIL | 1 项 verifier 失败；30 轮上限 | 40（25/15） | 158s | 工具阻塞 → 预算 |
| MORROW-005 | 困难 | FAIL | 8 项 verifier 失败；30 轮上限 | 32（20/12） | 126s | 工具阻塞 → 预算 |
| MORROW-006 | 困难 | FAIL | verifier 收集失败；实现缺失；30 轮上限 | 49（32/17） | 121s | 工具阻塞 → 预算 |
| EXTERNAL-001 | 简单 | PASS | 13 passed | 13（10/3） | 226s | 通过；残留临时测试 |
| EXTERNAL-002 | 中等 | PASS | 4 passed | 13（12/1） | 99s | 通过；残留临时测试 |
| EXTERNAL-003 | 困难 | FAIL | 2 passed, 2 failed | 11（10/1） | 151s | Agent 语义判断 |
| EXTERNAL-004 | 困难 | FAIL | 16 passed, 1 failed | 22（21/1） | 263s | Agent 语义判断 |

六个 Morrow 历史任务均在修改文件前终止，单项出现 26–51 次工具调用和 2–9 次非成功调用。
四个外部任务均留下 Agent 自建的测试文件，共 5 个非预期文件；这降低结果整洁度，但本次没有
证据证明删除/清理流程会阻塞 Stage 7，因此未扩展修复范围。

## 修复后完整复测

以下矩阵使用同一公开 REPL、同一模型/权限和同一工作区外 verifier。所有驱动均正常退出；
`BLOCKED` 只表示 Provider 环境在 MORROW-006 结束前超时，不能当作产品 verifier 通过。

| ID | 用户任务与动作 | 实际证据 | 状态 |
|---|---|---|---|
| MORROW-001 | 读取任务，检查 CLI/Provider 代码，运行离线测试并修复 | 40 次调用（31/9），未改文件；verifier 5 failed；结束于未预期错误 | FAIL |
| MORROW-002 | 诊断 Provider 错误分类并运行相关测试 | 51 次（42/9），未改文件；verifier 3 failed；工具轮次上限 | FAIL |
| MORROW-003 | 跨 Session 恢复、CLI 与持久化完成闭环 | 44 次（35/9），未改文件；verifier 2 failed；工具轮次上限 | FAIL |
| MORROW-004 | 修复 Preference Writer 恢复状态并验证 | 40 次（32/8），未改文件；verifier 1 failed；工具轮次上限 | FAIL |
| MORROW-005 | 修复异步 Preference Review 生命周期 | 43 次（34/9），未改文件；现有 46 项测试通过后仍未实现目标 | FAIL |
| MORROW-006 | 实现安全分层 Runtime Policy 并验证安全边界 | 26 次（24/2），未改文件；模型连接超时，verifier 未运行 | BLOCKED |
| EXTERNAL-001 | 实现 NANP 电话号码清洗并运行验证 | 11 次（8/3），修改 2 个文件；外部 verifier 13 passed | PASS |
| EXTERNAL-002 | 实现简化 Grep，验证标志、顺序和空结果 | 21 次（19/2），修改 5 个文件；外部 verifier 4 passed | PASS |
| EXTERNAL-003 | 实现 Reactive Cells 稳定传播并验证回调 | 16 次（15/1），修改 2 个文件；外部 verifier 4 failed | FAIL |
| EXTERNAL-004 | 实现 Forth 子集解释器并验证重定义 | 19 次（17/2），修改 1 个文件；外部 verifier 16 passed/1 failed | FAIL |

修复后总工具调用为 311（257 成功、54 失败），其中 `run_command` 有 16 次策略拒绝：
13 次 `destructive_not_enabled`、3 次 `outside_workspace`；拒绝原因已持久化且没有权限绕过。
MORROW-001/002/003/004/005 的命令路径均能继续读取或执行测试，未再出现“看不到既有项目运行时”
这一共同工具阻塞。MORROW-006 的两次模型瞬态重试后仍连接超时，归类为 Provider/环境阻塞。

本轮还观察到 15 次写入审批提示（外部任务），无用户人工介入；测试驱动使用 PTY 自动确认，
不改变评测任务内容。外部工作区共留下 6 个 Agent 自建的非预期文件（包括测试文本/fixtures），
仍记录为清理质量问题，不作为本轮工具修复对象。

## 代表性复杂旅程

### 旅程 A：Session 恢复命令闭环（MORROW-003）

Agent 需要跨 CLI、Session 状态、持久化与测试定位问题。原始运行反复尝试不可用的测试命令，
无法获得可操作的拒绝原因，最终在未修改代码时耗尽 30 轮。修复后的完整复测中，命令工具可以
继续执行并返回结果，但 Agent 继续重复阅读和验证，44 次调用后仍耗尽轮次。

### 旅程 B：Reactive Cells 稳定传播（EXTERNAL-003）

Agent 成功读取实现、修改代码、运行自测并进入隐藏 verifier。工具链完整，最终失败来自回调参数
传递了 cell 对象而不是值。这是实现语义错误，不应通过修改工具掩盖。

### 旅程 C：Forth 子集解释器（EXTERNAL-004）

Agent 完成多轮读取、写入和测试，17 项隐藏检查通过 16 项；剩余失败是词重新定义的编译语义。
该旅程证明 Direct Agent 可完成较长工具链，但缺少对边界语义的稳定推理与回归覆盖。

### 旅程 D：安全分层 Runtime Policy（MORROW-006）

任务需要理解跨模块配置权威与安全边界。原始运行在 49 次工具调用后仍未写入实现，外部 verifier
因目标能力缺失而无法完成收集。其前置工具阻塞已由共同修复覆盖；是否能完成架构型实现应留给
Stage 7 Direct/Workflow 对照验证。

## 工具修复与复测证据

### 已修复：拒绝诊断不可恢复

- `PreparedIntent` 现在持久化最多 8 个经过校验的 policy reason code。
- Direct 与 durable ToolCycle 使用同一条有界、安全的拒绝消息。
- `run_command` 明确说明 stdout/stderr 已自动捕获，并提示使用 `argv`、移除重定向、管道和
  工作区外路径；同时明确网络、依赖安装、Git 写入和破坏性操作不可绕过。
- 回归测试证明持久化原因码可在恢复后的模型工具消息中看到，且不会回显完整参数。

### 已修复：sandbox 看不到既有项目运行时

- 只选择现有的工作区 `.venv`、当前 `sys.prefix` 与 `sys.base_prefix`；拒绝 `/`、HOME、
  失效路径和工作区外的伪 `.venv`。
- 仅把精确 toolchain root 以只读方式暴露给原生 sandbox，并由这些精确 `bin` 构造 PATH；
  不继承任意 Host PATH。
- macOS Seatbelt 实机回归证明 sandbox 内可导入当前运行时的 `pydantic`，同时仍禁止网络、
  HOME、原工作区读取/写入，只允许变更快照。

### 修复后代表性复测

| ID | 结果 | 工具行为变化 | 仍未通过的原因 |
|---|---|---|---|
| MORROW-001 | FAIL | 27 次调用（22/5）；3 个项目命令成功，policy deny 降至 2 次 | 未产生实现，最终内部失败；无新的共同工具缺口证据 |
| MORROW-003 | FAIL | 41 次调用（34/7）；项目测试成功执行 | 重复探索后仍触发 30 轮上限，属于 Agent/预算行为 |

此前两次代表性复测已被本轮完整复测覆盖；完整复测仍显示通过率 `2/10`，不能把工具调用成功数
的变化解读为任务成功率提升。Stage 7 比较应继续使用同一评测协议，并对困难任务分别运行修复后的
Direct 与 Workflow。

## 发现与优先级

| 优先级 | 发现 | 状态 | Stage 7 处置 |
|---|---|---|---|
| P1 | durable policy deny 丢失原因，Agent 无法安全恢复 | 已修复 | 纳入公共工具契约 |
| P1 | native sandbox 无法访问既有只读项目运行时 | 已修复 | 保持精确 root，不扩大 Host 能力 |
| P2 | Direct Agent 在复杂仓库任务中过度阅读并耗尽 30 轮 | 未修复（非工具） | 作为 Workflow/分解/上下文策略的核心对照指标 |
| P2 | 隐藏边界语义仍易遗漏 | 未修复（推理） | Stage 7 评估 reviewer/test 节点是否改善 |
| P3 | 自建临时测试文件未清理 | 记录，不修复 | 若未来造成提交污染，再独立验证删除/提升流程 |

## 覆盖缺口与下一步

本轮只证明本地代码 Agent 的读、写、搜索、sandbox 命令、测试和验证路径；没有覆盖 MCP、Skill、
浏览器、长时间后台任务或多 Agent Workflow。Provider-backed 10 项均已启动，9 项完成 verifier，
MORROW-006 因连接超时 BLOCKED。Stage 7 可以开始，修复后的完整复测可作为 current Direct
baseline，但应保持相同模型、权限、工作区、
外部 verifier 与预算，并优先比较 `MORROW-003`、`MORROW-005`、`MORROW-006`、
`EXTERNAL-003`、`EXTERNAL-004`。

## Provider 证据

- 适配器/模型：`opencode-go/mimo-v2.5`；Provider-backed 测试已获授权并实际执行。
- 调用范围：10 个独立公开 REPL 任务；当前终端仅公开工具调用摘要，不公开模型调用数、Token
  或货币成本，因此不推断这些指标。
- 结果：9 项完成外部 verifier；MORROW-006 在两次可恢复瞬态后连接超时，标记 BLOCKED。
- 没有切换模型、复制凭据或把原始 Provider payload 写入报告。

## 确定性验证

- 工具与持久化聚焦矩阵：`37 passed`。
- macOS 原生 sandbox 实机矩阵：`2 passed`。
- 完整离线门禁：`1081 passed, 2 deselected in 39.18s`。
- `ruff format --check`：449 files already formatted。
- `ruff check`、`compileall`、`morrow --help`、`git diff --check`：通过。
- 修复后完整 Direct 复测：`2 PASS / 7 FAIL / 1 BLOCKED`；所有 10 项公开 REPL 驱动正常退出。
