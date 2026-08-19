# Stage 4 最终只读代码审核报告

> **审查者：** Grok Build（代表 Codex）
> **日期：** 2026-08-20
> **活动：** 全量只读审查；未修改 `src/`、`tests/` 或其他既有文件；本文件是唯一写入物
> **代码基线：** 当前工作树，分支 `feat/stage4-operational-store`，`HEAD` `b2a37e9 docs(acceptance): close Stage 4`
> **对照合同：** `.agent/PLAN.md`、Subplans 36–45、`docs/decisions/stage-4-*.md`、`docs/ARCHITECTURE.md`、`docs/roadmap/stage-4-task-session-and-persistence.md`、`docs/acceptance/stage-4-durable-agent-evidence.md`、既有 `docs/reviews/stage-4-subplan37-38-code-review.md`
> **政策：** 不为风格或纯粹偏好制造问题。每条问题都追溯到可触发的代码路径。

---

## 1. 审查范围

覆盖 Stage 4 Subplans 36–45 已落地的生产实现、测试、迁移、文档一致性，以及下列产品面：

| 切片 | 主要生产面 |
|---|---|
| 36 Operational Store | `core/store.py`，`adapters/state/operational.py`，`migrations.py`，busy/WAL/维护锁/未来与损坏拒绝 |
| 37 Session / Conversation | `core/domain.py`，`runtime/conversation.py`，`runtime/durable_log.py`，`application/turns.py` `submit_user`/`restore_into` |
| 38 Tool journal / Approval | `core/execution.py`，`application/prepared.py`，`runtime/agent.py` persist-before-effect，审批 consume |
| 39 Recovery / crash | `core/recovery.py`，`application/recovery.py`，`runtime/conversation.py` `plan_recovery_close`，crash 测试 |
| 40 TaskRun / TaskOutcome | `application/tasks.py`，v5 迁移，`LEGAL_TASK_TRANSITIONS` |
| 41 Artifact | `application/artifacts.py`，`adapters/state/artifacts.py`，发布/校验/孤儿 |
| 42 Checkpoint / Fork | `application/checkpoints.py`，`journal.load_effective_records` |
| 43 API / CLI / doctor / backup | `application/api.py`，`interfaces/cli.py`，`application/doctor.py`，`application/backup.py`，`application/cleanup.py` |
| 44 Grants / Full Access Manual | `core/permissions.py`，`application/grants.py`，`bootstrap.py` `create_foreground_grant`，撤销/冻结 |
| 45 Acceptance / docs | `docs/acceptance/stage-4-durable-agent-evidence.md`，`ARCHITECTURE.md`，`ROADMAP.md`，`README.md` |

**未做：** 不改代码、不 commit、不切换分支、不跑 Live/真实网络测试、不以本审查重跑 600 条 offline 套件来“再验收一次”。判断依据是当前源码、测试与文档的交叉阅读。

---

## 2. 方法

1. 读主计划、Subplan 目录、Stage 4 ADR、架构/路线/验收文档，以及 37/38 既有 review。
2. 按故障面而不是按文件清单阅读：事务边界、恢复分类、幂等、权限授予、跨 workspace 过滤、future/corrupt schema、CLI/API/REPL 是否同一条业务路径。
3. 对每个候选问题走完整调用链（CLI → API → journal / REPL → `SessionPersistence` → journal），避免把测试里存在的路径误当成产品入口。
4. 用 grep 复核：`RETRY`/`retry_of_execution_id`、`apply_recovery`/`resolve_recovery`、`resume_session_id`、`call_aliases`、`SECRET_NEEDLES`、`query_only`、grant 创建入口、future schema。
5. 分级：
   - **blocker**：按已声明的 Stage 4 产品闭环，用户无法完成“崩溃后恢复并继续同一 Session”，或恢复命令会留下不可操作状态。
   - **高风险**：真实正确性/安全/数据语义缺陷，有明确触发条件。
   - **普通问题**：有限窗口、需并发或特定顺序，或文档/合同漂移。
   - **仅建议**：防御纵深或可维护性，不是当前正确性缺陷。
   - **不成立**：对照代码后否定的担忧，包括 37/38 review 中已被实现或被 schema 消解的项。

---

## 3. 总体结论

**Stage 4 的持久化基底是扎实的，但“已关闭、可日用的耐久前台 Agent”这一验收声明不能按产品入口成立。**

SQLite Operational Store v1–v9、`BEGIN IMMEDIATE`、未来/损坏/外源拒绝、ConversationLog 语法权威、副作用前提交 intent、Host/sandbox 缺完成即 `outcome_unknown`、Artifact 发布协议、Fork 的不可变 prefix、Grant 只能由本地界面创建且 crash resume 不继承——这些核心不变量在库路径和测试里是对的，而且大多有针对性测试。

真正挡住 Stage 4 产品闭环的是 **两条恢复/继续路径没有接到同一条完整业务合同**：

- 库路径 `SessionPersistence.restore_into` + `apply_recovery` 会发现中断工作、分类、关闭 ToolCycle、在 RESUME 时新建 `AgentRun`，并把 `sessions.health` 写回 `ok`。这条路径几乎只被测试调用。
- 产品路径里，默认 `morrow` REPL **每次新建 Session**，从不传入 `resume_session_id`；`morrow session resume` 只打印行，不进入 REPL；`morrow recovery resolve` 走 `OperationalApplicationService.resolve_recovery`，**不更新 Session health、不创建 resume AgentRun、不关闭 turn-submit receipt**；REPL 也没有 `/recovery`。

