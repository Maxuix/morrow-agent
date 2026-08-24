# Stage 6 Dependency and Contract Spike — ADR

> Status: accepted（Subplan 63 产出，待用户批准依赖变更后才可进入 Subplan 72/73）
> Evidence branch: `codex/feat/stage6-contract-spike`
> Scope: 只读/临时 Spike。`pyproject.toml` 与 `uv.lock` 未修改；无生产代码变更。
> Prototype: `tests/spikes/fake_mcp_stdio_server.py`、`tests/spikes/test_mcp_stdio_spike.py`、
> `tests/spikes/test_stage6_budgets.py`

## 1. 结论（TL;DR）

1. **MCP 依赖**：采用官方 MCP Python SDK `mcp==2.0.0`（MIT，requires-python `>=3.10`）。它原生支持
   Python 3.12/3.13、stdio 生命周期、每次调用的读超时、类型化结果和确定性离线 Fake Server。SDK 已在
   传递依赖中携带 `jsonschema>=4.20`。**任何依赖变更仍需用户明确批准**（见 §8 门禁）。
2. **JSON Schema 校验**：使用 `jsonschema`（4.26.0，经 `referencing` 0.37.0），但**不**把“未知
   `$schema` 静默回退最新 draft”当作默认行为。Morrow 侧包装器只接受一个受控 dialect
   （draft 2020-12；无 `$schema` 时按 2020-12 处理），其余一律 fail-closed 拒绝；使用前先
   `check_schema()` 编译。
3. **SDK 事实**：`ClientSession.call_tool(..., read_timeout_seconds=...)` 超时抛
   `MCPError("Request 'tools/call' timed out")`；超时后会话仍可用；SDK 层对超时调用**不自动重试**
   （spike 以服务器调用日志证实）；没有公开的 per-request cancel API，取消模型 = 我方
   `asyncio.wait_for` + 关闭进程/会话。SDK 的 `opentelemetry-api` 依赖默认是 no-op（无 exporter），
   不产生网络或日志。
4. **预算（实测，见 §6）**：AgentRunSnapshot 基座 3 192 B，加 8 个 Skill ref + 1 个 MCP ref 后
   4 822 B / 65 536 B（约 7.4%）；Skill 上下文单条 250 B（上限锁 2 KiB/条、16 KiB/次运行）；
   64 个 MCP 工具快照 21 474 B（上限锁 4 KiB/工具、64 KiB/服务器）。
5. **Skill 包规范化**：见 §7 —— `skv_<id>` 目录、`managed-version.json` envelope、普通文件唯一
   canonical tree（sorted path、type、exec-mode、size、SHA-256），拒绝 symlink/hardlink/device/
   socket/FIFO、逃逸引用、大小写/Unicode 归一化碰撞。
6. **持久化**：v14/v15/v16 迁移与 backup bundle v2 的接缝已确认（§8），实现时不改现有 v1 语义。

## 2. 当前接缝记录（源码引用）

Subplan 63 任务 1 的输出。这些是 Stage 6 各子计划的插桩点，全部为本子计划实测核对。

### 2.1 AgentRun 与运行装配

- `AgentRunSnapshot`：`src/morrow/core/domain.py:515-616`（“Immutable non-secret AgentRun evidence”）。
  字段含 `model: ModelRef`（:520）、`provider_id`（:521）、`source_revisions`（:522）、
  `run_policy_digest`（:523）、`tool_schema_digest`（:524）、`permission_profile_digest`（:525）、
  `runtime_instance_id`（:526）。上限 `AGENT_RUN_SNAPSHOT_MAX_BYTES = 64 * 1024`（:45），
  `enforce_budget_and_redaction` :576-616。尚无 Skill/MCP 引用字段。
- 新 run 创建链：`AgentLoop.run_task`（`runtime/agent.py:387-870`，submit 分支 :450-495）→
  `SessionPersistence.submit_user`（`application/turns.py:217-237`）→
  `TurnSubmissionCoordinator.submit_user` 的 `work(txn)`（`application/turn_lifecycle.py:240-349`）：
  preference_loader 读 YAML（:234-236）→ `build_agent_run_snapshot`（:317-325，定义 :615-745）→
  `txn.create_agent_run`（:327-336）。
