# Stage 4 Durable Agent 验收证据

> 验收日期：2026-08-20
> 当前状态：Subplan 45 验收完成；Stage 5 保持未激活
> 当前分支：`feat/stage4-operational-store`
> Subplan 44 基线提交：`3e54dee` (`feat(permissions): complete Full Access Manual grants`)

本文只记录本次工作树实际运行过的命令、测试和观察结果。Live Provider、真实网络和未提供
runner 的平台不被标记为通过；Stage 3 macOS 宿主级安全证据继续由
[`stage-3-local-code-agent-evidence.md`](stage-3-local-code-agent-evidence.md) 保留。

## 1. 最终库存与所有权

| 合同 | 生产实现 | 直接测试/证据 |
|---|---|---|
| Operational Store v1–v9、迁移、健康与备份 | `src/morrow/adapters/state/operational.py`, `migrations.py`, `journal.py` | `tests/test_operational_store.py`, `tests/test_stage4_session_conversation.py`, `tests/test_stage4_backup.py`, `tests/test_stage4_operational_store_spike.py` |
| Session / Turn / ConversationLog / AgentRun | `src/morrow/application/turns.py`, `src/morrow/runtime/conversation.py`, `src/morrow/runtime/agent.py` | `tests/test_stage_boundary.py`, `tests/test_stage4_session_conversation.py`, `tests/test_stage4_recovery_crash.py` |
| TaskRun / TaskOutcome | `src/morrow/application/tasks.py`, `src/morrow/core/domain.py` | `tests/test_stage4_task_outcome.py` |
| Tool intent / Approval / Recovery | `src/morrow/core/execution.py`, `src/morrow/application/recovery.py` | `tests/test_stage4_tool_journal.py`, `tests/test_stage4_tool_persist.py`, `tests/test_stage4_recovery_crash.py` |
| Artifact / checkpoint / fork | `src/morrow/application/artifacts.py`, `checkpoints.py`, `src/morrow/adapters/state/artifacts.py` | `tests/test_stage4_artifacts.py`, `tests/test_stage4_context_fork.py` |
| Command / Query / Event / Doctor | `src/morrow/application/api.py`, `doctor.py`, `src/morrow/interfaces/cli.py` | `tests/test_stage4_application_api.py`, `tests/test_stage4_cli_operational.py`, `tests/test_stage4_doctor.py` |
| Full Access Manual | `src/morrow/core/permissions.py`, `src/morrow/application/grants.py`, `src/morrow/runtime/agent.py` | `tests/test_stage4_permissions.py`, `tests/test_stage4_tool_persist.py`, `tests/test_capability_policy.py` |

固定库存边界如下：schema 当前为 v9；唯一 Stage 4 elevated capability 是
`unconfined_host_process`；生产工具仍是 Stage 3 已声明集合，Full Access 只改变同一
`run_command` intent 的显式 grant/approval 证据，不增加 browser、MCP、网络专用、Git 写入或
outside-file 工具；CLI/REPL/API 的 grant command 是唯一提权入口；Full Access Auto/raw auto
没有生产路径。

## 2. Definition of Done 矩阵

| # | 要求 | 证据 | 结果 |
|---:|---|---|---|
| 1 | 工作空间隔离的 Session 可创建、列出、恢复、归档、Fork | `tests/test_stage_boundary.py`, `tests/test_stage4_session_conversation.py`, `tests/test_stage4_context_fork.py`；隔离 wheel smoke 的 Session 重启脚本 | 通过 |
| 2 | 消息顺序和 ToolCycle 恢复唯一且合法 | `tests/test_stage4_session_conversation.py`, `tests/test_stage4_durable_log.py`, `tests/test_stage4_tool_persist.py` | 通过 |
| 3 | turn submit 按 `client_message_id` 幂等，恢复只创建关联 AgentRun | `tests/test_stage4_recovery.py`, `tests/test_stage4_recovery_crash.py`, `tests/test_stage_boundary.py` | 通过 |
| 4 | side-effecting intent 在 handler 前提交 | `tests/test_stage4_tool_persist.py::test_intents_are_visible_from_a_fresh_connection_before_handler`, `::test_handler_does_not_run_when_intent_commit_fails` | 通过 |
| 5 | 未完成 Host/Sandbox/写入按证据分类且不自动重放 | `tests/test_stage4_recovery_crash.py`, `tests/test_stage4_recovery.py`, `tests/test_sandbox.py` | 通过；嵌套环境跳过两项真实 Seatbelt 测试 |
| 6 | Store、Session lifecycle、Session health/quarantine 独立，损坏/future 不静默覆盖 | `tests/test_operational_store.py`, `tests/test_stage4_session_conversation.py`, `tests/test_stage4_doctor.py` | 通过 |
| 7 | TaskRun 跨 Turn 继续、纠正、取消、接受并生成版本化 Outcome | `tests/test_stage4_task_outcome.py` | 通过 |
| 8 | Artifact 有界、脱敏、hash/size/provenance 可验证 | `tests/test_stage4_artifacts.py`, `tests/test_stage4_backup.py` | 通过 |
| 9 | checkpoint 保留完整 Cycle/来源，Fork 不修改父 Session/文件 | `tests/test_stage4_context_fork.py` | 通过 |
| 10 | CLI/REPL/未来客户端共享 Application boundary，events 有序且无 outbox | `tests/test_stage4_application_api.py`, `tests/test_stage4_cli_operational.py`, `tests/test_stage4_doctor.py` | 通过 |
| 11 | Doctor、online backup、restore、migration、contention、corruption、Artifact fault 有证据 | `tests/test_stage4_doctor.py`, `tests/test_stage4_backup.py`, `tests/test_operational_store.py`, `tests/test_stage4_operational_store_spike.py`, `tests/test_stage4_artifacts.py` | 通过 |
| 12 | CapabilityGrant 按 AgentRun 创建、冻结、过期、撤销，crash resume 不继承 | `tests/test_stage4_permissions.py`, `tests/test_stage4_recovery_crash.py`, `tests/test_stage4_tool_persist.py` | 通过 |
| 13 | Full Access Manual 每次 elevated effect 单独审批并展示 unconfined 风险，Auto 不可用 | `tests/test_stage4_tool_persist.py`, `tests/test_capability_policy.py`, `tests/test_capability_executor.py` | 通过 |
| 14 | Stage 3 产品故事、安全门禁、offline suite、package recovery 继续通过 | 本文第 3–5 节；`tests/test_stage2_product_acceptance.py`, `tests/test_stage3_product_acceptance.py`, package smoke | 通过；Linux 保持 unsupported |

