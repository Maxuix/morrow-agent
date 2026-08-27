# Morrow Agent 深度审计与可执行修复清单

**审计快照：** `f69a0ce9ec7efc6a7eea07f7c5a1bb36945e9dfa`  
**审计日期：** 2026 年 8 月 26 日  
**审计对象：** Agent Loop、模型适配、上下文、工具系统、会话与恢复、安全、Skill/MCP、CLI/TUI、测试、评测、CI 与产品化。

> 说明：以下是基于该提交的源码、架构文档、验收报告和仓库配置所做的源码级审计。当前执行环境在克隆仓库时无法解析 GitHub 域名，因此我没有独立复跑测试；文中“1081 个测试通过”“2/10 任务通过”等数字均来自仓库自己的验收记录，而不是我的独立复测结果。审计快照已固定到上述提交，避免后续主分支变化造成结论漂移。

---

## 一、结论先行

Morrow Agent **不是一个只完成了聊天和几个工具调用的早期原型**。

它已经具备很多成熟 Harness 才有的基础设施：

- 类型化工具协议；
- 工具调用前的持久化意图；
- 权限模式与审批；
- macOS 原生 Sandbox；
- 会话恢复、检查点与分支；
- SQLite 状态迁移；
- 上下文投影；
- 循环上限、超时、取消；
- Preferences、Profile、Knowledge、Skill 和 MCP 治理；
- 只读 Git 能力；
- 比较系统化的离线测试。

从 **Harness 基础设施完整度** 看，我会给它约 **7.5/10**。

但从 **真实代码任务完成能力** 看，目前只能给约 **3/10**。仓库自己的 Direct Agent 基线在十个任务上只有 **2 个通过、7 个失败、1 个阻塞**；六个 Morrow 自身历史任务全部失败，多数表现为大量阅读后没有产生文件修改，并最终触发轮次或工具调用上限。复测共发生 311 次工具调用，其中 54 次失败，还残留了 6 个非预期临时文件。验收报告自己的结论也是：当前还不适合复杂代码任务。

因此 Morrow 当前最准确的定位是：

> **拥有较强安全性、持久化和治理能力的 Code Agent Alpha，但还不是可以稳定承担日常中型代码任务的主力 Agent。**

### “离可使用还有多远”的分级判断

| 使用目标 | 当前状态 | 判断 |
|---|---:|---|
| 学习 Harness、演示架构 | 已达到 | 架构内容已经很丰富 |
| macOS 上处理明确的小型任务 | 有条件可用 | 必须由人审查 Diff 和测试结果 |
| 日常处理中型仓库任务 | 尚未达到 | 任务成功率和上下文控制不足 |
| 长时间自主执行 | 尚未达到 | 缺少 Steering、语义压缩、持续进度判断和完整清理 |
| Linux/macOS 跨平台使用 | 尚未达到 | Linux 原生安全执行后端未完成 |
| 无人值守或云端多租户 | 明显未达到 | 当前安全模型仍偏向本地单用户 |
| 面向外部用户发布的 1.0 产品 | 尚有较大距离 | 缺少 CI、发行、兼容矩阵、稳定自动化接口和更大规模评测 |

如果把“可信赖的本地日常 Code Agent”作为目标，Morrow 不是差最后一个 UI，也不是差几个新工具；它还缺少一个完整的：

# **Direct Agent Reliability——直接 Agent 可靠性阶段**

这个阶段应当插在当前 Stage 6 和计划中的 Workflow/Multi-Agent 之间。

---

## 二、Morrow 当前最突出的结构性问题

Morrow 现在存在一种很典型的 **基础设施倒挂**：

```text
安全治理、耐久状态、恢复能力
            明显领先
                │
                ▼
上下文决策、代码任务完成率、产品反馈闭环
            明显落后
```

仓库报告有 1081 个离线测试通过，Ruff、编译、帮助命令和 Diff 检查也通过；但真实任务基线仍然只有 2/10。这说明现有测试主要证明了：

```text
系统不会轻易损坏状态
权限边界基本正确
工具输入输出满足协议
恢复和迁移逻辑可以工作
```

它们还没有充分证明：

```text
Agent 能否在有限预算内找到正确文件
能否及时从探索转向修改
能否理解隐藏语义边界
能否完成修改、测试和清理
能否稳定重复成功
```

这并不是说现有测试没有价值。恰恰相反，安全与状态测试非常难得。但当前测试组合对“系统正确性”的覆盖远强于对“Agent 实际有效性”的覆盖。

因此接下来最错误的路线是：

```text
成功率不高
   ↓
增加 Planner Agent
   ↓
增加 Reviewer Agent
   ↓
增加工作流图
   ↓
成本、延迟和失败面一起放大
```

更合理的路线是：

```text
先让一个 Direct Agent 稳定完成任务
   ↓
建立真实评测和失败分类
   ↓
确定单 Agent 的能力边界
   ↓
只在有数据证明有效的节点引入多 Agent
```

仓库自己的路线图也将 Workflow、GUI、后台任务和产品化放在后续阶段，并提出应当与 Direct Agent 基线进行比较。

---

# 三、当前做得好的部分

## 3.1 Agent 执行内核不是玩具

`src/morrow/runtime/agent.py` 已经处理了：

- 模型调用与流式事件；
- Task/Turn 生命周期；
- 上下文构建；
- 模型错误；
- Tool Cycle；
- 运行截止时间；
- 工具轮次上限；
- 总工具调用上限；
- 重复工具调用检测；
- 用户取消；
- 会话写入；
- 工具意图持久化；
- 执行后状态收敛。

特别值得肯定的是：工具副作用发生前，调用意图会先进入耐久状态。这比“调用成功之后才写日志”的实现更利于崩溃恢复和审计。模型因长度或异常未正常完成时，也不会直接把不完整 Tool Call 当作合法操作执行。

## 3.2 工具和权限边界较强

现有工具使用 Pydantic 模型、不可变工具集合、调用前能力检查、审批、确定性错误封装、结果大小限制和事实提取。文件系统部分包含路径解析、符号链接防护、受保护凭据、版本冲突检查、原子写入等设计。命令工具还有危险操作识别、网络行为约束和 Git 写操作限制。

## 3.3 会话、恢复与状态层明显领先于一般原型

架构文档显示，Morrow 已建立 SQLite 状态迁移、恢复、Artifacts、Checkpoints、Forks、Preferences、Learning、Skills 和 MCP 等耐久能力。Agent Loop 被设计为单一聊天历史写入者，ContextBuilder 从不可变状态快照构造模型上下文。这些设计为后续崩溃恢复、分支、审计和产品化提供了很好的基础。

