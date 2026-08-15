# Stage 2 Agent Core 修订审批稿 Review（可执行性审阅）

> 对象：[Stage 2 Agent Core 完整方案（修订审批稿）](stage-2-agent-core-final-proposal.md)（2026-08-14，回应 [审批稿 Review](stage-2-agent-core-final-proposal-review.md) 的修订版）
> 对照：现行权威路线 [`docs/roadmap/stage-2-agent-core.md`](../roadmap/stage-2-agent-core.md)、Stage 1 实际代码（`src/morrow` 当前树）、[`docs/ROADMAP.md`](../ROADMAP.md)
> 日期：2026-08-15
> 审阅问题：**这份计划是否可执行**
> 结论：**可执行，建议"有条件批准"。** 修订稿对 Stage 1 代码现状的全部关键事实假设均与仓库吻合，四个垂直切片顺序正确，全部不变量可以用现有依赖（Pydantic v2、标准库 tomllib）落地，无需新增第三方依赖。存在两个切片时序接缝和四处一句话级欠定义，均可在创建子计划时修复，不涉及架构返工。

---

## 0. 一句话判断

修订稿是三份 Stage 2 文档中第一份**可以直接拿去拆子计划开工**的文档：它对现有代码的每一条引用性陈述都属实，review 提出的全部 P0/P1 问题都有具体机制回应，被拒绝的建议也都给出了此前已确认要求的出处。剩余问题不在"设计对不对"，而在"切片之间怎么接"。

---

## 1. 事实核对：修订稿对 Stage 1 现状的假设全部成立

可执行性的第一道检验是计划描述的代码现状是否真实。逐条核对结果：

| 修订稿的假设 | 代码证据 | 结论 |
|---|---|---|
| `Session.accept_user()/accept_assistant()` 是公开可变写入口 | `src/morrow/runtime/session.py:28-33` | 属实 |
| `AgentRuntime.run_turn()` 自己写 Session | `src/morrow/runtime/agent.py:43,139-140` | 属实 |
| `ContextBuilder.build(session, current_user=...)` 可能重复追加当前 user | `src/morrow/application/context.py:76-81`（有按值去重，但契约上仍是双入口） | 属实 |
| `HandoffService._fallback()` 直接扫 `session.messages` 取最后 user/assistant | `src/morrow/services/handoff.py:29-46` | 属实 |
| `complete_structured()` 用 `type(context.messages[0])(...)` 猜测构造 | `src/morrow/runtime/structured.py:54` | 属实 |
| Provider 对消息直接 `model_dump()` | `src/morrow/adapters/models/openai_compatible.py:59-60` | 属实 |
| Provider 只处理 `delta.content`，无 tool-call fragment 组装 | `openai_compatible.py:74-79` | 属实 |
| 非 `stop` finish 一律转错误 | `openai_compatible.py:84-89` | 属实 |
| `ModelProvider.stream()` 无 `tools` 参数；`ModelEvent.completed` 不携带消息对象 | `src/morrow/core/ports.py:24-29`、`src/morrow/core/models.py:74-79` | 属实 |
| Stage 1 上下文预算 24000 字符 | `application/context.py:26` | 属实 |
| 终端按 `text.delta` 打印、`turn.completed` 只收尾，不会重复渲染答案 | `src/morrow/interfaces/terminal.py:19-25`；且当前 `completion_payload` 根本不含 `text` 字段（`core/events.py:51-52`） | 属实——修订稿 18.4 对前一份 review 的反驳成立 |
| 阶段边界测试按**目录名**（`tools`/`loop` 等）禁止，而非文件名 | `tests/test_stage_boundary.py:8-10` | 属实 |
| 无进展临时错误有限重试、有可见文本后不重试 | `runtime/agent.py:83-97` | 属实，`made_progress` 语义是现有行为的自然扩展 |

另外两项对可执行性有利的事实：

- **迁移面比想象的小**：`accept_user/accept_assistant` 在全仓库只有 14 处引用（2 个源文件、3 个测试文件）；直接读 `session.messages` 的非测试代码只有 `context.py` 和 `handoff.py` 两处。第八节的 Session 迁移是一个下午量级的机械改动，不是风险项。
- **零新增依赖**：Pydantic v2（`pydantic>=2.9`）已就位，`tomllib` 是 Python 3.12 标准库（`requires-python = ">=3.12"`）。第十节的参数校验方案和第十五节的 TOML 策略都不需要动 `pyproject.toml`。

---

## 2. 需要在批准前修订的问题（2 项）