因此：测试可以证明“崩溃后分类且不盲放”，验收文档也可以写“Session 重启脚本通过”；用户按发布的 CLI/REPL **不能**在崩溃后继续同一条 Session，也不能用文档里的 recovery 命令把一个已被 `restore_into` 标成 `needs_recovery` 的 Session 救回可输入状态。

**未发现：** 凭据写入 YAML/SQLite/backup bundle；模型或工具创建 Grant；结构化工具因 Grant 被抬权；future/corrupt schema 被就地重建；doctor 改写业务历史；Fork 回滚工作区文件。

**建议：** 在声称 Stage 4 关闭之前，先把恢复/继续接到唯一的 Application 边界（health + receipt + resume AgentRun），并让 REPL/CLI 真正恢复同一 Session。其余高风险项（`RETRY` 空操作、`close_all=True`、`call_id` 别名）应在同一轮修，不必另开架构。

---

## 4. 问题清单

### 4.1 Blocker

#### B1 — 产品入口无法恢复并继续同一 Session

- **严重级别：** blocker（产品闭环 / DoD 1、10）
- **文件与行号：**
  - `src/morrow/interfaces/cli.py:168-173` — 默认 REPL 调用 `build_session_application(...)`，不传 `resume_session_id`
  - `src/morrow/bootstrap.py:218-231` — 只有显式 `resume_session_id` 才会 `restore_into`
  - `src/morrow/interfaces/cli.py:486-502` — `session resume` / `session status` 只 `api.get_session` 后打印
- **触发条件：** 用户完成一轮带持久化的对话后退出或崩溃，再运行 `morrow`；或按帮助运行 `morrow session resume SES_ID`，期望回到该 Session。
- **影响：** 每次交互启动都分配新的 `ses_*`。旧 Session 的消息、未闭合 ToolExecution、open TaskRun 仍在库里，但前台产品不会加载它们。`session resume` 名称暗示恢复，实际是只读 dump。Stage 4 路线图写的“创建或恢复 Session → 崩溃后以安全健康状态重新打开 → 继续”在发布入口上不存在。验收证据里的“同一 Session ID 重建”走的是测试/脚本里的 `resume_session_id=`，不是 CLI。
- **判断依据：** 全仓库 `resume_session_id=` 只出现在 `bootstrap.py` 和测试（`test_stage4_session_conversation.py`、`test_stage_boundary.py`、`test_stage4_task_outcome.py`）。README 甚至仍按进程内对话描述产品。
- **建议：** 给默认 REPL 增加显式 `--session-id` / 最近 Session 恢复；让 `morrow session resume` 进入同一 `run_repl` 并调用 `restore_into`。在此之前，不要把 DoD 1 写成已由 CLI/REPL 满足。

#### B2 — 唯一用户可见的 recovery 命令是不完整的半路径

- **严重级别：** blocker（恢复语义 / 可操作性）
- **文件与行号：**
  - `src/morrow/application/api.py:1062-1166` — `resolve_recovery` 只 `decide` + `commit_decision` + application event
  - `src/morrow/interfaces/cli.py:956-990` — CLI 只调用上述 API
  - `src/morrow/application/turns.py:691-760` — 完整路径：`commit_decision` 之后按结果写 health、RESUME 时 `create_agent_run`
  - `src/morrow/application/commands.py`、`src/morrow/interfaces/terminal.py` — 无 `/recovery`
- **触发条件：** 某进程对已有 Session 调用了 `restore_into`（测试、未来接好的 REPL、或任何库调用者），`sessions.health` 被写成 `needs_recovery`。用户随后用 `morrow recovery resolve … --resolution abort|resume` 处理报告。
- **影响：**
  1. 报告可以变为 `resolved`，执行行也可以被关闭，但 **`sessions.health` 仍是 `needs_recovery`**。
  2. 下一次 `restore_into`：`discover` 在“无 open report、无未闭合 execution、无 active turn”时返回 `None`，然后 **原样保留 row.health**，再写回 `needs_recovery`（`turns.py:548-609`）。`submit_user` 在 `health is NEEDS_RECOVERY` 时直接返回 recovery（`turns.py:437-438`）。`/new` 也被挡住（`commands.py:113-116`）。
  3. RESUME 不会创建带 `resume_of_agent_run_id` 的新 AgentRun，与 ADR“crash resume 新建 AgentRun、不继承 Grant”的产品路径脱节。
  4. recovery 不关闭 `turn_submit_receipts`。Abort 后若用同一 `client_message_id` 再提交，收据仍是 `accepted_open`，会被当成需要恢复（`turns.py:425-436`），且此时可能已经没有 `open_report`，`apply_recovery` 会因 “no open recovery report” 失败。
