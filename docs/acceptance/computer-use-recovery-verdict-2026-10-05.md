# Luna 现场复测的恢复后计分修复（2026-10-05）

结论：已修复后台 enabled 在 stale 恢复成功后仍被历史 `not_checked` 误判为 unsupported 的验收缺口。原现场复测与所有原始 JSON 保持 **4 passed / 1 unsupported**；修复后对同一证据做离线重算为 **5 passed / 0 unsupported**。本轮新增原生输入、模型请求均为 0，不将离线重算称为新的现场复测。

## 根因与修复

`campaign_verdict.py` 原先对全部历史 action_outcomes 执行 any(not_checked)，并对属性、存在、文本后置条件执行 any(passed)。第一次 stale/not_started 未执行，后置条件自然 not_checked；恢复后唯一输入已得到精确读取及 passed，仍被历史记录覆盖。相反，历史 passed 也可能掩盖最终 failed。

现在先保留既有恢复校验：历史尝试必须有同 call_id 的 stale/not_started 证据，新观察不可重复，动作变体正确，原生入口恰好一次，快照健康且新 revision 有效。之后只根据**最终动作 call_id 关联的工具诊断 outcome**计分。重复输出的相同历史回执可接受；最终回执缺失、冲突、错误 envelope、或未证明输入已发生时，拒绝判为通过。

- 最终属性 passed：与独立点击效果共同计为 passed。
- 最终属性 not_checked：效果成立仍为 unsupported；效果不成立为 failed。
- 最终 failed/pending 或无证据：不能借用任何历史 passed。
- 既有 unknown/completed 历史输入不允许恢复；多个 SDK 输入仍失败。

同一规则也覆盖 element_exists/text_appears，避免它们借用较早的成功结果。生产 SDK、输入路由、审批、观察寿命和恢复逻辑均未修改；原生完成 unknown 没有提升为 completed，也没有重投。

## 原证据离线重算

依据 [Luna max 原现场报告](/Users/ruirui/Documents/Project/Agent/developing/docs/acceptance/computer-use-luna-functional-retest-2026-10-05.md)。[重算 sidecar](evidence/computer-use-recovery-verdict-2026-10-05/recomputed.json) 记录每份原 evidence 的 SHA-256、原判定、baseline 源码 SHA、修复源码 SHA、独立快照、属性证明、输入次数及重算结果。原 complete.json 的 SHA 也被保留，未覆盖原始汇总。

| 场景 | 原始 verdict | 修复后离线重算 | 已证明未执行的恢复 / 原生输入 |
| --- | --- | --- | --- |
| 语义滚动容器 | passed | passed | 0 / 1 |
| 前台 enabled | passed | passed | 0 / 1 |
| 后台 enabled | unsupported | passed | 1 / 1 |
| 前台坐标滚轮 | passed | passed | 1 / 1 |
| 后台元素双击 | passed | passed | 2 / 1 |

后台属性只采用最终 luna-5 的 unknown/background/postcondition=passed，较早 luna-3 的 stale/not_started/not_checked 继续作为恢复证明保留。最终 exact/readable/true 及旧 token 匹配事实未改写；原生完成历史仍为 [not_started, unknown]。

## 回归与交付

新增 18 案：属性/存在/文本恢复成功，重复历史回执与两次 stale 的去重汇总，最终不可用、最终失败、最终回执缺失/错 call_id/冲突/错误 envelope/not_started，以及历史 unknown/completed、缺恢复证明、多原生输入。定向测试 **47 passed in 0.88s**；computer-use **556 passed in 15.90s**；完整 offline **3171 passed / 2 deselected in 208.78s**。Ruff check、改动文件 format --check、compileall、CLI --help 与 git diff --check 均通过；全仓库 format 仅用户原 run_tb2.py 差异，未改动该文件。

[computer-use 日志](evidence/computer-use-recovery-verdict-2026-10-05/computer.log) · [完整 offline 日志](evidence/computer-use-recovery-verdict-2026-10-05/offline.log)。

只修改验收脚本、回归与验收说明，无 GUI/生产 SDK 改动，不重复构建。修复提交 1288ed64；报告与 SHA 绑定 sidecar 独立保留。用户原 benchmark/docs staged/dirty 未触碰、未提交。