- **插桩点**：`preference_loader` 闭包（`bootstrap.py:427-460`）是当前唯一的每次运行实时 YAML 刷新；
  `submit_user` 事务内 `:234-349` 是“prepare 前”的自然拦截带；`build_agent_run_snapshot` 是纯构造器。
- 重放/恢复：`closed_replay` 由 receipt + 最后 assistant 文本判定（:212-223）；恢复只读 journal
  （`SessionRestoreCoordinator.restore_into` :476-513，`_restore_active_work` :564-596）；
  `load_run_context_projection`/`build_run_context_projection`
  （`application/learning/memory_run_projection.py:44-54,57+`）明确“不咨询实时资格”。
- `AgentLoop` 不读 YAML/SQLite/FS：`runtime/agent.py` 仅 stdlib + `core`/`runtime`/`application.context`
  导入；全部持久化走 `Session.committer` / `Session.durable_runtime`（`runtime/session.py:177-178`）。

### 2.2 Tool 执行、审批与权限

- `ToolDefinition`/`ToolFunction`：`core/models.py:139-152`（`parameters` 无约束 dict）。
  **名称正则** `TOOL_NAME_PATTERN = ^[A-Za-z0-9_-]{1,64}$`（:26），点号拒绝 —— 这是 `mcp__<server>__<tool>`
  命名约定的硬依据（classic `foo.bar` 会被拒）。
- `RegisteredTool`：`runtime/tools.py:195-205`（`arguments_model`、`handler`、`execution_policy`、
  `approval_preview`、`intent_resolver`）。参数校验在 `ToolExecutor.execute` 内
  `model_validate_json(call.arguments, strict=True)`（:309-324）。
- `ToolExecutor`：`tools.py:257`；`execute(call, *, result_limit, skip_approval, allow_unconfined_host)`
  :294-301；策略判定 `capability_policy.evaluate(intent, ...)` :349-351；approval 条件 :370-379；
  `request_approval` :484-498；结果预算 :461-467、截断 :536-604。Journal 写不在 executor，在
  `ToolCycleExecutor.execute_call`（`runtime/tool_cycle.py:51-179`，`_gate_durable` :212-252）。
- `PermissionSnapshot`：`core/permissions.py:236-365`；创建
  `application/turn_permissions.py:30-92` + `freeze` :111-157；执行重校验 :159-204；
  handler 进入冻结门 `core/execution.py:982-1085`。
- `CapabilityPolicy`：`runtime/capabilities.py:57-182`，单方法 `evaluate(intent, *,
  allow_unconfined_host=False) -> PolicyDecision`（:75-139）；verdict 枚举 `ALLOW/REQUIRE_APPROVAL/DENY`
  （`core/capabilities.py:179-202`）；判定顺序 :78-139（profile → full-access host → denied risks →
  外部影响/破坏性 → 只读拒绝 → 变更审批 → sandbox → PROCESS 分支 → 配置写审批 → workspace 写审批 →
  读放行 → 兜底 DENY）。Tool Journal：`adapters/state/tool_journal.py`（`tool_executions` :39-47、
  `approvals` :48-54；`put_approval` :283-369、`insert_execution` :492-606）。恢复分类
  `core/recovery.py:264-285`。
- 预算在 `RunPolicy`（`runtime/policy.py:41-56`），循环内执行（`agent.py:527-530,639-652,682-707`）；
  取消轮询 `tool_cycle.py:181-202`。

### 2.3 进程与沙箱

- `ProcessExecutionService`：`services/process.py:92`；`preflight` :113（cwd 根校验）、`intent` :163、
  `execute(plan, *, result_limit, run, ...)` :183；最小环境白名单 :267-273（PATH/LANG/LC_ALL/TMPDIR/
  SystemRoot/ComSpec，无 HOME 无凭据）；输出上限 `MAX_COMMAND_OUTPUT_BYTES=8 KiB`、
  `MAX_COMMAND_RESULT_BYTES=16 KiB`（:24-25）；风险分类 :331。
- `HostProcessAdapter.run`：`adapters/local/process.py:64-165`（timer + process-group SIGTERM→SIGKILL
  :177-216）；`NativeSandboxProcessAdapter`：`adapters/local/sandbox.py:54`（私有 HOME/TMPDIR、
  75 s 上限）；Seatbelt/bwrap 后端 :244/:326。
- **Skill Script 不经过 workspace-rooted `ProcessExecutionService`**（方案 §7）—— 独立
  `SkillScriptExecutionService`，只读包根 + 隔离输出。