- **判断依据：** `SessionHealth.OK` 的写入只出现在 `turns.py:733` 的 `apply_recovery`。`apply_recovery` 的生产调用点只有测试 `tests/test_stage4_recovery_crash.py:425-435`。CLI 测试 `tests/test_stage4_cli_operational.py` 不覆盖 recovery resolve，也不断言 health。
- **建议：** 把 `apply_recovery` 的后半段（health、receipt `accepted_closed`、RESUME AgentRun）搬进 `OperationalApplicationService.resolve_recovery` 的同一事务。REPL 增加确认过的 `/recovery`。CLI 与 REPL 必须走同一方法。`restore_into` 在“无剩余中断工作”时应把 health 收回到 `ok`，而不是只升不降。

---

### 4.2 高风险

#### H1 — `RETRY` 只记账，不创建 linked retry，也不关闭旧 execution

- **严重级别：** 高风险
- **文件与行号：**
  - `src/morrow/application/recovery.py:216-231` — item 级 `RETRY` 只 `apply_item_resolution`
  - `src/morrow/application/recovery.py:260-275` — `commit_decision` 只在 ACKNOWLEDGE/ABORT 时关 execution
  - `src/morrow/core/recovery.py:253-254, 180-183` — 分类允许 RETRY
  - `docs/decisions/stage-4-durable-execution-and-recovery.md:46-47, 178-179` — 合同要求新 `ToolExecution` 且 `retry_of_execution_id` 相连
- **触发条件：** 用户对 `SAFE_TO_RETRY` / 部分 `NEVER_STARTED` 项选择 `retry`（CLI `recovery resolve --resolution retry --item-id …`，或测试中的 `decide`）。
- **影响：** 旧行保持 `executing`。item.resolution 被设为 `retry` 后不再 `blocking`，`apply_report_resume` 可以成功。随后若走 `apply_recovery` RESUME，会在旧 execution 仍为 `executing` 时新建 AgentRun、把 health 标 `ok`。同进程里 `ConversationLog` 若仍有未闭合 ToolCycle，下一条用户输入会在 `plan_begin_turn` 处失败。若再 `restore_into`，会重新发现该 `executing` 并再开一份报告。`retry_of_execution_id` 在生产写入路径上从未被赋值（仅模型字段 + 单测构造）。
- **判断依据：** 全仓库生产代码没有创建 linked retry 的路径。测试只断言“允许 RETRY”和 receipt 幂等，不断言新 execution。
- **建议：** 按 ADR：关闭或终止旧 attempt 的“当前尝试”语义，插入新 `ToolExecution(retry_of=...)`，并在恢复关闭 ToolCycle 之前不要允许 RESUME；或者在实现之前从 CLI/API 拿掉 `retry`。

#### H2 — API/CLI 的 `close_all` 默认为 True，单条 ACKNOWLEDGE 会关掉整份报告的执行

- **严重级别：** 高风险
- **文件与行号：**
  - `src/morrow/application/api.py:1071` — `close_all: bool = True`
  - `src/morrow/interfaces/cli.py:982-989` — 不传 `close_all`
  - `src/morrow/application/recovery.py:269-275` — `close_all` 时遍历全部 items
  - 对照：`src/morrow/application/turns.py:727` — `apply_recovery` 只用 `close_all=(resolution is ABORT)`
- **触发条件：** 一份报告里有多个未闭合 execution。用户对其中一条 CLI `recovery resolve --resolution acknowledge --item-id rit_…`。
- **影响：** 其他尚未决议的 execution 也被写成 `interrupted` 并关闭。报告里其余 item 仍 `resolution is None`，但底层已经没有对应的 open execution。这是错误恢复：把未选择的中断工作标成已处理。`apply_recovery` 与 API 行为不一致，违反“同一 Application 边界”。
- **判断依据：** 默认值与 CLI 未传参可直接从源码读出；两条路径的 `close_all` 策略不同。
- **建议：** API 默认 `close_all=False`；仅 report 级 `abort` 传 `True`。加一条多 item 测试锁住。

#### H3 — 持久化把 `tool_call_id` 改写成 `call1`，存活投影与恢复投影分裂

- **严重级别：** 高风险（既有 MUST-03，仍未修）
- **文件与行号：**
  - `src/morrow/runtime/durable_log.py:36-51` — Assistant calls 存成 `call{index}`，ToolMessage 经 alias 映射，缺省 `"call0"`
  - `src/morrow/application/turns.py:666-678` — 同时把 `DurableToolExecution.call_id` 改成 alias
  - `src/morrow/application/turns.py:685-687` — COMMIT 后 `apply_committed(planned)`，存活 log 仍是 Provider 原 ID
- **触发条件：** 一轮带 tool calls 的 Assistant 提交后崩溃，再 `restore_conversation_log`。或同一进程内任何按 `call_id` 关联“存活 log”与“durable execution”的代码。
- **影响：** 恢复后的 ConversationLog 与下一轮 Provider 请求看到的是 `call1`，不是线上 `call_abc123`。严格回显 `tool_call_id` 的模型会在恢复后续轮次里对不上。存活路径按 ordinal 取 execution，所以同进程执行往往仍能跑完；跨重启后只有 alias。`call_aliases.get(..., "call0")` 在 alias 表空时会把多条 ToolMessage 压到同一个伪 ID。ADR 要求的是参数脱敏，不是改写关联 ID。
- **判断依据：** 与 `docs/reviews/stage-4-subplan37-38-code-review.md` MUST-03 同一处代码仍在。`apply_committed(planned)` 明确不采用已提交的脱敏快照。
- **建议：** `payload_json` 与 `tool_executions.call_id` 保存原始、已校验的 `call.id`。参数/结果继续 `{}` / `{"redacted":true}`。删掉 `"call0"` 回退，改为拒绝无法关联的 tool 记录。

