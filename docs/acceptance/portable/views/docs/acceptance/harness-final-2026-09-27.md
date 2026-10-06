> Portable reading copy. Original bytes and SHA-256 are preserved in [the evidence manifest](../../../manifest.json). Links alone are adapted for this repository.

# Harness 九项改进最终验收

> 最终更新：修复提交 `6018258d` 已通过第六轮代码与离线验收。此前缺陷探针全部转绿；最新结论与真实环境验收边界见[第六轮验收](harness-sixth-acceptance-2026-09-27.md)。本文是第一轮历史记录。

> 本文保留第一次验收的历史结果。后续 `2161dd33` 已修复前三项及第四项的同一 run 路径；再次验收仍发现 F4 跨 run 事实投影缺口。最新状态见[再次验收报告](harness-reacceptance-2026-09-27.md)。

日期：2026-09-27。验收对象：`803493e2b4d58c87127eaba955d2a2f62afcde46`。
对照基线：`172750ec4bffbd45a8e2bc4ec100a7944c412a44`。

## 验收结论

**未通过最终验收，不能确认九项实施全部无误。**

现有离线回归全部通过，九项均能找到实际实现；但本次补充故障注入复现了 **4 个缺陷、5 个失败断言**。问题集中在新增机制的交界处：后台输出与脱敏、溢出压缩与截止时间、交付复核与重试、后台命令与验证事实。应修复这些问题后再签收。

本次只执行验收与记录，未修改生产代码。以下“通过”均指所检查实现及离线覆盖，不代表真实 benchmark 成功率已提高。没有启动 Live 测试、真实模型请求或新一轮 Harbor 评测。

## 一、实际验证结果

| 检查 | 结果 |
|---|---|
| `uv run pytest -m 'not live' -q` | **2529 passed, 2 deselected**，186.61 秒 |
| `PYTHONPATH=evals/benchmarks evals/benchmarks/.venv/bin/python -m unittest discover -s evals/benchmarks/tests -q` | **27 tests，OK** |
| `uv run ruff format --check .` | 通过，688 files already formatted |
| `uv run ruff check .` | 通过 |
| `uv run python -m compileall -q src tests` | 通过 |
| `uv run morrow --help` | 通过 |
| `git diff --check` | 通过 |
| 本次独立验收探针 | **5 failed**，对应下面 4 个缺陷 |

复现文件：[regression_probes.py](../../../raw/docs/acceptance/harness-final-2026-09-27/regression_probes.py)。原始结果：[regression-results.txt](../../../raw/docs/acceptance/harness-final-2026-09-27/regression-results.txt)。主回归与 benchmark 输出保存在同目录的 `offline-results.txt`、`benchmark-results.txt`。

在项目根目录运行：

```bash
uv run pytest -q docs/acceptance/harness-final-2026-09-27/regression_probes.py
```

这些探针断言的是应满足的正确行为，当前版本失败是验收证据，不是预期通过的普通测试；没有使用 xfail 掩盖失败。文件位于验收材料目录，默认 `testpaths = ["tests"]` 不会收集它。探针只用合成凭据、脚本 Provider、假时钟与受控替身，不读取实际密钥，不启动后台进程，不使用真实网络或睡眠计时断言。修复后应将相应用例并入正式测试集。

## 二、阻止签收的问题及修改方案

### F1 · P1：后台输出跨边界时会泄漏已登记凭据的片段