## 3.4 安全默认值较保守

Morrow 当前主要面向本地单用户场景，macOS 提供原生 Sandbox；Git 默认限制为只读，包安装、网络命令和高风险操作不会被静默放行。对一个仍在 Alpha 阶段的 Agent，这种默认保守是正确的。

这些基础不要推翻。后续修复应当围绕它们增加“任务有效性”，而不是重写一套 Agent 框架。

---

# 四、目前缺少的关键能力

## 4.1 缺少项目指令自动发现和分层

仓库已经明确把这一点列为高优先级改进项：当前不能像成熟 Code Agent 那样自动发现并分层处理：

- 根目录 `AGENTS.md`；
- 子目录局部 `AGENTS.md`；
- `AGENTS.override.md`；
- `CLAUDE.md`；
- 仓库约定；
- 当前文件路径所对应的局部规则。

这会直接影响真实任务。模型即使找到正确代码，也可能不知道：

- 该仓库禁止修改什么；
- 应使用什么测试命令；
- 代码风格和架构约束；
- 哪些生成文件不能手改；
- 某个目录特有的规则；
- 任务完成前必须做哪些验证。

Morrow 已经有项目输入不可信、权限治理和 ContextBuilder 的基础，因此不需要简单地把这些文件直接拼进最高权限 System Prompt；应当做成有来源、层级、大小限制和信任标记的 Project Instruction 层。

---

## 4.2 缺少真正的长期上下文压缩

当前 ContextBuilder 在预算不足时主要采用：

1. 将旧 Tool Result 替换为省略标记；
2. 删除较早完整 Turn；
3. 删除当前 Turn 中较早的 Tool Cycle；
4. 仍然超限时停止。

它没有模型生成或结构化生成的语义摘要。架构文档也明确说明 ContextBuilder 不调用摘要模型。当前所谓 Checkpoint 主要是状态元数据，而不是一份能够继续指导模型工作的任务摘要。

这种“删除旧消息”的方式容易丢失：

- 用户最初目标；
- 已确认的限制；
- 为什么排除了某个方案；
- 已读取和已修改文件；
- 测试失败的历史；
- 当前仍未解决的问题；
- 下一步应该做什么。

它会加剧验收中出现的“不断重新阅读”和“探索后没有行动”。

---

## 4.3 缺少运行中的任务状态与进度控制

当前循环能发现完全相同或非常相似的工具调用后缀，但无法充分识别：

```text
read file A lines 1-200
read file A lines 201-400
search keyword X
search keyword X with another glob
read file B
再回到 file A
```

这些调用参数并不相同，所以可能绕过简单的重复签名检测；但从任务进展看，Agent 可能已经连续多步没有获得新证据、没有建立假设、没有修改文件、没有运行有效验证。

验收报告中的六个内部任务都出现了大量探索后无修改、最终耗尽轮次的现象。这不是单纯“轮次上限太低”，而是 Agent 缺少从：

```text
探索 → 假设 → 修改 → 验证 → 清理 → 完成
```

进行阶段转换的进度控制。

当前缺少一个模型可见、系统也可检查的 Working State，例如：

```python
class WorkingState:
    goal: str
    constraints: list[str]
    current_hypothesis: str | None
    files_inspected: list[str]
    files_changed: list[str]
    tests_run: list[TestRecord]
    unresolved_questions: list[str]
    pending_steps: list[str]
    consecutive_no_progress_calls: int
```

这不等于强制模型遵循复杂工作流，而是让 Harness 能判断“是否取得新进展”。

---

## 4.4 文件工具还不能完整覆盖代码修改生命周期

当前文件变更模型主要是创建、替换和 Patch，没有一等公民级别的：

- 删除；
- 移动；
- 重命名；
- 批量 ChangeSet；
- 运行级回滚；
- 目录重构；
- 二进制文件处理。

这与验收中出现的临时测试文件残留直接相关。Agent 能创建测试文件，却缺少同等级安全、结构化的清理手段。

真实 Code Agent 不能只有：

```text
read + patch + run_command
```

它至少还需要安全表达：

```text
delete(path, expected_hash)
rename(source, destination, expected_hash)
move(source, destination)
apply_changeset(operations[])
revert_changeset(change_set_id)
```

每种操作都要进入审批、冲突检测、Sandbox Promotion、Artifact 和恢复机制。

---

## 4.5 结束条件过于依赖模型自己说“完成了”

成熟 Agent 不能仅凭模型返回一段自然语言就认为任务完成。

对于一个“修改代码”的任务，Harness 应在允许最终完成前至少检查：

- 是否真的产生了预期 Diff；
- 是否存在未跟踪临时文件；
- 是否运行了相关测试；
- 测试是否成功；
- 是否有命令失败后被模型忽略；
- 是否修改了任务范围外文件；
- 是否仍存在未处理的 Tool Error；
- 是否留下 Sandbox 中未 Promotion 的关键修改。

当前验收中既有“任务要求修改但没有修改”的失败，也有“通过测试后留下临时文件”的问题，说明需要一个确定性的 Completion Validator，而不是单纯再添加一个 Reviewer Prompt。

---

## 4.6 多工具调用能够接收，但执行仍然基本串行

Agent 能接收一个模型响应中的多个 Tool Call，但当前循环通过顺序 `for` 执行。对于多个互不依赖的只读调用，例如：

```text
read pyproject.toml
read README.md
read src/morrow/runtime/agent.py
```

完全串行会增加端到端延迟，并降低模型在相同预算内可以完成的有效工作量。

正确方式不是“所有工具全部并行”，而是按副作用分类：

```text
PURE_READ
    可以并行

WORKSPACE_WRITE
    按文件或 ChangeSet 串行

EXTERNAL_EFFECT
    审批后串行

SESSION_MUTATION
    严格串行
```

并行执行后，Tool Result 仍应按模型原始调用顺序写回上下文，保持确定性。

---

## 4.7 模型适配仍偏薄

当前主要生产适配器是 OpenAI-compatible Chat Completions 风格。请求包含消息、工具、Tool Choice 和流式处理，但能力模型仍比较有限：

- 没有统一暴露 Thinking/Reasoning 增量；
- 没有完整 Usage 事件；
- 没有持久化 Token/Cost；
- 没有模型原生 Token 计算；
- 没有充分表达图片、文档等内容块；
- 没有清晰的 Provider Capability Negotiation；
- 没有原生 Anthropic、Gemini 或 OpenAI Responses 语义；
- 没有系统化处理 Reasoning Effort、最大输出 Token、Prompt Cache 等模型能力。

