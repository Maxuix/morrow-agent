# Stage 4 真实用户场景测试报告

## 修复复核（2026-08-20）

本报告提出的问题已基于当前分支重新核对：

- **R-001 确认存在并已修复。** Session application 现在构造一个绑定同一 journal、workspace
  和 workspace root 的 `RecoveryService`，同时注入 `SessionPersistence` 与
  `OperationalApplicationService`；Recovery 发现、acknowledge 和 resume 不再使用断裂的服务装配。
- 新增从 `build_session_application(..., resume_session_id=...)` 进入的崩溃恢复回归测试，覆盖
  `/recovery` 展示、acknowledge、resume，以及内存与持久化 Session health 最终恢复为 `ok`。
- **CLI 的 opaque command ID 摩擦确认存在并已优化。** `recovery resolve` 省略
  `--command-id` 时会自动生成幂等 ID；需要安全重试同一请求的客户端仍可显式复用该参数。
- **CLI resume 的说明缺口确认存在并已优化。** 命令帮助与 README 现在明确：独立 CLI 的
  `resume` 只提交恢复决策并准备 AgentRun，不调用 Provider；后续需重新进入对应 Session。
- `artifact_orphan` 仍被确认是独立 Artifact 的正确诊断，不属于存储损坏，未改变其行为。

修复后验证：Recovery/CLI 定向测试 `16 passed`；完整非 live 测试 `609 passed, 1 deselected`；
Ruff check、Ruff format check 与 compileall 均通过。未运行 live/real-network 测试。

## 结论

本轮在 Stage 4 最终 review-fix 提交 `e6f9547` 上，使用临时隔离状态目录执行了 Session、REPL、Recovery、TaskRun、Artifact、checkpoint/fork、Backup、CLI 及权限边界场景。

核心持久化与 CLI 流程均可用，但发现一个会阻塞普通用户恢复工作的高优先级问题：

> Session REPL 可以发现 Recovery 报告，也可以显示 `/recovery ack ...` 的确认动作；确认后真正执行时却报“Recovery 报告不存在”。

因此，发生工具执行中断后，普通用户无法在 `morrow --session-id ...` 的正常 REPL 中完成 acknowledge、abort、quarantine 或 resume。独立的 `morrow recovery resolve` CLI 入口可以绕过该问题，但这不是正常交互路径的等价替代。

本轮按测试任务要求只记录和分析问题，没有修改代码，也没有重复调用 Grok review。

## 测试信息

- 测试日期：2026-08-20
- 被测提交：`e6f9547 fix(stage4): close final recovery review findings`
- 分支：`feat/stage4-operational-store`
- 状态目录：每个场景均使用一次性临时目录，测试后自动清理
- Provider：`ScriptedModelProvider`，用于稳定复现用户可见回答；未使用真实网络或凭据
- CLI：Typer `CliRunner`，执行真实 CLI application boundary
- Crash 模拟：先把未完成 ToolExecution 和未闭合对话写入 Operational Store，再关闭句柄并用同一个 Session ID 重启，模拟进程在工具执行阶段退出

这种方式覆盖了真实产品边界和持久化重启行为，同时避免把真实命令、网络或用户凭据带入测试。

## 场景结果

| 编号 | 用户操作 | 结果 | 观察 |
|---|---|---|---|
| U01 | 新建 Session，发送普通问题，获得回答 | PASS | 用户消息、回答、Turn、AgentRun 和 TaskRun 均持久化 |
| U02 | 重启并恢复同一个 Session | PASS | Session ID 保持不变，历史消息恢复，health 为 `ok` |
| U03 | `/status`、`/task`、`/accept`、`/exit` | PASS | TaskRun 从 `ready_for_acceptance` 进入 `accepted`，生成 TaskOutcome，退出提示会保留历史 |
| U04 | 创建 deterministic checkpoint | PASS | `chk_real` 创建成功，投影有边界、预算和 retained record 信息 |
| U05 | 按 checkpoint fork Session | PASS | 子 Session 成功创建，父子 lineage、cut position 和 checkpoint ID 正确保存 |
| U06 | 发布、读取、CLI 查看和 pin Artifact | PASS | Artifact 可读、完整性校验通过，CLI pin 后 retention 为 `pinned` |
| U07 | 尝试持久化 synthetic secret-like Artifact | PASS | Artifact 被拒绝，错误为 `artifact content is not redacted` |
| U08 | 创建并验证 Operational Backup | PASS | SQLite integrity、foreign keys、manifest、Artifact 均通过；`credentials_excluded=True` |
| U09 | CLI session/task/artifact/state/events/fork | PASS | 相关命令均以 exit code 0 完成并输出可读结果 |
| U10 | Crash 后重启，查看 `/recovery` | PASS | Session 进入 `needs_recovery`，Recovery report 和 item 能显示 |
| U11 | 在 Session REPL 中执行 `/recovery ack ...` 并确认 | **FAIL / Blocker** | 确认动作出现，但实际执行报 `RuntimeError: Recovery 报告不存在` |
| U12 | 使用独立 CLI `recovery resolve` 做 acknowledge + resume | PASS | 报告最终为 `resolved`，Session health 恢复为 `ok` |
| U13 | `/grant` 权限边界 | PASS | 普通 `manual` 明确不授予额外权限；`full-access-manual` 先显示风险提示，再允许 arm 下一次 AgentRun |

