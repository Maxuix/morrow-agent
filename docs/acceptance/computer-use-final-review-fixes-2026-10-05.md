# Computer-use 最终 review 缺口修复

日期：2026-10-05。基线：main `c01ca89356e044e5268df67fc62ad59130d9bdf3`。

## 修复结果

本轮修复最终 review 确认的两处 P2 问题，不新增桌面动作或驱动补丁。

1. **候选刷新误撤销已确认窗口。** `LocalCandidateRegistry.clear()` 同时清除候选目录和确认绑定；共享 owner 的另一个会话刷新窗口列表，就会让前一个会话在 SDK Session 创建前收到 `unknown_target`。现在目录刷新、重新发布与到期都只清理候选编号，确认绑定保留至一次性消费或明确撤销。开启运行仍核对原进程出生身份；取消/quarantine/关闭仍撤销所有待用绑定。
2. **模型工具说明沿用旧官方 SDK 能力限制。** 普通运行的 `computer_action` 描述现在说明功能驱动支持后台元素左键双击、前台坐标滚轮、滚动容器引用和原元素 `enabled` 精确读回；同时保留旧官方 0.30.4 的限制，以及后台坐标/右键双击仍不支持的边界。未新增 capability 框架，也没有让 Application 导入 adapter SDK 常量。

确认绑定与目录寿命分离后，Application 在替换、清除、淘汰或拒绝过期待用选择时，按该选择的窗口身份释放绑定；CLI `/computer clear` 使用同一回收入口。否则反复重选会积累弃用绑定直至触发 `target_budget`。这一回收不影响其他会话的选择，已移交给运行的绑定仍由 owner 一次性消费。

## 验证

- 增加 13 个参数化回归场景，覆盖正常/空列表/预算拒绝刷新、跨会话确认与独立消费、刷新后进程身份变化、明确撤销、按选择回收、跨会话 HTTP 刷新、超过预算次数的原目录重选/刷新重选、CLI 清除。
- 首轮定向：142 passed；纠正旧窗口范围用例后，该文件 13 passed。
- 全部 computer-use 离线用例：569 passed in 17.18s。
- 最终完整离线：3184 passed、2 deselected in 207.60s。
- Ruff check、改动的 11 个 Python 文件格式检查、compileall、`morrow --help`、`git diff --check` 通过。全仓格式检查仅用户原有 `evals/benchmarks/run_tb2.py` 差异，未修改该文件。
- 首轮全套测试暴露旧用例要求“刷新撤销确认绑定”，已改为“显式丢弃撤销绑定”；刷新保持确认由新用例覆盖。

本轮使用假 SDK 与 scripted Provider，未新增原生桌面输入或真实模型 API 请求；历史 raw 验收证据保持原字节。原生 `unknown` 不被提升为 completed，也不重投。1.7 真实 API 模型全矩阵仍因 Provider 余额条件未满足而独立保留。

## 交付

代码提交：`c45f472c`。最终完整离线门禁通过；修复与本报告一同交付。用户已有 benchmark/docs staged/dirty 不包含在本轮提交中。
