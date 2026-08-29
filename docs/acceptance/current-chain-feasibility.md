# 当前正式链路可行性测试

> 日期：2026-08-30
> 结论：**PASS**（当前正式本地链路未因旧版兼容清理而阻塞）

## Test basis

- 从 README、`morrow --help` 及各命令组 help 建立公开能力清单，再以用户可调用入口执行。
- 使用一次性目录 `/tmp/morrow-chain-audit.0FaQt8/`，隔离 workspace、state root、Provider 配置、Skill 与 MCP 定义；未读取或修改用户正式数据。
- Provider 使用本机 loopback 的确定性 OpenAI-compatible 测试服务，覆盖非流式连接检查、SSE 文本流和 function calling；没有外部网络或真实凭据。
- MCP 使用仓库公开测试夹具 `tests/spikes/fake_mcp_stdio_server.py`；Skill 使用一次性最小 `SKILL.md` 包。
- 内部 API 只用于解释失败原因，不作为通过证据。

## User surface inventory

| 能力面 | 公开入口 | 本轮状态 | 证据范围 |
|---|---|---|---|
| 工作区与交互对话 | 主命令、`workspace relink` | PASS | 新目录确认登记、目标输入、REPL 对话、保存退出；relink help 可达 |
| 无头正式运行 | `run` | PASS | 新 Session JSONL、续接 Session、稳定终态与 AgentRun metrics |
| Provider | `provider list/show/presets/add/remove/test/configure` | PASS | add/list/show/presets/test；configure/remove 未对已验证配置做破坏性操作 |
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
| Live Provider / 网络 MCP | 显式 live lane | BLOCKED | 用户未授权真实网络/凭据；未运行，也不以 fake 结果替代 |

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

## Complex journeys

1. **从零配置到可续接对话**：Provider add/test → Model add/use/current → 工作区交互登记 → REPL 对话 → headless 新回合 → 指定 Session 续接 → Session/Task/AgentRun 查询。
2. **模型工具调用到可审查任务闭合**：模型生成 `write` function call → 权限与工具执行 → 文件落盘 → 第二次模型请求 → JSONL 终态 → Task accept → Learning Review 入队。
3. **持久状态治理**：Preference CRUD 与乐观并发拒绝 → Doctor → 当前完整 Backup → verify-backup → cleanup dry-run。
4. **扩展控制面**：Skill validate/install/enable/pin/disable/remove；Fake stdio MCP add/refresh/Catalog mapping/enable/status/inspect/disable/remove。

## Findings

### F-01 — 控制面错误信息过于概括

- 状态：**FAIL**（低严重度诊断问题，不阻塞正式链路）
- 复现：对 Preference 使用过期 revision 时只输出 `application command failed`；MCP Server ID 不满足 `mcp_...` 约束时只输出泛化的 ValidationError 文案。
- 原因：CLI 通用异常翻译没有把已知冲突详情或 Pydantic 字段约束映射为安全、具体的用户诊断。
- 影响：状态仍 fail closed，数据没有被覆盖；但用户难以仅凭 CLI 判断应刷新 revision 或修正 Server ID。
- 与本次清理关系：未发现因果关系；这是已有控制面可诊断性不足。

### F-02 — 同一 state root 上并发 MCP 查询可能返回 busy

- 状态：**INCONCLUSIVE**（低严重度并发边界，顺序正式链路通过）
- 复现：同时发起 `mcp list/show/status` 时，`status` 一次返回 `busy`；随后按正常用户顺序执行 refresh、enable、status、inspect、disable、remove 全部成功。
- 原因分析：MCP 查询组装会为 Catalog 打开可写 Operational Store handle；多进程同刻竞争 SQLite/迁移锁时可能 fail closed。
- 影响：没有状态损坏，也未阻塞顺序控制面；是否需要读连接或有限 busy retry 应另立实现任务验证。
- 与本次清理关系：未发现因果关系；兼容代码移除后，AgentLoop、工具执行或持久化主链均未出现对应失败。

### Test-harness correction — 非产品缺陷

本地 fake Provider 最初把 `stream=false` 的连接检查也返回为 SSE，导致 `provider test` 报空响应。按 OpenAI-compatible 非流式响应修正测试桩后立即通过；正式 SSE 对话和 function calling 此后也通过，因此该失败不归因于产品。

## Coverage and gaps

- 本轮证明当前 macOS 本地正式链路在隔离状态下可行，不证明真实 Provider 的回答质量、限流、计费或长时网络稳定性。
- 没有合成 interrupted execution、corrupt Artifact、Learning Candidate、Memory 或多版本 Skill，因此相应 resolve/decision/rollback 分支为 NOT RUN，而不是 PASS。
- Linux 原生沙箱、远程 MCP、后台 Worker/调度和 Stage 7+ Workflow 不在当前已交付正式链路范围。
- 历史 acceptance 文档仍是决策/执行记录；当前行为以 README、ARCHITECTURE、ROADMAP、当前阶段文档和本报告为准。

## Provider evidence

- 真实 Provider：**BLOCKED**（无显式授权与凭据）。
- 本地 loopback Provider：**PASS**，覆盖连接检查、普通流式回答、工具调用、工具结果回送、Session 续接与指标持久化。
- 没有把 scripted/fake Provider 结果表述为真实模型质量证据。

## Validation gates

| 门禁 | 结果 |
|---|---|
| 正式用户链路（隔离 workspace/state、loopback Provider、Fake stdio MCP） | PASS |
| 聚焦 CLI/headless/AgentRun/MCP/Skill/Provider/Backup/Preference 回归 | `123 passed in 11.00s` |
| `uv run pytest -m 'not live'` | `1280 passed, 2 deselected in 103.15s` |
| `uv run ruff format --check .` | `483 files already formatted` |
| `uv run ruff check .` | `All checks passed!` |
| `uv run python -m compileall -q src tests` | PASS |
| `uv run morrow --help` | PASS |
| 当前文档本地链接检查 | 8 个当前文档，0 个缺失目标 |
| `git diff --check` | PASS |

## Recommended actions

1. 将已知 revision conflict 与 MCP schema/ID 校验翻译成安全、明确的 CLI 错误；保持退出码 2 与 fail-closed 语义。
2. 为并发 MCP 查询建立独立回归，确认只读查询是否应使用只读 handle，或对可恢复 SQLite busy 采用有界策略。
3. 若要宣告真实 Provider 生产可用性，另行授权 Live lane，并使用兼容凭据执行长回合、function calling、限流和中断恢复场景。