以下两项都在第二十节"垂直切片实施计划"内部，属于切片时序问题，不触动任何锁定条款，但如果不改，第一个切片开工当天就会撞上。

### R1. 阶段边界测试的改写被排到 Slice 4，但 Slice 1 就可能违反它

`tests/test_stage_boundary.py` 禁止 `src/morrow` 下出现名为 `tools`、`loop` 等的**目录**。修订稿把"stage-boundary 测试改为禁止能力越界"排进 Slice 4，但 Slice 1 就要落地 Registry/Executor/AgentLoop。只要实现者按模块表起了 `src/morrow/tools/` 这样的目录名，默认 `pytest` 从 Slice 1 起就是红的。

**修订建议（二选一，写进第二十节）**：

1. 把边界测试改写提前到 Slice 1 第一批提交（该测试只有 10 行，改写成本低，且能力型守卫越早生效越好）；或
2. 明文约束 Slice 1–3 的落点只在现有目录（`core/`、`runtime/`、`application/`、`adapters/`），新目录留到 Slice 4 随边界测试一起引入。

顺带指出一处措辞失真：修订稿说改成"禁止能力越界，而不是禁止 `agent_loop.py` 文件名"——现行测试禁的是目录名不是文件名，`runtime/agent_loop.py` 今天就能通过。这不影响结论，但合并进正式路线时应按前一份 review 的准确表述改写。

### R2. Slice 1 与 Slice 2 之间的历史事实源过渡未定义

Slice 1 的完成标准包含"普通无工具聊天仍通过同一入口工作"，同时包含"最小 ConversationLog/ToolCycle 追加约束"；但 Session 迁移（Session 持有 Log、`accept_*` 降级、ContextBuilder/Handoff 改读 Snapshot）整段排在 Slice 2。于是 Slice 1 期间存在两种可能的中间态，都不是修订稿允许的终态：

- 两份历史并存：`run_task` 写 ConversationLog，`run_turn`/产品面仍写 `Session.messages`——正是第八节禁止的双写；
- 或者 `run_task` 同时写两处——一座 Slice 2 立刻拆掉的临时桥。

**修订建议**：把"Session 持有 ConversationLog、`run_task` 成为唯一写入者、`Session.messages` 降为只读派生 tuple"提前为 Slice 1 的交付内容；Slice 2 只负责清理剩余读者（Handoff fallback、structured、ContextBuilder 签名、测试夹具）。第一节已核实迁移面只有 14 处引用，Slice 1 扛得下。这样第八节"只能存在一条聊天历史写入路径"从第一个切片起就成立，而不是到 Stage 2 结束才成立。

---

## 3. 建议在子计划中补一句话的欠定义（4 项）

不阻塞批准，但每个都可能造成实现分歧，创建子计划时应各补一句。

| # | 位置 | 欠定义 | 建议裁定 |
|---|---|---|---|
| C1 | 11.4/11.6 vs 10.4 | `tool.status` 的 `skipped` 是公开事件状态，但 `ToolErrorCode` 没有对应码；被跳过 call 的 envelope 写什么未说明 | 写明映射：因取消跳过 → envelope `cancelled`；因预算/deadline 跳过 → envelope `budget_exhausted`；`skipped` 只出现在 `tool.status` |
| C2 | 10.5 vs 16.1 | `model_output_limit` 同时覆盖 Provider `finish=length` 和运行时"Cycle 最小闭合空间不足拒绝接纳 Assistant"两种成因，与第十六节"精确分类更可诊断"的自我论证略有张力 | 保留一个公开码可接受，但应在恢复矩阵注明双成因；或在该路径的 `tool.status`/error message 中带上运行时标记 |
| C3 | 15.1/15.3 | `ProviderToolSupport.safe_request_chars` 的**数据来源**未定义（静态表？provider 配置？TOML？） | 因 `None → 160k` fallback 已锁定，Slice 3 允许先全 `None` 起步，但需写明首个版本的声明位置（建议：随包 `agent-policy.toml` 内的按模型静态表，或全部留空） |
| C4 | 18.4/21.6 | mixed-content 终端分段和"人工可区分"目前只有人工验收 | 现有 `tests/test_terminal.py` 基础上补 `Terminal.show_event` 级单测：喂 `text.delta → tool.status → text.delta` 序列断言换行与分隔符位置，把分段规则变成离线可回归 |

---

## 4. 对前一份 review 裁决的复核

修订稿第一节裁决表逐条复核结果：