当前上下文预算主要使用 JSON 字符数量估算，而不是模型对应的 Tokenizer；终端也没有向用户呈现 Token 和费用。

字符估算可以作为保守兜底，但不能成为长期主要预算机制。

---

## 4.8 Provider 重试缺少完整退避策略

当前 Agent 对一部分可恢复模型错误会重新进入调用，但在 Agent 主循环层没有看到完整的：

- 指数退避；
- 随机抖动；
- `Retry-After`；
- 总重试时间预算；
- 按错误类型区分策略；
- Provider 熔断；
- Endpoint 健康度。

立即重试会在限流、网络抖动或 Endpoint 故障时形成重试风暴。

---

## 4.9 工具和命令输出缺少完整的流式反馈

终端目前主要显示：

```text
tool step n/m: tool_name
```

结束后再展示工具成功数、失败数、改动文件和验证信息。公共事件主要包括 Turn、Status、Text Delta、Tool Status、Error 和 Turn Completed，缺少更丰富的：

- Tool 参数摘要；
- Tool Progress；
- stdout/stderr 增量；
- Usage；
- Context 压缩；
- Context 丢弃量；
- 模型重试；
- Approval Request；
- ChangeSet；
- Completion Validation。

命令执行结果以有界尾部为主，完整长日志没有自动进入可追溯 Artifact。

这会让用户看到 Agent “卡住”，却不知道是在编译、测试、下载还是等待。

---

## 4.10 缺少运行中的 Steering 和 Follow-up 队列

当前终端在等待完整任务执行时，不适合继续接收新的用户输入，因此用户很难在运行中说：

```text
不要改数据库层。
优先复用现有测试。
不要安装新依赖。
先停一下，我要补充约束。
```

仓库自己的改进文档也把 In-run Steering 列为缺失能力。

应当区分：

- **Steering Message**：当前 Run 尚未完成时，在安全点注入；
- **Follow-up Message**：当前 Run 正常完成后继续执行的新任务；
- **Abort**：立即取消模型流和工具进程。

这三者语义不同，不应只用 Ctrl+C 统一处理。

---

## 4.11 缺少面向自动化的 Agent 接口

仓库有运营类 CLI、Preferences、MCP、Skills 等命令，但缺少稳定的：

```bash
morrow run \
  --workspace . \
  --prompt "修复登录测试" \
  --events jsonl \
  --approval-policy deny-external
```

也缺少清晰的 SDK/RPC Agent 运行接口。现有 `--json` 更偏向列表或运营类命令，而不是完整的 Agent Event Stream。

这会限制：

- 自动评测；
- CI 集成；
- IDE 接入；
- Web UI；
- 批量回归；
- 远程调用；
- 可重复故障分析。

---

## 4.12 跨平台安全执行仍未完成

macOS 原生 Sandbox 是当前优势，但 Linux 原生隔离尚未完成；架构文档明确列出 Bubblewrap 等 Linux 后端尚未支持。普通 Host Command 即使经过审批，本质上仍以当前用户权限运行。

因此：

- macOS 本地 Alpha：可以继续；
- Linux 日常使用：还缺关键安全后端；
- 云端多租户：现有边界远远不够；
- Windows：需要独立设计，不能默认等同于 Linux。

---

## 4.13 受控环境准备能力不足

现有安全策略会限制包安装、Git 写入、代码生成器、复杂构建链和网络访问。仓库也把这一点列为高优先级改进。

这对安全是好事，但真实仓库常常需要：

- 根据锁文件安装依赖；
- 创建隔离虚拟环境；
- 运行数据库迁移生成器；
- 运行代码生成；
- 拉取测试依赖；
- 创建分支或暂存修改；
- 启动临时本地服务。

正确修复不是开放任意网络和 Shell，而是增加受控的 Environment Preparation Lane。

---

## 4.14 缺少稳定 CI 和发行工程

仓库根目录没有可见的 GitHub Actions 工作流，Actions 页面仍是初始化引导状态。`pyproject.toml` 的开发依赖主要是 pytest、pytest-asyncio 和 Ruff，没有看到强制类型检查、覆盖率阈值、依赖安全扫描、跨操作系统矩阵或安装产物验证。仓库也尚未形成完整 Releases、Packages、LICENSE、SECURITY 和公开发行流程。

当前 HEAD 的文档修订还留下了明显乱码，这不是 Agent 能力问题，但说明文档和验收材料也需要自动检查门禁。

---

## 4.15 核心文件已经出现维护性热点

几个模块已经很大：

- `runtime/agent.py` 超过千行；
- `interfaces/cli.py` 接近两千行；
- Bootstrap、Terminal、文件服务和工具运行时也承担了较多职责。

这不会立刻导致任务失败，但会让后续加入 Compaction、Steering、Usage、并发调度和 Provider 能力时更难保证不变量。重构应当发生，但不能优先于真实成功率修复。

---

# 五、建议的目标架构

不要重写现有系统。建议在现有架构上增加四个核心模块：

```text
用户输入
   │
   ▼
ProjectInstructionResolver
项目规则发现、分层、来源与信任
   │
   ▼
TaskProgressController
目标、假设、进展、预算和阶段控制
   │
   ▼
ContextAssembler
原始历史 + Working State + Compaction + 最近消息
   │
   ▼
Agent Loop
模型调用
   │
   ▼
ToolBatchScheduler
只读并行、写入串行、外部副作用审批
   │
   ▼
CompletionValidator
Diff、测试、临时文件、未处理错误、Sandbox 状态
   │
   ▼
会话完成或再次进入 Agent Loop
```

横向再增加：

```text
Model Usage / Cost / Retry
Event JSONL / SDK
Run ChangeSet / Undo
Linux Sandbox Backend
Evaluation Trace Store
```

建议坚持三个平面分离：

### 控制平面

负责：

- Agent Loop；
- Progress；
- Retry；
- Abort；
- Steering；
- Completion。

### 数据平面

负责：

- Messages；
- Tool Results；
- Artifacts；
- Session；
- Checkpoints；
- Compaction；
- Metrics。

### 策略平面

负责：

- Permissions；
- Approval；
- Trust；
- Sandbox；
- Network；
- Git；
- Dependency Install。

不要让这些逻辑继续集中进入 `agent.py`。

---

# 六、P0 修复清单：达到“可靠本地 Alpha”前必须完成

## P0-01 建立 Stage 6.5：Direct Agent Reliability

- [ ] **冻结新的 Workflow/Multi-Agent 主线开发，建立 `Stage 6.5 Direct Agent Reliability`。**

**建议文件**

