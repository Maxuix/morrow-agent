> Portable reading copy. Original bytes and SHA-256 are preserved in [the evidence manifest](../../../manifest.json). Links alone are adapted for this repository.

# Harness 缺陷修复再次验收

> 后续状态：`91929726` 已修复本文的跨 run 可见性问题，原 7 个探针通过；第三轮验收发现历史终态回读被当成新验证的时序缺陷。最新结论见[第三轮完整验收](harness-third-acceptance-2026-09-27.md)。本文保留第二轮历史结果。

日期：2026-09-27。验收提交：`2161dd338b07b9aaeeb15a852285716b08578278`。
对照提交：`803493e2b4d58c87127eaba955d2a2f62afcde46`。

## 结论

**仍未通过最终签收：F1、F2、F3 已通过本次离线复验；F4 同一 run 内已修复，但跨 run 的事实投影仍有缺口。**

上次五个探针全部转绿，修复提交也将相关回归纳入正式测试。但本次对 F4 增加生命周期边界检查后，两种合法场景仍失败。该问题是原“后台命令应进入事实链”的残留，不是增加新功能要求。

本次没有修改生产代码，没有运行 Live、真实模型或 Harbor trial。真实评测收益、H8 容器超时日志回收及 H9 请求级硬额度的既有边界不变。

## 复验范围和结果

| 检查项 | 结果 |
|---|---|
| 上次独立探针 `regression_probes.py` | **5 passed** |
| 完整离线回归 `pytest -m 'not live' -q` | **2549 passed, 2 deselected**，197.13 秒，包含新增正式回归 |
| benchmark 侧 `unittest discover` | **27 tests，OK** |
| 本次跨 run 探针 `boundary_probes.py` | **2 failed**，同一个 F4 残留缺陷 |
| Ruff 格式检查 | 通过，689 files already formatted |
| Ruff 静态检查 | 通过 |
| `compileall -q src tests` | 通过 |
| `morrow --help` | 通过 |
| `git diff --check` | 通过 |

所有检查均为本轮实际执行，未将修复说明里的历史结果算作本轮结果。完整回归、原探针与 benchmark 输出分别保存为本报告同名目录中的 `offline-results.txt`、`original-probe-results.txt`、`benchmark-results.txt`。

| 原缺陷 | 本次代码复核 | 判定 |
|---|---|---|
| F1 输出脱敏 | 在保留窗口先识别凭据及边界片段，再按原始字节游标分页；支持小 limit、UTF-8、stderr 和截断 | 原探针通过，正式测试覆盖相关边界；本次未发现新阻塞 |
| F2 overflow 压缩超时 | overflow 分支增加剩余预算检查及整段 await timeout，截止转为 `run_timeout` | 截止后拒绝和摘要等待超时已有正式测试；原探针通过 |
| F3 复核重试 | 成功消费响应后才清除复核提示，失败 attempt 保留提示和候选 | 原探针通过；正式测试覆盖网络/无效响应、第二轮复核及工具不重放 |
| F4 后台事实链 | start 记录 running，首次终态记录结束/验证，重复 poll 去重 | **仅同一 run 闭环；新 run 的 running poll 被完全略过** |

## 唯一剩余阻塞：F4 · P2 · 后台进程跨 run 后仍可绕过验证失效判断

定位：[bash_execution.py:219](../../../../../../src/morrow/services/bash_execution.py#L219)。相关入口：[agent.py:1046](../../../../../../src/morrow/runtime/agent.py#L1046)。

`_tracked_facts()` 对非 start 且没有新终态的调用直接返回空元组。因此 running 进程的 poll 永远不投影事实。与此同时，每次 `AgentLoop.run_task()` 创建新的 `ToolRunContext`；进程 registry 可以跨回合保留，acceptance 进程还允许同一会话的后续任务访问。

原 start 产生的 running 事实不在新 run 的 facts 中。于是后续 run 即使刚通过 poll 确认该进程仍在运行，`active_tracked_executions(run.facts)` 仍为空，completion check 可以继续把已有验证视为有效。这与同一 run 中“正在运行、可能持续修改工作区的进程应使验证不确定”的新规则不一致。

**本次确定性复现的两个场景：**

1. `lifecycle=task`，同一任务进入下一 run，再次 poll 仍在运行的进程。
2. `lifecycle=acceptance`，同一会话的下一任务 poll 前一任务保留的进程。

两者均使用真实 registry 可见性检查、`run_bash(poll)`、事实投影和 `check_completion()`；只将底层存活刷新替换为受控 running 状态，不启动 OS 进程。结果都是 `payload.status=running`、`facts=()`、`validation_outcome=passed`、`issues=()`，而正确行为应保持验证不确定。

这证明事实链与已观察状态不一致；本次没有声称复现了真实文件被修改或 benchmark 错判得分。

**修改方案：**

- 区分“执行生命周期事件只结算一次”与“当前 run 需要看见已有执行状态”。不能用全局去重替代当前 run 的状态投影。
- 将当前 `run` 传入事实投影；当前 run 第一次观察某 execution 时投影其真实状态，其后相同状态的 poll 不重复记账。也可在交付检查时合并任务可见的存活进程快照，但需保持统一的事实来源。
- 检查 `claim_terminal_fact()` 的全局 `terminal_fact_recorded` 作用域：持久化终态可只写一次，其他 run 仍须能够读取已存在的终态证据。不要为修复可见性重复计费或重复执行命令。
- 对仍在运行的进程保持保守验证状态，直到终态或能证明其不影响验收范围；在后续 run 中也应保持相同规则。

**复验条件：**本次两个断言转绿；同一 run 重复 poll 仍幂等；终态在后续 run 可见；结束后重新验证可恢复 passed；已结束的失败验证不会因另一 run 曾读取而消失；不存在工具重放或终态重复结算。

## 复现与证据

```bash
uv run pytest -q docs/acceptance/harness-final-2026-09-27/regression_probes.py
uv run pytest -q docs/acceptance/harness-reacceptance-2026-09-27/boundary_probes.py
uv run pytest -m 'not live' -q
PYTHONPATH=evals/benchmarks evals/benchmarks/.venv/bin/python -m unittest discover -s evals/benchmarks/tests -q
```

新增探针：[boundary_probes.py](../../../raw/docs/acceptance/harness-reacceptance-2026-09-27/boundary_probes.py)。失败原文：[boundary-results.txt](../../../raw/docs/acceptance/harness-reacceptance-2026-09-27/boundary-results.txt)。其断言保持为正确行为要求，没有 xfail；验收目录不在默认 pytest 收集范围。

修复 F4 的跨 run 可见性并完成上述复验后，可再次判定代码层面的签收；不能仅凭原五个探针转绿宣布生命周期所有边界均已闭环。
