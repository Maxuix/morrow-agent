# Stage 7 前置：Direct Agent 复杂代码任务基线

> 执行日期：2026-08-25—2026-08-26  
> 基线 Revision：`05e3603090dce7955d89f72f08f7df2ed2d7b120`  
> 评测集：Code Agent Mini Eval（10 项，一次完整运行）  
> Agent / 模型：生产 Direct Agent / `opencode-go/mimo-v2.5`  
> 权限：`auto-sandboxed`；每项使用独立工作区；结果由工作区外 verifier 判定

## 结论

**基线已建立，Direct Agent 的复杂任务就绪度为 PARTIAL。** 原始完整运行结果为
`2 PASS / 8 FAIL / 0 BLOCKED / 0 INCONCLUSIVE`，总计 312 次工具调用、约 32 分 52 秒。
这足以作为 Stage 7 Workflow 对照基线，但当前 Direct Agent 本身尚不适合复杂代码任务。

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
- 重复策略：完整 10 项只运行一次；修复后只复测两个受工具缺口影响的代表任务。
- 成本限制：当前终端摘要不公开 Token 与货币成本，因此未推断或伪造这两项数据。

## 用户能力面清单

| 能力面 | 评测覆盖 | 证据 |
|---|---|---|
| 工作区进入与任务理解 | 是 | 10 项均由公开 REPL 读取 `TASK.md` |
| 目录、文件读取与代码搜索 | 是 | 所有任务均产生读取/搜索调用 |
| 文件修改与审批 | 是 | 4 项外部任务进入写入流程，共 12 次写审批 |
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

六个 Morrow 历史任务均在修改文件前终止，单项出现 32–49 次工具调用和 10–17 次非成功调用。
四个外部任务均留下 Agent 自建的测试文件，共 5 个非预期文件；这降低结果整洁度，但本次没有
证据证明删除/清理流程会阻塞 Stage 7，因此未扩展修复范围。

## 代表性复杂旅程

### 旅程 A：Session 恢复命令闭环（MORROW-003）

Agent 需要跨 CLI、Session 状态、持久化与测试定位问题。原始运行反复尝试不可用的测试命令，
无法获得可操作的拒绝原因，最终在未修改代码时耗尽 30 轮。修复后，现有项目测试运行时可在
sandbox 中执行，证明工具阻塞已解除；但 Agent 继续重复阅读和验证，41 次调用后仍耗尽轮次。

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

这两次复测只验证受影响工具路径，不能重算 10 项总分。完整 Stage 7 比较应继续使用同一评测协议，
并对困难任务分别运行修复后的 Direct 与 Workflow。

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
浏览器、长时间后台任务或多 Agent Workflow。Stage 7 可以开始，但应保持相同模型、权限、工作区、
外部 verifier 与预算，并优先比较 `MORROW-003`、`MORROW-005`、`MORROW-006`、
`EXTERNAL-003`、`EXTERNAL-004`。不要把本次两个局部复测当作新的完整 Direct 分数。

## 确定性验证

- 工具与持久化聚焦矩阵：`37 passed`。
- macOS 原生 sandbox 实机矩阵：`2 passed`。
- 完整离线门禁：`1081 passed, 2 deselected in 39.18s`。
- `ruff format --check`：449 files already formatted。
- `ruff check`、`compileall`、`morrow --help`、`git diff --check`：通过。

