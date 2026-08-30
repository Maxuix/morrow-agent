# 当前正式链路可行性测试

> 日期：2026-08-30
> 结论：**PASS**（当前正式本地链路未因旧版兼容清理而阻塞）
> 已执行场景：15 PASS、0 FAIL、0 BLOCKED、0 NOT RUN、0 INCONCLUSIVE；未具备目标状态的能力缺口另列于 inventory。

## Test basis

- 离线阶段从 `main@a740a3e` 开始；真实 Provider 阶段从文档收口后的 `main@b28f088` 开始。两个阶段运行时共有的四个既有 runtime/test 改动随后形成 `6a65b68`，因此本轮最终被测工作树内容与 `6a65b68` 一致；报告改动不参与被测行为。
- 平台为 Darwin 25.6.0 arm64，Python 3.13.0；测试时间为 2026-08-30（Asia/Shanghai）。
- 从 README、`morrow --help` 及各命令组 help 建立公开能力清单，再以用户可调用入口执行。
- 第一阶段使用一次性目录 `/tmp/morrow-chain-audit.0FaQt8/`，隔离 workspace、state root、Provider 配置、Skill 与 MCP 定义；Provider 使用本机 loopback 的确定性 OpenAI-compatible 测试服务。
- 用户在第二阶段明确授权 Provider 测试。先通过正式 `provider test` 验证当前配置，再把不含秘密的 Provider/Model 配置复制到一次性 state root `/tmp/morrow-live-provider.E04JGK/state`；真实凭据仍只由系统 CredentialStore 解析，不进入文件或输出。
- 真实 Provider 阶段使用当前配置 `opencode-go/deepseek-v4-flash`，覆盖连接检查、SSE 文本流、跨进程 Session 续接、function calling、工具结果回送和多轮工具自纠正。一次默认 state 的连接检查只更新公开的 `last_test` 状态；会话和文件操作均发生在隔离 state/workspace。
- MCP 使用仓库公开测试夹具 `tests/spikes/fake_mcp_stdio_server.py`；Skill 使用一次性最小 `SKILL.md` 包。
- 内部 API 只用于解释失败原因，不作为通过证据。

## User surface inventory

| 能力面 | 公开入口 | 本轮状态 | 证据范围 |
|---|---|---|---|
| 工作区与交互对话 | 主命令、`workspace relink` | PASS | 新目录确认登记、目标输入、REPL 对话、保存退出；relink help 可达 |
| 无头正式运行 | `run` | PASS | 新 Session JSONL、续接 Session、稳定终态与 AgentRun metrics |
| Provider | `provider list/show/presets/add/remove/test/configure` | PASS | 离线 add/list/show/presets/test；真实 `opencode-go` 连接检查连续通过；configure/remove 未对已验证配置做破坏性操作 |
| Model | `model list/show/add/sync/use/remove/current` | PASS | add/list/show/sync/use/current；remove 未对当前唯一模型执行 |
| Session | `session list/create/status/resume/archive/fork` | PASS | list/status、跨回合恢复、闭合 Turn fork、archive；create 由正式运行创建 |
| Task | `task show/list/new/accept/cancel/resume` | PASS | 自动创建、show/list、accept 与 Learning Review 入队；无故障 Task，cancel/resume 未执行 |
| AgentRun | `agent-run show` | PASS | 两次模型请求、一次工具轮次、一次成功工具调用均可查询 |
| Artifact | `artifact list/show/pin/release` | NOT RUN | 本次 `write` 不发布 Artifact；空列表可查询，无目标可执行 show/pin/release |
| Recovery | `recovery show/resolve` | NOT RUN | 正式链路无 interrupted/pending execution；空查询通过，无恢复目标 |
| Grant | `grant list/show/create/revoke` | NOT RUN | 当前 manual 文件工具无需 elevated grant；空列表通过 |
| State | `state doctor/events/backup/verify-backup/cleanup` | PASS | schema 22 health ok、完整 bundle create/verify、cleanup dry-run；无 application event 可列出 |
| Preferences | `preferences status/list/write/show/add/replace/remove/enable/disable` | PASS | workspace Preference 完整 CRUD、启停及 revision 冲突拒绝；自然语言 write 未单独触发 |
| Learning | `learning status/set-mode/inbox/show/reviews/promotions/undo/accept/edit/reject/review/retry/request/list` | INCONCLUSIVE | Task accept 原子入队、status/reviews/promotions/空 inbox；无候选，决策与 Reviewer 执行未运行 |
| Memory | `memory list/show/disable/enable/dispute/delete`、`memory selection list/show` | NOT RUN | 空列表可达；无已晋升 Memory 或 Selection 可操作 |
| Skill | `skill list/show/validate/install/enable/disable/pin/rollback/remove/draft-*/usage` | PASS | validate/install/list/show/enable/pin/disable/remove；仅一个版本，无合法 rollback 目标；Draft/Usage 无候选 |
| MCP | `mcp add/list/show/inspect/status/enable/disable/remove/refresh` | PASS | Fake stdio discovery、4 工具 Catalog、2 工具映射、enable/status/inspect/disable/remove |
| Live Provider | `provider test`、`run` | PASS | `opencode-go/deepseek-v4-flash` 完成文本、续接和 4 轮工具调用；真实凭据未进入证据 |
| 网络 MCP | 当前无公开远程 transport | NOT RUN | MCP 当前只实现 stdio；没有把本次 Provider 网络授权扩展为不存在的远程 MCP 测试 |