#### H4 — backup `create()` 在 Artifact 副本失败时仍返回成功对象

- **严重级别：** 高风险（备份完整性）
- **文件与行号：** `src/morrow/application/backup.py:85-97`
- **触发条件：** SQLite online backup 与 FK/integrity 通过，但至少一个 AVAILABLE Artifact 副本失败（缺失、hash 变、IO 错）。随后 `morrow state backup`。
- **影响：** 只在 DB integrity/FK 失败时 `raise`。Artifact 失败时仍返回 `BackupBundleReport`，`integrity_ok` 可能为 `False`，CLI `_emit_model` 后 **exit 0**（`cli.py:1043-1053`）。对照 `verify-backup` 会在 `not report.ok` 时 exit 2。用户可能把一份缺字节的 bundle 当成可用备份。这不是静默丢业务行，但是静默接受不完整备份。
- **判断依据：** `create()` 与 `verify-backup` 的失败策略不一致；`tests/test_stage4_backup.py` 只覆盖成功创建后再篡改 verify。
- **建议：** `create()` 在 `not verified.ok` 时删除 bundle 并 raise；CLI 以非零退出。至少把 `integrity_ok=false` 变成硬失败。

---

### 4.3 普通问题

#### O1 — 重复的 open `client_message_id` 只改内存 health（既有 MUST-01，部分缓解）

- **严重级别：** 普通（多进程 / doctor 可见性）
- **文件与行号：** `src/morrow/application/turns.py:425-436`
- **触发条件：** 同一 Session 再次提交仍为 `accepted_open` 的 `client_message_id`。
- **影响：** 内存 `session.health = NEEDS_RECOVERY`，SQLite `sessions.health` 仍可能是 `ok`。`restore_into` 若随后因 `has_active_turn` 或 open execution 跑过，会把 health 写回去（`605-609`），所以单进程崩溃后再用 `resume_session_id` 打开通常能自愈。Doctor 与第二个只读进程在 `restore_into` 之前会看到 `ok`。
- **判断依据：** 与 37/38 MUST-01 同一缺口；`restore_into` 已补上“发现中断则落盘”的路径，因此从 blocker 降为普通。
- **建议：** 在返回 `recovery` 之前，用同一短事务把 `sessions.health` 写成 `needs_recovery`。

#### O2 — `apply_recovery` 在报告事务外创建 resume AgentRun

- **严重级别：** 普通
- **文件与行号：** `src/morrow/application/turns.py:721-752`
- **触发条件：** `commit_decision` 已把报告标 `resolved` 后、`create_agent_run` 前进程死亡。
- **影响：** 报告已关闭，没有新的 resume AgentRun。下次 `restore_into` 若 turn 仍开着，会再生成一份可能为空的报告，用户可再 RESUME。可恢复，但会丢一次“与旧 run 相连的 resume”证据。Grant 不继承的合同仍然成立（新 run 本来就不会带旧 snapshot）。
- **建议：** 把 `create_agent_run` + `save_session(health=ok)` 放进与 `save_report` 同一 `BEGIN IMMEDIATE`。

#### O3 — `SECRET_NEEDLES` 含裸 `"sk-"`，会误杀合法载荷

- **严重级别：** 普通
- **文件与行号：** `src/morrow/core/domain.py:48, 406-411`
- **触发条件：** 持久化文本/JSON 的 casefold 形式包含子串 `sk-`（例如 “mask-”、“ask-”、“desk-” 在部分拼写下；更常见是用户指令里的 “mask-” 类词或非密钥的 `sk-` 前缀）。
- **影响：** `ValueError: cannot contain secret material`，合法 Session fork reason、AgentRun snapshot、Artifact excerpt、application event 可能被拒。这是可用性/拒绝服务，不是漏密钥。Stage 3 的 `sk-[A-Za-z0-9_-]+` 替换（`core/models.py:452`）更严。
- **建议：** 与 Stage 3 看齐，改成带长度的 token 模式；不要用三字符子串。

#### O4 — `quarantine_is_health_not_lifecycle` 仍包含空操作分支

- **严重级别：** 普通
- **文件与行号：** `src/morrow/core/domain.py:574-596`
- **触发条件：** 构造 `DurableSession(lifecycle=deleted, health=ok)` 或相反组合。
- **影响：** 该校验器现在真正检查 fork 字段完整性（这是 37/38 之后的改进），但 `DELETED + OK` 与其他组合都 `return self`。名字承诺的 “quarantine 改 health、不改 lifecycle” 仍未执行。DB CHECK 只限制枚举值，不限制组合。
- **建议：** 删掉空分支，或显式禁止 `lifecycle=deleted` 且 `health!=ok` / `health=quarantined`。

#### O5 — 事务重试体内分配 ID