```text
docs/roadmap/stage-6.5-direct-agent-reliability.md
docs/acceptance/direct-agent-release-gate.md
evals/code-agent-core/
```

**实施内容**

1. 固定当前 10 题基线、模型、Prompt、工具配置和预算。
2. 为每个失败任务保存脱敏事件轨迹。
3. 失败必须归类，至少包括：

```text
NO_EDIT
WRONG_FILE
SEMANTIC_ERROR
TEST_FAILURE
CLEANUP_FAILURE
POLICY_BLOCK
PROVIDER_FAILURE
ROUND_LIMIT
CONTEXT_LOSS
```

4. 所有 Harness 改动必须在固定评测上比较：
   - 成功率；
   - 工具调用数；
   - 无进展调用数；
   - Token；
   - 费用；
   - 时间；
   - 非预期文件；
   - 人工介入次数。

**验收标准**

- 每次核心 Agent PR 都能生成可比较的评测结果。
- 不再只记录 PASS/FAIL，而能确定失败属于模型、上下文、工具还是策略。
- Workflow 阶段只有在 Direct Agent 达到明确 Gate 后才能继续。

---

## P0-02 增加 Headless JSONL Agent 模式

- [ ] **实现稳定、无 TUI 依赖的 Agent 运行接口。**

**建议命令**

```bash
morrow run \
  --workspace ./repo \
  --prompt-file task.md \
  --events jsonl \
  --permission-mode auto-sandboxed \
  --non-interactive
```

**建议改动位置**

```text
src/morrow/interfaces/run_cli.py
src/morrow/interfaces/events.py
src/morrow/application/agent_service.py
src/morrow/core/events.py
```

不要继续把全部实现放进现有 `interfaces/cli.py`。

**事件至少包含**

```json
{"type":"run.started","run_id":"..."}
{"type":"model.started","attempt":1}
{"type":"text.delta","text":"..."}
{"type":"tool.started","call_id":"...","name":"read_file"}
{"type":"tool.progress","call_id":"...","summary":"..."}
{"type":"tool.completed","call_id":"...","is_error":false}
{"type":"approval.requested","approval_id":"..."}
{"type":"usage.updated","input_tokens":0,"output_tokens":0}
{"type":"context.compacted","before_tokens":0,"after_tokens":0}
{"type":"validation.completed","passed":true}
{"type":"run.completed","status":"success"}
```

**验收标准**

- 同一个任务可在 TUI 和 Headless 模式运行。
- 两种模式消费同一套核心事件。
- Headless 模式不导入 prompt-toolkit 或 Rich 组件。
- Event Schema 有版本号。
- 事件可重放生成最终运行摘要。
- 退出码能够区分成功、任务失败、权限阻塞、模型失败和用户取消。

---

## P0-03 实现项目指令发现和分层

- [ ] **增加 `ProjectInstructionResolver`。**

**建议改动位置**

```text
src/morrow/application/project_instructions.py
src/morrow/application/context.py
src/morrow/core/project_instructions.py
tests/application/test_project_instructions.py
```

**加载顺序建议**

```text
用户全局规则
    ↓
仓库根目录规则
    ↓
从根目录到当前工作目录的局部规则
    ↓
目标文件所在目录规则
    ↓
override 文件
```

**每一段指令都携带**

```python
InstructionSource(
    path: str,
    scope: str,
    digest: str,
    trust: "user" | "project",
    precedence: int,
)
```

**安全要求**

- 防止通过符号链接读取工作区外文件；
- 单文件和总大小限制；
- 二进制和异常编码处理；
- 项目指令必须标记为“项目提供的规则”，不能伪装成系统消息；
- 在 UI 中显示本轮加载了哪些规则；
- 文件变更时使用 Digest 失效缓存。

**验收标准**

- 根目录与子目录规则能按层级组合。
- Override 能覆盖普通规则。
- 相邻两个子目录可以得到不同规则视图。
- 恶意项目文件不能突破已有安全边界。
- 评测任务能够记录模型实际看到的指令来源。

---

## P0-04 从字符裁剪升级为 Token 预算和语义 Compaction

- [ ] **实现模型感知的上下文预算。**

**建议接口**

```python
class TokenEstimator(Protocol):
    def count_request(self, request: ModelRequest) -> int: ...

class ContextCompactor(Protocol):
    async def compact(
        self,
        source: ContextSlice,
        working_state: WorkingState,
    ) -> CompactionResult: ...
```

**预算规则**

```text
context_window
- max_output_tokens
- tool_schema_tokens
- safety_reserve
= 可用于历史的预算
```

优先使用 Provider 返回的 Usage 或对应 Tokenizer；无法识别模型时再使用保守字符估算。

- [ ] **将 Compaction 作为耐久 Session Entry 保存，而不是覆盖原始历史。**

建议摘要结构：

```text
用户目标
明确约束
已确认事实
关键决策及原因
读取过的关键文件
修改过的文件
测试及结果
失败方案
未解决问题
下一步
```

**必须遵守的不变量**

- 不能在 Tool Call 与对应 Tool Result 之间切分；
- 原始消息不得删除；
- Compaction 记录包含来源范围、摘要版本和 Hash；
- Compaction 失败不得破坏原会话；
- 最近若干 Turn 原文保留；
- 摘要应能被重新生成；
- Overflow Recovery 与主动阈值压缩分别处理。

**验收标准**

- 长会话压缩后仍能回答最初任务目标和约束。
- 压缩前后已修改文件列表完全一致。
- 重启后能够从 Compaction Entry 重建有效上下文。
- 触发压缩后不再重复读取已经明确记录的关键事实。
- 通过构造测试证明不会生成孤立 Tool Result。

---

## P0-05 增加 Working State 和无进展检测

- [ ] **实现 `TaskProgressController`，解决“不断阅读但不行动”。**

**建议状态**

```python
class WorkingState(BaseModel):
    goal: str
    constraints: list[str]
    hypothesis: str | None
    inspected_files: dict[str, str]       # path -> revision/hash
    changed_files: list[str]
    validations: list[ValidationRecord]
    pending_steps: list[str]
    unresolved_questions: list[str]
    latest_failure_signature: str | None
    consecutive_no_progress_calls: int
```

**何谓取得进展**

至少发生一个：

- 新的关键文件被定位；
- 建立或更新可证伪的假设；
- Diff 发生变化；
- 测试状态发生变化；
- 先前错误被消除；
- 用户问题被澄清；
- 下一步计划明显收敛。

单纯重复读取相邻行、换一种搜索表达式、再次运行相同失败命令，不应自动算作进展。

**建议控制规则**

