> Portable reading copy. Original bytes and SHA-256 are preserved in [the evidence manifest](../../../manifest.json). Links alone are adapted for this repository.

# Harness 第六轮验收

日期：2026-09-27。验收提交：`6018258dd3276c37c4a8b8b5f7915eb80064a6fd`。
修复差异基线：`cbf45b8207a768031a18d83ffaaec67211f0e21a`。

## 结论

**通过本轮代码与离线验收。** 原有阻塞探针、新增证据有效性矩阵、完整离线回归及 benchmark 侧测试均通过；本轮代码复核未发现新的阻塞。此前 F1–F4 及历次追加的证据时序问题已在本次验收范围内闭环。

本轮聚焦原 F1–F4 缺陷及历次验收发现的 F4 生命周期、来源、时序遗漏。未修改生产代码；未执行 Live、真实模型、真实 Harbor trial 或 benchmark 重测。

## 实际验证结果

| 检查 | 本轮结果 |
|---|---|
| 前五轮全部独立验收探针 | **15 passed** |
| 完整离线回归 `pytest -m 'not live' -q` | **2564 passed, 2 deselected**，189.26 秒 |
| 新增证据有效性组合矩阵 | **64 passed** |
| benchmark 适配器及账本 unittest | **27 tests，OK** |
| `uv run ruff format --check .` | 通过，689 files already formatted |
| `uv run ruff check .` | 通过 |
| `uv run python -m compileall -q src tests` | 通过 |
| `uv run morrow --help` | 通过 |
| `git diff --check` | 通过 |

以上为本轮实际执行，未使用修复者的历史结果代替。验收矩阵文件也单独通过 Ruff 检查和格式检查。

## 已确认的修复行为

| 项目 | 本轮确认 |
|---|---|
| F1 后台输出脱敏 | 原运行中／已结束分页探针通过，正式脱敏边界回归纳入完整测试 |
| F2 overflow 压缩 deadline | 截止后拒绝压缩的原探针通过，摘要等待限时已在实现及正式回归中覆盖 |
| F3 交付复核重试 | 原候选与提示保留探针通过，正式测试覆盖网络错误、无效响应和第二轮复核 |
| F4 后台事实可见性 | running 在后续 run 可见，同一 run 重复观察不重复记录；终态可再次读取 |
| F4 证据新旧关系 | 首次领取与重复读取使用统一有效性规则，不以首次 poll 时间作为验证执行时间 |
| F4 跨 run 遗漏 | 来源不同或未知直接判为 historical，即使当前 run 的事实链为空也不放宽 |
| F4 本 run 的有效验证 | 来源相同且有启动事实，启动后无相关变更时可形成当前证明；后续变更／新验证阻止旧结果覆盖 |

本轮核心修复位于 [bash_execution.py:313](../../../../../../src/morrow/services/bash_execution.py#L313)：先要求 `origin_run_id` 非空且等于当前 run，再要求可找到启动事实，最后检查启动后的事实。该顺序消除了上一轮“扫描空事实链后默认有效”的分支。

测试夹具也补充了真实来源 run 和启动事实，没有继续依赖来源未知时的宽松行为。跨 run 旧失败改为历史证据可见、当前 `not_run`；当前重新验证仍可恢复 passed。

## 独立矩阵覆盖

本轮矩阵使用真实 `run_bash()`、registry 终态领取／回读、事实投影、completion check 与 metrics，仅替换 OS 存活刷新和缓冲区。没有启动 OS 进程、调用模型或使用实际凭据。

组合为 **4 × 2 × 2 × 4 = 64**：

- 来源：本 run 且有启动事实、本 run 但缺失启动事实、其他 run、未知来源。
- 原始终态：成功、失败。
- 观察：首次领取、已结算后读取。
- 当前较新事实：无、文件修改、新成功验证、新失败验证。

每个组合同时断言：

1. `historical` 与来源／变更条件一致。
2. completion check 与 run metrics 的 validation_outcome 一致。
3. 历史成功／失败不覆盖当前验证。
4. 同一状态重复 poll 不产生新事实，终态结算值不变。
5. 当前重新验证成功可恢复 passed，再次 poll 不使旧结果重新生效。

这组矩阵验证了本次约定的保守契约，不代表工作区外部并发修改、真实模型行为或所有运行环境已获得穷尽证明。

## 证据与复现

[evidence_matrix.py](../../../raw/docs/acceptance/harness-sixth-acceptance-2026-09-27/evidence_matrix.py) · [matrix-results.txt](../../../raw/docs/acceptance/harness-sixth-acceptance-2026-09-27/matrix-results.txt)。其他输出在同目录的 `offline-results.txt`、`previous-probe-results.txt`、`benchmark-results.txt`。

```bash
uv run pytest -q docs/acceptance/harness-final-2026-09-27/regression_probes.py docs/acceptance/harness-reacceptance-2026-09-27/boundary_probes.py docs/acceptance/harness-third-acceptance-2026-09-27/terminal_order_probes.py docs/acceptance/harness-fourth-acceptance-2026-09-27/delayed_observation_probes.py docs/acceptance/harness-fifth-acceptance-2026-09-27/cross_run_probes.py
uv run pytest -q docs/acceptance/harness-sixth-acceptance-2026-09-27/evidence_matrix.py
uv run pytest -m 'not live' -q
PYTHONPATH=evals/benchmarks evals/benchmarks/.venv/bin/python -m unittest discover -s evals/benchmarks/tests -q
```

## 签收范围

本轮签收对象是已报告缺陷的代码正确性与离线契约。H6 的真实长任务语义效果、H8 的真实 Harbor 容器强制超时日志回收仍需环境实测；H9 当前仍是任务接纳预算，不提供请求级硬 token 额度。这些是此前已经明确的验收边界，不因本轮探针通过而变成已验证能力。

没有重跑真实 benchmark，因此不能把旧成绩当作修复后成绩，也不能据此推断完成率或准确率提升幅度。
