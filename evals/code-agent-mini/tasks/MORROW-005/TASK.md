# 修复异步 Preference Review 生命周期

异步 Preference Review 在真实使用中存在多个生命周期缺口：过大的上下文可能让已完成 Turn 回滚；可重试失败会阻断后续待处理 Job；后台 Worker 没有稳定调度下一次重试；显式 Review 可能绕过统一的 claim/complete 流程；最终过期尝试没有进入 exhausted；CLI 和 REPL 也缺少足够的可观察与唤醒行为。

请在不改变 Review 权威边界的前提下修复：

- 过大或不适合 Review 的 Snapshot 只能跳过 Review，不能回滚已经完成的 Turn；
- 单个可重试失败不能阻塞队列中的其他 Job；
- 后台 Worker 使用注入式调度器安排重试，不使用墙钟 sleep；
- 显式 Review 与后台路径共享 lease、claim、完成和失败语义；
- 最后一次过期尝试进入稳定的 exhausted 状态；
- 查询和 CLI 暴露有界 Job 状态，不泄露原始上下文、reasoning 或异常；
- REPL 在一次分发闭合后安全唤醒 Review Worker。

使用脚本化 Reviewer、固定时钟和临时存储完成离线测试。