- **接受的 10 项**（垂直切片、单一入口、步间 deadline、Pydantic 校验、删除 Anthropic fixture、删除 RequestSizer Protocol 等）：每项在正文中都有对应的具体机制，不是口头接受。抽查确认：11.4 的 `min(tool_timeout, remaining_run_seconds)` 正确堵住了 review 6.2 指出的"32 calls × 120s 合法越过 1800s"漏洞；15.3 的 `min()` 公式正确堵住了"无 safe_request_chars 时直接发 800k"漏洞。
- **拒绝的 6 项**（模块边界、开发者配置、循环检测、最小 terminal 记录、旧结果清理、13 个停止码）：拒绝理由均为引用此前已确认的要求或给出新的论证（如 ToolCycle 等额上限的"单结果限制不能约束整组"），且都以"保留能力、降级为薄护栏、删除文件合同"的折中落地。这些是判断分歧而非事实错误，属于提案者的正当裁量，不构成不可执行因素。
- **一处对 review 的事实纠错**（18.4：终端今天不会重复渲染 completed.text）：核对 `terminal.py` 与 `events.py` 后确认**修订稿正确、原 review 有误**。这提高了对修订稿代码 grounding 的信任度。

唯一保留意见：15.2 的默认值（30 轮 / 1800s / 800k chars）对两个演示工具仍明显偏大，原 review 的 P1 批评只被"可配置 + min() 公式"部分化解。既然修订稿已把这些数值明确降级为"开发者策略、可按证据调整、不锁架构"，这不再是可执行性问题，只是 Slice 3 落地时建议先按保守值起测。

---

## 5. 合并进正式路线时需机械对齐的差异

修订稿第二十三节的批准动作是"合并进正式 Stage 2 路线"。与 `docs/roadmap/stage-2-agent-core.md` 对照，以下差异需要在合并时显式改写，避免两份"权威"并存：

1. **run_turn 的地位**：路线 9.1 保留它作为独立的无工具兼容入口；修订稿 8.1 降级为薄委托（或删除）。按修订稿。
2. **Anthropic fixture**：路线 15.1 验收要求 fixture 验证映射契约；修订稿 7.4 删除。按修订稿。
3. **旧结果清理顺序**：路线 11.4 区分成功/失败 Cycle 两套队列；修订稿 13.4 简化为严格时间序。按修订稿。
4. **实施顺序**：路线第十四节 6 步模块顺序及"不得在第一项实现完整 Agent Loop"；修订稿第二十节 4 个垂直切片整体取代。
5. **预算集合**：路线 9.4 的 5 个预算 → 修订稿 15.1 的 `AgentPolicy` 17 字段 + `ProviderToolSupport`。
6. **新增能力**：per-cycle call 上限、Cycle 字符等额上限、循环提前止损、开发者 TOML、步间 deadline——路线中均无，随合并写入。

另 `docs/ROADMAP.md` 阶段索引中 Stage 2 的状态行（"设计基线已锁定，尚未开始实现"）需同步更新。

---

## 6. 验收矩阵的可测试性抽查

- 21.1–21.5 的条目均可用 Fake Provider + 注入 Clock/小预算离线验证，与现有测试基础设施（`conftest.py` 的网络守卫、pytest-asyncio auto 模式）兼容。
- 默认验证命令（`pytest` / `ruff check` / `ruff format --check` / `python -m compileall`）与仓库现有工具链一致，无缺口。
- 21.6 中"十轮聊天、Provider、配置、Handoff、workspace 隔离、degraded mode、EOF 和 Ctrl+C 全量回归"对应现有测试文件全部在位（`test_cli_commands.py`、`test_state_and_workspace.py`、`test_structured_and_handoff.py` 等），回归是执行既有套件，不是新建。
- 唯一无法离线自动化的是 18.4 的人工可区分验收（见 C4 的补救建议）和可选 Live smoke（已正确标记为可选）。

---

## 7. 最终建议

> **有条件批准。** 条件只有两条，都改第二十节，不触动任何锁定条款：
>
> 1. **R1**：把阶段边界测试改写提前到 Slice 1（或明文约束 Slice 1–3 不新建 `tools/`/`loop/` 目录）；
> 2. **R2**：把"Session 持有 ConversationLog、单一写入口"提前为 Slice 1 交付内容，Slice 2 只清理剩余读者。
>
> C1–C4 在创建四个垂直切片子计划时各补一句话即可。第五节的路线合并差异清单建议随批准一并执行，避免双权威。

按修订稿自己的口径：修复 R1/R2 后即可创建 Slice 1 子计划开工；不建议再等一轮全文评审——剩余不确定性（流式组装的真实 Provider 行为、取消时序）只有第一个垂直切片才能暴露，这正是修订稿把 E2E 提前到 Slice 1 的目的。