```text
连续 4～6 个无进展工具调用
    → 注入系统 Steering：
       “你正在重复探索，请总结证据并选择修改、验证、询问或停止。”

继续无进展
    → 要求模型输出结构化决策

仍无进展
    → 结束为 needs_clarification 或 stalled
       而不是消耗到全局轮次上限
```

阈值应可配置，并通过评测调优。

**验收标准**

- 当前六个“无修改耗尽轮次”任务不再以相同模式失败。
- 失败时能清楚说明缺少什么信息，而不是只报告达到上限。
- 运行轨迹能够显示每个 Tool Call 是否产生新进展。
- Working State 可以进入 Compaction Summary。

---

## P0-06 增加安全的 Delete、Move、Rename 和 ChangeSet

- [ ] **补齐文件生命周期工具。**

**建议接口**

```python
delete_file(
    path: str,
    expected_sha256: str,
)

move_file(
    source: str,
    destination: str,
    expected_source_sha256: str,
    destination_must_not_exist: bool = True,
)

apply_changeset(
    operations: list[FileOperation],
)
```

**实施要求**

- 使用现有 Workspace Resolver；
- 处理符号链接和大小写不敏感文件系统；
- 删除必须校验原内容 Hash；
- Move/重命名尽量原子化；
- 目标存在时默认拒绝；
- 每项操作产生 Artifact 和审计事实；
- Sandbox Promotion 支持删除和重命名；
- 一批操作在逻辑上属于同一个 ChangeSet；
- 失败时明确指出已执行和未执行的操作；
- 最好支持基于 Preimage 的回滚。

**验收标准**

- Agent 能创建临时测试、使用后删除。
- 重命名文件时引用更新和验证可以在一个任务中完成。
- 冲突文件不会被静默覆盖。
- Sandbox 内删除能够正确 Promotion 到 Host。
- 新增针对验收中“残留临时文件”的回归测试。

---

## P0-07 增加 Completion Validator

- [ ] **在接受模型最终答案前执行确定性完成检查。**

**建议模块**

```text
src/morrow/application/completion.py
src/morrow/core/completion.py
```

**输入**

```python
CompletionContext(
    task_intent,
    working_state,
    change_set,
    tool_history,
    sandbox_status,
    validation_results,
)
```

**检查内容**

对于需要代码修改的任务：

1. 是否存在非空 ChangeSet；
2. 是否有失败但未处理的 Tool Call；
3. 是否运行了至少一种相关验证；
4. 是否存在非预期临时文件；
5. 是否有 Sandbox 变更未 Promotion；
6. 是否修改了受保护或任务范围外文件；
7. 是否出现“测试通过”但实际退出码非零；
8. 是否仍存在未解决冲突。

**输出**

```python
CompletionDecision(
    status="complete" | "needs_more_work" |
           "needs_user_input" | "blocked",
    reasons=[...],
    suggested_next_actions=[...],
)
```

**注意**

不能规定所有任务必须修改文件。查询、解释、审计类任务允许零修改；应先由确定性规则和模型共同识别 Task Intent。

**验收标准**

- 要求修改但没有 Diff 的任务不能被标记为成功。
- 有临时文件残留时必须重新进入清理阶段。
- 测试失败不能被最终文本掩盖。
- 最终答案中的测试声明必须能对应到真实命令记录。

---

## P0-08 捕获并持久化 Token、费用和上下文指标

- [ ] **扩展模型协议和事件，记录 Usage。**

**建议改动位置**

```text
src/morrow/core/models.py
src/morrow/adapters/models/openai_compatible.py
src/morrow/core/events.py
现有 SQLite migration/state adapter
```

**记录字段**

```text
provider
model
request_id
input_tokens
cached_input_tokens
output_tokens
reasoning_tokens
total_tokens
estimated_cost
first_token_latency
total_latency
retry_count
context_window
context_tokens_before_compaction
context_tokens_after_compaction
```

对于不返回 Usage 的兼容 Endpoint：

```text
usage_source = provider | tokenizer | estimated
```

必须显式标记估算来源。

**验收标准**

- 每个模型调用都有 Usage Record，哪怕是估算值。
- 每个 Run 能汇总 Token、费用、模型次数和压缩次数。
- Headless JSONL 和终端都能显示。
- 评测结果可以比较成功率与成本，而不仅是 PASS/FAIL。
- 费用计算表与 Provider 逻辑解耦，避免将价格散落在 Adapter 中。

---

## P0-09 对只读工具进行受控并行调度

- [ ] **增加 `ToolBatchScheduler`。**

**建议副作用分类**

```python
class ToolEffect(Enum):
    PURE_READ = "pure_read"
    WORKSPACE_WRITE = "workspace_write"
    PROCESS = "process"
    NETWORK = "network"
    SESSION_MUTATION = "session_mutation"
```

**调度规则**

- 多个 `PURE_READ` 可以并行；
- 同一真实文件路径上的写入串行；
- Workspace Write 默认按 ChangeSet 串行；
- Process 是否并行由资源策略决定；
- 外部副作用和审批严格有序；
- 任一工具要求 Sequential 时整批按顺序；
- Abort 传播到全部并行子任务；
- 最终 Tool Result 按模型原始 Tool Call 顺序写回。

**验收标准**

- 三个独立 Read 的总耗时接近最慢一个，而不是三者相加。
- 并行完成顺序不同不会改变消息历史顺序。
- 两个写同一文件的 Tool Call 不会发生覆盖竞态。
- 权限确认顺序稳定且可预测。
- 一个并行调用失败不会导致其他 Tool Result 丢失。

---

## P0-10 建立 Provider Retry Policy

- [ ] **将模型重试从 Agent 主循环中抽成独立策略。**

**建议接口**

```python
RetryPolicy(
    max_attempts,
    max_elapsed_time,
    initial_delay,
    max_delay,
    jitter,
    retryable_error_classes,
)
```

**处理**

- 429：尊重 `Retry-After`；
- 408/连接超时：指数退避；
- 5xx：有限重试；
- 认证错误：不重试；
- 参数错误：不重试；
- 上下文溢出：进入 Compaction Recovery，而不是普通重试；
- 已经产生 Tool Call 或可见进度后，不能盲目重放整个请求；
- 每次重试产生事件。

**验收标准**

- 通过 Fake Provider 注入 429、500、连接中断和半流中断。
- 不出现零间隔重试风暴。
- 总重试时间受 Run Deadline 限制。
- 用户可以看到当前是重试而不是“卡住”。
- 相同 Tool Call 不会因请求重放被重复执行。

---

## P0-11 建立 GitHub Actions 与质量门禁

