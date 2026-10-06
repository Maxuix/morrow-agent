> Portable reading copy. Original bytes and SHA-256 are preserved in [the evidence manifest](../../../manifest.json). Links alone are adapted for this repository.

# Computer-use 五项功能的 Luna max 现场复测（2026-10-05）

**结论：五项功能的真实效果均已验证；验收仍存在一处恢复后的计分缺口。原始判定为 4 passed / 1 unsupported，不能记录为原始 5/5 passed。** 后台 enabled 的最终精确读回和实际点击均成功，却因更早 stale/not_started 的 not_checked 被误判为不可用。

## 版本与方法

- 当前 HEAD：`787dd246f526eb836fe25bd09d70bab5d5c0faa1`；macOS arm64 功能驱动 `0.30.4+morrow.3`，基于官方 0.30.4 的仓库补丁构建，不是未补丁的官方包。
- 已安装 dylib SHA-256：`dc35e83565283558c7ee46e08ff834bcd36eaebf56855084dae200985c557703`，与交付构建一致；版本、安装来源、fixture 源码摘要见[环境](../../../raw/docs/acceptance/assets/computer-use-luna-functional-retest-2026-10-05-0337/environment.json)。
- 实際控制模型：**Codex gpt-6-luna / max**，每份决策均根据当前 Morrow 请求、真实树和截图独立生成，经 receipt-only LiveControllerProvider、普通 AgentLoop/ToolExecutor、Morrow computer-use 和已安装驱动执行。
- 五案各用新编译的正式 v4 fixture 与独立 Python host。未用 ScriptedProvider、DeepSeek、直接 SDK/CUA 代替 Luna 的 Morrow 工具控制；控制代理未读 oracle。父代理只做启动、透明 SDK 记录、独立 fixture 审计和报告。
- **33 份回执全部 received/accepted；5 次原生输入，每案 1 次；2 次精确属性只读调用独立计数。** 请求、决策、图片哈希均匹配；SDK 观察/输入/属性读取命中相同本案 PID/window；旧对象读取 token 与派发 token 一致；五个 fixture 进程均已退出。
- 总共 9 次动作尝试，4 次明确 stale/not_started 未执行，按当前 Morrow 提示的最多三次尝试规则刷新恢复。未扩大 TTL、未伪造时钟、未重投 unknown/completed 输入。源码未因现场测试而修改。

[原始汇总](../../../raw/docs/acceptance/assets/computer-use-luna-functional-retest-2026-10-05-0337/complete.json) · [逐案审计](../../../raw/docs/acceptance/assets/computer-use-luna-functional-retest-2026-10-05-0337/artifact-audit.json) · [上一轮比较](../../../raw/docs/acceptance/assets/computer-use-luna-functional-retest-2026-10-05-0337/comparison.json) · [Luna 控制记录](../../../raw/docs/acceptance/assets/computer-use-luna-functional-retest-2026-10-05-0337/controller-notes.json)

## 逐项结果

| 场景 | 尝试 / stale 恢复 / 原生输入 | 实际效果与核验 | 原始 verdict |
| --- | --- | --- | --- |
| 语义滚动容器（background） | 1 / 0 / 1 | 真实 AXScrollArea/fixture-scroll ref，观察与派发 token 相等；区域内 3 次 wheel，deltaY 各 -1，scrollOffset +30。功能通过。 | passed / independent_effect |
| 前台 enabled 精确读回 | 1 / 0 / 1 | Count、按钮回调各 +1；旧对象 exact/readable/true，token 匹配，postcondition passed。功能通过。 | passed / independent_effect |
| 后台 enabled 精确读回 | 2 / 1 / 1 | 首次 stale 未执行；新观察恢复后 Count、按钮回调各 +1，旧对象 exact/readable/true，postcondition passed。功能通过，计分误判。 | unsupported / sdk_exact_attribute_readback_unavailable |
| 前台坐标滚轮 | 2 / 1 / 1 | 首次 stale 未执行；刷新后前台单次派发，区域内 3 次 wheel，deltaY 各 -1，scrollOffset +30。功能通过。 | passed / independent_effect |
| 后台元素左键双击 | 3 / 2 / 1 | 前两次均 stale 未执行；新观察第三次派发一次，clickCount=[1,2]，Count +2，局部坐标 (80,382)，delivery=background。功能通过。 | passed / independent_effect |