### 2.4 AdapterRegistry 与 Provider/Model

- `AdapterRegistry`：`adapters/registry.py:13-52`；`ModelProvider` Protocol 只有两个方法
  `stream(model, messages, tools)` / `complete(model, messages)`
  （`core/ports.py:38-46`）；注册点 `bootstrap.py:206-212`（仅 `openai-compatible`）。
- `ProviderConfig`：`core/models.py:320-326`，**无能力/限额/modality/成本字段** —— Stage 6 能力快照是
  新增契约；`ProviderModelConfig` :309 只有 `api_model_id`。`GlobalConfig.active_model` 是唯一默认
  （:328-342）。
- 运行级配置指纹已存在先例：`SourceRevisionRef`（`core/domain.py:474-489`）在
  `AgentRunSnapshot.source_revisions`（`turn_lifecycle.py:689` 附近写入 global_config revision +
  `content_sha256`）。

### 2.5 迁移与备份

- `SUPPORTED_SCHEMA_VERSION = 13`：`core/store.py:27`；`RESERVED_SCHEMA_VERSIONS = frozenset(range(1,14))`
  :28。`SchemaMigration`（version/name/statements + checksum）：`adapters/state/migrations.py:46-56`；
  `MigrationRegistry` :1399-1447；`production_registry()` :1450-1465（V1–V13 显式注册）。
  目标化模块模式：`migrations_v13_preferences.py` 只导出 `V13_NAME`/`V13_STATEMENTS`，在
  `migrations.py:15,1396,1464` 手写接线。执行器 `operational.py:_migrate_locked` :578-624（迁移前自动
  online backup :603）；checksum 校验 :927-957（篡改 → NEEDS_REPAIR）。
- Backup v1：`BACKUP_MANIFEST_VERSION = 1`（`core/backup.py:20`）；`BackupManifest` :59-72（32 KiB
  预算 + `refuse_secret_material`）；bundle 布局写死 `database.sqlite`/`manifest.json`/
  `manifest.sha256`/`artifacts/<id>.artifact`（`application/backup.py:47-87` 创建、:116-179 校验）。
  凭据排除黑名单 :330-336。跨域 verify 函数三件套模板：`application/learning/learning_backup.py`、
  `memory_backup.py`、`preferences/backup.py`（按 schema 版本拆 verify_* 模块）。
- Doctor 模式：`application/doctor.py`（顺序 checks + 每域 `_inspect_*`；外部域检查器签名
  `(journal, workspace_id, counts, issues, issue_factory=...)`，learning :149-155 / memory :159-165）。

## 3. 官方 MCP Python SDK 评估（任务 2）

评估环境：`/tmp/mcp-eval-venv`（Python 3.13.0，与项目 `uv run` 一致；`requires-python` 要求
`>=3.10`，Python 3.12/3.13 均满足）。未触碰 `pyproject.toml`/`uv.lock`。