## Scenario results

| 场景 | 状态 | 可观察结果 |
|---|---|---|
| 首次工作区登记与 REPL 对话 | PASS | 明确确认后登记；模型文本流返回；`/exit` 保存对话并以 0 退出 |
| 已登记工作区的 headless 普通回合 | PASS | `turn.started → text.delta → turn.completed → run.completed` JSONL 完整 |
| 指定 Session 续接 | PASS | Session ID 与 TaskRun ID 保持，第二回合产生独立 Turn/AgentRun |
| 模型发起工作区写入 | PASS | `write` 状态 running/succeeded；`hello.txt` 落盘为 `hello\n`；随后模型完成回答 |
| AgentRun 与任务闭合 | PASS | metrics 显示 `model_attempts=2`、`tool_calls=1`、`tool_rounds=1`、成功工具 1；Task 从 ready_for_acceptance 转为 accepted |
| 学习队列衔接 | PASS | Task 接受后同链路生成 pending Learning Review；未执行真实 Reviewer 质量 lane |
| Preference 生命周期 | PASS | add/replace/disable/enable/remove 依次递增 revision；旧 revision 写入被拒绝且未覆盖新状态 |
| Doctor、完整备份与校验 | PASS | health ok、schema 22；manifest/database/FK/files/YAML/skills/artifacts/references 全部 ok；credentials excluded |
| Skill 生命周期 | PASS | 包校验、安装后 disabled、显式 enable、pin、disable、remove 均成功 |
| MCP 生命周期 | PASS | refresh 得到 ready Catalog；allowlist 工具 ready、未映射工具保持 unmapped；启停和移除成功 |
| 未登记 workspace 的 headless 请求 | PASS | 以退出码 2 fail closed，明确要求先交互确认，没有隐式登记 |
| 真实 Provider 连接检查 | PASS | 当前与隔离 state 的 `provider test opencode-go` 均以 0 退出并显示“连接成功” |
| 真实 Provider 普通流式回答 | PASS | 完整 JSONL 返回 `LIVE_OK`；AgentRun 为 1 次模型请求、0 工具、`finish_reason=stop` |
| 真实 Provider Session 续接 | PASS | 恢复相同 Session 后正确回忆上一轮标记 `LIVE_OK`，Session/Task 身份保持不变 |
| 真实 Provider 工具自纠正 | PASS | 模型调用 `write → read → bash → read`，4 次工具全部成功，最终仅回复 `TOOL_OK`；文件精确为 17 字节 `provider-live-ok\n`，Doctor health ok |

## Complex journeys

1. **从零配置到可续接对话**：Provider add/test → Model add/use/current → 工作区交互登记 → REPL 对话 → headless 新回合 → 指定 Session 续接 → Session/Task/AgentRun 查询。
2. **模型工具调用到可审查任务闭合**：模型生成 `write` function call → 权限与工具执行 → 文件落盘 → 第二次模型请求 → JSONL 终态 → Task accept → Learning Review 入队。
3. **持久状态治理**：Preference CRUD 与乐观并发拒绝 → Doctor → 当前完整 Backup → verify-backup → cleanup dry-run。
4. **扩展控制面**：Skill validate/install/enable/pin/disable/remove；Fake stdio MCP add/refresh/Catalog mapping/enable/status/inspect/disable/remove。
5. **真实 Provider 连续性与工具自纠正**：连接检查 → 隔离工作区登记 → 普通流式回答 → 跨进程恢复同一 Session 并回忆先前标记 → 新 Session 中真实 function calling → 读取验证 → 发现缺少尾换行 → 使用 Host 命令修正 → 再读确认 → AgentRun/Doctor 查询。最终用户文件目标达成，且状态保持健康。

## Findings

### F-01 — 控制面错误信息过于概括（已修复）

- 状态：**PASS**（2026-08-30 修复并回归）
- 修复前复现：对 Preference 使用过期 revision 时只输出 `application command failed`；MCP Server ID 不满足 `mcp_...` 约束时只输出泛化的 ValidationError 文案。
- 原因：CLI 通用异常翻译没有把已知冲突详情或 Pydantic 字段约束映射为安全、具体的用户诊断。
- 修复：Preference 写入口只翻译已有的类型化 `ToolExecutionError`；MCP 只输出移除 input/context 的首条字段校验详情，不回显原始参数。
- 验证：旧 revision 现在返回 `preference_conflict: Preference document revision is stale`；非法 ID 返回 `invalid_configuration`、`server_id` 和当前格式约束。两者仍以退出码 2 fail closed。
- 与本次清理关系：未发现因果关系；这是已有控制面可诊断性不足。