- **严重级别：** 普通
- **文件与行号：** `src/morrow/application/turns.py:472-476`（task id）；`src/morrow/runtime/durable_log.py:119-124`（record id）
- **触发条件：** `BEGIN IMMEDIATE` 遇到 `BUSY` 并重试整个 `work`。
- **影响：** 每次重试消耗新的随机 ID；失败的 ID 被丢弃。不是数据损坏，但收据/关联在争用下不可复现。`turn_id` / `agent_run_id` / `command_id` 已在事务外分配，模式不一致。
- **建议：** 所有将写入的 ID 都在 `transact` 之前分配。

#### O6 — README / 验收文档与代码漂移

- **严重级别：** 普通（文档合同）
- **文件与行号：**
  - `README.md:1-5, 59-60` — 仍写“进程内连续对话”，REPL 命令列表无 `/grant` `/task` `/accept`，也无 session resume
  - `docs/acceptance/stage-4-durable-agent-evidence.md:33-47, 103-111` — DoD 1/10 把测试/`resume_session_id` 脚本当成 CLI/REPL 证据
  - `docs/roadmap/stage-4-task-session-and-persistence.md:3-4, 14-25` — 声明产品循环已完成
- **触发条件：** 读者按 README 或验收报告理解 Stage 4 能力。
- **影响：** 关闭声明超前于发布入口。不是运行时 bug，但会让后续 Stage 5 建立在错误前提上。
- **建议：** README 补上 Session 恢复、recovery、doctor/backup、Full Access Manual。验收矩阵把“库路径通过”和“产品入口通过”分开。在 B1/B2 修复前，不要写“用户可以恢复 Session”。

#### O7 — 默认 REPL 不提示已有可恢复 Session

- **严重级别：** 普通
- **文件与行号：** `src/morrow/interfaces/cli.py:168-173`
- **触发条件：** 数据根里已有同一 workspace 的 `needs_recovery` / 未闭合 Session，用户再次 `morrow`。
- **影响：** 静默开新 Session。旧中断工作留在库中，doctor 能看见，前台当它不存在。与 B1 相关，但即使接上 `--session-id`，没有提示也会造成“看起来丢了对话”。
- **建议：** 启动时查询当前 workspace 的 active/needs_recovery Session 并提示恢复或新建。

---

### 4.4 仅建议

#### S1 — `execution_is_visible` 仍是同连接读取

- **文件：** `src/morrow/application/turns.py:762-763`，调用点 `src/morrow/runtime/agent.py:591-597`
- **说明：** COMMIT 之后同连接 `get_execution` 只能证明“这个 handle 看得到行”，不能证明另一连接看得到。测试 `test_intents_are_visible_from_a_fresh_connection_before_handler` 用了第二把 `OperationalStore`，生产路径没有。成功的 `COMMIT` + `synchronous=FULL` 已经是真正的耐久证明。此检查作为门闩价值有限，作为“跨连接证明”则名不副实。
- **建议：** 删掉，或用短生命周期只读连接探测。不要再把它写进 ADR 符合性叙述。

#### S2 — `observe_file` 未拒绝 `..`，生产 evidence 目前由 workspace resolver 挡住

- **文件：** `src/morrow/core/execution.py:176-181`，`src/morrow/core/recovery.py:306-309`
- **说明：** `_clean_relative_path` 只拒绝对路径和 NUL。`observe_file` 用 `root.joinpath(*relative_path.split("/"))`。若 durable evidence 被写成 `../../../etc/passwd`，恢复分类会读工作区外文件（只读 hash，不写入）。生产 `file_evidence_from_plan` 的 path 来自 `WorkspacePathResolver.validate_relative_path`，后者拒绝 `..`（`services/files.py:122`）。当前不是可从模型参数直达的绕过。
- **建议：** 在 `FileMutationEvidence` 与 `observe_file` 再拒一次 `..`，作为 journal 毒化/未来写入者的纵深防御。

#### S3 — `PRAGMA query_only` 在 `finally` 里二次失败时可能粘住

- **文件：** `src/morrow/adapters/state/operational.py:237-245`
- **说明：** `finally` 会在 RW/CREATE 连接上关 `query_only`。若这一句自己抛 `sqlite3.Error`，连接可能一直只读。窗口窄，且会被翻译成 `StorageError`。
- **建议：** 先读回旧值再恢复；或只读使用单独连接。

#### S4 — 审批预览存在两套预算

- **文件：** `src/morrow/core/execution.py` 的 durable preview 上限；`src/morrow/runtime/tools.py` 的 live `ApprovalPreviewBudget`
- **说明：** `prepare_cycle_executions` 先按工具 live budget 截断，再被 durable 边界再校验。可能出现“过了第一关、栽在第二关”的含糊错误。不是权限绕过。
- **建议：** 单一导出的 durable budget，live 步明确使用它。

#### S5 — `DataRoot.ensure` 仍不限制既有 `locks/` 目录 mode

- **说明：** Operational 布局对 `store/`、`artifacts/`、`backups/`、`operational-store.lock` 做了 0700/0600 校验。YAML 侧 `locks/` 仍按旧行为 `mkdir`。ADR 允许 YAML 目录暂不收紧。
- **建议：** 文档写清“有意保持”；或对 `locks/` 也 `restrict_path`。

#### S6 — `morrow session resume` 与 `session status` 是同一个只读 dump

- **文件：** `src/morrow/interfaces/cli.py:486-488`
- **说明：** 即使用户读过帮助，也会把 resume 理解成“继续”。与 B1 一起修。