- [ ] **添加持续集成。**

**建议工作流**

```text
.github/workflows/ci.yml
.github/workflows/package.yml
.github/workflows/eval-smoke.yml
```

**最小 CI**

1. Ruff Check；
2. Ruff Format Check；
3. 类型检查，选择 Pyright 或 Mypy；
4. Pytest；
5. 覆盖率阈值；
6. `python -m compileall`；
7. 构建 Wheel；
8. 在干净环境安装 Wheel；
9. `morrow --help`；
10. Session Migration 测试；
11. Fake Provider Agent Loop 测试；
12. 文档拼写和 Markdown 检查；
13. Linux 与 macOS 基础矩阵；
14. 依赖漏洞扫描；
15. 打包内容检查，防止漏文件。

**验收标准**

- 主分支不能合入失败测试。
- PR 能看到覆盖率变化。
- Wheel 安装后能够运行，不依赖仓库源码目录。
- 文档乱码和失效内部链接会被门禁发现。
- Sandbox 相关测试按操作系统正确跳过或运行，不静默忽略。

---

## P0-12 扩大评测并引入重复运行

- [ ] **将当前十题 Mini Eval 扩展为分层评测。**

当前十题适合低成本迭代，但仓库文档也说明它不是具有统计意义的公开排行榜。

**建议分层**

```text
Tier A：10 个快速回归任务
Tier B：30～50 个真实小中型任务
Tier C：少量长任务和复杂仓库任务
Security：权限、越界、恶意项目规则
Recovery：崩溃、取消、重启、半写入
```

**每个任务记录**

```text
pass@1
重复运行成功率
工具调用数
无进展工具调用数
Token
费用
持续时间
修改文件数
非预期文件数
测试结果
安全策略触发
人工介入
```

**验收标准**

- 同一模型、同一预算、同一环境至少进行重复运行。
- 报告平均值之外，还显示成功次数和波动。
- Prompt、工具或 Context 改动必须运行 Tier A。
- 定期运行 Tier B。
- 评测任务覆盖 Skill、MCP、长上下文、取消、恢复和 Sandbox。
- 与 Pi 或其他基线比较时严格保持模型、任务、预算和环境一致。

---

# 七、P1 修复清单：达到“日常主力 Beta”前完成

## P1-01 Linux 原生 Sandbox

- [ ] **实现 Bubblewrap 或容器型 Linux Backend。**

**要求**

- Workspace 以明确读写权限挂载；
- Home、SSH、Cloud Credentials 默认不可见；
- 默认断网；
- 可配置域名或网络能力；
- 限制 CPU、内存、进程数和文件大小；
- 超时后杀死完整进程树；
- Runtime/解释器以只读方式提供；
- Promotion 继续通过 ChangeSet；
- Sandbox 不可用时 Fail Closed。

**验收**

- Linux 下运行恶意路径、读取 SSH Key、Fork Bomb、无限输出和网络外连测试。
- 安全语义与 macOS Backend 有统一 Capability Matrix。
- Sandbox 退出后不遗留子进程。

---

## P1-02 受控依赖安装和环境准备

- [ ] **增加结构化 `prepare_environment` 能力，而不是开放任意安装命令。**

**支持范围**

- 根据已有锁文件安装；
- 只在 Sandbox 内执行；
- 使用项目局部环境；
- 用户审批；
- 网络域名白名单；
- 下载缓存；
- 禁止全局安装；
- 记录安装来源和文件变化；
- 设置时间和磁盘预算。

**优先支持**

```text
uv sync --locked
pip install --require-hashes
npm ci
pnpm install --frozen-lockfile
cargo fetch --locked
```

未检测到锁文件时默认不自动安装。

---

## P1-03 命令输出流式化并保留完整日志

- [ ] **改造 Process Service 和 Tool Event。**

**行为**

```text
stdout/stderr
    ├─ 小段流式发给 UI
    ├─ 有界摘要发给模型
    └─ 完整内容写入 Artifact
```

**验收**

- 长测试运行时终端持续显示进度。
- 模型上下文不会被数 MB 日志淹没。
- 用户可以打开完整日志。
- 日志截断提示包含 Artifact ID 和尾部范围。
- Abort 后不再产生迟到的输出事件。

---

## P1-04 In-run Steering 与 Follow-up 队列

- [ ] **在 Agent 内增加两个独立队列。**

```text
SteeringQueue
    在工具完成或模型调用前的安全点注入

FollowUpQueue
    当前 Run 原本结束后启动下一轮
```

**必须处理**

- Steering 与正在执行的写操作不能形成中间不一致；
- 新指令要记录来源和时间；
- Abort 优先级最高；
- Steering 注入后重新评估 Completion；
- TUI 必须允许运行中输入；
- Headless 模式通过 stdin 或 RPC 发送 Steering。

**验收**

- Agent 执行期间输入“不要改文件 X”，下一次写入前能够生效。
- Follow-up 不会被误当作本轮原始目标。
- Steering 被持久化并可恢复。

---

## P1-05 升级 Provider Capability Layer

- [ ] **从单一 OpenAI-compatible 字符串消息升级为能力协商。**

**统一能力**

```text
text
reasoning
tool_calls
parallel_tool_calls
images
prompt_caching
usage
max_output_tokens
reasoning_effort
structured_output
request_timeout
```

**建议结构**

```python
ProviderCapabilities
ModelCapabilities
ModelRequestOptions
ContentBlock
Usage
```

先保留 OpenAI-compatible Adapter，再逐步加入原生 Adapter。不要在 Agent Loop 中写 Provider 分支。

**验收**

- 不支持某项能力的模型在启动前明确降级或报错。
- Thinking 不会被误当成普通回复永久写入。
- Tool Call ID 和消息顺序跨 Provider 保持合法。
- Capability 测试使用录制 Fixture，不依赖真实网络。

---

## P1-06 Run 级 ChangeSet、Undo 与 Revert

- [ ] **为每个 Run 建立可回退的变更事务。**

**保存**

- 文件 Preimage；
- Postimage；
- Hash；
- 操作类型；
- Sandbox 来源；
- Approval ID；
- Run ID。

**提供**

```bash
morrow changes show <run-id>
morrow changes revert <run-id>
```

**验收**

- Host 直接修改和 Sandbox Promotion 都能回退。
- 文件在 Run 后又被用户修改时，Revert 必须检测冲突。
- 不允许用旧 Preimage 覆盖用户的新修改。
- Revert 本身也产生审计记录。

---

## P1-07 结构化 Git 写工具

- [ ] **在强审批下增加有限 Git Write，而不是直接开放任意 Git 命令。**