| 评估项 | 事实 |
|---|---|
| 版本/许可 | `mcp 2.0.0`，MIT；wheel `mcp-2.0.0-py3-none-any.whl` 342 016 B |
| Python | `>=3.10`；当前 3.13 实测通过；3.12 由元数据保证（anyio `<3.14` 分支为 4.9+） |
| stdio 生命周期 | `mcp.client.stdio.stdio_client(StdioServerParameters(command, args, env, cwd, ...))`：spawn 子进程、stdin/stdout newline-delimited JSON、上下文退出终止进程（spike 实测干净关闭） |
| 握手 | `ClientSession.initialize()`；Fake server 回显协议版本即可协商；`notifications/initialized` 由客户端发出（服务器端忽略） |
| 工具 | `tools/list` 翻页参数 `PaginatedRequestParams`；Python 侧 `Tool` 字段为 snake_case（`input_schema`、`server_info`），JSON 侧 `inputSchema` |
| 调用 | `call_tool(name, arguments, read_timeout_seconds=...)`；结果 `CallToolResult(content, is_error, structured_content, ...)`；内容块 `TextContent/ImageContent/AudioContent/EmbeddedResource/Resource/...` |
| 错误 | `MCPError(code, message, data)`；协议错误码含 `REQUEST_TIMEOUT(-32001)`、`METHOD_NOT_FOUND(-32601)`；超时消息 `Request 'tools/call' timed out` |
| 取消/超时 | 每调用 `read_timeout_seconds`（会话级默认可设）；超时抛 `MCPError`；**无公开 per-request cancel API**；取消模型 = 我方 wait_for + 关闭会话/进程 |
| 不重试 | spike 用服务器调用日志证明：超时调用 handler 只进入 1 次，SDK 不自动重试 |
| 结果校验 | `validate_tool_result` 在有 `output_schema` 时用 `jsonschema` 校验 `structured_content`（不匹配抛 `RuntimeError`）；`is_error=true` 结果跳过该校验 |
| OTel | 依赖 `opentelemetry-api`（1.44.0）；`get_tracer("mcp-python-sdk")` 仅 proxy，无 exporter、默认不产生网络/日志（`resync_tracer` 是 CPython 3.11 coverage 兼容补丁，与本项目无关） |
| 离线 Fake | 通过（§5）：纯 stdlib JSON-RPC over stdio 服务器即可，无需 SDK server 端 |
| 直接依赖 | `anyio>=4.9/4.10`、`httpx2>=2.5`、`jsonschema>=4.20`、`mcp-types==2.0.0`（精确钉）、`opentelemetry-api>=1.28`、`pydantic>=2.12`、`pyjwt[crypto]>=2.10`、`python-multipart>=0.0.9`、`sse-starlette>=3`、`starlette>=0.27/0.48`、`typing-extensions>=4.13`、`typing-inspection>=0.4`、`uvicorn>=0.31`（win32 另有 pywin32） |
| 传递依赖（实测 venv） | `cryptography`、`cffi`、`pycparser`、`h11`、`httpcore2`、`idna`、`jsonschema-specifications`、`referencing`、`rpds-py`、`truststore` |

对 Morrow 有利的关键事实：只有 stdio 客户端路径会被使用；服务器侧（uvicorn/sse-starlette/starlette）
只是依赖存在，不引入网络监听。httpx2 是 SDK 的 HTTP 传输依赖，v1 stdio 不用。

**风险提示**：SDK 2.0 处于活跃演进期（协议版本 `2025-11-25`/`2026-07-28`，含 InputRequired/claims/
`x-mcp-header` 等新机制，本 Spike 均未启用）。Morrow v1 只用最小面（initialize/list_tools/call_tool/
ping + 超时），并把 SDK 对象隔离在 `adapters/mcp/` 内；若升级 SDK 的行为变化发生在该窄面上，适配器
测试可拦截。

## 4. JSON Schema 方言与校验器选择（任务 3）

测量事实（jsonschema 4.26.0 + referencing 0.37.0）：

- `jsonschema.validators.validator_for(schema)`：`$schema` 缺失或未知时**静默回退**到最新 draft
  （2020-12），且对未知 `$schema` 发 DeprecationWarning —— 这是 fail-open 行为，Morrow 不得依赖。
- `Draft202012Validator.check_schema({...})`：对非法 schema 抛 `SchemaError`（能做到 fail-closed 编译）。
- `FormatChecker` 可用；format 默认仅注解不断言。

**选定方案**：wrapper 只放行一个受控 dialect：

- 支持：`https://json-schema.org/draft/2020-12/schema`；`$schema` 缺失时按 2020-12 处理
  （MCP 规范对 `inputSchema` 的约定）；
- 其余任何 `$schema`（含 draft-07/2019-09/draft-04 及未知 URI）→ 注册/校验前拒绝，报稳定错误；
- 使用前 `check_schema()`；校验引用的 schema 必须可解析（禁外部 `$ref` 拉取）；
- format 保持默认（注解）：结构校验失败才算无效，避免对格式的误拒；
- SDK 自带的结构化输出校验（`validate_tool_result`）保留为协议层事实，Morrow 归一化层再按
  `McpNormalizedResult` 契约做 bounded 转换。

**被否方案**：

1. 自研最小 JSON Schema 校验器 —— 复刻 2020-12（`$ref`、`unevaluated*`、format、组合关键词）成本高、
   易错；且 `jsonschema` 已随 mcp 传递存在，无维护收益。
2. 用 Pydantic 解释任意 JSON Schema —— pydantic 只解释由自身模型生成的 schema，不能直接消费任意
   外部 schema；需要翻译层，语义漂移风险。