原始证据：[语义容器](../../../raw/docs/acceptance/assets/computer-use-luna-functional-retest-2026-10-05-0337/semantic-container/evidence.json)、[前台属性](../../../raw/docs/acceptance/assets/computer-use-luna-functional-retest-2026-10-05-0337/enabled-foreground/evidence.json)、[后台属性](../../../raw/docs/acceptance/assets/computer-use-luna-functional-retest-2026-10-05-0337/enabled-background/evidence.json)、[前台滚轮](../../../raw/docs/acceptance/assets/computer-use-luna-functional-retest-2026-10-05-0337/foreground-wheel/evidence.json)、[后台双击](../../../raw/docs/acceptance/assets/computer-use-luna-functional-retest-2026-10-05-0337/background-double/evidence.json)。各目录保存 before/after 独立快照、完整 request/decision/receipt-status、实际截图与运行日志。原始 unsupported 未改写为 passed。

五案最终 native outcome 都是 **unknown/unverified_action**；这与独立 fixture 效果通过、两案属性 postcondition=passed 分开记录。没有将 native 状态提升为 completed，Luna 没有因 unknown 重试。健康导出与新 revision 均有效，原有三个 SDK 能力拒绝/容器缺失阻塞本轮已不再出现。

## P2：后台属性恢复成功仍被计为 unsupported

位置：[campaign_verdict.py:261](../../../../../../evals/computer_use/campaign_verdict.py#L261)。代码对所有历史 action_outcomes 执行 `any(postcondition == not_checked)`；未执行的 stale 尝试也被纳入“精确属性读回不可用”的判断。

本轮实际序列：

1. 第一次：stale_observation / not_started / not_checked，没有 SDK 输入。
2. 新观察恢复后：unknown / background / postcondition=passed；唯一原生输入、Count +1、exact/readable/true 和旧 token 匹配均成立。
3. verdict 已承认 recovered_not_started=1，却仍因第 1 步的 not_checked 返回 unsupported，掩盖成功的最终读回。

[隔离诊断](../../../raw/docs/acceptance/assets/computer-use-luna-functional-retest-2026-10-05-0337/attribute-stale-scoring-diagnostic.json)保留证据与 verdict 源码 SHA：未修改副本的 verdict 为 unsupported；只在诊断副本中去掉前面的未执行 outcome，其他动作尝试、输入次数、恢复证明及最终效果不变，verdict 即为 passed。此诊断新增原生输入 0，属于原因分析，不能充当新的现场通过案。原始证据字节保持不变。

修复建议：属性计分应绑定已通过恢复校验的**最终动作及其 call_id/诊断结果**，只用该动作的后置条件判断 passed/not_checked；此前已证实 not_started 的结果用于恢复校验，不用于判最终能力。保留唯一原生入口、新观察/引用、快照健康及新 revision 的要求，不允许跳过 unknown/completed 历史输入。

补充回归：首次 stale/not_started/not_checked → 最终属性 passed 应通过；恢复后最终 not_checked 仍应 unsupported；更早出现 passed 不能掩盖最终失败；无合法恢复证据、此前 unknown/completed、超过一次 native 输入仍拒绝。现有 538 个离线测试没有捕获本轮组合。本轮只复测和报告，未修改该实现。

## 验证与范围

- 本轮运行 `uv run pytest -q -m 'not live' tests/test_computer*.py`：**538 passed in 15.20s**，见[验证记录](../../../raw/docs/acceptance/assets/computer-use-luna-functional-retest-2026-10-05-0337/offline-validation.json)。
- action_inputs、session、projection、live_provider、campaign_verdict 的 scoped Ruff check 与 format --check 通过。
- 本轮五个验收辅助脚本 Ruff check、format --check、py_compile 通过；git diff --check 通过。
- 17 份相关源码/依赖声明/补丁的当前哈希与测试前归档一致，见[来源清单](../../../raw/docs/acceptance/assets/computer-use-luna-functional-retest-2026-10-05-0337/source-manifest.json)和[源码审计](../../../raw/docs/acceptance/assets/computer-use-luna-functional-retest-2026-10-05-0337/source-audit.json)。旧报告、旧证据保持原值；未覆盖或提交用户已有 benchmark/docs 变更，未更新 `.agent/`。
- 本轮范围为五个旧失败项及出现的恢复分支，未重跑所有 computer-use 功能/其他平台/旧 macOS。实际模型控制路径是 Codex Luna 经审计桥调用 Morrow，没有验证 Morrow 的 Luna HTTP Provider 接入。
- 没有新增或恢复 password 等关键词安全过滤，也没有把内容关键词过滤列为修复方案。