建议能力：

```text
git_create_branch
git_stage_paths
git_unstage_paths
git_commit
git_restore_paths
```

默认不提供：

```text
push
force push
reset --hard
clean -fdx
修改远端配置
```

**验收**

- Agent 只能暂存明确路径。
- Commit 前显示完整摘要。
- Dirty Workspace 下不会覆盖用户修改。
- Push 永远需要独立明确授权。

---

## P1-08 Repo Map 与符号级导航

- [ ] **减少反复全文搜索。**

第一阶段不必直接上完整向量数据库，可以先实现：

- 文件树摘要；
- 语言和包识别；
- Import/Dependency 图；
- 类、函数和符号索引；
- 测试与源文件关联；
- Git 最近修改热点；
- 文件摘要缓存；
- 内容 Hash 驱动增量更新。

后续再接 Tree-sitter 或 LSP。

**验收**

- 已索引仓库的初始定位工具调用显著减少。
- 文件改变后只重建受影响索引。
- Repo Map 是辅助证据，不作为未经读取的事实来源。

---

## P1-09 Plan 和运行状态可见性

- [ ] **让用户看到 Agent 当前处于什么阶段。**

终端至少显示：

```text
目标
当前假设
当前阶段
已检查文件
已修改文件
最近验证
下一步
剩余工具预算
```

这不是要求模型输出很长的计划，而是把 Working State 投影给用户。

**验收**

- 用户能判断 Agent 是在探索、修改、测试还是清理。
- Steering 可以针对当前步骤输入。
- Plan 的变化进入事件流和会话。

---

## P1-10 丰富公共事件协议

- [ ] **将 Event Stream 提升为稳定产品接口。**

增加：

```text
model.started
model.retrying
model.completed
tool.arguments
tool.progress
approval.requested
approval.resolved
changeset.updated
context.compacted
working_state.updated
usage.updated
validation.started
validation.completed
run.settled
```

**验收**

- Terminal、JSONL、未来 GUI 不需要解析日志字符串。
- 每个事件有 Run ID、Turn ID、时间戳和 Schema Version。
- 敏感 Tool 参数在事件输出前经过脱敏。
- 事件重放能还原用户可见运行状态。

---

# 八、P2 修复清单：产品化与高级能力

## P2-01 LSP 和 AST 级代码操作

- [ ] **增加符号跳转、引用查找、诊断和结构化重命名。**

优先提供：

```text
go_to_definition
find_references
document_symbols
workspace_symbols
diagnostics
rename_symbol
code_actions
```

这比让模型对大型文件持续做字符串搜索和全文替换可靠。

---

## P2-02 PTY 和后台进程

- [ ] **为开发服务器、Watcher 和交互命令设计受控进程会话。**

需要：

- PTY；
- Session ID；
- 输入发送；
- 后台日志；
- 心跳；
- 资源限制；
- 显式关闭；
- 崩溃恢复时的孤儿进程清理。

不要直接把现有 `run_command` 变成无限后台 Shell。

---

## P2-03 受控文档和网络获取

- [ ] **增加只读、域名受限、内容有界的 HTTP/Docs Tool。**

要求：

- 默认关闭；
- 域名白名单；
- DNS/IP 重绑定防护；
- 响应大小限制；
- 禁止访问本机和云 Metadata；
- 内容标记为外部不可信；
- 下载文件进入 Sandbox；
- 请求记录进入审计。

---

## P2-04 在 Direct Agent 达标后再引入 Workflow/Multi-Agent

- [ ] **仅对有数据证明能够提升成功率的任务采用多 Agent。**

推荐最先评估的节点：

```text
Direct Agent
    ↓
确定性 Completion Validator
    ↓
仅在复杂 Diff 上调用 Reviewer
```

不要一开始建立全局 Planner、Coder、Tester、Reviewer 四 Agent 流程。

每新增一个 Agent 节点都必须比较：

- 成功率增量；
- Token 增量；
- 延迟增量；
- 新失败模式；
- 取消与恢复复杂度。

---

## P2-05 IDE/GUI 接入

- [ ] **在 Headless Event API 稳定后再做 IDE 或桌面 UI。**

否则 UI 会反向绑定尚未稳定的内部类和数据库结构。

---

## P2-06 完成公开发行工程

- [ ] **补齐公共项目所需元数据。**

包括：

```text
LICENSE
SECURITY.md
CONTRIBUTING.md
CHANGELOG.md
兼容性矩阵
威胁模型
权限说明
数据与遥测说明
Release Checklist
签名或校验和
```

并建立：

- 版本号策略；
- Wheel 发布；
- 安装验证；
- 升级与数据库迁移测试；
- 回滚说明；
- 最低运行时兼容策略。

---

## P2-07 拆分超大模块

- [ ] **在 P0 行为被测试固定后，拆分维护热点。**

建议将 `runtime/agent.py` 拆为：

```text
runtime/run_controller.py
runtime/model_call.py
runtime/tool_batch.py
runtime/progress.py
runtime/termination.py
runtime/retry.py
```

将 `interfaces/cli.py` 拆为：

```text
interfaces/commands/run.py
interfaces/commands/session.py
interfaces/commands/skills.py
interfaces/commands/mcp.py
interfaces/commands/preferences.py
```

重构原则：

- 先用事件序列和不变量测试固定行为；
- 再移动代码；
- 不要在同一个 PR 同时重构和改变 Agent 决策逻辑。

---

## P2-08 故障注入、性质测试和模糊测试

- [ ] **从固定案例测试扩展到不变量测试。**

需要覆盖：

- Provider 在第 N 个 Token 断开；
- Tool 完成副作用后进程崩溃；
- SQLite 写入失败；
- JSON 或事件半写入；
- Sandbox 进程忽略终止信号；
- 两个写入并发命中同一文件；
- Compaction 在 Tool Pair 中间；
- Extension/MCP 返回异常数据；
- 超长路径、异常编码和符号链接环；
- 用户在 Approval 期间取消；
- 恢复后重复 Tool Call。

关键性质：

```text
每个已承诺 Tool Call 最终恰好有一个终态
未授权副作用永不发生
恢复不会重复已完成副作用
原始历史不会因压缩丢失
Tool Result 顺序确定
Abort 最终传播到所有底层操作
```

---

# 九、建议的前 12 个 PR 顺序

下面的顺序考虑了依赖关系，避免同时大改所有核心模块。

## PR 1：质量基线与 CI

- [ ] 新增 GitHub Actions。
- [ ] 固定当前测试和十题评测格式。
- [ ] 增加文档检查。
- [ ] 输出 Wheel 安装冒烟测试。