---

### 4.5 不成立的担忧

| 担忧 | 结论 |
|---|---|
| 37/38 MUST-06：`recovery_reports` 按 `session_id` 的部分唯一索引导致跨 workspace 抢占 | **不成立。** `sessions.session_id` 是全局主键（`migrations.py:61`）。两个 workspace 不能持有同一 `ses_*`。`put_report` 还要求 `get_session(workspace_id, session_id)` 命中。该索引与全局 Session 身份一致，不是跨租户洞。`secrets.token_urlsafe(12)` 的碰撞只会造成创建失败，不会串数据。 |
| 37/38 MUST-05：`create_task_run` 不能推进当前任务指针 | **已修复。** `journal.py:364-383` 在 `make_current` 且当前任务仍 OPEN/READY 时拒绝；否则更新指针。 |
| Fork 不复制 `conversation_records` 是数据丢失 | **不成立，是设计。** `load_effective_records`（`journal.py:1330-1377`）投影 parent 前缀 + child 本地行。测试 `test_checkpoint_requires_a_closed_boundary_and_fork_has_no_parent_copy` 锁住这一点。父 Session 追加不会进入已切过的 prefix。 |
| Doctor 会修复/改写历史 | **不成立。** `OperationalDoctor.inspect` 只开 `DIAGNOSE`/`READ_ONLY`，写报告。Cleanup 默认 dry-run，且拒绝删除仍有 metadata 的文件（`cleanup.py:52-58`）。 |
| Grant 可被模型、工具、Preferences 或恢复历史创建 | **不成立。** `CapabilityGrantService.create` 要求 `GrantSource.LOCAL_INTERFACE_COMMAND`；API 再检查 Full Access Manual 的 AgentRun digest。生产写入点是 CLI `grant create`（本地 `typer.confirm`）和 REPL `/grant` → `grant_provider`（`bootstrap.py:382-404`）。结构化工具不会因为同一 run 有 grant 就带 elevated evidence（`prepared.py:139-165`）。 |
| crash resume 继承旧 Grant | **不成立（库路径）。** `apply_recovery` RESUME 新建 AgentRun，`current_permission_snapshot_id = None`，`tools=()`。测试 `test_stage4_recovery_crash.py:431-448` 锁住。产品路径目前到不了这里（见 B2）。 |
| future/corrupt/empty/foreign schema 会被就地重建 | **不成立。** `classify` / `migrate` / `initialize` 对 future、identity mismatch、非 SQLite 头、`needs_repair` 都是拒绝且保留原文件。Backup 允许复制 future schema，但不 migrate。 |
| Backup 复制 live `-wal`/`-shm` | **不成立。** `_backup_locked` 使用 `Connection.backup()`。 |
| 凭据进入 backup bundle | **未发现。** Bundle 只含 `database.sqlite`、`manifest.json`、Artifact 字节；`credentials_excluded` 检查文件名。CredentialStore/keyring 不在复制集里。 |
| `observe_file` 是工作区逃逸的产品漏洞 | **当前不成立。** 生产 evidence path 先经过 workspace resolver。见 S2。 |

---

### 4.6 37/38 既有 MUST/SHOULD 复验

| ID | 原判定 | 现在 |
|---|---|---|
| MUST-01 重复 open 不落盘 health | 仍在 `turns.py:435-436` | 部分缓解：`restore_into` 会落盘。降为 O1。 |
| MUST-02 `execution_is_visible` 同连接 | 仍在 | 降为 S1。 |
| MUST-03 `call_id` 别名 | 仍在 `durable_log.py:36-51` | 仍为 H3。 |
| MUST-04 空 validator | 校验器已承担 fork 不变量；health/lifecycle 分支仍空 | 降为 O4。 |
| MUST-05 当前任务指针 | 已修 | 不成立。 |
| MUST-06 V4 部分索引缺 workspace | schema 上 session_id 全局唯一 | 不成立。 |
| SHOULD-02 事务内分配 ID | 仍在 | O5。 |
| SHOULD-04 `sk-` | 仍在 | O3。 |
| SHOULD-05 双预览预算 | 仍在 | S4。 |
| SHOULD-06 `query_only` | 仍在 | S3。 |

---

## 5. 按主题的正确部分（避免误报）

这些应保留，不要在“修恢复入口”时拆掉。

- **Store：** `application_id` / `user_version` / `store_identity` 三方一致；future/corrupt/foreign 拒绝；WAL + `synchronous=FULL` + `BEGIN IMMEDIATE` + 有界 busy retry；维护锁只用于 init/migrate/backup/diagnose。
- **Conversation：** ConversationLog 仍是唯一语法权威；候选 → 校验 → 同一事务写记录 → COMMIT → 投影；recovery 只能追加 error/interrupted envelope，不能 STOP 成功。
- **Persist-before-effect：** Assistant + intents 同一 `BEGIN IMMEDIATE`；handler 前 `assert_handler_may_enter`；Host/sandbox 声明为 `OUTCOME_UNKNOWN`；生产工具缺 declaration 时 composition 失败。
- **文件对账：** hash/size，不是 mtime；`MATCHES_EXPECTED` / `BEFORE` / `THIRD_PARTY` 分类干净。
- **TaskRun：** `open → ready_for_acceptance → accepted`，follow-up 重开；FAILED 可显式 resume；`/task new` 会放弃仍开放的当前任务。
- **Artifact：** 只接受已脱敏字节；ID 派生路径；`O_NOFOLLOW`；fsync → rename → parent fsync；缺/坏文件可见失败。
- **Fork：** 父必须健康且 Turn 已闭合；子不继承 current TaskRun / Grant；effective records 不复制父行。
- **Grant：** 仅 `unconfined_host_process`；`full_access + auto` 仍 DENY；撤销会作废 pending approval 并请求取消 executing Host，不伪造回滚。
- **Doctor / cleanup：** 只读诊断；cleanup 拒绝 symlink、错误 mode、仍有 metadata 的文件。

