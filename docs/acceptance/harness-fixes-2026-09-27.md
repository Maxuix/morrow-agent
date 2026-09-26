# Harness 故障注入修复复验

日期：2026-09-27。范围：`harness-final-2026-09-27.md` 的 F1–F4。

| 缺陷 | 修复 | 复验 |
| --- | --- | --- |
| F1 后台输出泄漏 | 在保留窗口内先识别完整凭据与边界片段，再按原始字节游标分页；stdout/stderr 共用规则，UTF-8 跨页时推进到字符边界。超长凭据无法从环形缓冲区识别时保守隐藏窗口。 | 新正式测试覆盖运行中/结束、小 limit、非 ASCII、任意游标、环形截断、分次到达与 stderr；原验收探针转绿。 |
| F2 溢出压缩越过截止 | overflow 恢复与主动压缩一样使用剩余工作预算限制整个 await，截止时走 `run_timeout` 终态。 | 新正式测试覆盖截止后拒绝和等待中超时；原验收探针转绿。 |
| F3 复核重试丢上下文 | 复核提示只在成功接收模型响应后清除；网络/无效响应重试保留原候选和提示，不写入正式历史。 | 新正式测试覆盖网络失败、无效响应及第二轮复核重试，工具不重放；原验收探针转绿。 |
| F4 后台命令缺事实 | start 记录运行事实；首次观察终态时记录结束事实，识别出的验证命令再记录验证事实。运行中的命令使已有验证保持不确定，重复 poll 不重复记录。 | 新正式测试覆盖旧验证失效、运行中复验不被误判、终态失败、重复 poll 和终态后的新验证；原验收探针转绿。 |

修复后执行：`uv run pytest -q docs/acceptance/harness-final-2026-09-27/regression_probes.py`，5 passed；`uv run pytest -q tests/test_harness_acceptance_fixes.py`，20 passed；benchmark 适配器离线测试 27 tests，OK。最终完整离线 Python 回归为 **2549 passed、2 deselected**。第一次完整回归中 `test_planning_generation_projection_and_cancel` 等待流式门闩失败，单独重跑通过，后续两次完整回归均通过。

`uv run ruff format --check .`、`uv run ruff check .`、`uv run python -m compileall -q src tests`、`uv run morrow --help` 和 `git diff --check` 均通过。

本次只验证离线路径。H9 仍是任务接纳预算，未增加请求级硬额度；真实 Harbor 生命周期、强制超时诊断回收和 benchmark 效果仍需单独实测。不能据此推断完成率或准确率变化。