**关联 H2。** 定位：[tracked_process.py:244](../../../../../../src/morrow/services/tracked_process.py#L244)、[process.py:132](../../../../../../src/morrow/services/process.py#L132)。

`_view()` 先按游标和 chunk 大小切原始输出；运行中再用 `_hold_secret_tail()` 固定扣除尾部长度，最后才做 exact-secret 替换。切点落在凭据内部时，交给 redactor 的只是前半段，因此无法匹配完整凭据。进程结束后不保留尾部，普通分页切点也有同样问题。

**复现：**已登记的合成凭据位于输出中间，分别模拟 running 和 exited。两种情况下 stdout 均出现凭据前缀，两个安全断言失败。这不是对真实凭据泄漏事件的声称，而是实际输出路径的确定性缺陷。

**修改：**在对外分页之前形成安全输出流，或采用带前后重叠、感知完整匹配和 UTF-8 边界的读取算法。已有 `SecretRedactor.release_cut()` 可作为边界处理参考，但不能只替换 running 分支；还须处理 finished 分页、任意 offset、ring buffer 截断以及字节/字符单位不同的情况。游标必须与选择的原始或脱敏流语义一致。

**复验：**stdout/stderr、运行中/已结束、凭据跨每一个切点、非 ASCII 凭据、输出截断及小 limit 均不返回可拼接的原始凭据片段；普通输出可持续读取，不能因保留区超过 limit 而永久不推进游标。

### F2 · P1：Provider 上下文溢出恢复绕过 run deadline

**关联 H3。** 定位：[agent.py:1977](../../../../../../src/morrow/runtime/agent.py#L1977)，对照主动压缩路径 [agent.py:1579](../../../../../../src/morrow/runtime/agent.py#L1579)。

主动压缩已使用剩余工作预算包裹压缩 await；`CONTEXT_OVERFLOW` 分支却直接调用 `_compact_context()`，没有同等的 `deadline.require_work()` 与 await 超时保护。主请求结束接近截止时间时，恢复分支仍可能发起摘要及重试，耗尽原本为终态和日志保留的时间。

**复现：**Provider 返回 context overflow；收到 compacting 事件后将假时钟推进到 10 秒（10 秒预算的工作截止为 9 秒）。实际仍进入 `_compact_context()`。探针证明“截止后仍接纳压缩”；真实摘要长时间挂起和 Harbor 强制终止是由缺失限时推导的风险，本次未用真实服务制造该场景。

**修改：**统一主动压缩和 overflow 恢复的限时入口，整段摘要请求、重试等待和恢复受同一个剩余工作预算约束。超时转为 `RunDeadlineExceeded`，通过既有终态通路记录 `run_timeout`，而不是将其当作普通压缩失败继续降级和请求。

**复验：**覆盖截止前耗尽、截止后不得接纳、摘要等待中截止、摘要重试跨截止四种路径；均停止新工作并保存已完成工具事实和部分 usage。避免只检查调用前预算而不限制正在等待的摘要。

### F3 · P2：交付复核在瞬态失败重试后丢失提示和候选回答

**关联 H4/H5。** 定位：[agent.py:1650](../../../../../../src/morrow/runtime/agent.py#L1650)、[agent.py:2145](../../../../../../src/morrow/runtime/agent.py#L2145)。

`pending_completion_review` 加入请求后立即清空，而候选回答按设计未写入 ConversationLog。若这次请求发生可重试网络错误，下一次请求从持久上下文重新组装，两者都消失。复核次数已经加一；当没有新的工具事实或 issue 时，可以直接接受重试返回的 final，无法保证真正完成过交付复核。

**复现：**工具成功 → 候选回答 → 复核请求网络失败 → 重试返回 final。共四次请求，第三次包含候选与复核内容，第四次缺失候选，最后仍正常 stop。现有重试机制确实恢复了请求，但没有恢复相同的复核语义。

**修改：**把复核状态绑定到一个逻辑请求，瞬态重试保留其候选文本和复核提示；只在成功消费响应、明确终止或以新复核替换时清理。候选继续保持为临时状态，不能为补救重试而提前写入正式历史。复核次数按逻辑复核计数，不随网络 attempt 重复增加。

**复验：**网络错误、无效响应、流中断重试后复核内容一致；已执行工具不重复执行；第二轮复核也覆盖相同情况，最终只提交被接受的回答。

### F4 · P2：后台命令没有进入事实链，旧验证仍被认作有效

**关联 H2/H4。** 定位：[bash_execution.py:81](../../../../../../src/morrow/services/bash_execution.py#L81)、[completion_check.py:41](../../../../../../src/morrow/runtime/completion_check.py#L41)。

foreground 会返回 CommandToolFact 和可选 ValidationFact；start/poll/stop 只返回 payload，`BashExecution.facts` 为空。新启动的后台命令可以改动工作区，但 completion check 无法看到它，验证结果也不会像前台不透明命令那样失效。后台验证完成后同样缺少相应终态事实。

**复现：**先记录 passed 验证，再通过真实 `run_bash(start)` 分支接纳 `printf changed > result.txt`；仅替换底层启动与读输出，避免实际进程副作用。返回 facts 为空，随后 `check_completion()` 仍为 passed、issues 为空。此探针验证事实投影缺口，不声称执行了真实文件修改。

**修改：**为跟踪命令增加“已接纳/运行中/已结束”的真实事实投影。启动可能改变工作区的命令时保守使旧验证失效；poll 观察到终态后幂等记录结果，识别出的验证命令才能形成 ValidationFact。不要伪造尚未结束的 CommandResult，也不要把普通重复 poll 当成新修改。持续运行且可能写工作区的任务，应保持验证不确定，直到停止或能够证明不会影响验收范围。

**复验：**验证通过 → 后台写入 → 收尾必须要求重新验证；后台测试失败必须可见；重复 poll 不重复记账、不反复使已经确定的新验证失效；合法服务保活不会被误报为已完成的成功命令。

## 三、九项逐项判定

| 原项 | 已确认实施 | 本次判定 |
|---|---|---|
| H1 shell 契约 | pinned shell；合法空参数/脚本处理；预检失败与真实非零退出区分 | **通过离线验收**，相关 shell 契约回归通过 |
| H2 timeout 与长进程 | 显式拒绝超上限；start/poll/stop；任务归属与 acceptance 生命周期 | **未通过**，F1、F4；已有生命周期测试仅证明其覆盖的宿主场景 |
| H3 deadline | 宿主预算传入、收尾预留、单请求总时限、重试/工具边界 | **未通过**，F2 的 overflow 恢复路径遗漏 |
| H4 交付复核 | 最多两次复核；公开事实；前台变更使旧证明失效；goal 保持 unverified | **未通过**，F3、F4 |
| H5 重试 | 工具后坏响应可重试；单故障窗口与 run 总等待分开；不重放已执行工具 | **核心离线回归通过，集成待修**，F3 说明请求语义未完整保留 |
| H6 上下文保真 | 摘要约束；降级状态线索；输出路径、反例、进程 ID 与 Artifact 引用 | **通过现有离线契约，效果证据不足**；语义用例使用脚本摘要，不能证明真实长任务最终验收相同 |
| H7 防循环 | 按相邻且相同参数/结果的失败计数；成功打断；重复成功仅提示 | **通过离线验收**，未发现本次范围内新阻塞 |
| H8 取证 | 增量诊断、取消时快照、部分 usage、脱敏指纹和轨迹投影 | **离线通过，真实环境待验**；尚未验证 Harbor 超时回收及日志挂载的完整链路 |
| H9 指纹与预算 | 冻结运行指纹、独立 run ID、进程锁、部分实耗和未知覆盖 | **接纳账本范围通过；请求级硬额度未实现**。代码与实施记录明确承认此边界，不能据此承诺绝不超支 |

H9 的硬上限是范围残留，不是账本代码暗中宣称已提供的能力。若签收标准仅包含原文列出的指纹、独立预算键、并发不丢账及未知覆盖，可按这些条件通过；若“全部九项完成”包含模型请求入口的严格 token 限制，则必须将这一项保留为未完成。

## 四、修复后如何签收

1. 先修 F1/F2，再修 F3/F4；将本次探针并入正式测试并补齐上述边界，全部转绿。
2. 重跑完整离线 Python 回归、benchmark adapter/账本测试和静态检查。不能只运行新增测试。
3. 分开记录“代码正确性验收”和“评测效果验收”。真实 Harbor 环境至少需要一次受控长命令/服务生命周期验证和一次强制超时诊断回收验证；当前 fake environment 测试不能替代日志挂载和容器清理实测。
4. 若要证明完成率/准确率改善，再使用冻结模型、effort、wheel、任务 checksum 和时限的可比评测，核查 trace/usage 覆盖。原来的 37/89 不能作为新实现分数，也不能由单元测试数量推算提升幅度。

当前可确认的是：基础改造已大幅落地、原有回归未发现失败，但新增边界缺陷尚未闭环。**最终签收应保持未通过，直到四项修复及对应复验完成。**
