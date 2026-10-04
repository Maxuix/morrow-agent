# Luna 复测问题修复与驱动能力审计（2026-10-04）

本轮修复了验收代码的健康快照判定顺序，新增 13 个回归用例。八案原始证据离线回算为 **3 passed / 4 unsupported / 1 blocked**。五个功能场景仍未通过；本轮没有发起新的原生输入或真实模型调用，也没有升级生产依赖。

## 判定修复

根因：`campaign_verdict` 在查看动作是否进入 SDK 前要求 revision 前进。没有动作、能力拒绝或 stale/not_started 时，健康快照保持 revision 5→5 合理，却被提前归为 `fixture_snapshot_invalid`。

现在所有要求 fixture 证据的场景先检查导出健康、字段合法、正整数 revision 及 revision 不倒退；零入口按真实动作/能力原因归类。只有实际进入原生输入的动作在核验效果前必须取得新 revision。不会把不健康导出、执行后旧快照、伪造的 revision_advanced=true 或超过一次原生输入计为通过。stale 恢复仍要求同 call ID 的未执行证据、新 observation，以及最终一次原生输入和新快照。

| 场景 | 原始判定 | 修复后离线判定 |
| --- | --- | --- |
| 前台坐标滚轮 | failed / fixture_snapshot_invalid | unsupported / native_not_entered |
| 后台元素双击 | failed / fixture_snapshot_invalid | unsupported / native_not_entered |
| 语义滚动容器 | failed / fixture_snapshot_invalid | blocked / action_not_attempted |
| 前后台 enabled（两案） | unsupported / sdk_exact_attribute_readback_unavailable | 不变 |
| fixture 语义点击、长文本及恢复、延迟绑定（三案） | passed | 不变 |

[回算 sidecar](assets/computer-use-luna-retest-fixes-2026-10-04/rescore.json)保存八份原始 evidence 的 SHA-256、原判定、当前判定及 verdict 源码哈希。原报告和原归档未改写。这是同一批证据的重新计分，不能当作新的实时复测或新增功能通过。

## 官方 0.32.0 审计与剩余修复条件

审计依据是官方 [0.32.0 发布标签](https://github.com/trycua/cua/releases/tag/cua-driver-rs-v0.32.0)的公开源码，使用 AST 读取接口字段，没有导入或运行新版 SDK。[审计摘要](assets/computer-use-luna-retest-fixes-2026-10-04/sdk-source-audit.json)保存源文件哈希和字段列表。发行 wheel 下载未完成，不声称完成 wheel 验证。

1. **前后台 enabled 精确读回**：新版 [ElementSelector / ElementPredicate](https://github.com/trycua/cua/blob/cua-driver-rs-v0.32.0/libs/cua-driver/python/src/cua_driver/_native_contract.py)仍只有 role、label_contains 选择条件，没有旧 element_token 绑定。重新扫描树无法证明是同一对象。驱动需新增同 PID/window、旧 token 对象的只读属性接口，明确 expired/removed/unknown，再接入 verifier。仅升级到该版本不能补齐这两案。
2. **语义容器**：新版 [AX 树遍历](https://github.com/trycua/cua/blob/cua-driver-rs-v0.32.0/libs/cua-driver/rust/crates/platform-macos/src/ax/tree.rs#L433)仍直接折叠 AXScrollArea/AXGroup 并递归子节点，未为滚动容器生成公开节点/token。这解释了扩大 Morrow 深度/节点预算也无法获取容器。驱动需保留真实可滚动 AXScrollArea 的身份、父子关系、frame 与 token，再在 Morrow 用实际容器 ref 验证 scrollOffset 和正确区域 wheel。
3. **前台滚轮、后台双击**：对比两个版本的 [scroll](https://github.com/trycua/cua/blob/cua-driver-rs-v0.32.0/libs/cua-driver/rust/crates/platform-macos/src/tools/scroll.rs)与 [double_click](https://github.com/trycua/cua/blob/cua-driver-rs-v0.32.0/libs/cua-driver/rust/crates/platform-macos/src/tools/double_click.rs)，变化包括参数描述、移除 index/snapshot 参数及新的 snapshot resolve；这些变化不能证明实际投递恢复。新版 [AppKit 能力表](https://github.com/trycua/cua/blob/cua-driver-rs-v0.32.0/libs/cua-driver/docs/action-support.md#L51)列出 PX background double，仍把 AX-addressed right/double 列为未证明；没有单列本案 PX foreground wheel。因此保留当前门禁，不能从文档推断本机一定失败或成功。下一步需要候选驱动的合成 fixture 诊断：相同 delivery 下 wheel 落在真实滚动区域且 offset 增加；元素双击实际生成 [1,2] 并命中正确窗口。诊断通过后才能接入并重跑原场景。

Morrow 已使用官方 session.call_tool 承载 scroll 的 delivery_mode；不是漏传参数。不会以键盘滚动、两次单击、自动切换 delivery 或新树中同标签元素的属性冒充修复。unknown 仍不自动重试，内容关键词过滤未恢复。

## 验证与交付边界

- verdict 聚焦测试：29 passed。
- computer-use 离线测试：516 passed（14.20s）。
- 全量离线：3131 passed、2 deselected（199.51s）。
- Ruff check 全仓库通过；两个修改 Python 文件 format --check 通过；compileall、CLI help、git diff --check 通过。
- 全仓库 format --check 仍只报告用户原有的 `evals/benchmarks/run_tb2.py:220`，未改动该文件。
- 没有 GUI、fixture 或生产 SDK 接入变化；不重复无关构建或现场测试。用户原 benchmark/docs 修改及已暂存文件保持原样。

五项能力缺口继续留在执行状态中，不能将本次计分修复标为全部功能完成。
