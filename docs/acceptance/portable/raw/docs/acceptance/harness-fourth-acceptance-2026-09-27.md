# Harness 第四轮验收

> 后续状态：`cbf45b82` 已使本文的延迟领取探针转绿；第五轮发现上一 run 的变更仍可在新 run 空事实链下被遗漏。最新状态见[第五轮验收](/Users/ruirui/Documents/Project/Agent/developing/docs/acceptance/harness-fifth-acceptance-2026-09-27.md)。本文保留历史结果。

日期：2026-09-27。验收提交：`dcea60e5644e15e143edec5e9368074652bc5679`。
本轮修复基线：`9192972657e77b3b107f427b4dfe12a08384614d`。

## 结论

**仍未通过最终签收。** 前三轮累计 9 个验收探针全部通过；已结算终态的重复读取现在会标记为 historical，且不会覆盖当前验证。仍有同一根因的遗漏路径：**旧进程已经结束，但终态尚未被 poll 结算时，第一次延迟读取会被错误当成当前的新验证。**

本轮补充同一 run／跨 run、后续修改／后续失败验证的组合，4 个断言均复现该问题。它们对应一个 F4 证据时序缺陷，不是四个独立缺陷。本次未修改生产代码。

## 本轮实际检查

| 检查 | 结果 |
|---|---|
| 前三轮独立探针 | **9 passed** |
| 完整离线回归 `pytest -m 'not live' -q` | **2555 passed, 2 deselected**，196.49 秒 |
| benchmark 适配器及账本 unittest | **27 tests，OK** |
| 新增延迟首次读取探针 | **4 failed**，全部为预期正确性断言失败 |
| Ruff 格式检查 | 通过，689 files already formatted |
| Ruff 静态检查 | 通过 |
| `compileall -q src tests` | 通过 |
| `morrow --help` | 通过 |
| `git diff --check` | 通过 |

全部结果均为本轮实际执行，未使用修复说明中的历史结果代替本轮检查。完整回归包含已增加的 historical 正反向用例，但尚未覆盖延迟首次领取终态的因果顺序。

## F4 · P2：将“首次领取终态”误当成“新执行的验证”

定位：[bash_execution.py:219](/Users/ruirui/Documents/Project/Agent/developing/src/morrow/services/bash_execution.py:219)、[tracked_process.py:183](/Users/ruirui/Documents/Project/Agent/developing/src/morrow/services/tracked_process.py:183)。

`_tracked_facts()` 初始化 `historical=False`。只有 `claim_terminal_fact()` 返回空、随后改为读取 `settled_terminal_fact()` 时才设置 historical。这个判断区分的是终态有没有被领取过，不能证明验证是否覆盖当前工作区版本。

合法顺序如下：后台测试成功退出 → 暂时未 poll → 工作区修改，或新的测试失败 → 首次 poll 旧进程。旧进程的 `terminal_fact` 仍为空，因此 claim 成功；投影创建 `historical=False` 的 ValidationFact，并使用此次 poll 的 call_id/ordinal 追加到事实链末尾。completion check 再次把旧 passed 当成最新验证。

**本轮复现矩阵：**

| 进程观察范围 | 旧测试结束后的事实 | 首次 poll 的错误结果 | 正确要求 |
|---|---|---|---|
| 同一 run，已有 running 启动事实 | 文件修改 | passed | 需要重新验证 |
| 同一 run，已有 running 启动事实 | 更新的 failed 验证 | passed | 不得抹掉较新失败 |
| 后续 run，可访问 acceptance 进程 | 文件修改 | passed | 需要重新验证 |
| 后续 run，可访问 acceptance 进程 | 更新的 failed 验证 | passed | 不得抹掉较新失败 |

四个场景均调用真实 `run_bash()`、registry 可见性检查、首次终态领取、事实记录和 completion check。仅对底层存活刷新使用替身，构造已知的 exited 进程与有序事实；不运行 OS 进程、不调用模型、不实际修改代码。最终断言均因 `validation_outcome='passed'` 失败，没有 fixture 或导入错误。它证明验收判断缺陷，不代表本轮测量了 benchmark 分数损失。

## 修复建议：用执行来源和版本关系判定证据有效性

1. 在命令接纳时保存原始执行身份、启动 run 及当时工作区变更水位；终态保存实际执行的来源信息。领取或重复读取只改变观察状态，不能改变验证的新旧关系。
2. 对“首次领取”和“重复读取”使用同一证据有效性规则。`terminal_fact is None` 只负责全局结算幂等，不能作为 fresh 验证的依据。
3. 保守规则可为：跨 run 且不能证明版本一致的验证仅作历史线索；同一 run 的后台验证，如果启动后出现会影响验收的变更或更新验证，不得仅凭稍后的 poll 提升为最新 passed。当前结构无法确定覆盖关系时保持未验证，并要求重新执行。
4. 保持历史结果可见，且不覆盖更新证据；running 状态的跨 run 投影、重复 poll 去重和终态单次结算继续保留。

**下一轮应覆盖完整组合：**首次领取／重复读取 × 同一 run／跨 run × 后续修改／新失败／新成功。再增加“修改后新启动测试、期间无变更、成功结束”的正向用例，确保新验证确实可以恢复 passed。旧失败也不得因延迟首次读取而伪装成比新成功更晚的验证。已有 9 个探针和完整离线回归须继续通过。

## 复现与证据

```bash
uv run pytest -q docs/acceptance/harness-final-2026-09-27/regression_probes.py docs/acceptance/harness-reacceptance-2026-09-27/boundary_probes.py docs/acceptance/harness-third-acceptance-2026-09-27/terminal_order_probes.py
uv run pytest -q docs/acceptance/harness-fourth-acceptance-2026-09-27/delayed_observation_probes.py
uv run pytest -m 'not live' -q
PYTHONPATH=evals/benchmarks evals/benchmarks/.venv/bin/python -m unittest discover -s evals/benchmarks/tests -q
```

新增探针：[delayed_observation_probes.py](/Users/ruirui/Documents/Project/Agent/developing/docs/acceptance/harness-fourth-acceptance-2026-09-27/delayed_observation_probes.py)。失败原文：[delayed-observation-results.txt](/Users/ruirui/Documents/Project/Agent/developing/docs/acceptance/harness-fourth-acceptance-2026-09-27/delayed-observation-results.txt)。其他原始输出保存在同目录。探针位于默认 pytest 收集范围之外，未标记 xfail；修复后应纳入正式回归。

此前其他通过项本轮未发现新的阻塞。未执行 Live、真实 Harbor trial 或新 benchmark；H6 实际语义效果、H8 容器超时回收及 H9 请求级硬额度的既有验收边界不变。
