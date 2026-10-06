> Portable reading copy. Original bytes and SHA-256 are preserved in [the evidence manifest](../../../manifest.json). Links alone are adapted for this repository.

# Harness 第三轮完整验收

> 后续状态：`dcea60e5` 已使本文的重复读取探针转绿；第四轮仍发现“旧进程终态延迟首次领取”会被当作新证明。最新结论见[第四轮验收](harness-fourth-acceptance-2026-09-27.md)。本文保留历史结果。

日期：2026-09-27。验收提交：`9192972657e77b3b107f427b4dfe12a08384614d`。
修复差异基线：`2161dd338b07b9aaeeb15a852285716b08578278`。

## 结论

**本轮仍不能最终签收：前两轮发现的问题所对应的 7 个探针已全部通过，但 F4 的跨 run 终态回读引入了证据时序错误。**

当前实现已经正确区分全局终态结算和每个 run 的状态可见性：running 状态能进入后续 run，同一 run 重复 poll 不重复记录，已结算的失败结果也可回读。不过，回读历史验证时使用了当前 poll 的 call_id 和 ordinal，导致旧验证被当成刚执行的新验证。已经过期的通过结果能覆盖新修改，甚至覆盖更新的失败结果。

这属于本次修复路径上的确定性准确率风险，不是要求新增功能。本次只做验收及文档记录，未修改生产代码。

## 实际检查

| 项目 | 本轮结果 |
|---|---|
| 第一轮 5 个探针＋第二轮 2 个跨 run 探针 | **7 passed** |
| 完整离线回归 `uv run pytest -m 'not live' -q` | **2552 passed, 2 deselected**，196.08 秒 |
| benchmark 适配器与账本 unittest | **27 tests，OK** |
| 本轮新增终态时序探针 | **2 failed**，对应同一缺陷 |
| `uv run ruff format --check .` | 通过，689 files already formatted |
| `uv run ruff check .` | 通过 |
| `uv run python -m compileall -q src tests` | 通过 |
| `uv run morrow --help` | 通过 |
| `git diff --check` | 通过 |

上述结果均为本轮实际执行；未以实施说明中的历史测试代替本轮检查。完整离线回归包含修复提交新增的跨 run 及终态回读正式测试，但未覆盖本轮发现的历史证明时序问题。

## 唯一阻塞：F4 · P2 · 历史通过结果被投影成新的有效验证

定位：[bash_execution.py:223](../../../../../../src/morrow/services/bash_execution.py#L223)、[bash_execution.py:261](../../../../../../src/morrow/services/bash_execution.py#L261)。判断依据：[completion_check.py:43](../../../../../../src/morrow/runtime/completion_check.py#L43)、[capabilities.py:338](../../../../../../src/morrow/core/capabilities.py#L338)。

`settled_terminal_fact()` 可读取以前结算的终态。`_tracked_facts()` 随后使用当前 poll 的调用 ID 和 ordinal 创建新的 ValidationFact，并追加到当前 run 的 facts 尾部。`check_completion()` 按事实顺序选取同 validator/scope 的最后一次验证；验证失效判断只检查该事实之后的变更。因此，“现在读取旧结果”被错误解释成“现在重新执行了验证”。

**确定性复现：**

1. 旧 run 的 acceptance 后台 pytest 已结束且 exit_code=0；旧 run 已 poll 并记录 passed。
2. 新 run 先产生较新的事实，分别测试两种情况：修改 `src/main.py`，或执行相同 scope 的验证并得到 failed。
3. 新 run poll 旧 execution，仅回读历史终态，没有执行新测试。
4. 两种情况都变为 `validation_outcome=passed`、`issues=()`。第二种情况下，更新的失败结果直接被旧成功结果遮蔽。

两个断言均失败。探针使用真实 registry 所有权检查、终态结算/回读、`run_bash()` 和 completion check；只替换操作系统存活刷新并注入已知事实，没有真实后台进程、模型调用或文件修改。该复现证明事实判定错误，不代表已经测量到真实 benchmark 分数受损。

## 修改方案与复验要求

- **保留原始验证的身份和顺序。** execution 的状态观察时间、验证实际执行时间、当前 poll 时间必须区分。回读不能制造一个在新修改之后执行过的验证事实。
- **历史结果应可见，但不自动成为当前产物的证明。** 最小保守方案是将回读结果作为历史证据投影，不允许旧 passed 提升当前 `validation_outcome`；当前通过状态应来自确实覆盖当前版本的新验证。如果复用历史证明，则需有明确版本或变更依据。
- **不要让回读覆盖更新验证。** 原始执行身份及可比较的顺序应参与 latest/stale 判定；缺少可比较依据时保持 unverified/not_run，而不是按 poll 追加顺序推断新旧。也不能仅靠把旧通过改成 failed，制造另一种失真。
- **保持本轮已修复行为。** 新 run 的 running 状态仍要可见；同一 run 相同状态的 poll 仍幂等；全局终态不重复结算；查询不能重放命令。

复验至少包括：旧 passed → 新修改 → 回读仍需重新验证；旧 passed → 新 failed → 回读不抹掉失败；旧 failed → 新 passed → 回读不把旧失败伪装成新失败；新验证成功后确实能够恢复 passed；前两轮 7 个探针及完整离线回归继续通过。

## 证据与验收边界

新增探针：[terminal_order_probes.py](../../../raw/docs/acceptance/harness-third-acceptance-2026-09-27/terminal_order_probes.py)。原始输出：[terminal-order-results.txt](../../../raw/docs/acceptance/harness-third-acceptance-2026-09-27/terminal-order-results.txt)。

```bash
uv run pytest -q docs/acceptance/harness-final-2026-09-27/regression_probes.py docs/acceptance/harness-reacceptance-2026-09-27/boundary_probes.py
uv run pytest -q docs/acceptance/harness-third-acceptance-2026-09-27/terminal_order_probes.py
uv run pytest -m 'not live' -q
PYTHONPATH=evals/benchmarks evals/benchmarks/.venv/bin/python -m unittest discover -s evals/benchmarks/tests -q
```

本轮回归输出保存于本报告同名目录。失败探针未标记 xfail，位于默认 pytest 收集范围外的验收目录，修复后应纳入正式测试。

H1、F1–F3 等此前通过项未发现本次范围内的新阻塞；本轮发现集中在 H2/H4 的终态历史证据时序。H6 的真实语义效果、H8 的真实 Harbor 超时回收、H9 的请求级硬额度仍沿用此前边界。没有执行 Live 或新 benchmark，不能据此声称完成率或准确率提升。