---

## 6. 未采纳的建议（审查者有意不升级）

| 建议 | 为何不升级成问题 |
|---|---|
| 给 `turn_submit_receipts` 加 `row_version` | 并发双提交同一 `client_message_id` 由 `UNIQUE(session_id, client_message_id)` 挡住。缺版本是一致性风格，不是当前丢失。可在修 B2 时顺便做。 |
| 去掉 `_connect` 的 immutable URI 启发式 | 只影响只读打开；未看到它把 future/corrupt 文件改写成可写。 |
| 为每个 workspace 拆库 | 与已接受 ADR 相反。workspace 隔离靠 `workspace_id` 查询 + Session 全局 PK，当前足够。 |
| 恢复时自动重放 Host/sandbox | 明确拒绝。缺 `handler_completed` 必须是 `outcome_unknown`。 |
| 把完整 tool 参数/结果写入 conversation_records | ADR 要求有界脱敏。问题是改写了 `call_id`（H3），不是红acted 参数本身。工具结果应去 Artifact；恢复后续轮次本就不应依赖原始 stdout。 |
| 引入 ORM / outbox / 后台 worker | 与 Stage 4 锁定路线相反。 |

---

## 7. 残余风险

即使修完 B1/B2/H1–H4，下列风险仍然存在，且与 Stage 4 的有意收缩一致。

1. **无 PID/死亡证明。** Host 与 native sandbox 在 Stage 4 v1 不能跨进程证明进程已死。未知结果必须保持未知。孤儿进程可能在用户 ACKNOWLEDGE 之后仍在跑。
2. **`asyncio.shield` 下的文件写。** 取消不能证明 write 未完成；只能靠 hash 对账。这是已接受的合同。
3. **对话记录故意丢掉 tool 载荷。** 重启后模型看不到先前 `read_file` 正文，只能看到最终 Assistant 文本和 Artifact 引用。这是载荷边界，不是实现疏漏；产品上要让用户知道“恢复不是完整 transcript 回放”。
4. **在线 backup 与 Artifact 复制非同一瞬间。** SQLite `backup()` 一致；之后拷贝的 Artifact 可能多/少一个并发发布的文件。H4 修完后这类 bundle 应失败而不是静默成功。
5. **单连接 + 线程亲和。** 普通写不得跨过模型调用持有事务。当前代码在 handler/模型等待期间不持有写事务，这一点是对的；以后加后台任务时必须重审。
6. **全局 `session_id` 主键。** 隔离靠 ID 不碰撞 + 查询带 `workspace_id`。对单用户数据根可接受；不要在文档里写成“密码学租户隔离”。
7. **默认 REPL 新建 Session** 在接上 resume 之前，会在 `~/.morrow` 留下越来越多未关闭的历史 Session。这是产品债，不是存储损坏。

---

## 8. 测试缺口（与上述问题对应）

现有 Stage 4 测试在**库路径**上相当强：WAL/crash `_exit`、intent-before-handler、Host unknown、文件 hash 对账、Grant 不继承、Artifact fault、fork 不改父历史、future/corrupt 拒绝。缺的是**产品路径**：

| 缺口 | 为何重要 |
|---|---|
| CLI `recovery resolve` 之后重开 Session，断言 `sessions.health` 与是否还能 `submit_user` | 直接锁 B2 |
| REPL/`morrow` 无 `resume_session_id` 时不会加载上次 Session | 锁 B1 |
| 多 item 报告上 CLI ACKNOWLEDGE 一条不得关闭其他 execution | 锁 H2 |
| `RETRY` 必须产生 `retry_of_execution_id` 或被 API 拒绝 | 锁 H1 |
| 恢复关闭后同一 `client_message_id` 应是 closed replay 或明确冲突，而不是再进 recovery | 锁 B2 的 receipt |
| backup `create()` 在缺 Artifact 字节时必须失败 | 锁 H4 |
| 从恢复后的 ConversationLog 再向 Provider 发请求，`tool_call_id` 仍是原始 ID | 锁 H3 |

`tests/test_stage4_cli_operational.py` 只有 60 行，只覆盖 create/list/doctor。这与 Subplan 43“CLI/REPL 与 API 同一边界”的完成声明不匹配。

---

## 9. 分层与过度抽象

没有发现为抽象而抽象、需要推倒的新层次。`OperationalApplicationService` 作为唯一命令边界是对的；问题是 **恢复的完整语义留在 `SessionPersistence.apply_recovery`，API 只包了前半段**。不建议再加一层 RecoveryFacade。把后半段搬进现有 API 即可。