### F-02 — 同一 state root 上并发 MCP 查询可能返回 busy（已修复）

- 状态：**PASS**（2026-08-30 修复并回归）
- 修复前复现：5 个全新 state root 中，每次并发发起 `mcp list/show/status` 都至少有一个命令失败，错误包括 `busy`、`unavailable` 或 `needs_repair`；已完成初始化的同一 state root 连续 10 轮并发查询全部通过。
- 原因分析：`mcp add` 过去只发布 YAML desired state；首次查询再用读写连接懒初始化 Catalog SQLite。多个进程可同时观察到缺失或初始化中的数据库，从而竞争维护锁或读取未完成状态。
- 修复：`mcp add` 在发布定义前完成 Operational Store 初始化；查询使用只读连接；无 MCP 定义的空查询不再创建 SQLite。没有增加重试框架或长期持锁。
- 验证：5 个全新 state root 上重复 `add → 并发 list/show/status`，15 个查询全部以 0 退出；MCP/CLI 聚焦回归通过。
- 影响：没有观察到状态损坏；修复限定在 MCP 控制面初始化与查询模式。
- 与本次清理关系：未发现因果关系；兼容代码移除后，AgentLoop、工具执行或持久化主链均未出现对应失败。

### Test-harness correction — 非产品缺陷

本地 fake Provider 最初把 `stream=false` 的连接检查也返回为 SSE，导致 `provider test` 报空响应。按 OpenAI-compatible 非流式响应修正测试桩后立即通过；正式 SSE 对话和 function calling 此后也通过，因此该失败不归因于产品。

## Coverage and gaps

- 本轮证明当前 macOS 本地正式链路和一个当前配置的真实 Provider 样本可行；它不证明跨模型质量、限流、计费、长时网络稳定性或高并发行为。
- 没有合成 interrupted execution、corrupt Artifact、Learning Candidate、Memory 或多版本 Skill，因此相应 resolve/decision/rollback 分支为 NOT RUN，而不是 PASS。
- Linux 原生沙箱、远程 MCP、后台 Worker/调度和 Stage 7+ Workflow 不在当前已交付正式链路范围。
- 历史 acceptance 文档仍是决策/执行记录；当前行为以 README、ARCHITECTURE、ROADMAP、当前阶段文档和本报告为准。

## Provider evidence

- 真实 Provider：**PASS**。Adapter/Model 为 `openai-compatible` / `opencode-go/deepseek-v4-flash`。
- 共发生 9 次最小必要 Provider completion：2 次连接检查、1 次普通回答、1 次 Session 续接回答、5 次工具任务模型请求。三个 headless AgentRun 可见 token usage 合计 35,499；Provider 未提供可用成本数据。
- 普通回答和续接分别得到 `LIVE_OK`；工具任务完成 4 次工具调用并得到 `TOOL_OK`，最终文件字节为 `70 72 6f 76 69 64 65 72 2d 6c 69 76 65 2d 6f 6b 0a`。
- 工具任务首次 `write` 后模型从 `read` 结果发现缺少尾换行，并自行用 `bash` 修正再读取。现有证据指向模型首次工具参数未满足精确格式，而非 Morrow 丢失工具调用或状态；结果正确，但该样本的工具效率为 5 次模型请求、4 轮工具。
- 本地 loopback Provider：**PASS**，覆盖连接检查、普通流式回答、工具调用、工具结果回送、Session 续接与指标持久化。
- 没有把 scripted/fake Provider 结果表述为真实模型质量证据。

## Validation gates

| 门禁 | 结果 |
|---|---|
| 正式用户链路（隔离 workspace/state、loopback Provider、Fake stdio MCP） | PASS |
| 真实 Provider 正式链路（连接、文本、续接、工具、自纠正） | PASS；9 次 completion，4 次工具全部成功，Doctor health ok |
| CLI 诊断与 MCP 首次并发查询修复 | `52 passed in 3.95s`；5 个全新 state root 的 15 个并发查询全部通过 |
| 聚焦 CLI/headless/AgentRun/MCP/Skill/Provider/Backup/Preference 回归 | `123 passed in 11.00s` |
| `uv run pytest -m 'not live'` | `1284 passed, 2 deselected in 103.07s` |
| `uv run ruff format --check .` | `483 files already formatted` |
| `uv run ruff check .` | `All checks passed!` |
| `uv run python -m compileall -q src tests` | PASS |
| `uv run morrow --help` | PASS |
| 当前文档本地链接检查 | 8 个当前文档，0 个缺失目标 |
| `git diff --check` | PASS |

## Recommended actions

1. 保留 Preference 冲突、MCP 字段校验和 add-before-parallel-query 回归，避免重新落回泛化错误或读写查询。
2. 若要从“本次真实样本可行”提升为生产质量声明，继续执行多次长回合、限流、断网/中断恢复及其他已配置模型的统计评测，并单独记录成本与失败率。