## 3. 实际产品与故障回归

专项命令：

```text
UV_CACHE_DIR=/tmp/morrow-uv-cache uv run pytest -q \
  tests/test_stage2_product_acceptance.py \
  tests/test_stage3_product_acceptance.py \
  tests/test_stage_boundary.py \
  tests/test_stage4_session_conversation.py \
  tests/test_stage4_task_outcome.py \
  tests/test_stage4_artifacts.py \
  tests/test_stage4_context_fork.py \
  tests/test_stage4_backup.py \
  tests/test_stage4_recovery_crash.py \
  tests/test_stage4_tool_persist.py \
  tests/test_stage4_permissions.py \
  tests/test_stage4_doctor.py \
  tests/test_operational_store.py \
  tests/test_stage4_operational_store_spike.py \
  tests/test_sandbox.py
→ 140 passed, 2 skipped in 6.42s
```

两个 skip 是 `tests/test_sandbox.py` 中要求在 nested Codex 环境外运行的真实 macOS Seatbelt
测试；其宿主级历史证据仍为 Stage 3 acceptance 的 2 passed。当前嵌套回归覆盖 sandbox
规则、fail-closed 和 production inventory，其余 Stage 3 产品故事均通过。

故障矩阵由上述专项切片覆盖：logical fault points、SQLite contention/WAL、subprocess
`os._exit`、intent/approval/handler/ToolMessage boundaries、Artifact publish/restore、
checkpoint/fork、grant freeze/revoke/expiry、crash resume、migration/future/corrupt store。
测试使用 `FixedClock`、barrier/pipe、Scripted Provider 和 bounded polling，不以 wall-clock
sleep 作为断言。

## 4. Package build/install/recovery

构建依赖首次在 sandbox 中因 DNS 无法解析 PyPI 失败；按允许的构建依赖网络权限重试成功：

```text
UV_CACHE_DIR=/tmp/morrow-uv-cache uv build --wheel
→ Successfully built dist/morrow_agent-0.1.0-py3-none-any.whl
SHA-256: 1a71fe0f60f43ee05ea4a325e630616c9317b8e5e98c300507ee8969cabb1182
```

Wheel 在 `/private/tmp/morrow-stage4-package-smoke.TJE9l1/.venv` 中以 `uv pip install --no-deps`
安装成功；使用已验证的 workspace dependency environment 作为运行时依赖路径执行：

```text
import morrow → /private/tmp/.../.venv/lib/python3.13/site-packages/morrow/__init__.py
importlib.metadata.version("morrow-agent") → 0.1.0
bundled agent-policy.toml → present
installed `morrow --help` → exit 0
```

同一隔离安装环境通过真实 `build_application()` / `build_session_application()` 组合完成一次
Scripted Provider 对话，关闭 persistence，再用同一 Session ID 重新构造应用并读取历史：

```text
installed package durable session recovery: passed ses_1 4
```

这证明 wheel 安装后的代码能够在隔离 data root 创建 Operational Store、持久化 Session 并在
进程对象重建后恢复；没有把运行时依赖声明为 wheel 自包含，也没有执行 Live Provider。

## 5. 最终质量门禁

Subplan45 closeout 重新运行了最终代码门禁：

```text
UV_CACHE_DIR=/tmp/morrow-uv-cache uv run pytest -m 'not live'
→ 600 passed, 2 skipped, 1 deselected in 12.02s

UV_CACHE_DIR=/tmp/morrow-uv-cache uv run ruff format --check .
→ 158 files already formatted

UV_CACHE_DIR=/tmp/morrow-uv-cache uv run ruff check .
→ All checks passed!

UV_CACHE_DIR=/tmp/morrow-uv-cache uv run python -m compileall -q src tests
→ exit 0

UV_CACHE_DIR=/tmp/morrow-uv-cache uv run morrow --help
→ exit 0; grant/state/session/task/artifact/recovery commands rendered

git diff --check
→ exit 0

UV_CACHE_DIR=/tmp/morrow-uv-cache uv build --wheel
→ Successfully built dist/morrow_agent-0.1.0-py3-none-any.whl
→ SHA-256: 1a71fe0f60f43ee05ea4a325e630616c9317b8e5e98c300507ee8969cabb1182
```

隔离 wheel 安装、CLI help、bundled policy 资源检查和同一 Session 的重建恢复已通过；结果见第 4
节。两个 Seatbelt skip 是 nested Codex 环境限制，宿主级历史证据仍由 Stage 3 acceptance 保留。
Live Provider/network、Linux native runner、Windows sandbox、Full Access Auto、background tasks、
GUI、MCP/Skills 和 multi-agent 仍明确为未实现或 unsupported。