`DurableConversationWriter` 的 alias 表是多余复杂度，应随 H3 删除，而不是再加 `redacted_tool_call_id`。

---

## 10. 结论表

| 类别 | 数量 | 是否阻止“Stage 4 产品关闭” |
|---|---:|---|
| Blocker | 2 | 是 — 恢复/继续入口未接通 |
| 高风险 | 4 | 是 — 应在同一轮修 |
| 普通 | 7 | 否 — 应排队，不必挡架构 |
| 仅建议 | 6 | 否 |
| 不成立 | 10 | — |

**对当前仓库的一句话：** Stage 4 的存储、分类、权限和 Artifact 基底可以支持一个耐久前台 Agent；发布的 CLI/REPL 还不能把用户带完“崩溃 → 对账 → 继续同一 Session”这条已写进路线图和验收报告的回路。在 B1/B2 关闭之前，应把 Stage 4 理解为“库与测试完成、产品入口未完成”，而不是“已关闭的耐久个人 Agent”。

---

## 11. Codex 核验与单轮 review-fix 结论（2026-08-20）

本节记录对上文每项实际问题的判断和唯一一轮修复结果。没有再次调用 Grok，也没有把风格偏好
升级成新的 review 问题。

### 已确认并修复

- **B1/B2、O2：** 增加根命令 `--session-id`，并让 `session resume` 真正进入同一 REPL；默认启动
  会提示当前工作空间可恢复的 Session。REPL 增加 `/recovery`（含 `show`、逐 item ACK/ABORT、
  report-level QUARANTINE/RESUME），CLI 与 REPL 共用 `resolve_recovery`。Recovery 的 Session
  health、TaskRun 终态、turn-submit receipt 和 RESUME AgentRun 现在在同一 Operational Store
  事务中提交；恢复后的继续使用同一个未闭合 Turn，并创建不继承旧权限快照的关联 AgentRun。
- **H1：** `RETRY` 原先确实是只记账的空路径。Stage 4 v1 现在不再把它暴露在允许决议中；分类
  仍可标记“未来 linked retry 安全”，但实际决议会明确拒绝，直到 `retry_of_execution_id` 的
  原子新 attempt 路径实现。
- **H2：** API 默认 `close_all=False`；只有 report-level ABORT 才关闭全部 execution。item-level
  决议不会误关其他 item；多 item 的 item-level ABORT 只在最后一个阻塞项关闭时终止 Turn。
- **H3：** 原问题属实，但“把 provider 原始 ID 原样持久化”会违反既有产品秘密安全验收（provider
  ID 也是不可信的持久化输入）。最终采用稳定的 `call_<sha256>` opaque projection，并在
  Assistant tool call、ToolMessage、ToolExecution intent、terminal interrupted IDs 和 recovery
  匹配处统一使用；恢复后关联稳定，原始 `secret-call-id` 不进入 Operational Store。
- **H4：** backup `create()` 现在对完整 verification 失败 fail closed，删除不完整 bundle 并返回
  非成功错误，不再把缺 Artifact 字节的备份当成成功。
- **O1/O3/O4/O7：** 重复 open receipt 会落盘 `needs_recovery`；裸 `sk-` 改为带长度的 token
  模式，避免 `mask-` 等合法文本误报；Session validator 重命名为明确的 fork provenance 校验，
  保留 lifecycle 与 health 的独立语义；默认 REPL 对可恢复 Session 给出明确提示。
- **O6：** README、Stage 4 acceptance evidence、Recovery ADR 已同步显式 Session resume、Recovery
  入口、opaque durable call correlation 和新的 v1 retry 边界。

### 判断为非阻塞、暂不改动

- **O5：** SQLite BUSY 重试中重新分配内部记录 ID 会消耗失败尝试的 ID，但事务回滚后没有脏行，
  不造成数据错误或幂等破坏；它是可观测性/可复现性优化，不足以扩大本轮改动。
- **S1–S6：** 这些是纵深防御、预算统一或维护性建议；没有发现当前 Stage 4 产品闭环的新增
  correctness/security blocker。本轮保持范围收敛，避免为抽象而抽象。

### 修复后验证

- `UV_CACHE_DIR=/tmp/morrow-uv-cache uv run pytest -m 'not live'`：**605 passed, 2 skipped,
  1 deselected**。两个 skip 是嵌套 Codex 环境不能运行的宿主级 Seatbelt 测试。
- 本轮针对性 Recovery/CLI/durable log/backup/tool persistence/secret scan：**70 passed**。
- `ruff format --check`：158 files already formatted；`ruff check`、`compileall`、`morrow --help`、
  `git diff --check`：通过。
- `uv build --wheel`：通过；最终 wheel SHA-256 为
  `22ef2890cc20854177dfc23b644af3fd9e6190e2bd3f4863bcd3928466e694a1`。隔离 venv 安装后的
  `import morrow`、版本读取和 `morrow --help`：通过。

结论：Grok 报告中的 B1/B2/H1–H4 及真实的 O1/O3/O4/O6/O7 已完成单轮核验和修复；没有遗留
高严重度 durability、security、migration 或 recovery finding。Stage 4 可以维持已关闭状态，
Stage 5 仍保持未激活。