3. 完全不校验（信任 SDK）—— SDK 只校验输出结构化内容，不校验我方即将发送的 arguments；边界
   校验（bounded、fail-closed）必须发生在 Morrow 侧。

## 5. Fake stdio 原型（任务 4）

`tests/spikes/fake_mcp_stdio_server.py`：纯 stdlib、同步 JSON-RPC 2.0 over stdio，实现
initialize/ping/tools/list/tools/call（echo、add、fail、slow 四个工具），`FAKE_MCP_CALL_LOG`
环境变量记录每次调用。`tests/spikes/test_mcp_stdio_spike.py` 使用官方 SDK 走完整
connect/list/call/close。运行结果（`uv run --with mcp pytest tests/spikes -q`，3.42 s，离线）：

```
tools = echo, add, fail, slow；tools[0].input_schema.properties.text.type == string
call echo {text: hello} -> TextContent "hello"（is_error False）
call add {a: 2, b: 3}   -> TextContent "5"
call fail {}            -> is_error True，content "boom"
call slow {seconds: 2} read_timeout_seconds=0.5
    -> MCPError: Request 'tools/call' timed out     （0.5 s 内抛出，无悬挂）
    -> 随后 call echo 仍成功（会话可用）
call_log: slow 恰好 1 次（SDK 不自动重试）
```

项目默认环境（无 mcp 依赖）下该测试 `importorskip` 跳过，保持离线门禁绿色：
`uv run pytest tests/spikes -q` → 1 skipped。

## 6. 预算测量（任务 5）与锁定预算

`tests/spikes/test_stage6_budgets.py`（确定性，无随机；sha256 用固定占位）。实测（紧凑 JSON）：

| 场景 | 实测 | 锁定上限 |
|---|---|---|
| AgentRunSnapshot 基座（8 preferences、4 source revisions、memory selection、Stage 6 provider_runtime + run_policy 证据） | 4 367 B | 64 KiB（不变） |
| + 8 个 Skill ref + 1 个 MCP ref（合计 9 个引用） | +1 630 B → 5 997 B | 单 ref ≤ 512 B（实测 ~181 B/个） |
| Skill 上下文条目（8 条，含短摘要） | 单条 250 B，合计 2 000 B | 单条 ≤ 2 KiB，每次运行合计 ≤ 16 KiB |
| MCP 工具快照（6 族真实形状 schema × 复制到 64） | 1 个 196 B；16 个 3 786 B；64 个 21 474 B | 单工具 ≤ 4 KiB（实测最大 3.8 KiB），每服务器合计 ≤ 64 KiB |
| MCP 归一化结果文本块 | — | ≤ 128 KiB/块（结果 DTO 上限；文本另有总字符预算） |

结论与方案一致：AgentRun 快照只放引用/摘要/计数（refs-only），正文走 `agent_run_skill_contexts`
专用表；MCP catalog 快照是证据不是第二权威。Subplan 65/67/72 使用以上常量。

## 7. Skill 包规范化规则（任务 6）

来源：最终方案 §6.3–6.5 不变，本 Spike 补充可执行规则：

- **envelope**：`skills/<source-kind>/<skill-id>/<skv-id>/package/...` + `managed-version.json`
  （由 Morrow 写入：展示版本、来源、安装时间、逐文件摘要、树摘要、批准证据引用）。目录名只用
  Morrow 分配的 `skv_<opaque-id>`，外部 manifest 的 `version` 永不直接成为目录名。
- **导入拒绝**：路径分隔符、`.`/`..` 段、绝对路径、NUL、保留名（如 `package`/`managed-version.json`）、
  非 UTF-8；**Unicode 归一化碰撞**（NFD/NFC 同形）与**大小写碰撞**（macOS 默认大小写不敏感 FS）
  在导入时以 canonical 比较拒绝；重名不同树摘要 → `identity_conflict`，不同 id 同名 → `name_conflict`。
- **canonical tree**：只含普通文件；路径归一化为 POSIX 相对路径（`/` 分隔、无 `.` 段、NFC）；
  按 (path, type, exec-mode, size, sha256) 排序后序列化 → `tree_digest = sha256(序列化)`；
  每个文件记录 type、与执行有关的 mode、长度、SHA-256。