## 已确认问题

### R-001：Session REPL 无法执行 Recovery 决策

- 严重度：Blocker / P1
- 影响范围：崩溃恢复后的正常交互路径
- 复现稳定性：100%（同一装配路径重复复现）

复现步骤：

1. 在 Operational Store 中写入一个未闭合的 `read_file` ToolExecution 和对应的未闭合 ToolCycle。
2. 关闭存储句柄，使用同一个 Session ID 重启 Session application。
3. 观察到 `session.health == needs_recovery`，执行 `/recovery` 可以看到：

   ```text
   Recovery 报告：rrp_1
   状态：open
   rit_1 read_file：completed；resolution=open；可选=acknowledge
   ```

4. 执行 `/recovery ack rit_1`，系统正确返回 `action=resolve_recovery`。
5. 模拟 REPL 确认后调用 `commands.resolve_recovery(...)`，得到：

   ```text
   RuntimeError: Recovery 报告不存在
   ```

实测装配结果为 `products.api.recovery is None`。原因是 [bootstrap.py](/Users/ruirui/Documents/Project/Agent/developing/src/morrow/bootstrap.py:371) 创建 `OperationalApplicationService` 时传入了 tasks、artifacts、checkpoints、forks 和 persistence，但没有传入 recovery service。之后 [commands.py](/Users/ruirui/Documents/Project/Agent/developing/src/morrow/application/commands.py:162) 通过 `api.get_recovery()` 读取报告；由于 API 没有 recovery service，返回 `None`，最终显示“Recovery 报告不存在”。

影响：

- `/recovery` 的只读展示可用，但所有需要确认的 Recovery 决策均不能从正常 REPL 完成。
- Session 保持 `needs_recovery`，用户无法继续普通输入，也无法在 REPL 内完成恢复闭环。
- CLI 旁路可工作，因此不是数据不可恢复，而是主用户入口被阻塞。

建议修复方向（本轮未执行）：

- 在 Session application 的 bootstrap 过程中构造并注入与 persistence 使用同一 workspace/journal 的 `RecoveryService`。
- 增加一条从 `build_session_application(..., resume_session_id=...)` 开始的集成回归测试，至少覆盖 `/recovery` 展示、ack、resume 和最终 `health=ok`，避免只测试底层 `OperationalApplicationService` 而漏掉生产装配。

## CLI 旁路验证

同一份 Recovery 状态使用 CLI 操作成功：

1. `recovery show ses_crash`：exit code 0，报告可见。
2. `recovery resolve rrp_1 acknowledge --command-id cmd_cli_ack --item-id rit_1`：exit code 0，item 变为 `acknowledge`。
3. `recovery resolve rrp_1 resume --command-id cmd_cli_resume`：exit code 0，报告变为 `resolved`。
4. `session status ses_crash`：exit code 0，Session health 为 `ok`。

这证明 Recovery journal、幂等 receipt 和状态迁移本身可用，问题集中在 Session application 的 service wiring，而不是底层 Recovery 数据结构。

CLI 路径存在一项较轻的体验摩擦：`recovery resolve` 要求用户手动提供 opaque `--command-id`。它服务于幂等和冲突检测，但普通用户通常不需要理解该 ID 的语义；后续可以考虑在 CLI 中自动生成并只在调试/审计输出中显示。该项不是本轮发现的阻塞问题。

## 非缺陷观察

- `state doctor` 在测试中输出 `artifact_orphan` warning，是因为测试用例发布了没有 provenance/reference 的独立 Artifact；数据库健康仍为 `ok`，Backup 验证也通过。它更像是对“未关联 Artifact”的正确诊断，不应与存储损坏混淆。
- `recovery resolve ... resume` 的 CLI 操作只完成持久化决策和 AgentRun 准备，不会自动调用 Provider；随后仍需通过带有有效 Provider 配置的 Session resume 继续模型回合。这与 CLI/REPL 分层一致，但可以在帮助文本中说明。

## 验证门禁

本轮实际执行：

```text
62 passed in 1.71s
605 passed, 2 skipped, 1 deselected in 10.45s
```

两个 skipped 是需要宿主机 Seatbelt 的真实 sandbox 测试，在当前嵌套 Codex sandbox 中按项目规则跳过；没有运行 live/real-network 测试。

完整 Stage 4 最终 Grok 审核报告见：[stage-4-final-grok-review.md](/Users/ruirui/Documents/Project/Agent/developing/docs/reviews/stage-4-final-grok-review.md)。