## PR 2：Headless JSONL

- [ ] 抽出核心 Event Schema。
- [ ] 新增 `morrow run --events jsonl`。
- [ ] 不改变 Agent 决策逻辑。

## PR 3：Usage 与运行轨迹

- [ ] Adapter 解析 Usage。
- [ ] Event 增加 Usage。
- [ ] SQLite 持久化 Run Metrics。
- [ ] 评测读取 Token、Cost 和重试。

## PR 4：项目指令加载器

- [ ] 根目录与局部规则发现。
- [ ] 来源、信任和大小限制。
- [ ] 加入 ContextBuilder。
- [ ] 添加安全测试。

## PR 5：Token Budget

- [ ] 引入 TokenEstimator。
- [ ] 将字符预算降为兜底。
- [ ] 输出 Context Budget Report。

## PR 6：Durable Compaction

- [ ] 新增 Compaction Entry。
- [ ] 实现结构化摘要。
- [ ] 保留原始历史。
- [ ] 增加恢复测试。

## PR 7：Working State 与无进展检测

- [ ] 实现 Progress Controller。
- [ ] 记录新证据、Diff、验证和重复失败。
- [ ] 增加 Steering 提示。
- [ ] 修复“阅读到轮次上限”。

## PR 8：文件删除、移动与清理

- [ ] 新增 Delete/Move/Rename。
- [ ] 扩展 ChangeSet 和 Sandbox Promotion。
- [ ] 添加临时文件清理回归。

## PR 9：Completion Validator

- [ ] 检查 Diff、测试、临时文件和未处理错误。
- [ ] 失败时重新进入 Agent Loop。
- [ ] 最终答案附带事实依据。

## PR 10：只读工具并行

- [ ] 工具副作用分类。
- [ ] Read/Search 并行。
- [ ] 写操作和审批保持顺序。
- [ ] 结果按原 Tool Call 顺序提交。

## PR 11：Retry 与命令流式输出

- [ ] Provider 指数退避。
- [ ] Retry-After。
- [ ] stdout/stderr Progress Event。
- [ ] 完整日志 Artifact。

## PR 12：Linux Sandbox

- [ ] Bubblewrap/Container Backend。
- [ ] 统一 Capability Matrix。
- [ ] Linux CI 安全测试。
- [ ] Fail-closed 验证。

完成这 12 个 PR 后，再重新决定是否进入 Workflow/Multi-Agent。

---

# 十、建议的发布 Gate

## Gate A：可靠本地 Alpha

- [ ] 当前十题快速基线至少达到 7/10。
- [ ] 简单和中等任务全部通过。
- [ ] 要求编辑的任务不再出现“无编辑却宣称完成”。
- [ ] 非预期临时文件为 0。
- [ ] Token、费用、持续时间和工具调用数全部可见。
- [ ] 连续无进展能够提前收敛，而不是耗尽轮次。
- [ ] 项目指令和 Compaction 已启用。
- [ ] CI、Wheel 安装和迁移测试通过。
- [ ] macOS 安全回归通过。

这里的 7/10 只是快速 Gate，不是最终质量证明。

## Gate B：日常主力 Beta

- [ ] 建立至少 30～50 个分层真实任务。
- [ ] 多次运行后成功率稳定，而不是偶然通过。
- [ ] 中型修改任务具有可靠的 Edit→Test→Cleanup 闭环。
- [ ] Linux 和 macOS 都具有强隔离执行后端。
- [ ] Headless JSONL 接口稳定。
- [ ] Steering、Undo、完整日志和 Completion Validator 可用。
- [ ] 没有高危路径逃逸、凭据泄露或重复副作用。
- [ ] 有明确的兼容性和升级策略。

## Gate C：公开 1.0

- [ ] 公共安装和升级体验稳定。
- [ ] Release、License、Security、Threat Model 完整。
- [ ] 支持的 Provider 与模型有明确兼容矩阵。
- [ ] 数据、日志和遥测策略透明。
- [ ] 有持续真实任务回归。
- [ ] 长任务、崩溃恢复和资源限制经过压力测试。
- [ ] Workflow/Multi-Agent 相对 Direct Agent 有可证明收益。
- [ ] 公共事件或 SDK 保持向后兼容。

---

# 十一、暂时不要做的事情

- [ ] **不要先扩展成复杂 Multi-Agent。** 当前问题首先发生在单 Agent 的探索、决策、修改和完成闭环。
- [ ] **不要简单提高工具轮次上限。** 这只会让无效阅读持续更久。
- [ ] **不要把所有历史继续塞进 Prompt。** 应做 Token Budget、Working State 和 Compaction。
- [ ] **不要直接开放任意包安装和网络命令。** 应建立受控环境准备通道。
- [ ] **不要通过 Prompt 声称命令是安全的。** 强边界仍必须在 Tool Policy 和 Sandbox。
- [ ] **不要为了并行而并行所有工具。** 写操作、审批和会话状态必须保持有序。
- [ ] **不要让 Reviewer LLM 代替确定性 Completion Validator。**
- [ ] **不要在没有行为测试的情况下直接重写 `agent.py`。**
- [ ] **不要只追求离线单元测试数量。** 必须增加真实任务成功率和失败分类。
- [ ] **不要把 Mini Eval 的一次运行当成统计结论。**

---

# 十二、最终判断

Morrow 最有价值的部分不是当前模型能完成多少任务，而是它已经搭好了很多难以事后补救的基础：

```text
安全边界
耐久状态
恢复语义
权限治理
工具协议
会话生命周期
```

这些基础说明项目方向是成立的。

但一个 Code Agent 最终要解决的是：

```text
在有限预算中
理解任务
找到正确代码
做出正确修改
运行有效验证
清理副作用
提供可信结果
```

Morrow 当前最大的缺口集中在中间这条任务链，而不是外围基础设施。

可以用一句话概括：

> **Morrow 已经拥有一副很结实的 Harness，但 Harness 中的 Agent 还没有形成稳定的“观察—决策—修改—验证—清理—完成”闭环。**

建议将下一阶段全部聚焦到以下六件事：

```text
1. Project Instructions
2. Token-aware Semantic Compaction
3. Working State 与 No-progress Control
4. 完整文件变更生命周期
5. Completion Validator
6. Headless Trace + Evaluation Gate
```

这六项完成并经过真实任务验证后，Morrow 才适合从“强基础设施 Alpha”升级为“可作为日常工具试用的 Code Agent Beta”。在此之前继续增加 Workflow、GUI 或更多治理模块，都会扩大系统表面积，却不一定改善最关键的任务成功率。