- **拒绝**：symlink、hardlink、device、socket、FIFO，以及任何逃逸包根的引用。
- **TOCTOU**：发现阶段一次安全遍历；选择时冻结 `version_id + tree_digest`；加载正文/引用/asset/script
  时从“同一次安全打开得到的字节”算摘要并直接用该批字节，不做 stat-then-read；任何 drift →
  `degraded/unavailable`，已开始运行的 run 不换读新内容。Script 执行用只读版本快照或已验证字节的
  临时副本。

## 8. 迁移与备份版本化确认（任务 7）

- **迁移**：三版切分保持 —— v14 Skills/run selection（Subplan 65）、v15 Draft/Usage（68）、
  v16 MCP（72）。实施点：`core/store.py:27-28`（`SUPPORTED_SCHEMA_VERSION` 13→14→15→16、
  `RESERVED_SCHEMA_VERSIONS` 相应扩）、`migrations.py:1450-1465`（`production_registry()` 注册），
  每版独立 `migrations_vNN_*.py` 模块（v13 模式：仅导出 `VNN_NAME`/`VNN_STATEMENTS`，手写接线）。
  幂等、checksum 固定、future-version 拒绝、迁移前自动 backup（`operational.py:603`）都已有机制；
  允许空迁移保留槽位。领域模型中 global 的 `scope_id` 为 null；v14 SQLite 表用非空空字符串
  sentinel 存储它，避免复合唯一键和外键被 SQLite 的 NULL 语义削弱。
- **Backup v2**：`BACKUP_MANIFEST_VERSION`（`core/backup.py:20`）→ 2；`BackupManifest` 增加 v2 字段
  （Extension YAML 引用、被引用 managed Skill 版本/envelope、各域 schema 版本），并保持 32 KiB +
  `refuse_secret_material`；`application/backup.py:156-179` 的解析/校验按 manifest_version 分支；
  bundle 布局在 `database.sqlite`/`manifest.json`/`manifest.sha256`/`artifacts/` 之外增加
  `extensions/`（YAML）与 `skills/`（immutable 包），文件名校验同样按版本分支；凭据排除黑名单
  （:330-336）扩展；跨域 verify 采用已有三件套模式（learning/memory/preferences backup.py），新增
  `verify_skill_references`(v14)/`verify_draft_usage`(v15)/`verify_mcp_references`(v16)，并在
  `BackupVerificationReport`（`core/backup.py:100-102`）加对应布尔槽位。v1 包继续可校验；v2 verifier
  不会把缺 Stage 6 文件的 v1 误报为损坏。

## 9. 依赖推荐与门禁（任务 8 结论）

**推荐（待用户批准）**：当实施进入 Subplan 72（MCP 控制面）前，向用户提交精确变更：

```text
add: mcp >= 2.0.0, < 3        # 官方 MCP Python SDK，两处：mcp 客户端路径 + 类型
add: jsonschema >= 4.20, < 5   # 直接声明（SDK 已传递携带）；Morrow 侧 fail-closed validator 接缝
```

备选范围建议：`mcp==2.0.0`（当前已验证版本）；若批准时已有 2.0.x 修复版可用，取 `>=2.0.0,<3` 内
最新并重跑 §5 spike。`jsonschema` 直接声明是为让 `core/mcp/` validator 有稳定、不依赖 mcp 包内部
的导入边界；若用户只批准 `mcp`，则 validator 从 mcp 的传递依赖导入并显式记录版本下限约束。

**被否方案**：

1. 自研最小 JSON-RPC/MCP 客户端 —— 协议风险（初始化、翻页、进度、错误分类、版本协商、未来修订）
   与维护成本高；PLAN §6 明确“不得以自制不完整协议冒充完成”。
2. HTTP/SSE（streamable-http）传输 —— v1 明确 stdio only，不引入。

**门禁**：在用户明确批准上述精确依赖变更前，Subplan 72/73 保持 blocked；Skills（65–69）、
AgentRun 装配（64）、Provider/Model（70）、动态 Tool 契约（71）不受影响。本 Spike 已完成，未批准
依赖时按 PLAN §6 执行（记 blocker，不声称 Stage 6 完成）。

## 10. 对既有方案/计划的修正

无矛盾。本 Spike 全部结论与
`docs/reviews/stage-6-skills-and-extensions-final-proposal.md` 及 `.agent/PLAN.md` 一致；补充的
量化数据（§6 预算、§7 规则、§9 精确依赖版本）在对应子计划中直接使用。
