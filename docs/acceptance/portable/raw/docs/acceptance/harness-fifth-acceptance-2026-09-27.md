# Harness 第五轮验收

> 后续状态：`6018258d` 已通过第六轮代码与离线验收；本文两个残留探针转绿，并新增 64 组有效性矩阵。最新结论见[第六轮验收](/Users/ruirui/Documents/Project/Agent/developing/docs/acceptance/harness-sixth-acceptance-2026-09-27.md)。本文保留历史结果。

日期：2026-09-27。验收提交：`cbf45b8207a768031a18d83ffaaec67211f0e21a`。
修复基线：`dcea60e5644e15e143edec5e9368074652bc5679`。

## 结论

**仍有一个 F4 残留，暂不签收。** 此前 13 个探针全部通过；首次领取和重复读取已使用同一有效性规则，同一 run 的启动锚点和后续变更也已有实现。但来源 run 不同或未知时，代码仍允许用当前 run 的空事实链证明旧结果有效，没有覆盖上一个 run 中发生的修改。

本次没有增加新的功能要求；发现仍属于原“旧验证不能证明修改后的产物”的条件遗漏。本次仅验收及记录，未修改生产代码。

## 本轮执行结果

| 检查 | 结果 |
|---|---|
| 前四轮独立验收探针 | **13 passed** |
| 完整离线回归 `pytest -m 'not live' -q` | **2562 passed, 2 deselected**，192.52 秒 |
| benchmark adapter/账本 unittest | **27 tests，OK** |
| 新增跨 run 变更探针 | **2 failed**，同一个缺陷的首次领取／重复读取路径 |
| Ruff 格式检查 | 通过，689 files already formatted |
| Ruff 静态检查 | 通过 |
| `compileall -q src tests` | 通过 |
| `morrow --help` | 通过 |
| `git diff --check` | 通过 |

所有结果均来自本轮实际执行。完整回归包含新增的同一 run 启动前后变更用例；本轮新增的“上一 run 已修改、当前 run 事实为空”场景仍未满足正确性要求。

## F4 · P2：新 run 没有变更事实，不代表旧测试后工作区未变

定位：[bash_execution.py:324](/Users/ruirui/Documents/Project/Agent/developing/src/morrow/services/bash_execution.py:324)、[bash_execution.py:349](/Users/ruirui/Documents/Project/Agent/developing/src/morrow/services/bash_execution.py:349)。

`_terminal_covers_current()` 只在 `origin_run_id == run.run_id` 时查找启动锚点。跨 run 或来源未知时，`anchor=-1`，函数扫描当前 run 的全部 facts；如果当前 run 还没有变更或验证事实，就返回 True。

然而 `AgentLoop.run_task()` 每轮创建新的 ToolRunContext。上一轮的修改并不在新 run 的 facts 中，工作区却保留该修改。该函数缺少跨 run 的版本一致性证明，无法从空列表推出“旧测试覆盖当前产物”。

**确定性复现顺序：**

1. 同一任务的旧 run 启动后台 pytest，进程已成功退出，记录真实来源 `started_run_id='old-run'`。
2. 在旧 run 修改 `src/main.py`，此时旧 run 的 completion check 已不再返回 passed。
3. 下一 run 创建空 ToolRunContext，继续使用同一个工作区和进程 registry。
4. 下一 run poll 旧 execution。没有执行新测试，却返回 `validation_outcome='passed'`，把旧证明重新认作有效。

分别测试“旧 run 已读取成功终态后再修改”及“退出后未读取，修改后在新 run 首次读取”；两条路径都失败。探针使用实际 registry、`run_bash()`、来源信息和 completion check；仅替换 OS 存活刷新并注入有序工具事实，没有启动进程、实际文件修改或模型请求。结论是判定逻辑有缺陷，不是声称本轮测得 benchmark 分数损失。

## 最小修复方案

当前没有跨 run 的工作区版本证明时，直接采用保守入口：

```python
if origin_run_id is None or origin_run_id != run.run_id:
    return False
```

该判断应位于同一 run 的锚点扫描之前。跨 run 的成功和失败仍可作为 historical 证据展示，但不得自动成为当前有效验证，也不得覆盖当前验证。这样不需要增加全局版本管理功能。

保留已实现的同一 run 规则：找到本 run 的原始启动事实，检查之后是否存在变更、不透明命令或更新验证；无变更且验证确实启动于当前版本时，终态仍可提供新证明。

只有未来确实需要跨 run 复用验证时，才引入能够证明版本未变的工作区标识或变更水位；不能仅扫描新 run 的局部 facts 替代该证明。

正式测试 `test_settled_terminal_state_stays_visible_to_later_runs` 当前把“新 run 无事实”解释成旧失败可直接决定当前 outcome，应同步调整为历史证据可见、当前验证未执行。否则测试会约束实现继续保持这一乐观分支。

## 复验要求

- 本次两个断言转绿，来源未知和来源不同即使当前事实为空，也不产生当前 passed。
- 跨 run 的旧 failed 不覆盖新 passed，旧 passed 不覆盖新 failed；历史输出仍可见。
- 本 run 内修改后新启动验证、期间无变更、成功结束，仍可得到 passed；启动后有修改则需要重验。
- 原有 13 个探针、正式回归、重复 poll 幂等及全局单次终态结算继续通过。

## 复现与证据

新增探针：[cross_run_probes.py](/Users/ruirui/Documents/Project/Agent/developing/docs/acceptance/harness-fifth-acceptance-2026-09-27/cross_run_probes.py)。失败结果：[cross-run-results.txt](/Users/ruirui/Documents/Project/Agent/developing/docs/acceptance/harness-fifth-acceptance-2026-09-27/cross-run-results.txt)。本轮其他输出保存在同目录。

```bash
uv run pytest -q docs/acceptance/harness-fifth-acceptance-2026-09-27/cross_run_probes.py
uv run pytest -m 'not live' -q
PYTHONPATH=evals/benchmarks evals/benchmarks/.venv/bin/python -m unittest discover -s evals/benchmarks/tests -q
```

新增探针未标记 xfail，位于默认 pytest 收集范围外的验收目录。既有探针本轮联合运行，全部通过。未执行 Live、真实 Harbor trial 或 benchmark 重测；H6 实际效果、H8 容器强制超时回收、H9 请求级硬额度等既有边界未发生变化。
